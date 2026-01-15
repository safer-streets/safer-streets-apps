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
    cache_crime_data,
    cache_demographic_data,
    geographies,
    get_counts_and_features,
    get_ethnicity,
)


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
    st.title("Crime Consistency Explorer")

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

    force = cast(
        Force, st.sidebar.selectbox("Force Area", get_args(Force), index=DEFAULT_FORCE)
    )  # default="West Yorkshire"

    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)

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

        observation_period = st.sidebar.slider(
            "Observation Period (years)",
            min_value=1,
            max_value=3,
            value=3,
            step=1,
            help="Number of years of data to examine",
        )

        counts = counts.iloc[:, -observation_period * 12 :]

        # process data
        with st.spinner("Processing crime and demographic data..."):
            ethnicity = get_ethnicity(raw_population, features)
            ethnicity_average = ethnicity.sum() / ethnicity.sum().sum()

            # make boundary work with the tooltip
            # TODO this could be cached
            boundary = boundary.rename(columns={"PFA23NM": "name"})
            # boundary["n_crimes"] = counts.sum().sum()
            boundary["population"] = ethnicity.sum().sum()
            boundary["crime_rate"] = 12 * counts.sum().mean()
            # this makes the toolips nice but prevents numerical sorting
            for eth in ethnicity.columns:
                boundary[eth] = f"{ethnicity_average[eth]:.1%}"

            hit_count = features[["geometry"]].copy()
            hit_count["name"] = hit_count.index
            hit_count["population"] = ethnicity.sum(axis=1)
            hit_count = hit_count.join(features.area_km2)
            hit_count["count"] = 0
            hit_count["crime_rate"] = 0

            # maximum number of times area can feature
            max_hits = 12 * observation_period + 1 - lookback_window

            for c in counts.T.rolling(lookback_window):
                if len(c) < lookback_window:
                    continue

                mean_count = c.mean().rename("mean")
                hit_count["crime_rate"] += mean_count

                mean_count_by_density = pd.concat(
                    [mean_count, features.area_km2, (mean_count / features.area_km2).rename("density")], axis=1
                ).sort_values(by="density")

                hit = (mean_count_by_density.area_km2.cumsum() > total_area - area_threshold) & (
                    mean_count_by_density["mean"] > 0
                )
                hit_count["count"] += hit

            # annualised crime rate
            hit_count.crime_rate *= 12 / max_hits
            hit_count = hit_count[hit_count["count"] > 0]
            hit_count["opacity"] = 192 * hit_count["count"] / max_hits

            for colname, values in ethnicity.div(ethnicity.sum(axis=1), axis=0).fillna(0).items():
                hit_count[colname] = values.apply(lambda x: f"{x:.1%}")

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

        st.markdown(
            f"## {category} in {force} PFA, {counts.columns[0]} to {counts.columns[-1]}\n"
            f"### Features in the top {area_threshold}km² - {lookback_window} month rolling window"
        )

        tooltip = {
            "html": f"Feature {{name}} population: {{population}}, annual crime rate {{crime_rate}}\nHotspot {{count}} times out of {max_hits} <br/>"
            "Ethnicity breakdown (2021 census):<br/>" + "<br/>".join(f"{eth}: {{{eth}}}" for eth in ethnicity.columns)
        }

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=960,
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
