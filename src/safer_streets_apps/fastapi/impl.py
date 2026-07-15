import geopandas as gpd
from duckdb import DuckDBPyConnection
from safer_streets_core.spatial import AdminGeography
from safer_streets_core.utils import fix_force_name

from safer_streets_apps.fastapi import sql
from safer_streets_apps.fastapi.models import CrimeCountsRequest, DfJson, FeaturesRequest, GeogLookupRequest

# geographies present as {geography}cd columns in the h3_*_geogs lookup tables
GEOG_LOOKUP_GEOGRAPHIES = ("PFA23", "LAD24", "MSOA21", "LSOA21", "OA21")


def crime_counts(con: DuckDBPyConnection, params: CrimeCountsRequest) -> DfJson:
    # NOTE: we use PFA boundary data to filter crime, so need to adjust force name
    force = fix_force_name(params.force)

    if params.geography in ["GRID", "HEX", "STREET"]:
        raise ValueError("only implemented for H3, MSOA21, LSOA21 and OA21.")
    elif (params.geography == "H3") != (params.resolution is not None):
        raise ValueError("resolution should be specified (only) when geography is H3")

    if params.geography == "H3":
        if params.resolution not in sql.H3_RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sql.H3_RESOLUTIONS}")
        return (
            con.sql(
                sql.H3_CRIME_COUNTS.format(transform=sql.TRANSFORM, extract=sql.EXTRACT, res=params.resolution),
                params={"pfa": force, "months": params.months, "crime_types": params.categories},
            )
            .to_arrow_table()
            .to_pylist()
        )

    return (
        con.sql(
            sql.CENSUS_CRIME_COUNTS.format(
                transform=sql.TRANSFORM, extract=sql.EXTRACT, geography=params.geography.lower()
            ),
            params={"pfa": force, "months": params.months, "crime_types": params.categories},
        )
        .to_arrow_table()
        .to_pylist()
    )


def geog_lookup(con: DuckDBPyConnection, params: GeogLookupRequest) -> dict[str, str]:
    if params.target not in GEOG_LOOKUP_GEOGRAPHIES:
        raise ValueError(f"target must be one of {GEOG_LOOKUP_GEOGRAPHIES}")
    if params.geography == params.target:
        raise ValueError("geography and target must differ")
    if (params.geography == "H3") != (params.resolution is not None):
        raise ValueError("resolution should be specified (only) when geography is H3")

    if params.geography == "H3":
        if params.resolution not in sql.H3_RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sql.H3_RESOLUTIONS}")
        query = sql.H3_GEOG_LOOKUP.format(transform=sql.TRANSFORM, res=params.resolution, target=params.target.lower())
    elif params.geography in GEOG_LOOKUP_GEOGRAPHIES:
        query = sql.CENSUS_GEOG_LOOKUP.format(
            transform=sql.TRANSFORM, source=params.geography.lower(), target=params.target.lower()
        )
    else:
        raise ValueError(f"geography must be H3 or one of {GEOG_LOOKUP_GEOGRAPHIES}")

    return dict(con.sql(query, params=(params.ids,)).fetchall())


def features(con: DuckDBPyConnection, params: FeaturesRequest, latlon: bool) -> gpd.GeoDataFrame:
    match params.geography:
        case "H3":
            raw_hexes = con.sql(sql.H3_FEATURES, params=(params.ids,)).fetchdf()
            features = gpd.GeoDataFrame(
                raw_hexes[["spatial_id"]], geometry=gpd.GeoSeries.from_wkt(raw_hexes.wkt), crs="epsg:4326"
            ).set_index("spatial_id", drop=True)
            if not latlon:
                features = features.to_crs(epsg=27700)
        case "PFA23" | "LAD24" | "MSOA21" | "LSOA21" | "OA21":
            raw_features = con.sql(
                sql.ADMIN_CENSUS_FEATURES.format(
                    extract=sql.EXTRACT, parquet=sql.ADMIN_CENSUS_PARQUET[params.geography]
                ),
                params=(params.ids,),
            ).fetchdf()
            features = gpd.GeoDataFrame(
                raw_features[["spatial_id"]], geometry=gpd.GeoSeries.from_wkt(raw_features.wkt), crs="epsg:27700"
            ).set_index("spatial_id", drop=True)
            if latlon:
                features = features.to_crs(epsg=4326)
        case _:
            raise ValueError(
                f"{params.geography} not supported, only implemented for H3, PFA23, LAD24, MSOA21, LSOA21 and OA21."
            )

    return features


def all_features(con: DuckDBPyConnection, geography: AdminGeography, latlon: bool) -> gpd.GeoDataFrame:

    print(sql.ALL_ADMIN_FEATURES.format(extract=sql.EXTRACT, parquet=sql.ADMIN_CENSUS_PARQUET[geography]))
    raw_features = con.sql(
        sql.ALL_ADMIN_FEATURES.format(extract=sql.EXTRACT, parquet=sql.ADMIN_CENSUS_PARQUET[geography]),
    ).fetchdf()
    print(raw_features)
    features = gpd.GeoDataFrame(
        raw_features[["spatial_id"]], geometry=gpd.GeoSeries.from_wkt(raw_features.wkt), crs="epsg:27700"
    ).set_index("spatial_id", drop=True)
    if latlon:
        features = features.to_crs(epsg=4326)
    return features
