from typing import cast, get_args

import pandas as pd
import pydeck as pdk
import streamlit as st
from safer_streets_core.utils import CATEGORIES, DEFAULT_FORCE, Force, latest_month

from safer_streets_apps.streamlit.common import (
    cache_demographic_data,
    date_range,
    geographies,
    get_boundary,
    get_counts_and_features,
    get_ethnicity,
    get_ethnicity_totals,
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
    if "observation_period" not in st.session_state:
        st.session_state.observation_period = 3
    if "demographics" not in st.session_state:
        st.session_state.demographics = False


def main() -> None:
    init()
    st.title("Crime Consistency Explorer")

    st.warning(
        "##### :construction: This page uses the experimental Safer Streets [geospatial data API]"
        "(https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net/docs#/)"
    )

    st.markdown("## Highlighting persistent crime hotspots")

    with st.expander("More info..."):
        st.markdown("""
The app uses [police.uk](https://data.police.uk) public crime data to determine, how consistently areas feature in the top areas for crimes of a
given type, over the last 3 years.

The interactive map displays the "hot" areas shaded in yellow according to the the number of times they feature in
the hotspots - that is, the set of areas that capture the most crime for the total coverage specified.

1. Select the Force Area, Crime Type and Spatial Unit.
2. Adjust the the land area you want to cover, the number of months to look back (average) over, and the time period you want to observe.
""")

    st.sidebar.header("Consistency")

    st.session_state.force = cast(
        Force, st.sidebar.selectbox("Force Area", get_args(Force), index=get_args(Force).index(st.session_state.force))
    )  # default="West Yorkshire"

    st.session_state.category = st.sidebar.selectbox(
        "Crime type", CATEGORIES, index=CATEGORIES.index(st.session_state.category)
    )

    st.session_state.spatial_unit_name = st.sidebar.selectbox(
        "Spatial Unit", geographies.keys(), index=list(geographies.keys()).index(st.session_state.spatial_unit_name)
    )

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

    st.session_state.observation_period = st.sidebar.slider(
        "Observation Period (years)",
        min_value=1,
        max_value=3,
        value=st.session_state.observation_period,
        step=1,
        help="Number of years of data to examine",
    )

    st.session_state.demographics = st.sidebar.checkbox(
        "Show feature demographics",
        help="Include a breakdown of the population by ethnicity in each feature, if available. "
        "NB this is resource-intensive and will slow the app down significantly",
    )

    try:
        with st.spinner("Loading crime data..."):
            boundary = get_boundary(st.session_state.force)
            total_area = boundary["area"].sum()
            centroid_lat, centroid_lon = boundary.lat.mean(), boundary.lon.mean()

            features, counts = get_counts_and_features(
                st.session_state.force,
                st.session_state.spatial_unit_name,
                st.session_state.category,
                str(latest_month()),
                st.session_state.observation_period * 12,
            )

        if st.session_state.demographics:
            with st.spinner("Loading demographic data..."):
                try:
                    raw_population = cache_demographic_data(st.session_state.force)
                except FileNotFoundError as e:
                    st.warning(e)
                    raw_population = None
        else:
            raw_population = None
        ethnicity = get_ethnicity(raw_population, features)
        ethnicity_total = get_ethnicity_totals(raw_population, st.session_state.force)

        # process data
        with st.spinner("Processing crime data..."):
            # make boundary work with the tooltip
            # boundary["n_crimes"] = counts.sum().sum()
            boundary["population"] = ethnicity_total.sum()
            boundary["crime_rate"] = f"{12 * counts.sum().mean():.1f}"
            # this makes the toolips nice but prevents numerical sorting
            for eth in ethnicity_total.index:
                boundary[eth] = f"{ethnicity_total[eth] / ethnicity_total.sum():.1%}"

            hit_count = features[["geometry"]].copy()
            hit_count["name"] = hit_count.index
            hit_count["population"] = ethnicity.sum(axis=1)
            hit_count = hit_count.join(features.area_km2)
            hit_count["count"] = 0
            hit_count["crime_rate"] = 0

            # maximum number of times area can feature
            max_hits = 12 * st.session_state.observation_period + 1 - st.session_state.lookback_window

            for c in counts.T.rolling(st.session_state.lookback_window):
                if len(c) < st.session_state.lookback_window:
                    continue

                mean_count = c.mean().rename("mean")
                hit_count["crime_rate"] += mean_count

                mean_count_by_density = pd.concat(
                    [mean_count, features.area_km2, (mean_count / features.area_km2).rename("density")], axis=1
                ).sort_values(by="density", ascending=False)
                mean_count_by_density["cum_area"] = mean_count_by_density.area_km2.cumsum().shift(fill_value=0)

                hit = (mean_count_by_density.cum_area < st.session_state.area_threshold) & (
                    mean_count_by_density["mean"] > 0
                )
                hit_count["count"] += hit

            # annualised crime rate
            hit_count.crime_rate *= 12 / max_hits
            hit_count = hit_count[hit_count["count"] > 0]
            hit_count["opacity"] = 192 * hit_count["count"] / max_hits

            for colname, values in ethnicity.div(ethnicity.sum(axis=1), axis=0).fillna(0).items():
                hit_count[colname] = values.apply(lambda x: f"{x:.1%}")

            hit_count.crime_rate = hit_count.crime_rate.map(lambda r: f"{r:.1f}")

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
                hit_count.__geo_interface__,
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

        start, end = date_range(
            latest_month() - st.session_state.observation_period * 12 + 1, st.session_state.observation_period * 12
        )
        st.markdown(f"""
            ### {st.session_state.category} in {st.session_state.force} PFA
            - **Crimes occurring from {start} to {end} inclusive**
            - **Features shown in the top {st.session_state.area_threshold}km² over a {st.session_state.lookback_window} month rolling window**
            - **{len(hit_count)} features appear at least once and cover a total area of {hit_count.area_km2.sum():.1f}km²
            which comprise {hit_count.area_km2.sum() / total_area:.2%} of the PFA ({total_area:.1f}km²)**
            - **{(hit_count["count"] == max_hits).sum()} features persistently appear in every lookback window**
            """)

        tooltip = {
            "html": f"Feature {{name}} population: {{population}}<br/>Annual crime rate {{crime_rate}}<br/>"
            f"Hotspot {{count}} times out of {max_hits} <br/>"
            "Ethnicity breakdown (2021 census):<br/>" + "<br/>".join(f"{eth}: {{{eth}}}" for eth in ethnicity.columns)
        }

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=800,
        )

        with st.expander("Hotspot Table"):
            st.dataframe(boundary.drop(columns="geometry"))  # .style.format("{:.1%}", subset=ethnicity.columns))
            st.dataframe(
                hit_count.drop(columns=["geometry", "name", "opacity"]).sort_values(
                    by=["count", "crime_rate"], ascending=False
                )
            )

    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
