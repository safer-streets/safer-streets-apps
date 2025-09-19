from time import sleep
from typing import get_args

import geopandas as gpd
import pandas as pd
import pydeck as pdk
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import get_force_boundary, map_to_spatial_unit
from safer_streets_core.utils import (
    CATEGORIES,
    Force,
    calc_gini,
    get_monthly_crime_counts,
    latest_month,
    load_crime_data,
    monthgen,
)

# streamlit seems to break load_dotenv
LATEST_DATE = latest_month()
all_months = Itr(monthgen(LATEST_DATE, backwards=True)).take(36).rev().collect()


@st.cache_data
def cache_crime_data(force: Force, category: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    force_boundary = get_force_boundary(force)
    data = load_crime_data(force, all_months, filters={"Crime type": category}, keep_lonlat=True)
    return data, force_boundary


st.set_page_config(layout="wide", page_title="Safer Streets", page_icon="👮")

geographies = {
    "Middle layer Super Output Areas (census)": ("MSOA21", {}),
    "Lower layer Super Output Areas (census)": ("LSOA21", {}),
    "Output Areas (census)": ("OA21", {}),
    "800m grid": ("GRID", {"size": 800.0}),
    "400m grid": ("GRID", {"size": 400.0}),
    "200m grid": ("GRID", {"size": 200.0}),
    "500m hexes": ("HEX", {"size": 500.0}),
    "250m hexes": ("HEX", {"size": 250.0}),
    "125m hexes": ("HEX", {"size": 125.0}),
}

st.set_page_config(page_title="Crime Consistency", page_icon="🌍")

st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Consistency Explorer")

    with st.expander("Help"):
        st.markdown("""
The app uses police.uk public crime data to determine, how consistently areas feature in the top areas for crimes of a
given type, over the last 3 years.

The interactive map displays the "hot" areas (in yellow). The height of each area is given by the number of months it features in
the set of areas capture the most crime. The height of the features can be adjusted if necessary using the "Elevation scale" slider in
the sidebar.

Below the map graphs are displayed of the percentage of crimes captured, percentage of spatial units, and the Gini index
over time. To view the animation, in the sidebar:

1. Select the Force Area, Crime Type and Spatial Unit.
2. Adjust the the land area you want to cover, and the number of months to look back.
3. Hit "Run..."
""")

    st.sidebar.header("Consistency")

    force = st.sidebar.selectbox("Force Area", get_args(Force), index=43)  # default="West Yorkshire"

    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)

    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)

    try:
        raw_data, boundary = cache_crime_data(force, category)

        area_threshold = st.sidebar.slider(
            "Coverage (km²)",
            10.0,
            boundary.area.sum() / 1_000_000,
            step=10.0,
            value=100.0,
            help="Focus on the smallest land area that captures the most crime",
        )

        lookback_window = st.sidebar.slider(
            "Lookback window",
            min_value=1,
            max_value=12,
            value=1,
            step=1,
            help="Number of months of data to aggregate at each step",
        )

        elevation_scale = 0 #st.sidebar.slider(
        #     "Elevation scale", min_value=100, max_value=300, value=150, step=10, help="Adjust the vertical scale"
        # )

        # map crimes to features
        centroid_lat, centroid_lon = raw_data.lat.mean(), raw_data.lon.mean()
        spatial_unit, spatial_unit_params = geographies[spatial_unit_name]
        crime_data, features = map_to_spatial_unit(raw_data, boundary, spatial_unit, **spatial_unit_params)
        # compute area in sensible units before changing crs!
        features["area_km2"] = features.area / 1_000_000
        # now convert everything to Webmercator
        crime_data = crime_data.to_crs(epsg=4326)
        boundary = boundary.to_crs(epsg=4326)
        features = features.to_crs(epsg=4326)
        # and aggregate
        counts = get_monthly_crime_counts(crime_data, features)
        num_features = len(features)
        area_threshold = features.area_km2.sum() - area_threshold
        stats = pd.DataFrame(columns=["Gini", "Percent Captured"])

        st.toast("Data loaded")

        view_state = pdk.ViewState(
            latitude=centroid_lat,
            longitude=centroid_lon,
            zoom=9,
            pitch=30,
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

        def render(month: str, area: float, rankings: gpd.GeoDataFrame) -> None:
            period = f"{counts.columns[0]} to {month} ({lookback_window} month average)"

            title.markdown(f"""
                ### {period}:
                {area:.1f}km² of land area contains {stats.iloc[-1]["Percent Captured"]:.1f}% of {category}

                {stats.iloc[-1]["Features Included"]:.1f}% of {spatial_unit_name}

                **Gini Coefficient = {stats.iloc[-1]["Gini"] / 100:.2f}**
                """)

            graph.line_chart(stats, x_label="Month")

            layers = [
                boundary_layer,
                pdk.Layer(
                    "GeoJsonLayer",
                    rankings.__geo_interface__,
                    opacity=1.0,
                    stroked=True,
                    filled=True,
                    extruded=True,
                    wireframe=True,
                    # count is in the range 0-36, so this gives a yellow to red heatmap
                    get_fill_color="""[
                        255,
                        255 - properties.count * 7,
                        0,
                        160
                    ]""",  # Yellow to red heatmap based on count
                    get_line_color=[255, 255, 255, 255],
                    pickable=True,
                    elevation_scale=elevation_scale,
                    # get_elevation="properties.count",
                ),
            ]

            tooltip = {
                "html": "Feature {name} appears {count} times in 36 months<br/>",
            }

            map_placeholder.pydeck_chart(
                pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip), height=720
            )

        def render_dynamic() -> None:
            running_total = features[["geometry"]].copy()
            running_total["count"] = 0
            running_total["name"] = running_total.index

            for c in counts.T.rolling(lookback_window):
                if len(c) < lookback_window:
                    continue

                period = f"{c.index[0]} to {c.index[-1]}" if lookback_window > 1 else c.index[0]
                mean_count = c.mean().rename("mean")

                mean_count_by_density = pd.concat(
                    [mean_count, features.area_km2, (mean_count / features.area_km2).rename("density")], axis=1
                ).sort_values(by="density")

                hit = (mean_count_by_density.area_km2.cumsum() > area_threshold) & (mean_count_by_density["mean"] > 0)
                running_total["count"] += hit

                stats.loc[period, "Percent Captured"] = (mean_count * hit).sum() / mean_count.sum() * 100
                stats.loc[period, "Features Included"] = hit.sum() / num_features * 100
                stats.loc[period, "Gini"] = calc_gini(mean_count)[0] * 100

                render(
                    c.index[-1], (mean_count_by_density.area_km2 * hit).sum(), running_total[running_total["count"] > 0]
                )
                sleep(0.1)

        run_button = st.sidebar.empty()

        title = st.empty()
        map_placeholder = st.empty()
        map_placeholder.pydeck_chart(pdk.Deck(map_style=st.context.theme.type, layers=[boundary_layer], initial_view_state=view_state), height=720)

        graph = st.empty()

        run = run_button.button("Run...")

        if run:
            render_dynamic()
    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
