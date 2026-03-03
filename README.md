# safer-streets-apps

- Crime GeoData API
- Crime Explorer App

## Install

```sh
uv sync --dev
```

## Run locally

### App

Picking up the default data dir:

```sh
uv run streamlit run src/safer_streets_apps/streamlit/Main.py
```

If you want to point it to another data dir (NB the docker image hard-codes this value)

```sh
SAFER_STREETS_DATA_DIR=<insert-here> uv run streamlit run src/safer_streets_apps/streamlit/Main.py
```

### API

Run a local dev API (using port 5000 to avoid conflicting with streamlit on 8000), picking up
the default data dir:

```sh
uv run fastapi dev src/safer_streets_apps/fastapi/app.py --port 5000
```

Use the [API doc page](http://localhost:5000/docs) to test the endpoints or use `curl`, e.g.

```sh
curl 'http://localhost:5000/pfa_geodata?force=West%20Yorkshire' \
  -H 'accept: application/json' -H 'x-api-key: '$SAFER_STREETS_API_KEY
```

## Containers

TODO docker compose?

### Build

Build the containers with this script:

```sh
uv run ./build-images.sh
```

NB this script builds a safer-streets-core wheel from the local copy (using `uv build`) and copies it to this folder so that it can be installed into each image.


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
  --network="host" \
  -e SAFER_STREETS_DATA_DIR=/mnt/data \
  -e SAFER_STREETS_API_URL=http://localhost:5000 \
  -e SAFER_STREETS_API_KEY=$SAFER_STREETS_API_KEY \
  ghcr.io/safer-streets/safer-streets-apps
```

NB `--network="host"` is required when running locally - localhost would otherwise refer to the safer-streets-apps
container

(On Azure, mount storage (Settings → Configuration → Path Mappings) and set the environment variable appropriately.
Also ensure the API env vars are set.)

### Push

This requires a PAT.

```sh
docker push ghcr.io/safer-streets/safer-streets-api
docker push ghcr.io/safer-streets/safer-streets-apps
```

## Note

- To (re)generate an API key and its hash:

  ```sh
  openssl rand -hex 16 | tee >(xxd -r -p | sha256sum)
  ```

## TODO

- [X] Multipage app - v2 of explorer (using area), another page measuring persistence of areas
- [X] Demographics demo
- [ ] Docker-compose implementation with
- [X] full datasets on the cloud or in a volume...
- [X] ...then remove redundant demo branch
- [X] improve UX

## References

- [Multipage app tutorial](https://docs.streamlit.io/get-started/tutorials/create-a-multipage-app)

