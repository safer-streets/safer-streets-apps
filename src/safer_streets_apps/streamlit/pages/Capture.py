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
    cache_demographic_data,
    date_range,
    geographies,
    get_boundary,
    get_counts_and_features,
    get_ethnicity,
    get_ethnicity_totals,
)


# TODO move to common if reusable
def get_ordered_counts(counts: pd.DataFrame, month: Month, features: gpd.GeoDataFrame) -> pd.DataFrame:
    ordered_counts = pd.concat([counts.sum(axis=1).rename("n_crimes"), features.area_km2], axis=1)
    ordered_counts["density"] = ordered_counts.n_crimes / ordered_counts.area_km2
    ordered_counts = ordered_counts.sort_values(by="density", ascending=False)
    # cum area not including current row
    ordered_counts["cum_area"] = ordered_counts.area_km2.cumsum().shift(fill_value=0)
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

Demographic data is take from the 2021 Census.

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

    try:
        with st.spinner("Loading crime and geographic data..."):
            boundary = get_boundary(force)
            total_area = boundary["area"].sum()
            centroid_lat, centroid_lon = boundary.lat.mean(), boundary.lon.mean()

            features, counts = get_counts_and_features(force, spatial_unit_name, category, str(month), lookback_window)

        # process data
        with st.spinner("Processing crime and demographic data..."):
            try:
                raw_population = cache_demographic_data(force)
            except FileNotFoundError as e:
                st.warning(e)
                raw_population = None

            ethnicity = get_ethnicity(raw_population, features)
            ethnicity_total = get_ethnicity_totals(raw_population, force)

            ordered_counts = get_ordered_counts(counts, month, features)

            # make boundary work with the tooltip
            boundary["n_crimes"] = ordered_counts.n_crimes.sum()
            boundary["population"] = ethnicity_total.sum()
            # this makes the toolips nice but prevents numerical sorting
            for eth in ethnicity_total.index:
                boundary[eth] = f"{ethnicity_total[eth] / ethnicity_total.sum():.1%}"
            boundary["n_crimes"] = ordered_counts.n_crimes.sum()

            # add tooltip info for the features
            tooltip_info = ethnicity.sum(axis=1).rename("population").to_frame()
            for colname, values in ethnicity.div(ethnicity.sum(axis=1), axis=0).fillna(0).items():
                tooltip_info[colname] = values.apply(lambda x: f"{x:.1%}")
            tooltip_info["name"] = ethnicity.index

            # deal with case where we've captured all incidents in a smaller area than specified
            captured_features = features[["geometry"]].join(
                ordered_counts[(ordered_counts.cum_area < area_threshold) & (ordered_counts.n_crimes > 0)],
                how="right",
            )
            captured_features = captured_features.join(tooltip_info)
            captured_features["opacity"] = 192 * captured_features.n_crimes / captured_features.n_crimes.max()

            if show_missed:
                missed_features = features[["geometry"]].join(
                    ordered_counts[(ordered_counts.cum_area > area_threshold) & (ordered_counts.n_crimes > 0)],
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

        start, end = date_range(month - lookback_window + 1, lookback_window)
        st.markdown(f"""
            ### {category} in {force} PFA
            - **{ordered_counts.n_crimes.sum()} incidents occurred between {start} and {end} inclusive**
            - **{len(captured_features)} features ({spatial_unit_name}) covering
            {captured_features.area_km2.sum():.1f}km² meet the required coverage of {area_threshold}km²**
            - **{captured_features.n_crimes.sum()} crimes
            ({captured_features.n_crimes.sum() / ordered_counts.n_crimes.sum():.1%}) are captured in these features,
            which comprise {captured_features.area_km2.sum() / total_area:.2%} of the PFA ({total_area:.1f}km²)**
            """)

        tooltip = {
            "html": "Feature {name} crimes: {n_crimes}<br/>"
            "Population: {population} (2021 census)<br/>"
            + "<br/>".join(f"{eth}: {{{eth}}}" for eth in ethnicity.columns)
        }

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=800,
        )

        with st.expander("Hotspot Table"):
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
