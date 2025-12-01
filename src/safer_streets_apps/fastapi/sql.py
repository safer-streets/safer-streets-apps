AGGREGATE_TO_HEX = """
CREATE TABLE crime_counts_hex AS
SELECT
    h.spatial_unit AS spatial_unit,
    c.crime_type AS crime_type,
    c.month AS month,
    COUNT(c.month) AS count
FROM
    hex200 h
RIGHT JOIN
    crime_data c ON ST_Intersects(h.geometry, c.geometry)
GROUP BY
    spatial_unit, month, crime_type;
"""


AGGREGATE_TO_OA21 = """
CREATE TABLE crime_counts_oa AS
SELECT
    h.OA21CD AS spatial_unit,
    c.crime_type AS crime_type,
    c.month AS month,
COUNT(c.month) AS count
FROM
    OA21_boundaries h
RIGHT JOIN
    crime_data c ON ST_Intersects(h.geometry, c.geometry)
GROUP BY
    spatial_unit, month, crime_type;
"""


PFA_GEODATA = """
WITH g as (
    SELECT
        ST_Area(geometry) / 1000000 AS area,
        ST_Transform(geometry, 'EPSG:27700', 'EPSG:4326', always_xy := true) AS geometry
    FROM force_boundaries
    WHERE PFA23NM = ?
)
SELECT
    area,
    ST_X(ST_Centroid(geometry)),
    ST_Y(ST_Centroid(geometry)),
    ST_AsGeoJson(geometry)
FROM g
"""


HEXES = """
SELECT spatial_unit, ST_AsText(hex200.geometry) AS wkt
FROM hex200
WHERE spatial_unit = ANY($1)
"""

CENSUS_GEOGRAPHIES = """
SELECT {geography}CD as spatial_unit, ST_AsText(geometry) AS wkt
FROM {geography}_boundaries
WHERE ST_Intersects(
    {geography}_boundaries.geometry,
    (SELECT ST_Union_Agg(geometry) FROM force_boundaries WHERE PFA23NM = ?)
);
"""

HEX_COUNTS = """
WITH h AS (
    SELECT * FROM hex200
    WHERE ST_Intersects(
        hex200.geometry,
        (SELECT ST_Union_Agg(geometry) FROM force_boundaries WHERE PFA23NM = $1)
    )
)
SELECT c.spatial_unit, c.month, c.count FROM crime_counts_hex c
RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
WHERE c.crime_type = $2
"""

CENSUS_COUNTS = """
WITH h AS (
    SELECT {geography}CD as spatial_unit, geometry FROM {geography}_boundaries
    WHERE ST_Intersects(
        {geography}_boundaries.geometry,
        (SELECT ST_Union_Agg(geometry) FROM force_boundaries WHERE PFA23NM = $1)
    )
)
SELECT c.spatial_unit, c.month, c.count FROM crime_counts_oa c
RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
WHERE c.crime_type = $2
"""

NATIONAL_HOTSPOTS_HEX = """
WITH h AS (
    SELECT spatial_unit, count
    FROM crime_counts_hex
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

FORCE_HOTSPOTS_HEX = """
WITH h AS (
    SELECT * FROM hex200
    WHERE ST_Intersects(
        hex200.geometry,
        (SELECT ST_Union_Agg(geometry) FROM force_boundaries WHERE PFA23NM = $1)
    )
)
SELECT c.spatial_unit, SUM(c.count) AS count, ST_AsText(h.geometry) AS wkt FROM crime_counts_hex c
RIGHT JOIN h ON h.spatial_unit = c.spatial_unit
WHERE c.crime_type = $2 AND c.month = ANY($3)
GROUP BY c.spatial_unit, wkt
ORDER BY count DESC, c.spatial_unit ASC
LIMIT $4;
"""

# get OA counts GDF for a single force, using density as a tiebreak (i.e. favour smaller OAs)
FORCE_HOTSPOTS_OA = """
WITH h AS (
    SELECT * FROM OA21_boundaries
    WHERE ST_Intersects(
        OA21_boundaries.geometry,
        (SELECT ST_Union_Agg(geometry) FROM force_boundaries WHERE PFA23NM = $1)
    )
)
SELECT
    c.spatial_unit, SUM(c.count) AS count,
    ST_Area(h.geometry) / 1000000 AS area,
    ST_AsText(h.geometry) AS wkt
FROM crime_counts_oa c
RIGHT JOIN h ON h.OA21CD = c.spatial_unit
WHERE c.crime_type = $2 AND c.month = ANY($3)
GROUP BY c.spatial_unit, wkt, h.geometry
ORDER BY count DESC, SUM(count) / area DESC, c.spatial_unit ASC
LIMIT $4;
"""

TABLE_SCHEMAS = """
SELECT table_name,
       column_name,
       data_type
FROM information_schema.columns;
"""
