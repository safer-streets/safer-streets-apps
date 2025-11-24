import duckdb
from itrx import Itr
from safer_streets_core.database import add_table_from_shapefile
from safer_streets_core.utils import CATEGORIES, data_dir, latest_month, monthgen

from safer_streets_apps.fastapi import sql

N_MONTHS = 36


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    # force boundaries
    add_table_from_shapefile(
        con, "force_boundaries", "Police_Force_Areas_December_2023_EW_BFE_2734900428741300179.zip", exists_ok=True
    )

    # census boundaries
    add_table_from_shapefile(
        con,
        "MSOA21_boundaries",
        "Middle_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V3_-6221323399304446140.zip",
        exists_ok=True,
    )
    add_table_from_shapefile(
        con,
        "LSOA21_boundaries",
        "Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5_4492169359079898015.zip",
        exists_ok=True,
    )
    add_table_from_shapefile(
        con, "OA21_boundaries", "Output_Areas_2021_EW_BGC_V2_-6371128854279904124.zip", exists_ok=True
    )

    # hex grid
    con.execute(f"CREATE TABLE hex200 AS SELECT * FROM '{data_dir() / 'england_wales_HEX-200_untrimmed.parquet'}'")

    timeline = Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev()

    # extract/load crime data
    all_files = Itr(data_dir().glob(f"extracted/{month}*street.parquet") for month in timeline).flatten()

    con.execute(f"""
        CREATE TABLE crime_data AS SELECT *
        FROM read_parquet({[f"{str(f)}" for f in all_files]})
        WHERE "Crime type" = ANY({list(CATEGORIES)});
        ALTER TABLE crime_data ADD COLUMN geom GEOMETRY;
        UPDATE crime_data
        SET geom = ST_Transform(ST_Point(Longitude, Latitude), 'EPSG:4326', 'EPSG:27700', always_xy := true);
    """)

    # transform to counts
    con.execute(sql.AGGREGATE_TO_HEX)
    con.execute(sql.AGGREGATE_TO_OA21)
