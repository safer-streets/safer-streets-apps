import json
import os
from io import StringIO
from typing import Any, cast, get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from dotenv import load_dotenv
from itrx import Itr
from safer_streets_core.spatial import get_force_boundary
from safer_streets_core.utils import CATEGORIES, Force, latest_month, monthgen

st.set_page_config(layout="wide", page_title="Crime Hotspots", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")

load_dotenv()

# URL = "http://localhost:5000"
URL = os.environ["SAFER_STREETS_API_URL"]
HEADERS = {"x-api-key": os.getenv("SAFER_STREETS_API_KEY")}
N_MONTHS = 36

HEX_AREA = 0.2**2 * 3**1.5 / 2


def _make_label(timeslice: tuple[str]):
    return timeslice[0] if len(timeslice) == 1 else f"{timeslice[0]} to {timeslice[-1]}"


@st.cache_data
def get_counts(force: Force, crime_type: str) -> pd.DataFrame:
    counts = (
        pd.DataFrame(get("hex_counts", params={"force": force, "category": crime_type}))
        .set_index(["spatial_unit", "month"])
        .unstack(level="month", fill_value=0)
    )
    counts.columns = counts.columns.droplevel(0)
    return counts


def get(endpoint: str, *, params: dict[str, Any]) -> Any:
    response = requests.get(f"{URL}/{endpoint}", params=params, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def post(endpoint: str, payload: Any) -> Any:
    response = requests.post(f"{URL}/{endpoint}", json=payload, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def main() -> None:
    st.title("Crime Hotspot Explorer")

    st.warning(
        "##### :construction: This page uses the experimental Safer Streets [geospatial data API]"
        "(https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net/docs#/)"
    )

    st.markdown("## Highlighting how temporal scales affect crime hotspots within a Police Force Area")

    with st.expander("More info..."):
        st.markdown("""
The app uses [police.uk](https://data.police.uk) public crime data to determine, given a target total land area, the
maximum number of crimes of a given type that can be captured within that area, in the chosen time window in the last
3 years.

Firstly select a threshold for hotspots, in terms of percentage coverage of the force area, between 0.1% and 5%. (At
least one hex cell will be considered).

A rolling window of crime counts (1, 3, 6, or 12 months) are aggregated onto a hex grid (200m side, ~350m height) and
ranked. The top percentage of cells are recorded for each window. The window is updated and the ranks recomputed to
cover the 3 years of data.

(As an example of the iteration: a 2-month update on a 3-month window will step from {Jan, Feb, Mar} to {Mar, Apr, May})

Finally, spatial units are then ranked by the number of times each spatial unit features in the top 1% over the 3
year period.

The interactive map displays the hotspot hex cells shaded in proportion to their frequency as a hotspot.
""")

    st.sidebar.header("Hotspots")

    force = cast(Force, st.sidebar.selectbox("Force Area", get_args(Force), index=43))  # default="West Yorkshire"

    # _spatial_unit = st.sidebar.selectbox(
    #     "Spatial unit", ["Hex cell", "Output area"], help="Choose either 200m hex cell or census output area"
    # )

    crime_type = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    coverage = st.sidebar.select_slider(
        "Area Coverage (%)",
        options=[0.1, 0.5, 1.0, 5.0],
        value=1.0,
        help="Percentage of hex cells to consider as hotspots",
    )

    window = st.sidebar.select_slider(
        "Lookback window (months)",
        options=[1, 3, 6, 12],
        value=1,
        help="Number of months of data to aggregate when determining hotspots",
    )

    update = st.sidebar.select_slider(
        "Update interval (months)",
        options=[1, 2, 3, 6],
        value=1,
        help="Number of months to step when determining window",
    )

    try:
        with st.spinner("Loading crime data..."):
            counts = get_counts(force, crime_type)

        with st.spinner("Processing data..."):
            timeline = (
                Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev().rolling(window).step_by(update)
            )
            pfa_area = get("pfa_area", params={"force": force})
            hotspot_area = coverage * pfa_area / 100
            n_hotspots = max(1, int(hotspot_area / HEX_AREA))

            props = pd.Series()
            temp = []
            for slice in timeline:
                months = [str(m) for m in slice]
                ranked = counts[months].sum(axis=1).sort_values(ascending=False)
                hotspots = ranked.head(n_hotspots)
                props.loc[_make_label(months)] = 100 * hotspots.sum() / ranked.sum()
                temp.append(ranked.head(n_hotspots).reset_index().spatial_unit)

            ranks = pd.concat(temp).value_counts()

        with st.spinner("Loading spatial data..."):
            hexes = gpd.read_file(StringIO(json.dumps(post("hexes", ranks.index.to_list())))).set_index("id")
            # TODO annoyingly comes back with a string index, can this be fixed?
            hexes.index = hexes.index.astype(int)
            # TODO also return in CRS we need for pydeck?
            hexes = hexes.join(ranks).to_crs(epsg=4326)
            n_obs = (N_MONTHS - window) // update + 1
            hexes["Frequency (%)"] = round(100.0 * hexes["count"] / n_obs, 1)

            boundary = get_force_boundary(force).to_crs(epsg=4326)
            centroid = boundary.union_all().centroid

        st.markdown(
            f"### Hotspot repetition, {crime_type} in {force}, {latest_month() - N_MONTHS + 1} to {latest_month()}"
        )

        st.markdown(f"""
            - **{window}-month lookback at {update}-month intervals ({n_obs} observations).**
            - **{coverage}% coverage corresponds to {n_hotspots} hex cells ({HEX_AREA * n_hotspots:.1f}km²).**
            - **{len(hexes)} cells ({HEX_AREA * len(hexes):.1f}km²) feature at least once as hotspots. (Total PFA area
            is {pfa_area:.1f}km²)**
        """)

        # render map
        view_state = pdk.ViewState(
            latitude=centroid.y,
            longitude=centroid.x,
            zoom=9,
            pitch=22,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            boundary.__geo_interface__,
            opacity=0.5,
            stroked=True,
            filled=False,
            extruded=False,
            pickable=True,
            line_width_min_pixels=2,
            get_line_color=[64, 64, 192, 255],
        )

        hotspot_layer = (
            pdk.Layer(
                "GeoJsonLayer",
                hexes.__geo_interface__,
                stroked=True,
                filled=True,
                wireframe=True,
                get_fill_color="[192, 0, 0, properties['Frequency (%)']]",  # [255, 0, 0, 160],
                get_line_color=[0x80, 0x80, 0x80, 0x80],
                line_width_min_pixels=2,
                pickable=True,
            ),
        )

        tooltip = {"html": f"Cell {{id}}<br/>Hotspot {{Frequency (%)}}% of the time ({{count}}/{n_obs})"}

        st.pydeck_chart(
            pdk.Deck(
                map_style=st.context.theme.type,
                layers=[boundary_layer, hotspot_layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            ),
            height=960,
        )

        st.markdown(
            f"#### Time variation of proportion of crimes captured within the {coverage:.1f}% hotspot coverage:"
        )

        st.bar_chart(props, x_label="Time window", y_label="Percentage of crime in hotspot")

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
