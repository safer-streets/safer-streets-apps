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
from safer_streets_core.utils import Force, Month, monthgen
from shapely import wkt

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


def _fix_force(force: Force) -> str:
    NAME_ADJUSTMENTS = {
        "Metropolitan": "Metropolitan Police",
        "Devon and Cornwall": "Devon & Cornwall",
        "City of London": "London, City of",
        "Dyfed Powys": "Dyfed-Powys",
    }
    return NAME_ADJUSTMENTS.get(force, force)



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

    query = """SELECT ST_Area(ST_Union_Agg(geom)) / 1000000 FROM force_boundaries WHERE PFA23NM = ?"""
    return app.state.con.sql(query, params=(_fix_force(force),)).fetchone()[0]


@app.post("/hexes")
async def hexes(ids: list[int]):
    """
    Return geometries for requested spatial units.
    Queries to fetch all hexes for a PFA are too slow/large
    """
    query = """
    SELECT spatial_unit, ST_AsText(hex200.geometry) AS wkt
    FROM hex200
    WHERE spatial_unit = ANY($1)
    """
    raw_hexes = app.state.con.sql(query, params=(ids,)).fetchdf()
    hexes = gpd.GeoDataFrame(
        raw_hexes[["spatial_unit"]], geometry=raw_hexes.wkt.apply(wkt.loads), crs="epsg:27700"
    ).set_index("spatial_unit", drop=True)
    return json.loads(hexes.to_json())


@app.get("/census_geographies")
async def census_geographies(geography: CensusGeography, force: Force):
    """Return geojson containing census geographies"""
    query = f"""
    SELECT {geography}CD as spatial_unit, ST_AsText(geom) AS wkt
    FROM {geography}_boundaries
    WHERE ST_Intersects(
        {geography}_boundaries.geom,
        (SELECT ST_Union_Agg(geom) FROM force_boundaries WHERE PFA23NM = ?)
    );
    """
    raw = app.state.con.sql(query, params=[_fix_force(force)]).fetchdf()
    features = gpd.GeoDataFrame(raw[["spatial_unit"]], geometry=raw.wkt.apply(wkt.loads), crs="epsg:27700").set_index(
        "spatial_unit", drop=True
    )
    return json.loads(features.to_json())


@app.get("/hex_counts")
async def hex_counts(force: Force, category: Category):
    """Returns counts for crimes aggregated to hexes for given force and category for all months by spatial unit id"""
    query = """
    WITH h AS (
        SELECT * FROM hex200
        WHERE ST_Intersects(
            hex200.geometry,
            (SELECT ST_Union_Agg(geom) FROM force_boundaries WHERE PFA23NM = $1)
        )
    )
    SELECT c.spatial_unit, c.month, c.count FROM crime_counts c
    RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
    WHERE c.crime_type = $2
    """
    data = app.state.con.sql(query, params=(_fix_force(force), category)).fetchdf()
    return data.to_dict()


@app.get("/census_counts")
async def census_counts(geography: CensusGeography, force: Force, category: Category):
    assert geography == "OA21", "only implemented for OA21. TODO: aggregate to L/MSOA21"
    """Returns counts for crimes aggregated to census geographies for given force and category for all months by spatial unit id"""
    query = f"""
    WITH h AS (
        SELECT {geography}CD as spatial_unit, geom FROM {geography}_boundaries
        WHERE ST_Intersects(
            {geography}_boundaries.geom,
            (SELECT ST_Union_Agg(geom) FROM force_boundaries WHERE PFA23NM = $1)
        )
    )
    SELECT c.spatial_unit, c.month, c.count FROM crime_counts_oa c
    RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
    WHERE c.crime_type = $2
    """
    data = app.state.con.sql(query, params=(_fix_force(force), category)).fetchdf()
    return data.to_dict()


@app.get("/hotspots")
async def hotspots(*, force: Force | None = None, category: Category,
                   month: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
                   lookback: Annotated[int, Query(ge=1, le=12)] = 1,
                   n_hotspots: Annotated[int, Query(ge=1)]):
    """
    Return geojson of top n_hotpots with features and counts for a specific force (or England & Wales if no
    force specified), category and month
    """

    months = Itr(monthgen(Month.parse_str(month), backwards=True)).take(lookback).map(str).collect()

    if not force:
        query = """
        WITH h AS (
            SELECT spatial_unit, count
            FROM crime_counts
            WHERE crime_type = $1 AND month = ANY($2)
            ORDER BY count DESC, spatial_unit ASC
            LIMIT $3
        )
        SELECT
            h.spatial_unit, SUM(h.count) AS count, ST_AsText(hex200.geometry) AS wkt
        FROM hex200
        RIGHT JOIN h ON h.spatial_unit = hex200.spatial_unit
        GROUP BY h.spatial_unit, wkt
        ORDER BY count DESC, h.spatial_unit ASC;
        """
        params = [category, months, n_hotspots]

    else:
        query = """
        WITH h AS (
            SELECT * FROM hex200
            WHERE ST_Intersects(
                hex200.geometry,
                (SELECT ST_Union_Agg(geom) FROM force_boundaries WHERE PFA23NM = $1)
            )
        )
        SELECT c.spatial_unit, SUM(c.count) AS count, ST_AsText(h.geometry) AS wkt FROM crime_counts c
        RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
        WHERE c.crime_type = $2 AND c.month = ANY($3)
        GROUP BY c.spatial_unit, wkt
        ORDER BY count DESC, c.spatial_unit ASC
        LIMIT $4;
        """
        params = [_fix_force(force), category, months, n_hotspots]

    hotspots = app.state.con.sql(query, params=params).fetchdf()
    hotspots = (
        gpd.GeoDataFrame(hotspots[["spatial_unit", "count"]], geometry=hotspots.wkt.apply(wkt.loads), crs="epsg:27700")
        .set_index("spatial_unit", drop=True)
        .dropna()
    )

    return json.loads(hotspots.to_json())
