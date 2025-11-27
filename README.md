# safer-streets-apps

## Install

From an activated venv,

```sh
uv sync --active --dev
```

## Run locally

### App

From the activated venv, picking up the default data dir:

```sh
streamlit run src/safer_streets_apps/streamlit/Main.py
```

If you want to point it to another data dir (NB the docker image hard-codes this value)

```sh
SAFER_STREETS_DATA_DIR=<insert-here> streamlit run src/safer_streets_apps/streamlit/Main.py
```

### API

From the activated venv, run a local dev API (using port 5000 to avoid conflicting with streamlit on 8000), picking up
the default data dir:

```sh
fastapi dev src/safer_streets_apps/fastapi/app.py --port 5000
```

Use the [swagger page](http://localhost:5000/docs) to test the endpoints or use `curl`, e.g.

```sh
curl 'http://localhost:5000/pfa_area?force=West%20Yorkshire' \
  -H 'accept: application/json' -H 'x-api-key: '$SAFER_STREETS_API_KEY
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

### Run locally

The API requires an API key (header parameter `x-api-key`) for authentication.

Run the app/api containers, mounting your local data directory:

```sh
docker run -p5000:5000 --mount type=bind,source=../data,target=/mnt/data \
  -e SAFER_STREETS_DATA_DIR=/mnt/data \
  ghcr.io/safer-streets/safer-streets-api

docker run -p8000:8000 --mount type=bind,source=../data,target=/mnt/data \
  -e SAFER_STREETS_DATA_DIR=/mnt/data \
  -e SAFER_STREETS_API_URL=http://localhost:5000 \
  -e SAFER_STREETS_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  ghcr.io/safer-streets/safer-streets-apps
```

(On Azure, mount storage (Settings → Configuration → Path Mappings) and set the environment variable appropriately.
Also ensure the API env vars are set.)

### Push

This requires a PAT.

```sh
docker push ghcr.io/safer-streets/safer-streets-api
docker push ghcr.io/safer-streets/safer-streets-apps
```

## Note

- The existing hosted demo app (including data) is on a dedicated branch (streamlit) in the safer-streets-eda repo. **Do
not delete (yet)!**

- To (re)generate an API key and its hash:

  ```sh
  openssl rand -hex 16 | tee >(xxd -r -p | sha256sum)
  ```

## TODO

- [X] Multipage app - v2 of explorer (using area), another page measuring persistence of areas
- [X] Demographics demo
- [ ] Docker-compose implementation with
- [X] full datasets on the cloud or in a volume...
- [ ] ...then remove redundant demo branch
- [ ] improve UX

## References

- [Multipage app tutorial](https://docs.streamlit.io/get-started/tutorials/create-a-multipage-app)

