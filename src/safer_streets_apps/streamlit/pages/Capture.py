from typing import cast, get_args

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
    get_ordered_counts,
)

st.set_page_config(layout="wide", page_title="Crime Capture", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")


def init() -> None:
    if "force" not in st.session_state:
        st.session_state.force = get_args(Force)[DEFAULT_FORCE]
    if "category" not in st.session_state:
        st.session_state.category = CATEGORIES[1]
    if "spatial_unit_name" not in st.session_state:
        st.session_state.spatial_unit_name = list(geographies.keys())[0]
    if "area_threshold" not in st.session_state:
        st.session_state.area_threshold = 10.0
    if "lookback_window" not in st.session_state:
        st.session_state.lookback_window = 1
    if "show_missed" not in st.session_state:
        st.session_state.show_missed = False
    if "month" not in st.session_state:
        st.session_state.month = all_months[-1]
    if "demographics" not in st.session_state:
        st.session_state.demographics = False


def main() -> None:
    init()
    st.title("Crime Capture Explorer")

    st.warning(
        "##### :construction: This page uses the experimental Safer Streets [geospatial data API]"
        "(https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net/docs#/)"
    )

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

    st.session_state.force = cast(
        Force, st.sidebar.selectbox("Force Area", get_args(Force), index=get_args(Force).index(st.session_state.force))
    )  # default="West Yorkshire"

    st.session_state.category = st.sidebar.selectbox(
        "Crime type", CATEGORIES, index=CATEGORIES.index(st.session_state.category)
    )

    st.session_state.spatial_unit_name = st.sidebar.selectbox(
        "Spatial Unit", geographies.keys(), index=list(geographies.keys()).index(st.session_state.spatial_unit_name)
    )

    # m = folium.Map(location=[53.924, -1.832], zoom_start=16)

    # # add marker
    # tooltip = "Tooltip"
    # folium.Marker(
    #     [53.9228, -1.8326], popup="?", tooltip=tooltip
    # ).add_to(m)

    # # call to render Folium map in Streamlit
    # sf.folium_static(m, width=800)

    st.session_state.area_threshold = st.sidebar.slider(
        "Coverage (km²)",
        1.0,
        100.0,
        step=1.0,
        value=st.session_state.area_threshold,
        help="Focus on the smallest land area that captures the most crime",
    )

    st.session_state.lookback_window = st.sidebar.slider(
        "Lookback window (months)",
        min_value=1,
        max_value=12,
        value=st.session_state.lookback_window,
        step=1,
        help="Number of months of data to aggregate at each step",
    )

    st.session_state.show_missed = st.sidebar.checkbox(
        "Show areas not captured",
        help="Areas that contain some crimes, but not enough to feature in the 'hot' list",
    )

    def display_name(m: Month) -> str:
        if st.session_state.lookback_window == 1:
            return str(m)
        return f"{m - st.session_state.lookback_window + 1} to {m}"

    st.session_state.month = st.sidebar.select_slider(
        "Month selection",
        all_months[st.session_state.lookback_window :],
        value=st.session_state.month,
        format_func=display_name,
        help="Select month",
    )

    st.session_state.demographics = st.sidebar.checkbox(
        "Show feature demographics",
        help="Include a breakdown of the population by ethnicity in each feature, if available. "
        "NB this is resource-intensive and will slow the app down significantly",
    )

    try:
        with st.spinner("Loading crime and geographic data..."):
            boundary = get_boundary(st.session_state.force)
            total_area = boundary["area"].sum()
            centroid_lat, centroid_lon = boundary.lat.mean(), boundary.lon.mean()

            features, counts = get_counts_and_features(
                st.session_state.force,
                st.session_state.spatial_unit_name,
                st.session_state.category,
                str(st.session_state.month),
                st.session_state.lookback_window,
            )

        # process data
        if st.session_state.demographics:
            with st.spinner("Processing demographic data..."):
                try:
                    raw_population = cache_demographic_data(st.session_state.force)
                except FileNotFoundError as e:
                    st.warning(e)
                    raw_population = None

        else:
            raw_population = None
        ethnicity = get_ethnicity(raw_population, features)
        ethnicity_total = get_ethnicity_totals(raw_population, st.session_state.force)

        with st.spinner("Processing crime data..."):
            ordered_counts = get_ordered_counts(counts, st.session_state.month, features)

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
                ordered_counts[
                    (ordered_counts.cum_area < st.session_state.area_threshold) & (ordered_counts.n_crimes > 0)
                ],
                how="right",
            )
            captured_features = captured_features.join(tooltip_info)
            captured_features["opacity"] = 192 * captured_features.n_crimes / captured_features.n_crimes.max()

            if st.session_state.show_missed:
                missed_features = features[["geometry"]].join(
                    ordered_counts[
                        (ordered_counts.cum_area > st.session_state.area_threshold) & (ordered_counts.n_crimes > 0)
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

        if st.session_state.show_missed:
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

        start, end = date_range(
            st.session_state.month - st.session_state.lookback_window + 1, st.session_state.lookback_window
        )
        st.markdown(f"""
            ### {st.session_state.category} in {st.session_state.force} PFA
            - **{ordered_counts.n_crimes.sum()} incidents occurred between {start} and {end} inclusive**
            - **{len(captured_features)} features ({st.session_state.spatial_unit_name}) covering
            {captured_features.area_km2.sum():.1f}km² meet the required coverage of {st.session_state.area_threshold}km²**
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
