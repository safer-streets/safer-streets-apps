import os
from typing import Any, get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv
from itrx import Itr
from safer_streets_core.api_helpers import fetch_gdf
from safer_streets_core.utils import CATEGORIES, Force, Month, data_dir, fix_force_name, monthgen

from safer_streets_apps.streamlit.common import date_range, get_oac, latest_month

st.set_page_config(layout="wide", page_title="Crime Hotspots", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")

load_dotenv()

FORCES = tuple(
    fix_force_name(f)  # name only used to query boundary
    for f in get_args(Force)
    if f not in ["BTP", "Greater Manchester", "Northern Ireland", "Gwent"]
)

# URL = "http://localhost:5000"  # note this won't resolve the host's loopback without running with --network=host
URL = os.environ["SAFER_STREETS_API_URL"]
N_MONTHS = 36

HEX_AREA = 0.2**2 * 3**1.5 / 2
EW_AREA = 151_047.0  # according to wikipedia

REF_LAT = 52.7
REF_LON = -2.0


@st.cache_data
def get_national_counts() -> pd.DataFrame:
    return pd.read_parquet(data_dir() / f"national_hotspots_{latest_month()}.parquet")


@st.cache_data
def get_force_counts() -> pd.DataFrame:
    return pd.read_parquet(data_dir() / f"force_hotspots_{latest_month()}.parquet")


@st.cache_data
def simplified_pfa_boundaries() -> tuple[dict[str, Any], dict[str, Any]]:
    force_boundaries = gpd.read_file(data_dir() / "Police_Force_Areas_December_2023_EW_BFE_2734900428741300179.zip")
    # this should be significantly smaller than a hex (although its not used in a spatial join)
    force_boundaries.geometry = force_boundaries.simplify(tolerance=50)
    force_boundaries = force_boundaries.to_crs(epsg=4326)

    return (
        force_boundaries[force_boundaries.PFA23NM.isin(FORCES)][["PFA23NM", "geometry"]].__geo_interface__,
        force_boundaries[~force_boundaries.PFA23NM.isin(FORCES)][["PFA23NM", "geometry"]].__geo_interface__,
    )


MONTHS = Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev().map(str).collect()


def main() -> None:
    if "lookback" not in st.session_state:
        st.session_state.lookback = 6
    if "lookforward" not in st.session_state:
        st.session_state.lookforward = 3
    if "ref_date" not in st.session_state:
        st.session_state.ref_date = MONTHS[-12]
    if "constrain" not in st.session_state:
        st.session_state.constrain = False
    if "hotspots" not in st.session_state:
        st.session_state.hotspots = 1

    st.title("Crime Intervention Explorer")

    st.warning(
        "##### :construction: This page uses the experimental Safer Streets [geospatial data API]"
        "(https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net/docs#/)"
    )

    st.markdown("## Highlighting how interventions might reduce crime in hotspots nationally")

    with st.expander("More info..."):
        st.markdown(
            """
The app uses [police.uk](https://data.police.uk) public crime data to determine hotspots (in 200m hex cells) for a given
crime type over a certain historic period, then calculate how many crimes occur over a subsequent period in those
hotspots (and thus may be deterred by any intervention imposed)

Data is available for 41 of the 43 Police Forces in England and Wales.

This app allows the exploration of how different timescales and other metrics affect the outcomes.

The following parameters can be set:
- crime type
- lookback window (6, 12, 18 or 24 months)
- look-forward window (1, 3, or 12 months)
- reference month (the start of the look-forward period)
- number of hotspots per Force (1, 2, or 5 nationally or 1, 2, 5, 20, or 50 per force)
- constrain hotspots (i.e. the top N in each force, or the equivalent number or hotspots nationally)

The zoomable map displays the hotspot hex cells in red alongside the PFA boundaries. Greyed out forces indicate
incomplete or missing data.
"""
        )

    st.sidebar.header("Interventions")

    crime_type = st.sidebar.selectbox("Crime type", CATEGORIES, index=0)  # st.session_state.crime_type_index)

    st.session_state.lookback = st.sidebar.select_slider(
        "Lookback window (months)",
        options=[6, 12, 18, 24],
        value=st.session_state.lookback,
        help="Number of months of data to aggregate when determining hotspots",
    )

    st.session_state.lookforward = st.sidebar.select_slider(
        "Look forward window (months)",
        options=[1, 3, 12],
        value=st.session_state.lookforward,
        help="Number of months of data to look forward when determining how well hotspots predict future crimes",
    )

    if st.session_state.lookforward != 12:
        st.session_state.ref_date = st.sidebar.select_slider(
            "Reference date",
            options=MONTHS[-12 :: st.session_state.lookforward],
            value=st.session_state.ref_date,
            help="Start of the look-forward period",
        )
    else:
        st.session_state.ref_date = MONTHS[-12]
        st.sidebar.markdown(f"Reference date: {st.session_state.ref_date}")

    st.session_state.constrain = st.sidebar.checkbox(
        "Constrain hotspots to each force", value=st.session_state.constrain
    )

    st.session_state.hotspots = st.sidebar.select_slider(
        "Number of hotspots per force",
        options=[1, 2, 5, 20, 50],
        value=st.session_state.hotspots,
        help="Total hotspots will be this number multiplied by the number of forces",
    )

    try:
        with st.spinner("Loading crime data..."):
            count_data = (get_force_counts() if st.session_state.constrain else get_national_counts()).loc[
                (
                    st.session_state.lookback,
                    st.session_state.ref_date,
                    st.session_state.lookforward,
                    st.session_state.hotspots,
                    crime_type,
                )
            ]

            hex_oa_mapping, oac_codes, oac_desc = get_oac()

        hexes = fetch_gdf(
            "hexes", count_data.index.get_level_values("spatial_unit").tolist(), http_post=True
        ).set_index("id")
        # TODO annoyingly comes back with a string index, can this be fixed?
        hexes.index = hexes.index.astype(int)
        hexes = hexes.to_crs(epsg=4326)

        hexes = hexes.join(hex_oa_mapping)
        hexes = hexes.merge(oac_codes, left_on="OA21CD", right_index=True)
        hexes = hexes.merge(oac_desc.rename("Supergroup"), left_on="supergroup_code", right_index=True)
        hexes = hexes.merge(oac_desc.rename("Group"), left_on="group_code", right_index=True)
        hexes = hexes.merge(oac_desc.rename("Subgroup"), left_on="subgroup_code", right_index=True)

        active_pfa_boundaries, missing_pfa_boundaries = simplified_pfa_boundaries()

        # render map
        view_state = pdk.ViewState(
            latitude=REF_LAT,
            longitude=REF_LON,
            zoom=6,
            pitch=22,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            active_pfa_boundaries,
            opacity=0.5,
            stroked=True,
            filled=False,
            extruded=False,
            pickable=False,
            line_width_min_pixels=1,
            get_line_color=[64, 64, 192, 128],
        )

        missing_layer = pdk.Layer(
            "GeoJsonLayer",
            missing_pfa_boundaries,
            opacity=0.1,
            stroked=True,
            filled=True,
            extruded=False,
            pickable=False,
            line_width_min_pixels=1,
            # fill_color=[192, 192, 192, 32],
        )

        hotspot_layer = (
            pdk.Layer(
                "GeoJsonLayer",
                hexes.__geo_interface__,
                stroked=True,
                filled=True,
                wireframe=True,
                # get_fill_color="[192, 0, 0, properties['Frequency (%)']]",  # [255, 0, 0, 160],
                get_fill_color=[192, 0, 0, 0x80],
                get_line_color=[0x80, 0x80, 0x80, 0x80],
                line_width_min_pixels=2,
                pickable=True,
            ),
        )

        tooltip = {"html": "Intersect {OA21CD} classification:<br/>{Supergroup}<br/>{Group}<br/>{Subgroup}"}

        st.pydeck_chart(
            pdk.Deck(
                map_style=st.context.theme.type,
                layers=[boundary_layer, missing_layer, hotspot_layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            ),
            height=880,
        )

        crime_coverage = (count_data.lookforward_total / count_data.lf_national_total).sum()
        ref_date = Month.parse_str(st.session_state.ref_date)
        hotspot_area = HEX_AREA * st.session_state.hotspots * len(FORCES)
        area_coverage = hotspot_area / EW_AREA

        lb_start, lb_end = date_range(ref_date - st.session_state.lookback, st.session_state.lookback)
        lf_start, lf_end = date_range(ref_date, st.session_state.lookforward)

        st.markdown(f"""
            - **Hotspots for {crime_type} determined using data from {lb_start} to {lb_end} inclusive**
            - **Crimes occurring in hotspots counted from {lf_start} to {lf_end} inclusive**
            - **{st.session_state.hotspots * len(FORCES)} hex cells ({hotspot_area:.1f}km²) capture {crime_coverage:.3%} of crimes
                ({count_data.lookforward_total.sum()} offences) in
            {area_coverage:.3%} of total land area**
        """)

        with st.expander("Hotspot counts by force"):
            st.dataframe(count_data.index.get_level_values("Force").value_counts().sort_values(ascending=False))

        with st.expander("Hotspot counts by by Output Area classfication"):
            levels = ["Supergroup", "Group", "Subgroup"]
            level = st.select_slider("OAC Level", levels, value="Group")
            st.dataframe(hexes[levels[: levels.index(level) + 1]].value_counts().sort_values(ascending=False))

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
