import os
from base64 import b64encode
from io import BytesIO
from typing import Any, get_args

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv
from itrx import Itr
from safer_streets_core.utils import (
    CATEGORIES,
    CrimeType,
    Force,
    data_dir,
    fix_force_name,
    monthgen,
)

from safer_streets_apps.streamlit.common import latest_month

st.set_page_config(layout="wide", page_title="Crime Overview", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")

load_dotenv()

FORCES = tuple(f for f in get_args(Force) if f not in ["BTP", "Greater Manchester", "Northern Ireland", "Gwent"])

FORCES_FOR_MAP = tuple(fix_force_name(f) for f in FORCES)

# URL = "http://localhost:5000"  # note this won't resolve the host's loopback without running with --network=host
URL = os.environ["SAFER_STREETS_API_URL"]
N_MONTHS = 36

HEX_AREA = 0.2**2 * 3**1.5 / 2
EW_AREA = 151_047.0  # according to wikipedia

REF_LAT = 52.7
REF_LON = -2.0


@st.cache_data
def get_crime_counts() -> pd.DataFrame:
    return pd.read_parquet(data_dir() / f"pfa-crime-counts-{latest_month()}.parquet").sort_index()


@st.cache_data
def simplified_pfa_boundaries() -> tuple[dict[str, Any], dict[str, Any]]:
    force_boundaries = gpd.read_file(data_dir() / "Police_Force_Areas_December_2023_EW_BFE_2734900428741300179.zip")
    # this should be significantly smaller than a hex (although its not used in a spatial join)
    force_boundaries.geometry = force_boundaries.simplify(tolerance=50)
    force_boundaries = force_boundaries.to_crs(epsg=4326)

    return (
        force_boundaries[force_boundaries.PFA23NM.isin(FORCES_FOR_MAP)][["PFA23NM", "geometry"]],
        force_boundaries[~force_boundaries.PFA23NM.isin(FORCES_FOR_MAP)][["PFA23NM", "geometry"]],
    )


MONTHS = Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev().collect()


def main() -> None:
    if "category" not in st.session_state:
        st.session_state.category = CATEGORIES[1]

    st.title("Crime Explorer - Overview")

    st.markdown("## Monthly crime counts by police force over the last 3 years")

    with st.expander("More info..."):
        st.markdown(
            "Useful for checking consistency in data collection in the public crime data across "
            "forces, and identifying trends or anomalies in crime counts over time. "
            "Also showcases how to embed graphs in tooltips :wink:"
        )

    st.sidebar.header("Overview")

    crime_type = st.sidebar.selectbox("Crime type", get_args(CrimeType), index=5)

    all_data = get_crime_counts()

    try:
        with st.spinner("Loading crime data..."):
            active_pfa_boundaries, missing_pfa_boundaries = simplified_pfa_boundaries()

            graphs = pd.Series(index=FORCES, dtype="object", name="graph")
            for f in FORCES:
                fig, ax = plt.subplots(figsize=(5, 3))
                data = all_data.loc[(crime_type, f)].sort_index()

                data.plot.bar(ax=ax, title=f"{f}: {crime_type} counts by month", legend=False)
                labels = [m if i % 3 == 2 else "" for i, m in enumerate(data.index)]
                ax.set_xticklabels(labels, rotation=45, ha="right")

                buffer = BytesIO()
                fig.savefig(buffer, format="png", bbox_inches="tight")  # , dpi=100)
                plt.close(fig)
                buffer.seek(0)
                b64 = b64encode(buffer.getvalue()).decode("ascii")
                graphs.loc[fix_force_name(f)] = f"data:image/png;base64,{b64}"

            active_pfa_boundaries = active_pfa_boundaries.merge(graphs, left_on="PFA23NM", right_index=True)
            # st.dataframe(graphs)

        # render map
        view_state = pdk.ViewState(
            latitude=REF_LAT,
            longitude=REF_LON,
            zoom=6,
            pitch=22,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            active_pfa_boundaries.__geo_interface__,
            opacity=0.5,
            stroked=True,
            filled=True,
            extruded=False,
            pickable=True,
            line_width_min_pixels=1,
            get_line_color=[64, 64, 192, 128],
            get_fill_color=[0, 0, 0, 0],
        )

        missing_layer = pdk.Layer(
            "GeoJsonLayer",
            missing_pfa_boundaries.__geo_interface__,
            opacity=0.1,
            stroked=True,
            filled=True,
            extruded=False,
            pickable=False,
            line_width_min_pixels=1,
            # fill_color=[192, 192, 192, 32],
        )

        tooltip = {
            "html": '<img src="{graph}" width="300"/>',
            "style": {
                "padding": "1px",
                "border-radius": "0px",
            },
        }

        st.pydeck_chart(
            pdk.Deck(
                map_style=st.context.theme.type,
                layers=[boundary_layer, missing_layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            ),
            height=880,
        )

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
