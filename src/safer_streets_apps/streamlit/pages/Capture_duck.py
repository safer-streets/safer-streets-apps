"""
This implementation avoids the API and uses duckdb (in-memory+azure parquet) directly
"""

from datetime import date
from typing import cast, get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from itrx import Itr
from safer_streets_core.database import duckdb_connector, get_gdf
from safer_streets_core.utils import CATEGORIES, DEFAULT_FORCE, CrimeType, Force, Month, fix_force_name, monthgen

st.set_page_config(layout="wide", page_title="Crime Capture", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")

load_dotenv()


# geography -> (spatial unit column, H3 lookup table, crime count table, boundary parquet (None for H3:
# cell geometry is computed on the fly from the cell id))
geographies: dict[str, tuple[str, str, str, str | None]] = {
    "Local authority districts (2024)": (
        "lad24cd AS spatial_id",
        "h3_8_geogs",
        "crime_counts_lad24cd",
        "local_authority_districts",
    ),
    "Middle layer Super Output Areas (census)": (
        "msoa21cd AS spatial_id",
        "h3_8_geogs",
        "crime_counts_msoa21cd",
        "msoa_2021",
    ),
    "Lower layer Super Output Areas (census)": (
        "lsoa21cd AS spatial_id",
        "h3_8_geogs",
        "crime_counts_lsoa21cd",
        "lsoa_2021",
    ),
    "Output Areas (census)": ("oa21cd AS spatial_id", "h3_8_geogs", "crime_counts_oa21cd", "output_areas_2021"),
    "H3(8)": ("spatial_id", "h3_8_geogs", "crime_counts_h3_8", None),
    "H3(9)": ("spatial_id", "h3_9_geogs", "crime_counts_h3_9", None),
    "H3(10)": ("spatial_id", "h3_10_geogs", "crime_counts_h3_10", None),
}


def date_range(start_month: Month, n_months: int) -> tuple[date, date]:
    start_date = date(start_month.year, start_month.month, 1)
    end_date = start_date + relativedelta(months=n_months, days=-1)
    return start_date, end_date


@st.cache_data
def all_months() -> tuple[Month, ...]:
    raw = st.session_state.con.sql("""
    SELECT DISTINCT _month FROM read_parquet('az://phase2/extract/crime_data.parquet')
    ORDER BY _month
    """).fetchall()
    return tuple(Month.parse_str(m[0]) for m in raw)


@st.cache_data
def get_boundary(force: Force) -> gpd.GeoDataFrame:
    return get_gdf(
        st.session_state.con,
        """
    SELECT spatial_id, *, ST_Area(geom) AS area, ST_AsText(ST_Transform(geom, 'EPSG:27700', 'EPSG:4326', always_xy := true)) AS wkt
    FROM read_parquet('az://phase2/extract/police_force_areas.parquet')
    WHERE pfa23nm = ?
    """,
        params=(fix_force_name(force),),
    )


@st.cache_data
def get_counts_and_features(
    force: Force, geography: str, category: CrimeType, month: str, lookback: int
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:

    spatial_unit, feature_table, count_table, boundary_table = geographies[geography]

    months = Itr(monthgen(Month.parse_str(month), backwards=True)).take(lookback).map(str).collect()

    if boundary_table is None:
        features = get_gdf(
            st.session_state.con,
            f"""
            SELECT
                spatial_id,
                cell_area / 1000000 AS area_km2,
                h3_cell_to_boundary_wkt(spatial_id) AS wkt
            FROM read_parquet('az://phase2/transform/{feature_table}.parquet')
            WHERE pfa23cd = (
                SELECT spatial_id FROM read_parquet('az://phase2/extract/police_force_areas.parquet')
                WHERE pfa23nm = ?
            )
            """,
            crs="EPSG:4326",
            params=(fix_force_name(force),),
        ).set_index("spatial_id")
    else:
        features = get_gdf(
            st.session_state.con,
            f"""
            WITH ids AS (
                SELECT DISTINCT {spatial_unit} FROM read_parquet('az://phase2/transform/{feature_table}.parquet')
                WHERE pfa23cd = (
                    SELECT spatial_id FROM read_parquet('az://phase2/extract/police_force_areas.parquet')
                    WHERE pfa23nm = ?
                )
            )
            SELECT
                spatial_id,
                ST_Area(geom) / 1000000 AS area_km2,
                ST_AsText(ST_Transform(geom, 'EPSG:27700', 'EPSG:4326', always_xy := true)) AS wkt
            FROM read_parquet('az://phase2/extract/{boundary_table}.parquet')
            WHERE spatial_id IN (SELECT spatial_id FROM ids)
            """,
            crs="EPSG:4326",
            params=(fix_force_name(force),),
        ).set_index("spatial_id")

    counts = (
        st.session_state.con.sql(
            f"""
        SELECT spatial_id, SUM(count) AS n_crimes FROM read_parquet('az://phase2/transform/{count_table}.parquet')
        WHERE spatial_id IN ? AND crime_type = ? AND month IN ?
        GROUP BY spatial_id
        """,
            params=(features.index.tolist(), category, months),
        )
        .df()
        .set_index("spatial_id")
    )

    return features, counts


def init() -> None:
    if "con" not in st.session_state:
        st.session_state.con = duckdb_connector(azure=True, writeable=False)
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
        st.session_state.month = all_months()[-1]


def main() -> None:
    init()
    st.title("Crime Capture Explorer")

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
        all_months()[st.session_state.lookback_window - 1 :],
        value=st.session_state.month,
        format_func=display_name,
        help="Select month",
    )

    try:
        with st.spinner("Loading crime and geographic data..."):
            boundary = get_boundary(st.session_state.force)
            total_area = boundary["area"].sum() / 1_000_000
            centroid_lat, centroid_lon = boundary.lat.mean(), boundary.long.mean()

            features, counts = get_counts_and_features(
                st.session_state.force,
                st.session_state.spatial_unit_name,
                st.session_state.category,
                str(st.session_state.month),
                st.session_state.lookback_window,
            )

        with st.spinner("Processing crime data..."):
            # ordered_counts = get_ordered_counts(counts, st.session_state.month, features)

            ordered_counts = features.join(counts, how="right")
            ordered_counts["density"] = ordered_counts.n_crimes / ordered_counts.area_km2
            ordered_counts = ordered_counts.sort_values(by="density", ascending=False)
            # cum area not including current row
            ordered_counts["cum_area"] = ordered_counts.area_km2.cumsum().shift(fill_value=0)

            # make boundary work with the tooltip
            boundary["n_crimes"] = ordered_counts.n_crimes.sum()

            # deal with case where we've captured all incidents in a smaller area than specified
            captured_features = ordered_counts[
                (ordered_counts.cum_area < st.session_state.area_threshold) & (ordered_counts.n_crimes > 0)
            ]
            captured_features["opacity"] = 192 * captured_features.n_crimes / captured_features.n_crimes.max()

            if st.session_state.show_missed:
                missed_features = ordered_counts[
                    (ordered_counts.cum_area > st.session_state.area_threshold) & (ordered_counts.n_crimes > 0)
                ]
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

        tooltip = {"html": "Feature {name} crimes: {n_crimes}<br/>"}

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=800,
        )

        with st.expander("Hotspot Table"):
            st.dataframe(boundary.drop(columns="geometry"))  # .style.format("{:.1%}", subset=ethnicity.columns))
            st.dataframe(
                captured_features.drop(columns=["geometry", "cum_area", "opacity"]).sort_values(
                    by="n_crimes", ascending=False
                )
            )

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
