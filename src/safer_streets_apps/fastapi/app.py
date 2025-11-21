import json
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import geopandas as gpd
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from itrx import Itr
from safer_streets_core.database import ephemeral_duckdb_spatial_connector
from safer_streets_core.spatial import CensusGeography
from safer_streets_core.utils import Force, Month, fix_force_name, monthgen
from shapely import wkt

import safer_streets_apps.fastapi.sql as sql
from safer_streets_apps.fastapi.auth import handle_api_key
from safer_streets_apps.fastapi.startup import init_db

Category = Literal["Violence and sexual offences", "Anti-social behaviour", "Possession of weapons"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.con = ephemeral_duckdb_spatial_connector()
    init_db(app.state.con)
    yield
    app.state.con.close()


app = FastAPI(title="Safer Streets API", lifespan=lifespan, dependencies=[Depends(handle_api_key)])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input", "details": exc.errors()},
    )


@app.get("/pfa_area")
async def pfa_area(force: Force) -> float:
    """
    Returns the area in km² of the given Police Force Area
    """
    return app.state.con.sql(sql.PFA_AREA, params=(fix_force_name(force),)).fetchone()[0]


@app.post("/hexes")
async def hexes(ids: list[int]):
    """
    Return geometries for requested spatial units.
    Queries to fetch all hexes for a PFA are too slow/large
    """
    raw_hexes = app.state.con.sql(sql.HEXES, params=(ids,)).fetchdf()
    hexes = gpd.GeoDataFrame(
        raw_hexes[["spatial_unit"]], geometry=raw_hexes.wkt.apply(wkt.loads), crs="epsg:27700"
    ).set_index("spatial_unit", drop=True)
    return json.loads(hexes.to_json())


@app.get("/census_geographies")
async def census_geographies(geography: CensusGeography, force: Force):
    """Return geojson containing census geographies"""
    raw = app.state.con.sql(
        sql.CENSUS_GEOGRAPHIES.format(geography=geography), params=[fix_force_name(force)]
    ).fetchdf()
    features = gpd.GeoDataFrame(raw[["spatial_unit"]], geometry=raw.wkt.apply(wkt.loads), crs="epsg:27700").set_index(
        "spatial_unit", drop=True
    )
    return json.loads(features.to_json())


@app.get("/hex_counts")
async def hex_counts(force: Force, category: Category):
    """Returns counts for crimes aggregated to hexes for given force and category for all months by spatial unit id"""
    data = app.state.con.sql(sql.HEX_COUNTS, params=(fix_force_name(force), category)).fetchdf()
    return data.to_dict()


@app.get("/census_counts")
async def census_counts(geography: CensusGeography, force: Force, category: Category):
    assert geography == "OA21", "only implemented for OA21. TODO: aggregate to L/MSOA21"
    """Returns counts for crimes aggregated to census geographies for given force and category for all months by spatial unit id"""
    data = app.state.con.sql(
        sql.CENSUS_COUNTS.format(geography=geography), params=(fix_force_name(force), category)
    ).fetchdf()
    return data.to_dict()


@app.get("/hotspots")
async def hotspots(
    *,
    force: Force | None = None,
    category: Category,
    month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
    lookback: Annotated[int, Query(ge=1, le=12)] = 1,
    n_hotspots: Annotated[int, Query(ge=1)],
):
    """
    Return geojson of top n_hotpots with features and counts for a specific force (or England & Wales if no
    force specified), category and month
    """

    months = Itr(monthgen(Month.parse_str(month), backwards=True)).take(lookback).map(str).collect()

    if not force:
        query = sql.NATIONAL_HOTSPOTS
        params = [category, months, n_hotspots]
    else:
        query = sql.FORCE_HOTSPOTS
        params = [fix_force_name(force), category, months, n_hotspots]

    hotspots = app.state.con.sql(query, params=params).fetchdf()
    hotspots = (
        gpd.GeoDataFrame(hotspots[["spatial_unit", "count"]], geometry=hotspots.wkt.apply(wkt.loads), crs="epsg:27700")
        .set_index("spatial_unit", drop=True)
        .dropna()
    )

    return json.loads(hotspots.to_json())
