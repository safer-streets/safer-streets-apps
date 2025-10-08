from typing import cast, get_args

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from safer_streets_core.charts import make_radar_chart
from safer_streets_core.measures import calc_gini, cosine_similarity, lorenz_curve
from safer_streets_core.utils import CATEGORIES, Force, Month
from sklearn.metrics import f1_score

from safer_streets_apps.streamlit.common import (
    all_months,
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
    st.title("Crime Consistency Explorer (More)")

    st.markdown("## Highlighting persistent crime hotspots")

    with st.expander("More info..."):
        st.markdown("""
The app uses police.uk public crime data to determine, how consistently areas feature in the top areas for crimes of a
given type, over the last 3 years.

1. Select the Force Area, Crime Type and Spatial Unit.
2. Adjust the the land area you want to cover, the number of months to look back (average) over, and the time period you want to observe.
""")

    st.sidebar.header("Consistency")

    force = cast(Force, st.sidebar.selectbox("Force Area", get_args(Force), index=43))  # default="West Yorkshire"

    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=1)

    try:
        with st.spinner("Loading crime and demographic data..."):
            raw_data, boundary = cache_crime_data(force, category)
            raw_population = cache_demographic_data(force)

            # map crimes to features
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

        observation_period = 3  # st.sidebar.slider(
        #     "Observation Period (years)",
        #     min_value=1,
        #     max_value=3,
        #     value=3,
        #     step=1,
        #     help="Number of years of data to examine",
        # )
        # counts = counts.iloc[:, -observation_period * 12 :]

        # process data
        with st.spinner("Processing crime and demographic data..."):
            ethnicity = get_ethnicity(raw_population, features)
            ethnicity_average = ethnicity.sum() / ethnicity.sum().sum()

            mean_density = counts.sum().sum() / observation_period / features.area_km2.sum()
            summary = pd.Series()
            summary["Population (2021)"] = ethnicity.sum().sum()

            summary["Overall Annual crime rate"] = counts.sum().sum() / observation_period
            summary["Number of spatial units"] = len(features)
            summary["Average crime rate per spatial unit"] = counts.sum().sum() / observation_period / len(features)
            summary["Maximum crime rate per spatial unit"] = counts.sum(axis=1).max() / observation_period
            summary["Minimum crime rate per spatial unit"] = counts.sum(axis=1).min() / observation_period

            summary["Average population per spatial unit"] = ethnicity.sum().sum() / len(features)
            summary["Maximum population per spatial unit"] = ethnicity.sum(axis=1).max()
            summary["Minimum population per spatial unit"] = ethnicity.sum(axis=1).min()

            summary = pd.concat([summary, 100 * ethnicity_average])

            summary["Total land area (km²)"] = features.area_km2.sum()
            summary["Average land area per spatial unit"] = features.area_km2.mean()
            summary["Maximum land area per spatial unit"] = features.area_km2.max()
            summary["Minimum land area per spatial unit"] = features.area_km2.min()

            summary.index.name = "Property"
            summary.name = "Value"

            # maximum number of times area can feature
            # max_hits = 12 * observation_period + 1 - lookback_window

            ethnicity_in_hotspots = pd.DataFrame(columns=ethnicity.columns)

            concentration_measures = pd.DataFrame(columns=["Gini", "Captured proportion", "Density Ratio"])
            lorenz_curves = pd.DataFrame()

            consistency_measures = pd.DataFrame(columns=["F1", "Rank-biased overlap", "Spearman Rank Correlation"])

            captured_by_period = pd.DataFrame(index=features.index)

            previous_period = None
            previous_ordered_counts = None
            # for c in counts.T.rolling(lookback_window):
            for month in all_months[lookback_window - 1 :]:
                ordered_counts = get_windowed_ordered_counts(counts, month, lookback_window, features)

                period = f"{month - lookback_window + 1} to {month}" if lookback_window > 1 else f"{month}"

                # deal with case where we've captured all incidents in a smaller area than specified
                hits = (ordered_counts.cum_area >= total_area - area_threshold) & (ordered_counts.n_crimes > 0)

                captured_by_period[period] = hits
                lorenz_curves[period] = lorenz_curve(ordered_counts, data_col="n_crimes")
                concentration_measures.loc[period, "Gini"] = calc_gini(lorenz_curves[period])
                concentration_measures.loc[period, "Captured proportion"] = (
                    ordered_counts[hits].n_crimes.sum() / ordered_counts.n_crimes.sum()
                )
                concentration_measures.loc[period, "Density Ratio"] = (
                    ordered_counts[hits].n_crimes.sum() / features[hits].area_km2.sum() / mean_density
                )

                if previous_period:
                    consistency_measures.loc[period, "F1"] = f1_score(
                        captured_by_period[previous_period], captured_by_period[period]
                    )
                    count_comparison = ordered_counts[["n_crimes"]].join(
                        previous_ordered_counts.n_crimes.rename("previous")
                    )
                    consistency_measures.loc[period, "Cosine similarity"] = cosine_similarity(count_comparison)
                    # TODO spearman and RBO
                    # consistency_measures.loc[period, "Rank-biased overlap"] =
                ethnicity_in_hotspots.loc[period] = ethnicity.loc[ordered_counts[hits].index].sum().T

                previous_period = period
                previous_ordered_counts = ordered_counts
            # break

        st.markdown(
            f"## {category} in {force} PFA, {counts.columns[0]} to {counts.columns[-1]}\n"
            f"### Features in the top {area_threshold}km² - {lookback_window} month rolling window"
        )

        st.markdown("## Concentration")

        cols = st.columns(2)
        fig, ax = plt.subplots(figsize=(8, 8))
        lorenz_curves.plot(ax=ax, legend=False, title="Lorenz Curves")
        cols[0].pyplot(fig)
        cols[1].line_chart(concentration_measures.iloc[:, :2])
        cols[1].line_chart(concentration_measures.iloc[:, 2:])

        st.markdown("## Consistency")

        st.line_chart(consistency_measures)

        st.markdown("## Hotspot Ethnicity")

        cols = st.columns(2)

        # demographics_graph.bar_chart(ethnicity, stack=True)
        ethnicity_in_hotspots = 100 * ethnicity_in_hotspots.div(ethnicity_in_hotspots.sum(axis=1), axis=0)
        cols[0].area_chart(ethnicity_in_hotspots, stack=True, height=600)

        radar_data = ethnicity_in_hotspots - 100 * ethnicity_average
        radar_data.columns = radar_data.columns.map(lambda col: col.split(" ")[0].replace(",", ""))
        fig = plt.figure(figsize=(9, 9))
        cols[1].pyplot(
            make_radar_chart(
                fig,
                111,
                radar_data,
                r_ticks={-100: "", 0: "Average", 100: "+100%"},
                title="Hotspot ethnicity: percentage deviation from PFA average",
            )
        )

        # # annualised crime rate
        # hit_count.crime_rate *= 12 / max_hits
        # hit_count = hit_count[hit_count["count"] > 0]
        # hit_count["opacity"] = 192 * hit_count["count"] / max_hits
        # hit_count = hit_count.join(ethnicity)

        # for colname, values in ethnicity.div(ethnicity.sum(axis=1), axis=0).fillna(0).items():
        #     hit_count[colname] = values.apply(lambda x: f"{x:.1%}")

        with st.expander("Summary Info"):
            st.table(summary)

            # st.dataframe(ethnicity_average)
            # st.dataframe(
            #     hit_count.drop(columns=["geometry", "name", "opacity"]).sort_values(
            #         by=["count", "crime_rate"], ascending=False
            #     )
            # )

    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
