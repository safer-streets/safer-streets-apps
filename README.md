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

If you want to point it to another data dir (NB the docker image hard-codes this value)

```sh
SAFER_STREETS_DATA_DIR=<insert-here> streamlit run src/safer_streets_apps/streamlit/Main.py
```


## Container

### Prerequisites

Build a safer-streets-core wheel and copy it to this folder (use `uv build`).

This currently relies on temporary manual workarounds:

- Build a wheel for safer-streets-core and copy it to here
- Make copies of a subset of the input datasets and place them in `./data-local`

### Build

NB this includes the `data-local` folder (West Yorkshire only) and the Dockerfile hard-codes this location

```sh
docker build -t ghcr.io/safer-streets/safer-streets-apps .
```

### Run

This command runs the app using data stored within it:

```sh
docker run -p8501:8501 ghcr.io/safer-streets/safer-streets-apps
```

### Push

This requires a PAT.

```sh
docker push ghcr.io/safer-streets/safer-streets-apps
```

## Note

The existing hosted demo app (including data) is on a dedicated branch (streamlit) in the safer-streets-eda repo. **Do
not delete (yet)!**

## TODO

- [X] Multipage app - v2 of explorer (using area), another page measuring persistence of areas
- [X] Demographics demo
- [ ] Docker-compose implementation with full datasets on the cloud or in a volume...
- [ ] ...then remove redundant demo branch
- [ ] improve UX

## References

- [Multipage app tutorial](https://docs.streamlit.io/get-started/tutorials/create-a-multipage-app)

