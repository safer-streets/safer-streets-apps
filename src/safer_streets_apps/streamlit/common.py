from datetime import date
from typing import Any, get_args

import geopandas as gpd
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta
from safer_streets_core.api_helpers import fetch_df, fetch_gdf, get, post
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
    fix_force_name,
    get_monthly_crime_counts,
    load_crime_data,
)


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
def time_window() -> list[Month]:
    """
    This should ensure that if the crime data is updated, things won't immediately break
    Restart the app to update this
    """
    months = get("time_window")
    return [Month.parse_str(m) for m in months]


@st.cache_data
def get_oac(ids: list[str]) -> tuple["pd.Series[str]", pd.DataFrame, pd.DataFrame]:
    h3_oa_mapping = pd.Series(
        post("/geog_lookup", payload={"geography": "H3", "ids": ids, "resolution": 9, "target": "OA21"}), name="oa21cd"
    )

    # TODO? API endpoint?
    oac_desc = pd.read_parquet(data_dir() / "extract/oac_classification.parquet").set_index("code")
    oac_actual = pd.read_parquet(data_dir() / "extract/oac.parquet").set_index("spatial_id")
    return h3_oa_mapping, oac_actual, oac_desc


all_months = time_window()


geographies: dict[str, tuple[SpatialUnit, dict[str, Any]]] = {
    "Local authority districts (2024)": ("LAD24", {}),
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "H3(7)": ("H3", {"resolution": 7}),
    "H3(8)": ("H3", {"resolution": 8}),
    "H3(9)": ("H3", {"resolution": 9}),
}


def get_counts_and_features_old(
    raw_data: gpd.GeoDataFrame, boundary: gpd.GeoDataFrame, spatial_unit: SpatialUnit, **spatial_unit_params: Any
) -> tuple[pd.DataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
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


# forces with complete data, named to match PFA23NM boundary data
FORCES = tuple(
    fix_force_name(f) for f in get_args(Force) if f not in ["BTP", "Greater Manchester", "Northern Ireland", "Gwent"]
)


@st.cache_data
def simplified_pfa_boundaries() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundaries = fetch_gdf("/features", params={"geography": "PFA23"})
    # this should be significantly smaller than a hex (although its not used in a spatial join)
    force_boundaries.geometry = force_boundaries.simplify(tolerance=50)
    force_boundaries = force_boundaries.to_crs(epsg=4326)

    active = force_boundaries.PFA23NM.isin(FORCES)
    return (
        force_boundaries[active][["PFA23NM", "geometry"]],
        force_boundaries[~active][["PFA23NM", "geometry"]],
    )


@st.cache_data
def get_boundary(force: Force) -> gpd.GeoDataFrame:
    # returns EPSG:4326, with area
    boundary = fetch_gdf("/pfa_geodata", params={"force": force})
    return boundary.set_index("spatial_id")


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
        .set_index(["spatial_id", "month"])["count"]
        .unstack(level="month", fill_value=0)
    )

    # GeoDataFrame.to_json resets the index and names it to "id"
    features = (
        fetch_gdf("/features", http_post=True, payload={"geography": spatial_unit, "ids": counts.index.to_list()})
        .rename(columns={"id": "spatial_id"})
        .set_index("spatial_id", drop=True)
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
