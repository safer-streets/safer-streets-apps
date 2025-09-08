from typing import get_args

import geopandas as gpd
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import get_demographics, get_force_boundary, load_population_data, map_to_spatial_unit
from safer_streets_core.utils import (
    CATEGORIES,
    Force,
    get_monthly_crime_counts,
    latest_month,
    load_crime_data,
    monthgen,
)

# streamlit seems to break load_dotenv
LATEST_DATE = latest_month()
all_months = Itr(monthgen(LATEST_DATE, backwards=True)).take(36).rev().collect()


@st.cache_data
def cache_crime_data(force: Force, category: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundary = get_force_boundary(force)
    data = load_crime_data(force, all_months, filters={"Crime type": category}, keep_lonlat=True)
    return data, force_boundary


@st.cache_data
def cache_population(force: Force) -> gpd.GeoDataFrame:
    return load_population_data(force)


st.set_page_config(layout="wide", page_title="Safer Streets", page_icon="👮")

geographies = {
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "800m grid": ("GRID", {"size": 800.0}),
    "400m grid": ("GRID", {"size": 400.0}),
    "200m grid": ("GRID", {"size": 400.0}),
    "500m hexes": ("HEX", {"size": 500.0}),
    "250m hexes": ("HEX", {"size": 250.0}),
    "125m hexes": ("HEX", {"size": 250.0}),
}

st.set_page_config(page_title="Crime Demographics", page_icon="🌍")

st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Demographics Explorer")

    with st.expander("Help"):
        st.markdown("""TODO""")

    st.sidebar.header("Demographics")

    force = st.sidebar.selectbox("Force Area", get_args(Force), index=43)  # default="West Yorkshire"
    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)
    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)
    _elevation_scale = st.sidebar.slider(
        "Elevation scale", min_value=100, max_value=300, value=150, step=10, help="Adjust the vertical scale"
    )

    try:
        raw_data, boundary = cache_crime_data(force, category)

        # map crimes to features
        _centroid_lat, _centroid_lon = raw_data.lat.mean(), raw_data.lon.mean()
        spatial_unit, spatial_unit_params = geographies[spatial_unit_name]
        crime_data, features = map_to_spatial_unit(raw_data, boundary, spatial_unit, **spatial_unit_params)
        # compute area in sensible units before changing crs!
        features["area_km2"] = features.area / 1_000_000
        # now convert everything to Webmercator
        crime_data = crime_data.to_crs(epsg=4326)
        boundary = boundary.to_crs(epsg=4326)
        features = features.to_crs(epsg=4326)
        population = cache_population(force).to_crs(epsg=4326)
        # and aggregate - annualised rate
        _counts = get_monthly_crime_counts(crime_data, features).sum(axis=1) / 3

        ethnicity = (
            get_demographics(population, features)
            .groupby(["spatial_unit", "C2021_ETH_20_NAME"])["count"]
            .sum()
            .unstack(level="C2021_ETH_20_NAME")
        )

        st.dataframe(ethnicity)

    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
