from typing import Any

import geopandas as gpd
import pandas as pd
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import (
    SpatialUnit,
    get_demographics,
    get_force_boundary,
    load_population_data,
    map_to_spatial_unit,
)
from safer_streets_core.utils import Force, get_monthly_crime_counts, latest_month, load_crime_data, monthgen

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
    "800m hexes": ("HEX", {"size": 800.0}),
    "400m hexes": ("HEX", {"size": 400.0}),
    "200m hexes": ("HEX", {"size": 200.0}),
    "1km grid": ("GRID", {"size": 1000.0}),
    "500m grid": ("GRID", {"size": 500.0}),
    "250m grid": ("GRID", {"size": 250.0}),
}


def get_counts_and_features(
    raw_data: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame, spatial_unit: SpatialUnit, **spatial_unit_params: Any
):
    crime_data, features = map_to_spatial_unit(raw_data, boundary, spatial_unit, **spatial_unit_params)
    # compute area in sensible units before changing crs!
    features["area_km2"] = features.area / 1_000_000
    # now convert everything to Webmercator
    crime_data = crime_data.to_crs(epsg=4326)
    boundary = boundary.to_crs(epsg=4326)
    features = features.to_crs(epsg=4326)
    # and aggregate
    counts = get_monthly_crime_counts(crime_data, features)
    return counts, features, boundary


def get_ethnicity(raw_population: gpd.GeoDataFrame | None, features: gpd.GeoDataFrame) -> pd.DataFrame:
    if raw_population is None:
        return pd.DataFrame(index=features.index, data={"n/a": 0})
    ethnicity = (
        get_demographics(raw_population, features)
        .groupby(["spatial_unit", "C2021_ETH_20_NAME"], observed=True)["count"]
        .sum()
        .unstack(level="C2021_ETH_20_NAME")
    ).reindex(features.index, fill_value=0)
    ethnicity.columns = ethnicity.columns.astype(str).str[:5]
    return ethnicity
