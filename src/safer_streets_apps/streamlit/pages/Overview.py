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
    load_crime_data,
    monthgen,
)

from safer_streets_apps.streamlit.common import latest_month

st.set_page_config(layout="wide", page_title="Crime Hotspots", page_icon="👮")
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
def get_crime_counts(crime_type: str) -> pd.DataFrame:
    return (
        pd.concat(
            [
                load_crime_data(f, MONTHS, filters={"Crime type": crime_type})
                .Month.value_counts()
                .sort_index()
                .rename(f)
                for f in FORCES
            ],
            axis=1,
        )
        .fillna(0)
        .astype(int)
    )


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

    st.markdown("## Highlighting ...")

    with st.expander("More info..."):
        st.markdown("Testing graphs embedded in tooltips")

    st.sidebar.header("Overview")

    crime_type = st.sidebar.selectbox("Crime type", get_args(CrimeType), index=5)

    st.write(crime_type)

    try:
        with st.spinner("Loading crime data..."):
            data = get_crime_counts(crime_type)

            active_pfa_boundaries, missing_pfa_boundaries = simplified_pfa_boundaries()

            graphs = []
            for f in FORCES:
                fig, _ = plt.subplots()
                data[f].plot.bar(ax=fig.gca())

                buffer = BytesIO()
                fig.savefig(buffer, format="png", bbox_inches="tight")  # , dpi=100)
                plt.close(fig)
                buffer.seek(0)
                b64 = b64encode(buffer.getvalue()).decode("ascii")
                graphs.append(f"data:image/png;base64,{b64}")
            active_pfa_boundaries["graph"] = graphs

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
            "html": """
                    <b>{PFA23NM}</b><br/>
                    <img src="{graph}" width="240", height="135"/>
                    """
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
