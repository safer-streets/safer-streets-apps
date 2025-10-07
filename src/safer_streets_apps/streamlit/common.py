import geopandas as gpd
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import get_force_boundary, load_population_data
from safer_streets_core.utils import Force, latest_month, load_crime_data, monthgen

LATEST_DATE = latest_month()
all_months = Itr(monthgen(LATEST_DATE, backwards=True)).take(36).rev().collect()


@st.cache_data
def cache_crime_data(force: Force, category: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundary = get_force_boundary(force)
    data = load_crime_data(force, all_months, filters={"Crime type": category}, keep_lonlat=True)
    return data, force_boundary


@st.cache_data
def cache_demographic_data(force: Force) -> gpd.GeoDataFrame:
    raw_population = load_population_data(force).to_crs(epsg=4326)
    return raw_population


geographies = {
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "800m grid": ("GRID", {"size": 800.0}),
    "400m grid": ("GRID", {"size": 400.0}),
    "200m grid": ("GRID", {"size": 200.0}),
    "500m hexes": ("HEX", {"size": 500.0}),
    "250m hexes": ("HEX", {"size": 250.0}),
    "125m hexes": ("HEX", {"size": 125.0}),
}
