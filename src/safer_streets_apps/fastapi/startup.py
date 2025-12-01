from typing import get_args
import logging

import duckdb
from itrx import Itr
from safer_streets_core.database import add_table_from_shapefile
from safer_streets_core.utils import CATEGORIES, CrimeType, data_dir, latest_month, monthgen

from safer_streets_apps.fastapi import sql

N_MONTHS = 36


def init_db(con: duckdb.DuckDBPyConnection) -> None:

    logging.info("Initialising database")
    # force boundaries
    add_table_from_shapefile(
        con,
        "force_boundaries",
        "PFA23NM",
        "Police_Force_Areas_December_2023_EW_BFE_2734900428741300179.zip",
        exists_ok=True,
    )

    # census boundaries
    add_table_from_shapefile(
        con,
        "MSOA21_boundaries",
        "MSOA21CD",
        "Middle_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V3_-6221323399304446140.zip",
        exists_ok=True,
    )
    add_table_from_shapefile(
        con,
        "LSOA21_boundaries",
        "LSOA21CD",
        "Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5_4492169359079898015.zip",
        exists_ok=True,
    )
    add_table_from_shapefile(
        con, "OA21_boundaries", "OA21CD", "Output_Areas_2021_EW_BGC_V2_-6371128854279904124.zip", exists_ok=True
    )

    # hex grid
    con.execute(
        f"CREATE TABLE hex200 AS SELECT spatial_unit, geometry FROM '{data_dir() / 'england_wales_HEX-200_untrimmed.parquet'}'"
    )
    logging.info("Initialised spatial data")

    timeline = Itr(monthgen(latest_month(), backwards=True)).take(N_MONTHS).rev()

    # extract/load crime data
    all_files = Itr(data_dir().glob(f"extracted/{month}*street.parquet") for month in timeline).flatten()

    con.execute(f"""
        CREATE TABLE crime_data AS SELECT
            Month AS month,
            "Reported by" AS reporter,
            "Falls within" AS force,
            "Crime type" AS crime_type,
            ST_Transform(ST_Point(Longitude, Latitude), 'EPSG:4326', 'EPSG:27700', always_xy := true) AS geometry
        FROM read_parquet({[f"{str(f)}" for f in all_files]})
        WHERE crime_type = ANY({list(get_args(CrimeType))});
    """)

    logging.info("Initialised crime data")

    # transform to counts
    cache_file = data_dir() / f"duckdb_cache/crime_counts_hex_{latest_month()}.parquet"
    if not cache_file.exists():
        logging.info("Creating hex crime count table")
        con.execute(sql.AGGREGATE_TO_HEX)
        con.sql("SELECT * FROM crime_counts_hex").fetchdf().to_parquet(cache_file)
    else:
        logging.info("Using cached hex crime count table")
        con.execute(f"CREATE TABLE crime_counts_hex AS SELECT * FROM read_parquet('{cache_file}')")

    cache_file = data_dir() / f"duckdb_cache/crime_counts_oa_{latest_month()}.parquet"
    if not cache_file.exists():
        logging.info("Creating OA21 crime count table")
        con.execute(sql.AGGREGATE_TO_OA21)
        con.sql("SELECT * FROM crime_counts_oa").fetchdf().to_parquet(cache_file)
    else:
        logging.info("Using cached OA21 crime count table")
        con.execute(f"CREATE TABLE crime_counts_oa AS SELECT * FROM read_parquet('{cache_file}')")

    logging.info("Initialised crime count data")
    logging.info("Database initialisation complete")
