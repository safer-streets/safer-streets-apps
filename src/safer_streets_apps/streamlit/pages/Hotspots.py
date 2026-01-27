from typing import cast, get_args

import pandas as pd
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv
from itrx import Itr
from safer_streets_core.api_helpers import fetch_df, fetch_gdf, get
from safer_streets_core.charts import DEFAULT_COLOUR
from safer_streets_core.utils import DEFAULT_FORCE, CrimeType, Force, data_dir, monthgen

from safer_streets_apps.streamlit.common import latest_month

st.set_page_config(layout="wide", page_title="Crime Hotspots", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")

load_dotenv()

# override env
# import os
# os.environ["SAFER_STREETS_API_URL"] = "http://localhost:5000"
N_MONTHS = 36

HEX_AREA = 0.2**2 * 3**1.5 / 2


def _make_label(timeslice: tuple[str]):
    return timeslice[0] if len(timeslice) == 1 else f"{timeslice[0]} to {timeslice[-1]}"


@st.cache_data
def get_counts(force: Force, crime_type: CrimeType) -> pd.DataFrame:
    counts = (
        fetch_df("hex_counts", params={"force": force, "category": crime_type})
        .set_index(["spatial_unit", "month"])
        .unstack(level="month", fill_value=0)
    )
    counts.columns = counts.columns.droplevel(0)
    return counts


@st.cache_data
def get_oac() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    hex_oa_mapping = pd.read_parquet(data_dir() / "hex-oa-mapping.parquet")
    oac_desc = pd.read_csv(data_dir() / "classification_codes_and_names-1.csv").set_index("Classification Code")[
        "Classification Name"
    ]
    oac_actual = (
        pd.read_csv(data_dir() / "UK_OAC_Final.csv")
        .set_index("Geography_Code")
        .rename(columns={"Supergroup": "supergroup_code", "Group": "group_code", "Subgroup": "subgroup_code"})
    )
    oac_actual.supergroup_code = oac_actual.supergroup_code.astype(str)
    return hex_oa_mapping, oac_actual, oac_desc


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

    force = cast(
        Force, st.sidebar.selectbox("Force Area", get_args(Force), index=DEFAULT_FORCE)
    )  # default="West Yorkshire"

    # _spatial_unit = st.sidebar.selectbox(
    #     "Spatial unit", ["Hex cell", "Output area"], help="Choose either 200m hex cell or census output area"
    # )

    crime_type = st.sidebar.selectbox("Crime type", get_args(CrimeType), index=5)

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

    prediction_window = st.sidebar.select_slider(
        "Prediction window (months)",
        options=[1, 3, 6, 12],
        value=1,
        help="Number of months of data to look forward when determining how well hotspots predict future crimes",
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

            # subsequent prediction_window months for each window in timeline, padded where data isnt available
            # prediction_timeline = Itr(monthgen(timeline.peek()[-1] + 1)).take(N_MONTHS - window).rolling(prediction_window).step_by(update)
            prediction_timeline = (
                Itr(monthgen(timeline.peek()[-1] + 1))
                .take(N_MONTHS - window)
                .rolling(prediction_window)
                .step_by(update)
                .chain([None] * prediction_window)
            )

            hex_oa_mapping, oac_codes, oac_desc = get_oac()

            pfa_geodata = get("pfa_geodata", params={"force": force})
            hotspot_area = coverage * pfa_geodata["properties"]["area"] / 100
            n_hotspots = max(1, int(hotspot_area / HEX_AREA))

            props = pd.DataFrame(columns=["Time slice", "Proportion in hotspots", "Proportion predicted by hotspots"])
            temp = []
            for i, (slice, prediction_slice) in timeline.zip(prediction_timeline).enumerate():
                months = [str(m) for m in slice]
                ranked = counts[months].sum(axis=1).sort_values(ascending=False)
                hotspots = ranked.head(n_hotspots)
                props.loc[i, "Time slice"] = _make_label(months)
                props.loc[i, "Proportion in hotspots"] = 100 * hotspots.sum() / ranked.sum()

                if prediction_slice:
                    pred_months = [str(m) for m in prediction_slice]
                    pred_counts = counts[pred_months].sum(axis=1)
                    # pred_props[_make_label(pred_months)] = 100 * pred_counts.loc[hotspots.index].sum() / pred_counts.sum()
                    props.loc[i, "Time slice"] += " predicting " + _make_label(pred_months)
                    props.loc[i, "Proportion predicted by hotspots"] = (
                        100 * pred_counts.loc[hotspots.index].sum() / pred_counts.sum()
                    )
                temp.append(ranked.head(n_hotspots).reset_index().spatial_unit)

            # map hexes to OAs and add OA classifications
            ranks = pd.concat(temp).value_counts().to_frame().join(hex_oa_mapping)
            ranks = ranks.merge(oac_codes, left_on="OA21CD", right_index=True)
            ranks = ranks.merge(oac_desc.rename("Supergroup"), left_on="supergroup_code", right_index=True)
            ranks = ranks.merge(oac_desc.rename("Group"), left_on="group_code", right_index=True)
            ranks = ranks.merge(oac_desc.rename("Subgroup"), left_on="subgroup_code", right_index=True)

        with st.spinner("Loading spatial data..."):
            hexes = fetch_gdf("hexes", ranks.index.to_list(), http_post=True).set_index("id")
            # TODO annoyingly comes back with a string index, can this be fixed?
            hexes.index = hexes.index.astype(int)
            # TODO also return in CRS we need for pydeck? Low priority - join/transform below takes ~20ms
            hexes = hexes.join(ranks).to_crs(epsg=4326)
            n_obs = (N_MONTHS - window) // update + 1
            hexes["Frequency (%)"] = round(100.0 * hexes["count"] / n_obs, 1)

        st.markdown(
            f"### Hotspot repetition, {crime_type} in {force}, {latest_month() - N_MONTHS + 1} to {latest_month()}"
        )

        # render map
        view_state = pdk.ViewState(
            latitude=pfa_geodata["properties"]["lat"],
            longitude=pfa_geodata["properties"]["lon"],
            zoom=9,
            pitch=22,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            pfa_geodata,
            opacity=0.5,
            stroked=True,
            filled=False,
            extruded=False,
            # pickable=True,
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

        tooltip = {
            "html": f"Cell {{id}}<br/>Hotspot {{Frequency (%)}}% of the time ({{count}}/{n_obs})<br/>"
            "{OA21CD} classification:<br/>"
            "{Supergroup}<br/>{Group}<br/>{Subgroup}"
        }

        st.pydeck_chart(
            pdk.Deck(
                map_style=st.context.theme.type,
                layers=[boundary_layer, hotspot_layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            ),
            height=800,
        )

        st.markdown(f"""
            - **{window}-month lookback at {update}-month intervals ({n_obs} observations)**
            - **{prediction_window}-month prediction window ({sum(~props["Proportion predicted by hotspots"].isna())} predictions)**
            - **{coverage}% coverage corresponds to {n_hotspots} hex cells ({HEX_AREA * n_hotspots:.1f}km²)**
            - **{len(hexes)} cells ({HEX_AREA * len(hexes):.1f}km²) feature at least once as hotspots. (Total PFA area
            is {pfa_geodata["properties"]["area"]:.1f}km²)**
        """)

        with st.expander("Output Area classfication rankings"):
            levels = ["Supergroup", "Group", "Subgroup"]
            level = st.select_slider("OAC Level", levels, value="Group")
            st.dataframe(ranks[levels[: levels.index(level) + 1]].value_counts().sort_values(ascending=False))

        st.markdown(
            f"#### Time variation of percentage of crimes captured and predicted within the {coverage:.1f}% hotspot coverage:"
        )

        # this is potentially slightly misleading as the lookback and prediction windows are not necessarily the same size
        # props["Proportion predicted by hotspots"] = props["Proportion predicted by hotspots"].shift()

        st.bar_chart(
            props,
            height=400,
            x="Time slice",
            y=["Proportion in hotspots", "Proportion predicted by hotspots"],
            x_label="Time window",
            stack="layered",
            color=[DEFAULT_COLOUR, "#C00000"],
            y_label="Percentage of crime captured",
        )
        with st.expander("View hotspot capture data"):
            st.dataframe(props.set_index("Time slice", drop=True).style.format("{:.1f}%"))

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
