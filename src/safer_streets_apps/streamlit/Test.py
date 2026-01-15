from time import sleep
from typing import get_args

import pydeck as pdk
import streamlit as st
from safer_streets_core.spatial import get_census_boundaries, get_force_boundary
from safer_streets_core.utils import (
    DEFAULT_FORCE,
    Force,
)

st.set_page_config(layout="wide", page_title="Safer Streets", page_icon="👮")


def main() -> None:
    st.title("Test")

    st.markdown("# Test")

    force = st.sidebar.selectbox("Force Area", get_args(Force), index=DEFAULT_FORCE)  # default="West Yorkshire"

    boundary = get_force_boundary(force)

    features = get_census_boundaries("MSOA21", overlapping=boundary)
    features["x"] = 0

    boundary = boundary.to_crs(epsg=4326)
    features = features.to_crs(epsg=4326)
    centroid = boundary.iloc[0].geometry.centroid

    with st.expander("Data"):
        dfview = st.dataframe(features.drop(columns="geometry"))  # noqa: F841

    st.toast("Loaded")

    view_state = pdk.ViewState(
        latitude=centroid.y,
        longitude=centroid.x,
        zoom=9,
        pitch=45,
    )

    boundary_layer = pdk.Layer(
        "GeoJsonLayer",
        boundary.__geo_interface__,
        opacity=0.5,
        stroked=True,
        filled=False,
        extruded=False,
        line_width_min_pixels=3,
        get_line_color=[192, 64, 64, 255],
    )

    layers = [boundary_layer]

    deck = pdk.Deck(layers=layers, initial_view_state=view_state)

    map = st.pydeck_chart(deck, height=720)

    st.fragment()

    def render_data() -> None:
        go = st.checkbox("Run")
        while True:
            if go:
                features.loc[features.sample(10).index, "x"] += 1

                layer = pdk.Layer(
                    "GeoJsonLayer",
                    features[features.x > 0].__geo_interface__,
                    opacity=1.0,
                    stroked=True,
                    filled=True,
                    extruded=True,
                    wireframe=True,
                    get_fill_color=[0x00, 0x39, 0xF5, 0x50],  # [255, 0, 0, 160],
                    get_line_color=[255, 255, 255, 255],
                    pickable=True,
                    elevation_scale=100,
                    get_elevation="properties.x",
                )

                map.pydeck_chart(
                    pdk.Deck(
                        map_style=st.context.theme.type, layers=[boundary_layer, layer], initial_view_state=view_state
                    ),
                    height=720,
                )

            sleep(0.1)

    render_data()


if __name__ == "__main__":
    main()
