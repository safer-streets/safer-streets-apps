import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

import geopandas as gpd
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from itrx import Itr
from safer_streets_core.database import duckdb_connector
from safer_streets_core.spatial import AdminGeography, CensusGeography, SpatialUnit
from safer_streets_core.utils import CrimeType, Force, Month, fix_force_name, monthgen
from shapely import wkt

import safer_streets_apps.fastapi.sql as sql
from safer_streets_apps.fastapi import impl
from safer_streets_apps.fastapi.auth import handle_api_key
from safer_streets_apps.fastapi.models import (
    CrimeCountsRequest,
    DfJson,
    FeaturesRequest,
    GeogLookupRequest,
    MonthStr,
)

load_dotenv()


# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.con = duckdb_connector(azure=True)
    yield
    app.state.con.close()


# using Stoplight elements for docs (requires an endpoint without auth)
app = FastAPI(
    title="Safer Streets API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    description="API serving public-domain crime (geo)data.",
)

open_routes = APIRouter(dependencies=[])
auth_routes = APIRouter(dependencies=[Depends(handle_api_key)])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": exc.__class__.__name__,
            "detail": exc.errors(),
            "path": request.url.path,
            "method": request.method,
        },
    )


@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": exc.__class__.__name__,
            "detail": str(exc),
            "path": request.url.path,
            "method": request.method,
        },
    )


@open_routes.get("/docs", include_in_schema=False)
async def api_documentation(request: Request):
    return HTMLResponse("""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
    <title>safer-streets API docs</title>

    <script src="https://unpkg.com/@stoplight/elements/web-components.min.js"></script>
    <link rel="stylesheet" href="https://unpkg.com/@stoplight/elements/styles.min.css">
  </head>
  <body>
    <elements-api
      apiDescriptionUrl="openapi.json"
      router="hash"
    />
  </body>
</html>""")


@open_routes.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("./assets/safer-streets-small.ico")


@auth_routes.get("/diagnostics")
async def diagnostics() -> DfJson:
    # arrow gives native python types (datetime, int) which FastAPI serialises (dates -> ISO 8601);
    # pandas Timestamp/int64 would break JSONResponse's json.dumps
    return app.state.con.sql(sql.TABLE_METADATA.format(index=sql.INDEX)).to_arrow_table().to_pylist()


@auth_routes.get("/time_window")
async def time_window() -> JSONResponse:
    """
    Returns the available months in the crime data
    """
    months = app.state.con.sql(
        "SELECT DISTINCT _month FROM read_parquet('az://phase2/extract/crime_data.parquet') ORDER BY _month"
    ).fetchall()
    return JSONResponse(Itr(months).flatten().collect())


@auth_routes.get("/pfa_geodata")
async def pfa_geodata(force: Force) -> JSONResponse:
    raw_data = app.state.con.sql(
        sql.PFA_GEODATA.format(extract=sql.EXTRACT),
        params=(fix_force_name(force),),
    ).fetchone()

    # duckdb returns the JSON column as a string; parse it or the response is double-encoded
    return JSONResponse(json.loads(raw_data[0]) if raw_data else {})


@auth_routes.get("/features")
async def all_features(geography: AdminGeography, latlon: Annotated[bool, Query] = False) -> Response:
    """
    Return all the geometries for large-area features (PFA/LAD)
    Will return BNG (EPSG:27700) coordinates, or degrees (EPSG:4326) if `latlon` is set to true
    """
    return Response(
        content=impl.all_features(app.state.con, geography, latlon).to_json(),
        media_type="application/json",
    )


@auth_routes.post("/features")
async def features(request: FeaturesRequest, latlon: Annotated[bool, Query] = False) -> Response:
    """
    Return geometries for requested features.
    Will return BNG (EPSG:27700) coordinates, or degrees (EPSG:4326) if `latlon` is set to true
    """
    # NB to_json drops the index name and replaces with "id"
    return Response(content=impl.features(app.state.con, request, latlon).to_json(), media_type="application/json")


@auth_routes.get("/h3/{resolution}")
async def h3(force: Force, resolution: int, latlon: Annotated[bool, Query] = False) -> Response:
    """
    Return H3 grid for a given PFA and resolution (e.g 7 ~ 5km², 8 ~ 0.7km², 9 ~ 0.1km²)
    Will return BNG (EPSG:27700) coordinates or degrees (EPSG:4326) if `latlon` is set to true
    """
    if not 0 <= resolution <= 15:
        raise ValueError("resolution must be between 0 and 15")

    raw = app.state.con.sql(
        sql.PFA_H3_GRID.format(extract=sql.EXTRACT, res=resolution),
        params={"pfa": fix_force_name(force)},
    ).fetchdf()

    features = gpd.GeoDataFrame(raw[["spatial_unit"]], geometry=raw.wkt.apply(wkt.loads), crs="epsg:27700").set_index(
        "spatial_unit"
    )
    if latlon:
        features = features.to_crs(epsg=4326)

    return Response(content=features.to_json(), media_type="application/json")


@auth_routes.post("/geog_lookup")
async def geog_lookup(request: GeogLookupRequest) -> dict[str, str]:
    """
    Map spatial ids from one geography onto another.

    Args:
        geography: The source geography of the ids (H3, PFA23, LAD24, MSOA21, LSOA21, OA21).
        resolution: Required when geography is H3, specifies the H3 resolution level.
        ids: The spatial ids to map.
        target: The geography to map onto (PFA23, LAD24, MSOA21, LSOA21, OA21).

    Returns:
        A JSON object keyed by the input ids, with values the id of the target-geography feature each
        input most overlaps (directly loadable as a pandas Series). H3 ids are mapped using the
        precomputed h3_{resolution}_geogs lookup; non-H3 geographies are mapped to each other via their
        resolution-8 H3 cells (majority vote). Ids not present in the data are omitted.
    """
    return impl.geog_lookup(app.state.con, request)


@auth_routes.get("/census_geographies")
async def census_geographies(geography: CensusGeography, force: Force) -> Response:
    """Return geojson containing census geographies"""
    raw = app.state.con.sql(
        sql.CENSUS_GEOGRAPHIES.format(extract=sql.EXTRACT, parquet=sql.ADMIN_CENSUS_PARQUET[geography]),
        params=(fix_force_name(force),),
    ).fetchdf()
    features = gpd.GeoDataFrame(raw["spatial_unit"], geometry=raw.wkt.apply(wkt.loads), crs="epsg:27700").set_index(
        "spatial_unit", drop=True
    )
    return Response(content=features.to_json(), media_type="application/json")


# TODO potentially deprecate in favour of crime_counts
@auth_routes.get("/census_counts", deprecated=True)
async def census_counts(geography: CensusGeography, force: Force, category: CrimeType) -> DfJson:
    """
    Returns counts for crimes aggregated to census geographies for given force and category for all months by
    spatial unit id

    **Deprecated: use the crime_counts endpoint**
    """
    if geography != "OA21":
        raise ValueError("only implemented for OA21. TODO: aggregate to L/MSOA21")
    return (
        app.state.con.sql(
            sql.CENSUS_COUNTS.format(extract=sql.EXTRACT, parquet=sql.ADMIN_CENSUS_PARQUET[geography]),
            params=(fix_force_name(force), category),
        )
        .to_arrow_table()
        .to_pylist()
    )


@auth_routes.post("/crime_counts")
async def crime_counts_post(
    params: CrimeCountsRequest,
) -> DfJson:
    """
    Get crime counts for given categories and months, aggregated by geography.

    Args:
        geography: The spatial unit for aggregation (HEX (200m), H3, MSOA21, LSOA21, OA21).
        resolution: Required when geography is H3, specifies the H3 resolution level.
        force: The police force to filter crimes by.
        categories: List of the crime type/category to filter by.
        months: List of months in YYYY-MM format.

    Returns:
        Crime counts aggregated to the specified geography as JSON/Arrow format.

    Raises:
        ValueError: If geography is GRID or STREET (not implemented).
        ValueError: If resolution is not specified for H3 geography or specified for other geographies.
    """
    return impl.crime_counts(app.state.con, params)


@auth_routes.get("/crime_counts")
async def crime_counts_get(
    *,
    geography: SpatialUnit,
    resolution: int | None = None,
    force: Force,
    category: CrimeType,
    month: MonthStr,
    lookback: Annotated[int, Query(ge=1, le=36)] = 1,
) -> DfJson:
    """
    Get crime counts for a specific category for a give period, aggregated by geography.

    Args:
        geography: The spatial unit for aggregation (HEX (200m), H3, MSOA21, LSOA21, OA21).
        resolution: Required when geography is H3, specifies the H3 resolution level.
        force: The police force to filter crimes by.
        category: The crime type/category to filter by.
        month: Optional month in YYYY-MM format. Defaults to the latest available month.
        lookback: Number of months to look back (1-36). Defaults to 1 (current month only).

    Returns:
        Crime counts aggregated to the specified geography as JSON/Arrow format.

    Raises:
        ValueError: If geography is GRID or STREET (not implemented).
        ValueError: If resolution is not specified for H3 geography or specified for other geographies.
    """

    month_ = Month.parse_str(month)
    months = Itr(monthgen(month_, backwards=True)).take(lookback).map(str).collect()

    query = CrimeCountsRequest(
        geography=geography, resolution=resolution, force=force, categories=[category], months=months
    )

    return impl.crime_counts(app.state.con, query)


@auth_routes.get("/hotspots")
async def hotspots(
    *,
    force: Force | None = None,
    category: CrimeType,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    lookback: Annotated[int, Query(ge=1, le=12)] = 1,
    n_hotspots: Annotated[int, Query(ge=1)],
    resolution: int = 9,
) -> Response:
    """
    Return geojson of top `n_hotpots` with features (H3 cells, default resolution 9 ~ 0.1km²) and counts of crimes of
    a given category in the period requested, for a specific force (or England & Wales if no force specified).

    The period is the `lookback` months up to and including `month`
    """
    if resolution not in sql.H3_RESOLUTIONS:
        raise ValueError(f"resolution must be one of {sql.H3_RESOLUTIONS}")

    months = Itr(monthgen(Month.parse_str(month), backwards=True)).take(lookback).map(str).collect()

    if not force:
        query = sql.NATIONAL_HOTSPOTS_H3.format(transform=sql.TRANSFORM, res=resolution)
        params = [category, months, n_hotspots]
    else:
        query = sql.FORCE_HOTSPOTS_H3.format(transform=sql.TRANSFORM, extract=sql.EXTRACT, res=resolution)
        params = [fix_force_name(force), category, months, n_hotspots]

    hotspots = app.state.con.sql(query, params=params).fetchdf()
    hotspots = (
        gpd.GeoDataFrame(hotspots[["spatial_unit", "count"]], geometry=hotspots.wkt.apply(wkt.loads), crs="epsg:27700")
        .set_index("spatial_unit", drop=True)
        .dropna()
    )

    return Response(content=hotspots.to_json(), media_type="application/json")


app.include_router(open_routes)
app.include_router(auth_routes)
