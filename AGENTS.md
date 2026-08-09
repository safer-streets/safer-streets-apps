# Agent Guidelines for `safer-streets-apps`

This file instructs AI agents acting as developer, reviewer, and QA for this repository.

## Project Overview

`safer-streets-apps` holds the public-facing applications of the Safer Streets project, built on top of
the [`safer-streets-core`](../safer-streets-core) library (an editable path dependency). There are two
deliverables:

1. **Crime GeoData API** — a FastAPI service serving public-domain crime (geo)data.
2. **Crime Explorer** — a multipage Streamlit app that consumes the API and visualises crime data.

The apps no longer build or read a local DuckDB database. Instead the API opens a **read-only, in-memory
DuckDB** connection (`duckdb_connector(azure=True)`) and reads every table directly from the project's
public Azure blob storage as parquet, e.g. `read_parquet('az://phase2/extract/police_force_areas.parquet')`.
`az://phase2/index.parquet` is the catalogue of available tables (surfaced by the `/diagnostics` endpoint).

The Azure data model is **H3-based**: crime counts and geography lookups are precomputed per H3 cell at
resolutions 8, 9 and 10 (`transform/crime_counts_h3_{8,9,10}.parquet`, `transform/h3_{res}_geogs.parquet`,
`transform/h3_{res}_{geog}cd_lookup.parquet`), keyed on a `spatial_id` column (the H3 cell id as a lowercase
hex string). Census boundaries (`extract/{output_areas_2021,lsoa_2021,msoa_2021}.parquet`) and the raw
`extract/crime_data.parquet` are also read directly; census-level aggregates are derived from these.

### Layout

```
src/safer_streets_apps/
  fastapi/              # the Crime GeoData API
    app.py              # FastAPI app, lifespan (opens the Azure DuckDB connection), route handlers
    sql.py              # SQL templates run against the Azure parquet files
    impl.py             # query implementations shared by GET/POST routes (features, crime_counts)
    models.py           # pydantic request models (CrimeCountsRequest, FeaturesRequest, ...)
    auth.py             # x-api-key header auth (sha256 hash check)
    startup.py          # legacy local-DB init (init_db) — unused since the Azure migration
  streamlit/            # the Crime Explorer app
    Main.py             # entry point
    common.py           # shared helpers; talks to the API via safer_streets_core.api_helpers
    pages/              # multipage app pages (Overview, Hotspots, Metrics, Consistency, ...)
  demo.py               # standalone demo
Dockerfile              # Streamlit app image  (ghcr.io/safer-streets/safer-streets-apps)
Dockerfile.api          # API image            (ghcr.io/safer-streets/safer-streets-api)
build-images.sh         # builds a safer-streets-core wheel locally and both images
```

## Toolchain

| Tool | Command |
| ---- | ------- |
| Package manager | `uv` |
| Linter / formatter | `ruff` (`uv run ruff check`, `uv run ruff format`) |
| Type checker | `ty` (`uv run ty check`) |
| Tests | `uv run pytest` (no repo tests yet) |
| Install deps | `uv sync --dev` |

Pre-commit hooks run `uv-lock`, `ruff-check --fix`, `ruff-format`, and `ty` on commit
(see [.pre-commit-config.yaml](.pre-commit-config.yaml)).

## Quality Gates

Before any change is considered complete:

```sh
uv run ruff check          # zero lint errors
uv run ruff format --check # zero formatting issues
uv run ty check            # zero type errors
uv run pytest              # if/when tests exist
```

There is no CI workflow and no coverage gate in this repo (unlike `safer-streets-core`) — the pre-commit
hooks are the gate. Run them locally before committing.

## Developer Rules

- **Data comes from Azure parquet, not local files.** All tables are read via `read_parquet('az://…')`
  through the read-only DuckDB connection. Do not reintroduce local-database build steps or `data_dir()`
  file reads into the API. New tables must exist in the Azure store and (ideally) the `index.parquet`
  catalogue.
- **Coordinate reference systems matter.** Geometry is British National Grid (EPSG:27700) by convention;
  `crime_data.geom` is lon/lat (OGC:CRS84 / EPSG:4326) and H3 cell boundaries come out in EPSG:4326.
  Be explicit about CRS on every spatial operation (`ST_Transform(..., always_xy := true)`); mixing CRSs
  silently produces wrong results.
- **Force names.** API `Force` values are the short PFA names; use `fix_force_name(force)` before matching
  against `police_force_areas.pfa23nm` (four forces differ: Metropolitan, Devon and Cornwall, City of
  London, Dyfed Powys).
- **H3 resolutions are 8, 9, 10.** Only these are precomputed on Azure — validate the `resolution`
  parameter against `sql.H3_RESOLUTIONS` before formatting it into a query.
- **SQL string formatting.** `sql.py` templates use `.format()` for values that cannot be bound as query
  parameters (parquet paths, resolutions, boundary table names). Only substitute values you have validated
  (fixed enums / ints), and bind everything else with DuckDB parameters (`?` / `$name`).
- **Type annotations required.** All function signatures need full annotations; `ty` will catch missing or
  incorrect ones.
- **Line length is 120** (configured in [pyproject.toml](pyproject.toml); `E501` is ignored).
- **No comments explaining what the code does.** Only add a comment when the *why* is non-obvious (hidden
  constraint, workaround, subtle invariant).
- **Runtime vs dev dependencies.** Runtime deps go in `[project.dependencies]`; tooling (`ruff`, `ty`,
  `pytest`, `pre-commit`) goes in `[dependency-groups.dev]`.

## Reviewer Checklist

1. **CRS correctness** — every geometry operation explicit and consistent about its coordinate reference
   system; degrees vs metres not mixed.
2. **Correctness** — reason about edge cases: empty results, missing force/geography, invalid geometries,
   an unsupported `geography`/`resolution`.
3. **Azure-backed data** — no accidental reintroduction of local-file reads; parquet paths correct;
   parameters bound rather than string-interpolated where possible.
4. **Types** — precise annotations; avoid `Any` unless unavoidable.
5. **Ruff rules** — active rules are `B, C, E, F, I, SIM` (`E501` ignored; `D103` ignored in `src/test`).
   Don't suppress a rule without justification.
6. **Docs** — if endpoints, run instructions, container build, or env vars change, update
   [README.md](README.md).

## QA Rules

- Run the full gate suite (`ruff check`, `ruff format --check`, `ty check`) before declaring a task done.
- The API needs the Azure connection and an API key (`x-api-key` header) — exercising endpoints requires
  network access to blob storage; note when a change can only be verified against live data.
- The minimum supported Python is **3.13** (`requires-python = ">=3.13"`).

## Running Locally

```sh
# API (dev, port 5000)
uv run fastapi dev src/safer_streets_apps/fastapi/app.py --port 5000

# Streamlit app (talks to the API via SAFER_STREETS_API_URL / SAFER_STREETS_API_KEY)
uv run streamlit run src/safer_streets_apps/streamlit/Main.py
```

See [README.md](README.md) for container build/run/push and API-key generation.

## Branch and Release Policy

- Work on feature branches and open pull requests targeting `main` — do not commit directly to `main`.
- Container images are published to GHCR (`ghcr.io/safer-streets/safer-streets-api` and
  `…/safer-streets-apps`); see [README.md](README.md) and [build-images.sh](build-images.sh).
- Version lives in [pyproject.toml](pyproject.toml) (`version = "x.y.z"`).
