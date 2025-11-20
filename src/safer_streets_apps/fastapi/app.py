import json
from contextlib import asynccontextmanager

import duckdb
import geopandas as gpd
from fastapi import Depends, FastAPI
from itrx import Itr
from safer_streets_core.database import add_table_from_shapefile, ephemeral_duckdb_spatial_connector
from safer_streets_core.utils import CATEGORIES, Force, data_dir, latest_month, monthgen
from shapely import wkt

from safer_streets_apps.fastapi.auth import handle_api_key

N_MONTHS = 36


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    # force boundaries
    add_table_from_shapefile(
        con, "force_boundaries", "Police_Force_Areas_December_2023_EW_BFE_2734900428741300179.zip", exists_ok=True
    )
    # hex grid
    con.execute(
        f"CREATE TABLE IF NOT EXISTS hex200 AS SELECT * FROM '{data_dir() / 'england_wales_HEX-200_untrimmed.parquet'}'"
    )

    timeline = Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev()

    # extract/load crime data
    all_files = Itr(data_dir().glob(f"extracted/{month}*street.parquet") for month in timeline.copy()).flatten()

    con.execute(f"""
        CREATE TABLE crime_data AS SELECT *
        FROM read_parquet({[f"{str(f)}" for f in all_files]})
        WHERE "Crime type" = ANY({list(CATEGORIES)});
        ALTER TABLE crime_data ADD COLUMN geom GEOMETRY;
        UPDATE crime_data
        SET geom = ST_Transform(ST_Point(Longitude, Latitude), 'EPSG:4326', 'EPSG:27700', always_xy := true);
    """)

    # transform to counts
    query = """
    CREATE TABLE crime_counts AS
    SELECT
    h.spatial_unit AS spatial_unit,
    c."Crime type" AS crime_type,
    c.Month AS month,
    COUNT(c.Month) AS count
    FROM
    hex200 h
    RIGHT JOIN crime_data c ON ST_Intersects(h.geometry, c.geom)
    GROUP BY
    spatial_unit,
    month,
    crime_type;
    -- ORDER BY
    --  spatial_unit --, month, crime_type;
    """
    con.execute(query)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.con = ephemeral_duckdb_spatial_connector()
    init_db(app.state.con)
    yield
    app.state.con.close()


app = FastAPI(title="Safer Streets API", lifespan=lifespan, dependencies=[Depends(handle_api_key)])


@app.get("/pfa_area")
async def pfa_area(force: Force) -> float:
    """
    Returns the area in km² of the given Police Force Area
    """

    query = """SELECT ST_Area(ST_Union_Agg(geom)) / 1000000 FROM force_boundaries WHERE PFA23NM = ?"""
    return app.state.con.sql(query, params=(force,)).fetchone()[0]


@app.post("/hexes")
async def hexes(ids: list[int]):
    """Return geometries for requested spatial units"""
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


@app.get("/counts")
async def counts(force: Force, category: str):
    """Returns count data for given force and category for all months by spatial unit id"""
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
    data = app.state.con.sql(query, params=(force, category)).fetchdf()
    return data.to_dict()


@app.get("/hotspots")
async def hotspots(force: Force, category: str, month: str, n_hotspots: int):
    """Return geojson with of top n hotpots with features and counts for a specifc force, category and month"""

    query = """WITH h AS (
        SELECT spatial_unit, count
        FROM crime_counts
        WHERE month = '{month}'
        AND crime_type = '{category}'
        ORDER BY count DESC, spatial_unit ASC
        LIMIT {n_hotspots}
    )
    SELECT
        h.spatial_unit,
        h.count,
        ST_AsText(hex200.geometry) AS wkt
    FROM hex200
    RIGHT JOIN h ON h.spatial_unit = hex200.spatial_unit
    ORDER BY count DESC, h.spatial_unit ASC;
    """

    hotspots = app.state.con.execute(query.format(month=month, category=category, n_hotspots=n_hotspots)).fetchdf()
    hotspots = (
        gpd.GeoDataFrame(hotspots[["spatial_unit", "count"]], geometry=hotspots.wkt.apply(wkt.loads), crs="epsg:27700")
        .set_index("spatial_unit", drop=True)
        .dropna()
    )

    return json.loads(hotspots.to_json())
