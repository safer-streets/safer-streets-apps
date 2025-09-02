# safer-streets-apps

## Install

From an activated venv,

```sh
uv sync --active --dev
```

## Run locally

From the activated venv,

```sh
streamlit run src/safer-streets-apps/demo.py
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

