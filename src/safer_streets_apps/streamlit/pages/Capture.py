from typing import cast, get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from safer_streets_core.utils import (
    CATEGORIES,
    DEFAULT_FORCE,
    Force,
    Month,
)

from safer_streets_apps.streamlit.common import (
    all_months,
    cache_crime_data,
    cache_demographic_data,
    date_range,
    geographies,
    get_counts_and_features,
    get_ethnicity,
)


# TODO move to common if reusable
def get_windowed_ordered_counts(
    counts: pd.DataFrame, month: Month, lookback_window: int, features: gpd.GeoDataFrame
) -> pd.DataFrame:
    windowed_counts = counts[[str(month - i) for i in range(lookback_window)]]
    windowed_counts = windowed_counts.sum(axis=1).rename("n_crimes")
    ordered_counts = pd.concat([windowed_counts, features.area_km2], axis=1)
    ordered_counts["density"] = ordered_counts.n_crimes / ordered_counts.area_km2
    ordered_counts = ordered_counts.sort_values(by="density")
    ordered_counts["cum_area"] = ordered_counts.area_km2.cumsum()
    return ordered_counts


st.set_page_config(layout="wide", page_title="Crime Capture", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Capture Explorer")

    st.markdown("## Highlighting crime hotspots")

    with st.expander("More info..."):
        st.markdown(
            """
The app uses [police.uk](https://data.police.uk) public crime data to determine, given a target total land area, the maximum number of
crimes of a given type that can be captured within that area, in the chosen time window in the last 3 years.

The interactive map displays the "hot" areas (in yellow) with the shaded in proportion to the crime count,
and - optionally - other crime-containing areas (blue). Hovering over a spatial feature will display information about
its crime and demographics (Hover on the force area boundary for average values.)

1. Select the Force Area, Crime Type and Spatial Unit.
2. Adjust the the land area you want to cover, the number of months to look back, and the months to display.
3. Use the slider to move backward or forwards in time
"""
        )

    st.sidebar.header("Capture")

    force = cast(
        Force, st.sidebar.selectbox("Force Area", get_args(Force), index=DEFAULT_FORCE)
    )  # default="West Yorkshire"

    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)

    # m = folium.Map(location=[53.924, -1.832], zoom_start=16)

    # # add marker
    # tooltip = "Tooltip"
    # folium.Marker(
    #     [53.9228, -1.8326], popup="?", tooltip=tooltip
    # ).add_to(m)

    # # call to render Folium map in Streamlit
    # sf.folium_static(m, width=800)

    try:
        with st.spinner("Loading crime and demographic data..."):
            raw_data, boundary = cache_crime_data(force, category)
            try:
                raw_population = cache_demographic_data(force)
            except FileNotFoundError as e:
                st.warning(e)
                raw_population = None

            # map crimes to features
            centroid_lat, centroid_lon = raw_data.lat.mean(), raw_data.lon.mean()
            spatial_unit, spatial_unit_params = geographies[spatial_unit_name]
            counts, features, boundary = get_counts_and_features(
                raw_data, boundary, spatial_unit, **spatial_unit_params
            )
            total_area = features.area_km2.sum()

        area_threshold = st.sidebar.slider(
            "Coverage (km²)",
            1.0,
            100.0,
            step=1.0,
            value=10.0,
            help="Focus on the smallest land area that captures the most crime",
        )

        lookback_window = st.sidebar.slider(
            "Lookback window (months)",
            min_value=1,
            max_value=12,
            value=1,
            step=1,
            help="Number of months of data to aggregate at each step",
        )

        show_missed = st.sidebar.checkbox(
            "Show areas not captured",
            help="Areas that contain some crimes, but not enough to feature in the 'hot' list",
        )

        def display_name(m: Month) -> str:
            if lookback_window == 1:
                return str(m)
            return f"{m - lookback_window + 1} to {m}"

        month = st.sidebar.select_slider(
            "Month selection",
            all_months[lookback_window:],
            value=all_months[-1],
            format_func=display_name,
            help="Select month",
        )

        # process data
        with st.spinner("Processing crime and demographic data..."):
            ethnicity = get_ethnicity(raw_population, features)
            ethnicity_average = ethnicity.sum() / ethnicity.sum().sum()

            ordered_counts = get_windowed_ordered_counts(counts, month, lookback_window, features)

            # make boundary work with the tooltip
            # TODO this could be cached
            boundary = boundary.rename(columns={"PFA23NM": "name"})
            boundary["n_crimes"] = ordered_counts.n_crimes.sum()
            boundary["population"] = ethnicity.sum().sum()
            # this makes the toolips nice but prevents numerical sorting
            for eth in ethnicity.columns:
                boundary[eth] = f"{ethnicity_average[eth]:.1%}"
            boundary["n_crimes"] = ordered_counts.n_crimes.sum()

            # add tooltip info for the features
            tooltip_info = ethnicity.sum(axis=1).rename("population").to_frame()
            for colname, values in ethnicity.div(ethnicity.sum(axis=1), axis=0).fillna(0).items():
                tooltip_info[colname] = values.apply(lambda x: f"{x:.1%}")
            tooltip_info["name"] = ordered_counts.index

            # deal with case where we've captured all incidents in a smaller area than specified
            captured_features = features[["geometry"]].join(
                ordered_counts[
                    (ordered_counts.cum_area >= total_area - area_threshold) & (ordered_counts.n_crimes > 0)
                ],
                how="right",
            )
            captured_features = captured_features.join(tooltip_info)
            captured_features["opacity"] = 192 * captured_features.n_crimes / captured_features.n_crimes.max()

            if show_missed:
                missed_features = features[["geometry"]].join(
                    ordered_counts[
                        (ordered_counts.cum_area < total_area - area_threshold) & (ordered_counts.n_crimes > 0)
                    ],
                    how="right",
                )
                missed_features = missed_features.join(tooltip_info)
                missed_features["opacity"] = 96 * missed_features.n_crimes / missed_features.n_crimes.max()

        # render map
        view_state = pdk.ViewState(
            latitude=centroid_lat,
            longitude=centroid_lon,
            zoom=9,
            pitch=30,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            boundary.__geo_interface__,
            opacity=0.5,
            stroked=True,
            filled=False,
            extruded=False,
            pickable=True,
            line_width_min_pixels=3,
            get_line_color=[192, 64, 64, 255],
        )

        hotspots = (
            pdk.Layer(
                "GeoJsonLayer",
                captured_features.__geo_interface__,
                stroked=True,
                filled=True,
                wireframe=True,
                get_fill_color="[201, 241, 0, properties.opacity]",  # [255, 0, 0, 160],
                get_line_color=[0xC9, 0xF1, 0x00, 0xA0],
                line_width_min_pixels=3,
                pickable=True,
            ),
        )

        layers = [boundary_layer, hotspots]

        if show_missed:
            layers.insert(
                1,
                pdk.Layer(
                    "GeoJsonLayer",
                    missed_features.__geo_interface__,
                    stroked=True,
                    filled=True,
                    wireframe=True,
                    get_fill_color="[0, 63, 245, properties.opacity]",  # [255, 0, 0, 160],
                    get_line_color=[0x00, 0x39, 0xF5, 0x50],
                    line_width_min_pixels=3,
                    pickable=True,
                ),
            )

        # st.dataframe(captured_features)

        start, end = date_range(month - lookback_window + 1, lookback_window)
        st.markdown(f"""
            ### {category} in {force} PFA
            - **Crimes occurring from {start} to {end} inclusive**
            - **{len(captured_features)} features ({spatial_unit_name}) covering
            {captured_features.area_km2.sum():.1f}km² meet the required coverage of {area_threshold}km²**
            - **{captured_features.n_crimes.sum()} crimes
            ({captured_features.n_crimes.sum() / ordered_counts.n_crimes.sum():.1%}) are captured in these features**
            """)

        tooltip = {
            "html": "Feature {name} population: {population}, crimes: {n_crimes}<br/>"
            "Ethnicity breakdown (2021 census):<br/>" + "<br/>".join(f"{eth}: {{{eth}}}" for eth in ethnicity.columns)
        }

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=960,
        )

        with st.expander("Hotspot Table"):
            st.dataframe(boundary.drop(columns="geometry"))  # .style.format("{:.1%}", subset=ethnicity.columns))
            st.dataframe(
                captured_features.drop(columns=["geometry", "cum_area", "name", "opacity"]).sort_values(
                    by="n_crimes", ascending=False
                )
            )

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
