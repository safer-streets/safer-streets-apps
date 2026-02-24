from datetime import date
from typing import Any

import geopandas as gpd
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from itrx import Itr
from safer_streets_core.api_helpers import fetch_df, fetch_gdf
from safer_streets_core.spatial import (
    SpatialUnit,
    get_demographics,
    get_force_boundary,
    load_population_data,
    map_to_spatial_unit,
)
from safer_streets_core.utils import (
    CrimeType,
    Force,
    Month,
    data_dir,
    get_monthly_crime_counts,
    load_crime_data,
    monthgen,
)
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
    "200m hexes": ("HEX", {"size": 200.0}),
    "H3(7)": ("H3", {"resolution": 7}),
    "H3(8)": ("H3", {"resolution": 8}),
    "H3(9)": ("H3", {"resolution": 9}),
}


def get_counts_and_features_old(
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


@st.cache_data
def get_boundary(force: Force) -> gpd.GeoDataFrame:
    # returns EPSG:4326, with area
    boundary = fetch_gdf("/pfa_geodata", params={"force": force})
    return boundary.set_index("spatial_unit")


@st.cache_data
def get_counts_and_features(
    force: Force, geography: str, category: CrimeType, month: str, lookback: int
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:

    spatial_unit, spatial_unit_params = geographies[geography]

    counts = (
        fetch_df(
            "/crime_counts",
            params={
                "category": category,
                "force": force,
                "geography": spatial_unit,
                "month": str(month),
                "lookback": lookback,
            }
            | spatial_unit_params,
        )
        .set_index(["spatial_unit", "month"])["count"]
        .unstack(level="month", fill_value=0)
    )

    # GeoDataFrame.to_json resets the index and names it to "id"
    features = (
        fetch_gdf("/features", http_post=True, payload={"geography": spatial_unit, "ids": counts.index.to_list()})
        .rename(columns={"id": "spatial_unit"})
        .set_index("spatial_unit", drop=True)
    )
    # get the areas
    features["area_km2"] = features.area / 1_000_000
    # now convert everything to Webmercator
    features = features.to_crs(epsg=4326)

    counts.index = counts.index.astype(str)

    return features, counts


def get_ordered_counts(counts: pd.DataFrame, month: Month, features: gpd.GeoDataFrame) -> pd.DataFrame:
    ordered_counts = pd.concat([counts.sum(axis=1).rename("n_crimes"), features.area_km2], axis=1)
    ordered_counts["density"] = ordered_counts.n_crimes / ordered_counts.area_km2
    ordered_counts = ordered_counts.sort_values(by="density", ascending=False)
    # cum area not including current row
    ordered_counts["cum_area"] = ordered_counts.area_km2.cumsum().shift(fill_value=0)
    return ordered_counts


def get_ethnicity_totals(raw_population: gpd.GeoDataFrame | None, force: Force) -> pd.Series:
    if raw_population is None:
        return pd.Series(index=[force], data=0)
    ethnicity = raw_population.groupby("C2021_ETH_20_NAME", observed=True).C_SEX_NAME.count().rename("count")
    ethnicity.index = ethnicity.index.map(lambda s: s[:5])
    return ethnicity


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
