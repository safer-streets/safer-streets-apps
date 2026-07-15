from typing import Any

from conftest import FORCE, LAD_CODE, PFA_CODE, TEST_API_KEY
from fastapi.testclient import TestClient


def _counts(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], int]:
    """Index crime count rows by their key columns (order-independent comparison)."""
    return {tuple(str(v) for k, v in row.items() if k != "count"): row["count"] for row in rows}


# --- open routes ---


def test_docs(anon_client: TestClient) -> None:
    response = anon_client.get("/docs")
    assert response.status_code == 200
    assert "elements-api" in response.text


def test_openapi(anon_client: TestClient) -> None:
    response = anon_client.get("/openapi.json")
    assert response.status_code == 200
    assert "/crime_counts" in response.json()["paths"]


def test_favicon(anon_client: TestClient) -> None:
    response = anon_client.get("/favicon.ico")
    assert response.status_code == 200


# --- auth ---


def test_auth_missing_key(anon_client: TestClient) -> None:
    assert anon_client.get("/diagnostics").status_code == 401


def test_auth_wrong_key(anon_client: TestClient) -> None:
    response = anon_client.get("/diagnostics", headers={"x-api-key": "deadbeef"})
    assert response.status_code == 403
    assert response.json()["detail"] == "API key missing or invalid"


def test_auth_non_hex_key(anon_client: TestClient) -> None:
    response = anon_client.get("/diagnostics", headers={"x-api-key": "not-hex!"})
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


def test_auth_valid_key(anon_client: TestClient) -> None:
    assert anon_client.get("/diagnostics", headers={"x-api-key": TEST_API_KEY}).status_code == 200


# --- diagnostics ---


def test_diagnostics(client: TestClient) -> None:
    response = client.get("/diagnostics")
    assert response.status_code == 200
    (row,) = response.json()
    assert row["name"] == "crime_data"
    assert row["has_geometry"] is True
    # timestamps must serialise to ISO 8601 strings
    assert isinstance(row["last_modified"], str) and "T" in row["last_modified"]


# --- pfa_geodata ---


def test_pfa_geodata(client: TestClient) -> None:
    response = client.get("/pfa_geodata", params={"force": FORCE})
    assert response.status_code == 200
    feature = response.json()
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    props = feature["properties"]
    assert props["spatial_id"] == PFA_CODE
    assert props["name"] == FORCE
    assert round(props["area"]) == 100  # 10km x 10km fixture square
    assert -3 < props["lon"] < 0 and 53 < props["lat"] < 55


def test_pfa_geodata_force_not_in_data(client: TestClient) -> None:
    response = client.get("/pfa_geodata", params={"force": "Kent"})
    assert response.status_code == 200
    assert response.json() == {}


def test_pfa_geodata_invalid_force(client: TestClient) -> None:
    response = client.get("/pfa_geodata", params={"force": "Gotham City"})
    assert response.status_code == 400
    assert response.json()["error"] == "RequestValidationError"


# --- features ---


def _h3_cells(client: TestClient, resolution: int) -> list[str]:
    """The fixture's H3 cell ids, recovered via the crime_counts endpoint."""
    response = client.post(
        "/crime_counts",
        json={
            "geography": "H3",
            "resolution": resolution,
            "force": FORCE,
            "categories": ["Burglary", "Robbery"],
            "months": ["2025-04", "2025-05"],
        },
    )
    assert response.status_code == 200
    return sorted({row["spatial_id"] for row in response.json()})


def test_features_h3(client: TestClient) -> None:
    cells = _h3_cells(client, 8)
    assert len(cells) == 2  # the two crime locations are 5km apart

    response = client.post("/features", json={"geography": "H3", "ids": cells})
    assert response.status_code == 200
    geojson = response.json()
    assert geojson["type"] == "FeatureCollection"
    assert sorted(f["id"] for f in geojson["features"]) == cells
    # BNG by default
    x, y = geojson["features"][0]["geometry"]["coordinates"][0][0]
    assert 400_000 < x < 460_000 and 400_000 < y < 460_000


def test_features_h3_latlon(client: TestClient) -> None:
    cells = _h3_cells(client, 8)
    response = client.post("/features", params={"latlon": True}, json={"geography": "H3", "ids": cells})
    assert response.status_code == 200
    lon, lat = response.json()["features"][0]["geometry"]["coordinates"][0][0]
    assert -3 < lon < 0 and 53 < lat < 55


def test_features_census(client: TestClient) -> None:
    response = client.post("/features", json={"geography": "OA21", "ids": ["E00000001", "E00000002"]})
    assert response.status_code == 200
    features = response.json()["features"]
    assert sorted(f["id"] for f in features) == ["E00000001", "E00000002"]


def test_features_unsupported_geography(client: TestClient) -> None:
    response = client.post("/features", json={"geography": "GRID", "ids": ["1"]})
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


# --- h3 grid ---


def test_h3_grid(client: TestClient) -> None:
    response = client.get("/h3/8", params={"force": FORCE})
    assert response.status_code == 200
    features = response.json()["features"]
    # ~136 res-8 cells (~0.737km²) cover the 100km² force square
    assert 100 < len(features) < 200
    assert all(len(f["id"]) == 15 for f in features)
    x, _ = features[0]["geometry"]["coordinates"][0][0]
    assert x > 400_000


def test_h3_grid_latlon(client: TestClient) -> None:
    response = client.get("/h3/8", params={"force": FORCE, "latlon": True})
    assert response.status_code == 200
    lon, lat = response.json()["features"][0]["geometry"]["coordinates"][0][0]
    assert -3 < lon < 0 and 53 < lat < 55


def test_h3_grid_invalid_resolution(client: TestClient) -> None:
    response = client.get("/h3/16", params={"force": FORCE})
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


# --- geog_lookup ---


def test_geog_lookup_h3(client: TestClient) -> None:
    cells = _h3_cells(client, 8)
    response = client.post("/geog_lookup", json={"geography": "H3", "resolution": 8, "ids": cells, "target": "OA21"})
    assert response.status_code == 200
    mapping = response.json()
    # one crime cell in each OA
    assert sorted(mapping) == cells
    assert sorted(mapping.values()) == ["E00000001", "E00000002"]

    response = client.post("/geog_lookup", json={"geography": "H3", "resolution": 8, "ids": cells, "target": "MSOA21"})
    assert response.status_code == 200
    assert response.json() == dict.fromkeys(cells, "E02000001")


def test_geog_lookup_h3_unknown_ids_omitted(client: TestClient) -> None:
    cells = _h3_cells(client, 9)
    response = client.post(
        "/geog_lookup",
        json={"geography": "H3", "resolution": 9, "ids": [*cells, "ffffffffffffff"], "target": "LSOA21"},
    )
    assert response.status_code == 200
    assert response.json() == dict.fromkeys(cells, "E01000001")


def test_geog_lookup_census(client: TestClient) -> None:
    # non-H3 to non-H3 mappings go via the res-8 H3 cells
    response = client.post(
        "/geog_lookup", json={"geography": "OA21", "ids": ["E00000001", "E00000002"], "target": "MSOA21"}
    )
    assert response.status_code == 200
    assert response.json() == {"E00000001": "E02000001", "E00000002": "E02000001"}

    response = client.post(
        "/geog_lookup", json={"geography": "OA21", "ids": ["E00000001", "E00000002"], "target": "PFA23"}
    )
    assert response.status_code == 200
    assert response.json() == {"E00000001": PFA_CODE, "E00000002": PFA_CODE}

    response = client.post("/geog_lookup", json={"geography": "LSOA21", "ids": ["E01000001"], "target": "LAD24"})
    assert response.status_code == 200
    assert response.json() == {"E01000001": LAD_CODE}


def test_geog_lookup_validation(client: TestClient) -> None:
    for bad in (
        {"geography": "H3", "resolution": 8, "target": "H3"},  # H3 target
        {"geography": "OA21", "target": "OA21"},  # source == target
        {"geography": "H3", "target": "OA21"},  # missing resolution
        {"geography": "OA21", "resolution": 8, "target": "LSOA21"},  # resolution without H3
        {"geography": "H3", "resolution": 7, "target": "OA21"},  # not precomputed
        {"geography": "GRID", "target": "OA21"},  # unsupported source
    ):
        response = client.post("/geog_lookup", json={"ids": ["dummy"]} | bad)
        assert response.status_code == 400, bad
        assert response.json()["error"] == "ValueError"


# --- census_geographies ---


def test_census_geographies(client: TestClient) -> None:
    response = client.get("/census_geographies", params={"geography": "OA21", "force": FORCE})
    assert response.status_code == 200
    assert sorted(f["id"] for f in response.json()["features"]) == ["E00000001", "E00000002"]

    response = client.get("/census_geographies", params={"geography": "MSOA21", "force": FORCE})
    assert response.status_code == 200
    assert [f["id"] for f in response.json()["features"]] == ["E02000001"]


# --- census_counts (deprecated) ---


def test_census_counts(client: TestClient) -> None:
    response = client.get("/census_counts", params={"geography": "OA21", "force": FORCE, "category": "Burglary"})
    assert response.status_code == 200
    assert _counts(response.json()) == {
        ("E00000001", "2025-05"): 2,
        ("E00000001", "2025-04"): 1,
        ("E00000002", "2025-05"): 1,
    }


def test_census_counts_unsupported_geography(client: TestClient) -> None:
    response = client.get("/census_counts", params={"geography": "LSOA21", "force": FORCE, "category": "Burglary"})
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


# --- crime_counts (POST) ---


def test_crime_counts_post_h3(client: TestClient) -> None:
    response = client.post(
        "/crime_counts",
        json={
            "geography": "H3",
            "resolution": 8,
            "force": FORCE,
            "categories": ["Burglary"],
            "months": ["2025-05"],
        },
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert sorted(row["count"] for row in rows) == [1, 2]
    assert all(row["crime_type"] == "Burglary" and row["month"] == "2025-05" for row in rows)


def test_crime_counts_post_census(client: TestClient) -> None:
    response = client.post(
        "/crime_counts",
        json={
            "geography": "OA21",
            "force": FORCE,
            "categories": ["Burglary", "Robbery"],
            "months": ["2025-05"],
        },
    )
    assert response.status_code == 200
    assert _counts(response.json()) == {
        ("E00000001", "Burglary", "2025-05"): 2,
        ("E00000002", "Burglary", "2025-05"): 1,
        ("E00000002", "Robbery", "2025-05"): 1,
    }


def test_crime_counts_post_validation(client: TestClient) -> None:
    base = {"force": FORCE, "categories": ["Burglary"], "months": ["2025-05"]}
    for bad in (
        {"geography": "GRID"},
        {"geography": "HEX"},
        {"geography": "STREET"},
        {"geography": "H3"},  # missing resolution
        {"geography": "OA21", "resolution": 8},  # resolution without H3
        {"geography": "H3", "resolution": 7},  # not precomputed
    ):
        response = client.post("/crime_counts", json=base | bad)
        assert response.status_code == 400, bad
        assert response.json()["error"] == "ValueError"


def test_crime_counts_post_invalid_month(client: TestClient) -> None:
    response = client.post(
        "/crime_counts",
        json={"geography": "OA21", "force": FORCE, "categories": ["Burglary"], "months": ["2025-13"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "RequestValidationError"


# --- crime_counts (GET) ---


def test_crime_counts_get_lookback(client: TestClient) -> None:
    response = client.get(
        "/crime_counts",
        params={
            "geography": "H3",
            "resolution": 8,
            "force": FORCE,
            "category": "Burglary",
            "month": "2025-05",
            "lookback": 2,
        },
    )
    assert response.status_code == 200
    assert sum(row["count"] for row in response.json()) == 4  # 3 in May + 1 in April


def test_crime_counts_get_single_month(client: TestClient) -> None:
    response = client.get(
        "/crime_counts",
        params={"geography": "OA21", "force": FORCE, "category": "Robbery", "month": "2025-05"},
    )
    assert response.status_code == 200
    assert _counts(response.json()) == {("E00000002", "Robbery", "2025-05"): 1}


def test_crime_counts_get_validation(client: TestClient) -> None:
    base = {"geography": "H3", "resolution": 8, "force": FORCE, "category": "Burglary", "month": "2025-05"}
    for bad in ({"month": "May 2025"}, {"lookback": 0}, {"lookback": 37}, {"force": "Gotham City"}):
        response = client.get("/crime_counts", params=base | bad)
        assert response.status_code == 400, bad
        assert response.json()["error"] == "RequestValidationError"


# --- hotspots ---


def test_hotspots_national(client: TestClient) -> None:
    response = client.get("/hotspots", params={"category": "Burglary", "month": "2025-05", "n_hotspots": 1})
    assert response.status_code == 200
    features = response.json()["features"]
    assert len(features) == 1
    assert features[0]["properties"]["count"] == 2


def test_hotspots_force(client: TestClient) -> None:
    response = client.get(
        "/hotspots", params={"force": FORCE, "category": "Burglary", "month": "2025-05", "n_hotspots": 10}
    )
    assert response.status_code == 200
    features = response.json()["features"]
    assert len(features) == 2  # only two cells have burglaries in May
    assert sum(f["properties"]["count"] for f in features) == 3


def test_hotspots_lookback(client: TestClient) -> None:
    response = client.get(
        "/hotspots",
        params={"category": "Burglary", "month": "2025-05", "lookback": 2, "n_hotspots": 1},
    )
    assert response.status_code == 200
    assert response.json()["features"][0]["properties"]["count"] == 3


def test_hotspots_invalid_resolution(client: TestClient) -> None:
    response = client.get(
        "/hotspots",
        params={"category": "Burglary", "month": "2025-05", "n_hotspots": 1, "resolution": 7},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


def test_hotspots_invalid_month(client: TestClient) -> None:
    response = client.get("/hotspots", params={"category": "Burglary", "month": "2025-5", "n_hotspots": 1})
    assert response.status_code == 400
    assert response.json()["error"] == "RequestValidationError"
