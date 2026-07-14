# All tables are read directly from the project's public Azure blob storage as parquet files.
# `{extract}`/`{transform}`/`{index}` are substituted with the paths below; `{parquet}`/`{res}` are
# substituted with a (validated) census-boundary parquet name / H3 resolution before execution.
EXTRACT = "az://phase2/extract"
TRANSFORM = "az://phase2/transform"
INDEX = "az://phase2/index.parquet"

# geography -> boundary parquet whose `spatial_id` column is that geography's code (e.g. OA21 -> E00...)
CENSUS_PARQUET = {
    "OA21": "output_areas_2021",
    "LSOA21": "lsoa_2021",
    "MSOA21": "msoa_2021",
}

# H3 resolutions for which crime counts / geography lookups are precomputed on Azure
H3_RESOLUTIONS = (8, 9, 10)


# Directly return geojson so translating via GeoPandas is not required. This is likely a far more
# efficient approach, but gpd isn't a bottleneck currently, and standard geojson doesn't include CRS.
PFA_GEODATA = """
WITH g AS (
    SELECT
        spatial_id,
        pfa23nm AS name,
        ST_Area(geom) / 1000000 AS area,
        ST_Transform(geom, 'EPSG:27700', 'EPSG:4326', always_xy := true) AS geom
    FROM read_parquet('{extract}/police_force_areas.parquet')
    WHERE PFA23NM = ?
)
SELECT json_object(
    'type', 'Feature',
    'geometry', ST_AsGeoJSON(geom)::json,
    'properties', json_object(
        'spatial_id', spatial_id,
        'name', name,
        'area', area,
        'lon', ST_X(ST_Centroid(geom)),
        'lat', ST_Y(ST_Centroid(geom))
    )
) AS feature
FROM g
"""


# H3 cell boundaries are computed on the fly from the cell ids (no stored geometry needed)
H3_FEATURES = """
WITH ids AS (
SELECT * AS spatial_unit FROM unnest(?)
)
SELECT spatial_unit, h3_cell_to_boundary_wkt(spatial_unit) AS wkt FROM ids
"""

CENSUS_FEATURES = """
SELECT spatial_id AS spatial_unit, ST_AsText(geom) AS wkt
FROM read_parquet('{extract}/{parquet}.parquet')
WHERE spatial_id IN ?
"""

# H3 grid over a police force area. `h3_polygon_wkt_to_cells` only handles single polygons, so the
# (multi)polygon force boundary is exploded with ST_Dump first.
PFA_H3_GRID = """
WITH parts AS (
    SELECT UNNEST(ST_Dump(ST_Transform(geom, 'EPSG:27700', 'EPSG:4326', always_xy := true))).geom AS g
    FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = $pfa
),
h AS (
    SELECT DISTINCT UNNEST(h3_polygon_wkt_to_cells(ST_AsText(g), {res})) AS id FROM parts
),
h3 AS (
    SELECT
        id,
        ST_Transform(ST_GeomFromWKB(h3_cell_to_boundary_wkb(id)), 'EPSG:4326', 'EPSG:27700', always_xy := true) AS geometry
    FROM h
)
SELECT lower(hex(id)) AS spatial_unit, ST_AsText(geometry) AS wkt FROM h3
"""


# census geographies overlapping a police force, resolved by spatial intersection with the force boundary
CENSUS_GEOGRAPHIES = """
WITH force AS (
    SELECT geom FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = ?
)
SELECT b.spatial_id AS spatial_unit, ST_AsText(b.geom) AS wkt
FROM read_parquet('{extract}/{parquet}.parquet') b, force
WHERE ST_Intersects(b.geom, force.geom)
"""

# Deprecated: crime counts aggregated to census geographies for a single force/category, all months.
# Points are spatial-joined against the boundaries at request time.
CENSUS_COUNTS = """
WITH force AS (
    SELECT geom FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = $1
),
b AS (
    SELECT b.spatial_id, b.geom
    FROM read_parquet('{extract}/{parquet}.parquet') b, force
    WHERE ST_Intersects(b.geom, force.geom)
)
SELECT b.spatial_id AS spatial_unit, c._month AS month, COUNT(*) AS count
FROM b JOIN read_parquet('{extract}/crime_data.parquet') c
    ON ST_Intersects(b.geom, ST_Transform(ST_Point(c.longitude, c.latitude), 'EPSG:4326', 'EPSG:27700', always_xy := true))
WHERE c.crime_type = $2
GROUP BY spatial_unit, month
"""

NATIONAL_HOTSPOTS_H3 = """
WITH h AS (
    SELECT spatial_id, SUM(count) AS count
    FROM read_parquet('{transform}/crime_counts_h3_{res}.parquet')
    WHERE crime_type = $1 AND month = ANY($2)
    GROUP BY spatial_id
    ORDER BY count DESC, spatial_id ASC
    LIMIT $3
)
SELECT
    spatial_id AS spatial_unit, count,
    ST_AsText(ST_Transform(h3_cell_to_boundary_wkt(spatial_id)::GEOMETRY, 'EPSG:4326', 'EPSG:27700', always_xy := true)) AS wkt
FROM h
ORDER BY count DESC, spatial_id ASC
"""

FORCE_HOTSPOTS_H3 = """
WITH cells AS (
    SELECT spatial_id FROM read_parquet('{transform}/h3_{res}_geogs.parquet')
    WHERE pfa23cd = (SELECT spatial_id FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = $1)
),
h AS (
    SELECT c.spatial_id, SUM(c.count) AS count
    FROM read_parquet('{transform}/crime_counts_h3_{res}.parquet') c
    JOIN cells USING (spatial_id)
    WHERE c.crime_type = $2 AND c.month = ANY($3)
    GROUP BY c.spatial_id
    ORDER BY count DESC, c.spatial_id ASC
    LIMIT $4
)
SELECT
    spatial_id AS spatial_unit, count,
    ST_AsText(ST_Transform(h3_cell_to_boundary_wkt(spatial_id)::GEOMETRY, 'EPSG:4326', 'EPSG:27700', always_xy := true)) AS wkt
FROM h
"""

# H3 crime counts come straight from the precomputed table, filtered to the force's cells.
H3_CRIME_COUNTS = """
SELECT c.spatial_id AS spatial_unit, c.crime_type AS crime_type, c.month AS month, c.count AS count
FROM read_parquet('{transform}/crime_counts_h3_{res}.parquet') c
WHERE c.spatial_id IN (
    SELECT spatial_id FROM read_parquet('{transform}/h3_{res}_geogs.parquet')
    WHERE pfa23cd = (
        SELECT spatial_id FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = $pfa
    )
)
AND c.month IN $months AND c.crime_type IN $crime_types
"""

# census crime counts are spatial-joined against the boundaries at request time
CENSUS_CRIME_COUNTS = """
WITH force AS (
    SELECT geom FROM read_parquet('{extract}/police_force_areas.parquet') WHERE pfa23nm = $pfa
),
b AS (
    SELECT b.spatial_id, b.geom
    FROM read_parquet('{extract}/{parquet}.parquet') b, force
    WHERE ST_Intersects(b.geom, force.geom)
)
SELECT b.spatial_id AS spatial_unit, c.crime_type AS crime_type, c._month AS month, COUNT(*) AS count
FROM b JOIN read_parquet('{extract}/crime_data.parquet') c
    ON ST_Intersects(b.geom, ST_Transform(ST_Point(c.longitude, c.latitude), 'EPSG:4326', 'EPSG:27700', always_xy := true))
WHERE c._month IN $months AND c.crime_type IN $crime_types
GROUP BY spatial_unit, crime_type, month
"""


TABLE_METADATA = """
SELECT * FROM read_parquet('{index}')
"""
