import json
from typing import cast, get_args

import pydeck as pdk
import streamlit as st
from safer_streets_core.spatial import get_force_boundary
from safer_streets_core.utils import CATEGORIES, Force, data_dir

st.set_page_config(layout="wide", page_title="Crime Hotspots", page_icon="👮")
st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Hotspot Explorer")

    st.warning(
        "##### :construction: This page is still in development and currently uses precomputed data, "
        "which is only available for the "
        "Metropolitan Police and West Yorkshire Forces at 1% coverage."
    )

    st.markdown("## Highlighting how temporal scales affect crime hotspots")

    with st.expander("More info..."):
        st.markdown(
            """
The app uses [police.uk](https://data.police.uk) public crime data to determine, given a target total land area, the
maximum number of crimes of a given type that can be captured within that area, in the chosen time window in the last
3 years.

A rolling window of crime counts (1, 3, or 12 months) are aggregated onto a hex grid (200m side, ~350m height) and
ranked. The top 1% of cells are recorded for each window. The window is updated and the ranks recomputed to cover the
3 years of data.

(As an example of the iteration: a 2-month update on a 3-month window will step from {Jan, Feb, Mar} to {Mar, Apr, May})

Finally, spatial units are then ranked by the number of times each spatial unit features in the top 1% over the 3
year period.

The interactive map displays the hotspot hex cells shaded in proportion to their frequency as a hotspot.

"""
        )

    st.sidebar.header("Hotspots")

    force = cast(Force, st.sidebar.selectbox("Force Area", get_args(Force), index=43))  # default="West Yorkshire"

    crime_type = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    _coverage = st.sidebar.slider(
        "Area Coverage (%)",
        1.0,
        1.01,
        step=1.0,
        value=1.0,
        help="Percentage of hex cells to consider as hotspots",
    )

    window = st.sidebar.select_slider(
        "Lookback window (months)",
        options=[1, 3, 12],
        value=1,
        help="Number of months of data to aggregate when determining hotspots",
    )

    update = st.sidebar.select_slider(
        "Update interval (months)",
        options=[1, 2, 3],
        value=1,
        help="Number of months to step when determining window",
    )

    try:
        with st.spinner("Loading data..."):
            filename = f"hotspots/{force}-hotspots-{crime_type}-{window}m-rolling-{update}m-update.geojson"
            # hotspots = gpd.read_file(data_dir() / filename)
            with (data_dir() / filename).open() as fd:
                hotspot_data = json.load(fd)

            boundary = get_force_boundary(force).to_crs(epsg=4326)

        centroid = boundary.union_all().centroid

        # render map
        view_state = pdk.ViewState(
            latitude=centroid.y,
            longitude=centroid.x,
            zoom=9,
            pitch=22,
        )

        boundary_layer = pdk.Layer(
            "GeoJsonLayer",
            boundary.__geo_interface__,
            opacity=0.5,
            stroked=True,
            filled=False,
            extruded=False,
            pickable=True,
            line_width_min_pixels=2,
            get_line_color=[64, 64, 192, 255],
        )

        hotspot_layer = (
            pdk.Layer(
                "GeoJsonLayer",
                hotspot_data,
                stroked=True,
                filled=True,
                wireframe=True,
                get_fill_color="[192, 0, 0, properties['Frequency (%)']]",  # [255, 0, 0, 160],
                # get_fill_color="[201, 241, 0, properties['Frequency (%)']]",  # [255, 0, 0, 160],
                get_line_color=[0x80, 0x80, 0x80, 0x80],
                # get_line_color=[0xC9, 0xF1, 0x00, 0xA0],
                line_width_min_pixels=2,
                pickable=True,
            ),
        )

        tooltip = {
            "html": f"Cell {{id}} appears {{Frequency (%)}}% of the time<br/>({window} month lookback, {update} month update)"
        }

        st.pydeck_chart(
            pdk.Deck(
                map_style=st.context.theme.type,
                layers=[boundary_layer, hotspot_layer],
                initial_view_state=view_state,
                tooltip=tooltip,
            ),
            height=960,
        )

    except Exception as e:
        st.error(e)
        raise


if __name__ == "__main__":
    main()
