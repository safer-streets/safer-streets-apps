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


## Containers

TODO docker compose...

### Prerequisites

Build a safer-streets-core wheel and copy it to this folder (use `uv build`).

This currently relies on temporary manual workarounds:

- Build a wheel for safer-streets-core and copy it to here
- Make copies of a subset of the input datasets and place them in `./data-local`

### Build

NB this includes the `data-local` folder (West Yorkshire only) and the Dockerfile hard-codes this location

```sh
docker build -f Dockerfile.api -t ghcr.io/safer-streets/safer-streets-api .
docker build -t ghcr.io/safer-streets/safer-streets-apps .
```

### Run

This command runs the app in the container, mounting your local data directory:

```sh
docker run -p5000:5000 --mount type=bind,source=../data,target=/mnt/data \
  -e SAFER_STREETS_DATA_DIR=/mnt/data \
  ghcr.io/safer-streets/safer-streets-api

docker run -p8000:8000 --mount type=bind,source=../data,target=/mnt/data \
  -e SAFER_STREETS_DATA_DIR=/mnt/data \
  -e SAFER_STREETS_API_URL=https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net \
  -e SAFER_STREETS_API_KEY=6e25d928f7ba7eba11654a216472ba87 \
  ghcr.io/safer-streets/safer-streets-apps
```

(On Azure, mount storage (Settings → Configuration → Path Mappings) and set the environment variable appropriately)

The API requires an API key (header parameter `x-api-key`) for authentication.

### Push

This requires a PAT.

```sh
docker push ghcr.io/safer-streets/safer-streets-api
docker push ghcr.io/safer-streets/safer-streets-apps
```

## Note

The existing hosted demo app (including data) is on a dedicated branch (streamlit) in the safer-streets-eda repo. **Do
not delete (yet)!**

## TODO

- [X] Multipage app - v2 of explorer (using area), another page measuring persistence of areas
- [X] Demographics demo
- [ ] Docker-compose implementation with
- [X] full datasets on the cloud or in a volume...
- [ ] ...then remove redundant demo branch
- [ ] improve UX

## References

- [Multipage app tutorial](https://docs.streamlit.io/get-started/tutorials/create-a-multipage-app)

