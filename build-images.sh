#!/bin/bash

# you may need to invoke with uv: e.g. uv run ./build-images.sh

# TODO a better approach would be to use a base image with safer-streets-core already installed
# (or possibly a wheel from a CI build artifact?)

ver=$(python -c "import safer_streets_core;print(safer_streets_core.__version__)")

cd ../safer-streets-core
uv build
cp dist/safer_streets_core-$ver-py3-none-any.whl ../safer-streets-apps
cd ../safer-streets-apps

docker build -f Dockerfile.api -t ghcr.io/safer-streets/safer-streets-api .
docker build -t ghcr.io/safer-streets/safer-streets-apps .
