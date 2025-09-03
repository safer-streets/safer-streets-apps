# safer-streets-apps

## Install

From an activated venv,

```sh
uv sync --active --dev
```

## Run locally

From the activated venv, picking up the default data dir:

```sh
streamlit run src/safer_streets_apps/streamlit/Main.py
```

If you want to point it to another data dir (e.g. for testing docker)

```sh
SAFER_STREETS_DATA_DIR=<insert-here> streamlit run src/safer_streets_apps/streamlit/Main.py
```


## Container

### Prerequisites

Build a safer-streets-core wheel and copy it to this folder (use `uv build`).

### Build

NB this includes the `data-local` folder (West Yorkshire only)

```sh
docker build -t safer-streets-apps .
```

### Run

This command runs the app using data stored within it:

```sh
docker run -e SAFER_STREETS_DATA_DIR=/app/data-local -p8501:8501 safer-streets-apps
```

## Note

The existing hosted demo app (including data) is on a dedicated branch (streamlit) in the safer-streets-eda repo. **Do
not delete (yet)!**

## TODO

- [ ] Multipage app - v2 of explorer (using area), another page measuring persistence of areas
- [ ] Docker-compose implementation with full datasets on the cloud or in a volume...
- [ ] ...then remove redundant demo branch

## Referemces

- [Multipage app tutorial](https://docs.streamlit.io/get-started/tutorials/create-a-multipage-app)

