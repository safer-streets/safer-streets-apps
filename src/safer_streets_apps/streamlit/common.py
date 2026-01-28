from datetime import date
from typing import Any

import geopandas as gpd
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from itrx import Itr
from safer_streets_core.spatial import (
    SpatialUnit,
    get_demographics,
    get_force_boundary,
    load_population_data,
    map_to_spatial_unit,
)
from safer_streets_core.utils import Force, Month, data_dir, get_monthly_crime_counts, load_crime_data, monthgen
from safer_streets_core.utils import latest_month as core_latest_month


@st.cache_data
def cache_crime_data(force: Force, category: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundary = get_force_boundary(force)
    data = load_crime_data(force, all_months, filters={"Crime type": category}, keep_lonlat=True)
    return data, force_boundary


@st.cache_data
def cache_demographic_data(force: Force) -> gpd.GeoDataFrame:
    raw_population = load_population_data(force).to_crs(epsg=4326)
    return raw_population


@st.cache_data
def latest_month() -> Month:
    """
    This should ensure that if the crime data is updated, things won't immediately break
    Restart the app to update this
    """
    return core_latest_month()


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


all_months = Itr(monthgen(latest_month(), backwards=True)).take(36).rev().collect()


geographies = {
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "800m hexes": ("HEX", {"size": 800.0}),
    "400m hexes": ("HEX", {"size": 400.0}),
    "200m hexes": ("HEX", {"size": 200.0}),
    "H3(9)": ("H3", {"resolution": 9}),
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


def date_range(start_month: Month, n_months: int) -> tuple[date, date]:
    start_date = date(start_month.year, start_month.month, 1)
    end_date = start_date + relativedelta(months=n_months, days=-1)
    return start_date, end_date
