"""Hermetic API test fixtures.

The API normally reads parquet straight from Azure blob storage; here a session-scoped fixture builds
a tiny replica of that data model in a temp directory (one square force area containing two output
areas and a handful of crimes) and repoints the ``sql`` module's path constants at it, so every
endpoint runs its real query against local files with no network or credentials.
"""

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from safer_streets_core.database import duckdb_connector

from safer_streets_apps.fastapi import auth, sql
from safer_streets_apps.fastapi.app import app

# any hex string works: the auth fixture repoints auth.KEY_HASH at this key's hash
TEST_API_KEY = "5afe57ee75"

FORCE = "West Yorkshire"
PFA_CODE = "E23000010"
LAD_CODE = "E08000035"

# 10km x 10km BNG square split vertically into two output areas at OA_SPLIT
XMIN, YMIN, XMAX, YMAX = 425_000, 430_000, 435_000, 440_000
OA_SPLIT = 430_000

# (crime_type, month, easting, northing): point A sits in OA E00000001, point B in E00000002
CRIMES = [
    ("Burglary", "2025-05", 427_500.0, 435_000.0),
    ("Burglary", "2025-05", 427_500.0, 435_000.0),
    ("Burglary", "2025-04", 427_500.0, 435_000.0),
    ("Burglary", "2025-05", 432_500.0, 435_000.0),
    ("Robbery", "2025-05", 432_500.0, 435_000.0),
]


def _build_data(root: Path) -> None:
    extract, transform = root / "extract", root / "transform"
    extract.mkdir()
    transform.mkdir()

    con = duckdb_connector()
    crime_rows = ", ".join(f"('{c}', '{m}', {e}, {n})" for c, m, e, n in CRIMES)
    con.execute(f"""
        CREATE TABLE crime AS
        SELECT crime_type, _month, ST_X(ll) AS longitude, ST_Y(ll) AS latitude, ST_Point(easting, northing) AS geom
        FROM (
            SELECT *, ST_Transform(ST_Point(easting, northing), 'EPSG:27700', 'EPSG:4326', always_xy := true) AS ll
            FROM (VALUES {crime_rows}) t(crime_type, _month, easting, northing)
        );
        COPY crime TO '{extract}/crime_data.parquet' (FORMAT parquet);

        -- each crime tagged with the geography codes containing it (OA depends on which side of the split)
        CREATE TABLE crime_geogs AS
        SELECT crime_type, _month AS month, latitude, longitude,
               '{PFA_CODE}' AS pfa23cd, '{LAD_CODE}' AS lad24cd, 'E02000001' AS msoa21cd, 'E01000001' AS lsoa21cd,
               CASE WHEN ST_X(geom) < {OA_SPLIT} THEN 'E00000001' ELSE 'E00000002' END AS oa21cd
        FROM crime;

        COPY (
            SELECT '{PFA_CODE}' AS spatial_id, '{FORCE}' AS pfa23nm,
                   ST_MakeEnvelope({XMIN}, {YMIN}, {XMAX}, {YMAX}) AS geom
        ) TO '{extract}/police_force_areas.parquet' (FORMAT parquet);

        COPY (
            SELECT * FROM (VALUES
                ('E00000001', ST_MakeEnvelope({XMIN}, {YMIN}, {OA_SPLIT}, {YMAX})),
                ('E00000002', ST_MakeEnvelope({OA_SPLIT}, {YMIN}, {XMAX}, {YMAX}))
            ) t(spatial_id, geom)
        ) TO '{extract}/output_areas_2021.parquet' (FORMAT parquet);

        COPY (
            SELECT 'E01000001' AS spatial_id, ST_MakeEnvelope({XMIN}, {YMIN}, {XMAX}, {YMAX}) AS geom
        ) TO '{extract}/lsoa_2021.parquet' (FORMAT parquet);

        COPY (
            SELECT 'E02000001' AS spatial_id, ST_MakeEnvelope({XMIN}, {YMIN}, {XMAX}, {YMAX}) AS geom
        ) TO '{extract}/msoa_2021.parquet' (FORMAT parquet);

        COPY (
            SELECT 'extract' AS phase, 'crime_data' AS name, 'test fixture' AS description,
                   {len(CRIMES)} AS n_rows, 5 AS n_columns, true AS has_geometry,
                   ['crime_type', '_month', 'longitude', 'latitude', 'geom'] AS columns,
                   now() AS last_modified
        ) TO '{root}/index.parquet' (FORMAT parquet);
    """)
    for res in sql.H3_RESOLUTIONS:
        con.execute(f"""
            CREATE TABLE counts_{res} AS
            SELECT lower(hex(h3_latlng_to_cell(latitude, longitude, {res}))) AS spatial_id,
                   crime_type, _month AS month, COUNT(*) AS count
            FROM crime
            GROUP BY ALL;
            COPY counts_{res} TO '{transform}/crime_counts_h3_{res}.parquet' (FORMAT parquet);
            COPY (
                SELECT DISTINCT
                    lower(hex(h3_latlng_to_cell(latitude, longitude, {res}))) AS spatial_id,
                    pfa23cd, lad24cd, msoa21cd, lsoa21cd, oa21cd
                FROM crime_geogs
            ) TO '{transform}/h3_{res}_geogs.parquet' (FORMAT parquet);
        """)
    for key in ("pfa23cd", "lad24cd", "msoa21cd", "lsoa21cd", "oa21cd"):
        con.execute(f"""
            COPY (SELECT {key} AS spatial_id, crime_type, month, COUNT(*) AS count FROM crime_geogs GROUP BY ALL)
            TO '{transform}/crime_counts_{key}.parquet' (FORMAT parquet);
        """)
    con.close()


@pytest.fixture(scope="session")
def _test_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    root = tmp_path_factory.mktemp("azure_data")
    _build_data(root)

    mp = pytest.MonkeyPatch()
    mp.setattr(sql, "EXTRACT", str(root / "extract"))
    mp.setattr(sql, "TRANSFORM", str(root / "transform"))
    mp.setattr(sql, "INDEX", str(root / "index.parquet"))
    mp.setattr(auth, "KEY_HASH", sha256(bytes.fromhex(TEST_API_KEY)).hexdigest())

    # the real lifespan opens an Azure connection, so it is bypassed (no `with` on the TestClient)
    # and the app is given a local connection directly
    app.state.con = duckdb_connector()
    try:
        yield
    finally:
        app.state.con.close()
        mp.undo()


@pytest.fixture(scope="session")
def client(_test_app: None) -> TestClient:
    """Authenticated client: raise_server_exceptions=False so error responses match production."""
    return TestClient(app, raise_server_exceptions=False, headers={"x-api-key": TEST_API_KEY})


@pytest.fixture(scope="session")
def anon_client(_test_app: None) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)
