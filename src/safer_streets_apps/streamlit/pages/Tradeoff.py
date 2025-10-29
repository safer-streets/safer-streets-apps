from typing import cast, get_args

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from safer_streets_core.utils import CATEGORIES, Force, data_dir

CONSISTENCY_MEASURES = ("RBO_7", "RBO_8", "RBO_9", "F1_10", "F1_20", "F1_50")
CONCENTRATION_MEASURES = ("L_10", "L_20", "L_50", "Gini", "Modified Gini")

help = """
The app uses [police.uk](https://data.police.uk) public crime data to determine the tradeoff between concentration and
consistency for spatially and temporally aggregated crimes of a given type, over the last 3 years.

1. Select the Force Area, Crime Type, and Aggregation window (data is not available for all forces).
2. Select up to 3 each from the concentration and consistency measures.

### Concentration measures

Label | Description
------|------------
L_10  | The proportion of spatial units required to capture 10% of all crime in the time window
L_20  | The proportion of spatial units required to capture 20% of all crime in the time window
L_50  | The proportion of spatial units required to capture 50% of all crime in the time window
Gini  | The naive Gini coefficient
Modified Gini | [Modified Gini](https://safer-streets.github.io/blog/2025/08/22/measuring-crime-concentration-part-1) coefficient

NB In general, higher values represent higher concentration or consistency. However for the Lorenz (L) measures, a
_lower_ value indicates greater concentration, so the x-axis is inverted.

### Consistency Measures

Label | Description
------|------------
RBO_7 | Rank-biased overlap. Weighted comparison of hotspot rankings from one period to the next, weighting decay of 0.7
RBO_8 | Rank-biased overlap. Weighted comparison of hotspot rankings from one period to the next, weighting decay of 0.8
RBO_9 | Rank-biased overlap. Weighted comparison of hotspot rankings from one period to the next, weighting decay of 0.9
F1_10 | F1-score for prediction of top 10% hotspots from previous period
F1_20 | F1-score for prediction of top 20% hotspots from previous period
F1_50 | F1-score for prediction of top 50% hotspots from previous period

The graphs display scatter plots of paired values for all the possible time windows within the 3 year period in which
data was collected. Consistency will increase with the time window, since the temporal units overlap.

The legend annotations include the mean crime count per the spatial unit in the time window. The dimensions given for
the hex units represent the length of one side. E.g. a 200m hex has a height of ~350m.
"""

st.set_page_config(layout="wide", page_title="Crime Tradeoff", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")


def _get_ax(axs, i: int, j: int, ni, nj):
    match ni, nj:
        case 1, 1:
            return axs
        case 1, _:
            return axs[j]
        case _, 1:
            return axs[i]
        case _, _:
            return axs[j, i]


def _get_count_label(data: pd.Series) -> str:
    mean_count = data.mean()
    if mean_count < 0.5:
        return "n<1"
    return f"n~{mean_count:.0f}"


@st.cache_data
def _cache_tradeoff_data(force: Force) -> pd.DataFrame:
    return pd.read_parquet(data_dir() / f"tradeoff_results_{force}.parquet")


def main() -> None:
    "Entry point"
    st.title("Crime Tradeoff: Concentration vs Consistency")

    st.markdown(
        "#### Highlighting the tradeoff between crime concentration and hotspot consistency, at different spatial and "
        "temporal scales, using a variety of measures"
    )

    with st.expander("More info..."):
        st.markdown(help)

    st.sidebar.header("Parameters")

    force = cast(Force, st.sidebar.selectbox("Force Area", get_args(Force), index=43))  # default="West Yorkshire"
    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)
    time_window = st.sidebar.select_slider("Aggregation window (months)", [1, 2, 3, 6, 12])
    concentration_measures = st.sidebar.multiselect("Concentration measures", CONCENTRATION_MEASURES, max_selections=3)
    consistency_measures = st.sidebar.multiselect("Consistency measures", CONSISTENCY_MEASURES, max_selections=3)

    if not concentration_measures or not consistency_measures:
        st.warning("**Select up to 2 each from concentration and consistency measures**")
        return

    try:
        tradeoff_data = _cache_tradeoff_data(force).loc[category]

        fig, axs = plt.subplots(len(consistency_measures), len(concentration_measures), figsize=(12, 12), sharey=True)

        for i, x in enumerate(concentration_measures):
            for j, y in enumerate(consistency_measures):
                ax = _get_ax(axs, i, j, len(concentration_measures), len(consistency_measures))
                for idx, data in tradeoff_data[["Count", x, y]].xs(time_window, level=1).iterrows():
                    label = f"{idx} ({_get_count_label(data.Count)})"
                    ax.scatter(data[x][1:], data[y], label=label, alpha=0.5)
                    # legend not working
                    # sns.kdeplot(x=data[x][1:], y=data[y], label=idx, fill=True, ax=ax)

                legend = ax.legend()
                for item in legend.legend_handles:
                    item.set_alpha(1)
                ax.set_xlim(0, 1)
                ax.set_ylim(0, 1)
                if j == len(consistency_measures) - 1:
                    ax.set_xlabel(f"Concentration ({x})")
                if i == 0:
                    ax.set_ylabel(f"Consistency ({y})")
                # invert if L-measure (means we can't have sharex=True)
                if x.startswith("L"):
                    ax.xaxis.set_inverted(True)

        st.markdown(f"### {category} in {force}, {time_window}-month windows")
        plt.tight_layout()
        st.pyplot(fig)

    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
