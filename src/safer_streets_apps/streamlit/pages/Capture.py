from time import sleep
from typing import get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import get_force_boundary, map_to_spatial_unit
from safer_streets_core.utils import (
    CATEGORIES,
    Force,
    calc_gini,
    get_monthly_crime_counts,
    latest_month,
    load_crime_data,
    monthgen,
)

LATEST_DATE = latest_month()
all_months = Itr(monthgen(LATEST_DATE, backwards=True)).take(36).rev().collect()


@st.cache_data
def cache_crime_data(force: Force, category: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundary = get_force_boundary(force)
    data = load_crime_data(force, all_months, filters={"Crime type": category}, keep_lonlat=True)
    return data, force_boundary


st.set_page_config(layout="wide", page_title="Safer Streets", page_icon="👮")

geographies = {
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "800m grid": ("GRID", {"size": 800.0}),
    "400m grid": ("GRID", {"size": 400.0}),
    "500m hexes": ("HEX", {"size": 500.0}),
    "250m hexes": ("HEX", {"size": 250.0}),
}

st.set_page_config(page_title="Crime Capture", page_icon="🌍")

st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Capture Explorer")

    with st.expander("Help"):
        st.markdown(
            """
The app uses police.uk public crime data to determine, given a target total land area, what is the maximimum number of
crimes of a given type can be captured within the that area, in any given month, over the last 3 years. The interactive
map displays the "hot" areas (in yellow) with the height in proportion to the crime count, and optionally other crime-
containing areas (blue). Below the map graphs are displayed of the percentage of crimes captured and the Gini index
over time. To view the animation, in the sidebar:

1. Select the Force Area, Crime Type and Spatial Unit.
2. Adjust the the land area you want to cover.
3. Hit "Run..."
"""
        )

    st.sidebar.header("Concentration")

    force = st.sidebar.selectbox("Force Area", get_args(Force), index=43)  # default="West Yorkshire"

    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)

    raw_data, boundary = cache_crime_data(force, category)

    area_threshold = st.sidebar.slider(
        "Coverage (km²)",
        1.0,
        boundary.area.sum() / 1_000_000,
        step=1.0,
        value=100.0,
        help="Focus on the spatial units comprising the desired percentage of total crimes",
    )

    show_missed = st.sidebar.checkbox(
        "Show areas not captured",
        help="Areas that contain some crimes, but not enough to feature in the 'hot' list")

    # map crimes to features
    centroid_lat, centroid_lon = raw_data.lat.mean(), raw_data.lon.mean()
    spatial_unit, spatial_unit_params = geographies[spatial_unit_name]
    crime_data, features = map_to_spatial_unit(raw_data, boundary, spatial_unit, **spatial_unit_params)
    # compute area in sensible units before changing crs!
    features["area_km2"] = features.area / 1_000_000
    # now convert everything to Webmercator
    crime_data = crime_data.to_crs(epsg=4326)
    boundary = boundary.to_crs(epsg=4326)
    features = features.to_crs(epsg=4326)
    # and aggregate
    counts = get_monthly_crime_counts(crime_data, features)
    num_features = len(features)
    area_threshold = features.area_km2.sum() - area_threshold
    stats = pd.DataFrame(columns=["Gini", "Percent Captured"])

    st.toast("Data loaded")

    view_state = pdk.ViewState(
        latitude=centroid_lat,
        longitude=centroid_lon,
        zoom=9,
        pitch=45,
    )

    boundary_layer = pdk.Layer(
        "GeoJsonLayer",
        boundary.__geo_interface__,
        opacity=0.5,
        stroked=True,
        filled=False,
        extruded=False,
        line_width_min_pixels=3,
        # get_fill_color=[0, 0, 200, 80],  # 180, 0, 200, 80
        get_line_color=[192, 64, 64, 255],
    )

    def render(m: str, c: pd.Series) -> None:
        weighted_counts = pd.concat([c.rename("n_crimes"), features.area_km2], axis=1)
        weighted_counts["density"] = weighted_counts.n_crimes / weighted_counts.area_km2
        weighted_counts.sort_values(by="density")
        weighted_counts = weighted_counts.sort_values(by="density")
        weighted_counts["cum_area"] = weighted_counts.area_km2.cumsum()

        gini, _ = calc_gini(c)

        # deal with case where we've captured all incidents in a smaller area than specified
        captured_features = features[["geometry"]].join(
            weighted_counts[(weighted_counts.cum_area >= area_threshold) & (weighted_counts.n_crimes > 0)], how="right"
        )

        coverage = captured_features.n_crimes.sum() / c.sum()

        title.markdown(f"""
            ### {m}: {captured_features.area_km2.sum():.1f}km² of land area contains {coverage:.1%} of {category}

            {len(captured_features) / num_features:.1%} ({len(captured_features)}/{num_features}) of {spatial_unit_name}

            **Gini Coefficient = {gini:.2f}**

            """)
        stats.loc[m, "Percent Captured"] = coverage * 100
        stats.loc[m, "Gini"] = gini * 100
        graph.line_chart(stats, x_label="Mon")

        layers = [boundary_layer, pdk.Layer(
            "GeoJsonLayer",
            captured_features.__geo_interface__,
            opacity=1.0,
            stroked=True,
            filled=True,
            extruded=True,
            wireframe=True,
            get_fill_color=[0xC9, 0xF1, 0x00, 0xA0], #[255, 0, 0, 160],
            get_line_color=[255, 255, 255, 255],
            # pickable=True,
            elevation_scale=20,
            get_elevation="properties.n_crimes",
        )]

        if show_missed:
            missed_features = features[["geometry"]].join(
                weighted_counts[(weighted_counts.cum_area < area_threshold) & (weighted_counts.n_crimes > 0)], how="right"
            )

            layers.append(pdk.Layer(
                "GeoJsonLayer",
                missed_features.__geo_interface__,
                opacity=1.0,
                stroked=True,
                filled=True,
                extruded=True,
                wireframe=True,
                get_fill_color=[0x00, 0x39, 0xF5, 0x50], #[255, 0, 0, 160],
                get_line_color=[255, 255, 255, 255],
                # pickable=True,
                elevation_scale=20,
                get_elevation="properties.n_crimes",
            ))

        map_placeholder.pydeck_chart(
            pdk.Deck(layers=layers, initial_view_state=view_state, tooltip=True), height=720
        )

    def render_static() -> None:
        m = str(all_months[0])
        if m not in counts.columns:
            st.error(f"No data for {m}")
        else:
            render(m, counts[m])

    def render_dynamic() -> None:
        for m, c in counts.items():
            render(m, c)
            sleep(0.5)

    run_button = st.sidebar.empty()

    title = st.empty()
    map_placeholder = st.empty()
    map_placeholder.pydeck_chart(pdk.Deck(layers=[boundary_layer], initial_view_state=view_state))

    graph = st.empty()

    run = run_button.button("Run...")

    if run:
        render_dynamic()
    else:
        render_static()


if __name__ == "__main__":
    main()
