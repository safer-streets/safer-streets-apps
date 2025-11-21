PFA_AREA = """
SELECT ST_Area(ST_Union_Agg(geom)) / 1000000
FROM force_boundaries WHERE PFA23NM = ?
"""


HEXES = """
SELECT spatial_unit, ST_AsText(hex200.geometry) AS wkt
FROM hex200
WHERE spatial_unit = ANY($1)
"""

CENSUS_GEOGRAPHIES = """
SELECT {geography}CD as spatial_unit, ST_AsText(geom) AS wkt
FROM {geography}_boundaries
WHERE ST_Intersects(
    {geography}_boundaries.geom,
    (SELECT ST_Union_Agg(geom) FROM force_boundaries WHERE PFA23NM = ?)
);
"""

HEX_COUNTS = """
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

CENSUS_COUNTS = """
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

NATIONAL_HOTSPOTS = """
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

FORCE_HOTSPOTS = """
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
