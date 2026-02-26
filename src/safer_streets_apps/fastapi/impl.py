import geopandas as gpd
from duckdb import DuckDBPyConnection
from safer_streets_core.utils import fix_force_name

from safer_streets_apps.fastapi import sql
from safer_streets_apps.fastapi.models import CrimeCountsRequest, DfJson, FeaturesRequest


def crime_counts(con: DuckDBPyConnection, params: CrimeCountsRequest) -> DfJson:
    # NOTE: we use PFA boundary data to filter crime, so need to adjust force name
    force = fix_force_name(params.force)

    if params.geography in ["GRID", "STREET"]:
        raise ValueError("only implemented for HEX(200m), H3, MSOA21, LSOA21 and OA21.")
    elif (params.geography == "H3") != (params.resolution is not None):
        raise ValueError("resolution should be specified (only) when geography is H3")

    if params.geography == "H3":
        return (
            con.sql(
                sql.H3_CRIME_COUNTS,
                params={
                    "resolution": params.resolution,
                    "pfa": force,
                    "months": params.months,
                    "crime_types": params.categories,
                },
            )
            .fetch_arrow_table()
            .to_pylist()
        )
    elif params.geography == "HEX":
        return (
            con.sql(
                sql.HEX_CRIME_COUNTS,
                params={"pfa": force, "months": params.months, "crime_types": params.categories},
            )
            .fetch_arrow_table()
            .to_pylist()
        )
    return (
        con.sql(
            sql.CENSUS_CRIME_COUNTS.format(geography=params.geography),
            params={"pfa": force, "months": params.months, "crime_types": params.categories},
        )
        .fetch_arrow_table()
        .to_pylist()
    )


def features(con: DuckDBPyConnection, params: FeaturesRequest, latlon: bool) -> gpd.GeoDataFrame:
    match params.geography:
        case "H3":
            raw_hexes = con.sql(sql.H3_FEATURES, params=(params.ids,)).fetchdf()

            features = gpd.GeoDataFrame(
                raw_hexes[["spatial_unit"]], geometry=gpd.GeoSeries.from_wkt(raw_hexes.wkt), crs="epsg:4326"
            ).set_index("spatial_unit", drop=True)
            if not latlon:
                features = features.to_crs(epsg=27700)
        case "HEX":
            raw_hexes = con.sql(sql.HEX_FEATURES, params=(params.ids,)).fetchdf()
            # TODO is there a more efficient way of rendering GeoJSON, including properties and CRS,
            # without going via geopandas?
            features = gpd.GeoDataFrame(
                raw_hexes[["spatial_unit"]], geometry=gpd.GeoSeries.from_wkt(raw_hexes.wkt), crs="epsg:27700"
            ).set_index("spatial_unit", drop=True)
            if latlon:
                features = features.to_crs(epsg=4326)
        case "MSOA21" | "LSOA21" | "OA21":
            raw_features = con.sql(
                sql.CENSUS_FEATURES.format(geography=params.geography), params=(params.ids,)
            ).fetchdf()
            features = gpd.GeoDataFrame(
                raw_features[["spatial_unit"]], geometry=gpd.GeoSeries.from_wkt(raw_features.wkt), crs="epsg:27700"
            ).set_index("spatial_unit", drop=True)
            if latlon:
                features = features.to_crs(epsg=4326)
        case _:
            raise ValueError(
                f"{params.geography} not supported, only implemented for HEX(200m), H3, MSOA21, LSOA21 and OA21."
            )

    return features
