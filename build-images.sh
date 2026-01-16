#!/bin/bash

ver=$(python -c "import safer_streets_core;print(safer_streets_core.__version__)")

cd ../safer-streets-core
uv build
cp dist/safer_streets_core-$ver-py3-none-any.whl ../safer-streets-apps
cd ../safer-streets-apps

docker build -f Dockerfile.api -t ghcr.io/safer-streets/safer-streets-api .
docker build -t ghcr.io/safer-streets/safer-streets-apps .
