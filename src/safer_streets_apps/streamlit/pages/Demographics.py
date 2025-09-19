from typing import get_args

import geopandas as gpd
import pydeck as pdk
import streamlit as st
from itrx import Itr
from safer_streets_core.spatial import get_demographics, get_force_boundary, load_population_data, map_to_spatial_unit
from safer_streets_core.utils import (
    CATEGORIES,
    Force,
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


@st.cache_data
def cache_population(force: Force) -> gpd.GeoDataFrame:
    return load_population_data(force)


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

ethnicities = {
    "Asian": "Asian, Asian British or Asian Welsh",
    "Black": "Black, Black British, Black Welsh, Caribbean or African",
    "Mixed": "Mixed or Multiple ethnic groups",
    "Other": "Other ethnic group",
    "White": "White",
}

st.set_page_config(page_title="Crime Demographics", page_icon="🌍")

st.logo("./assets/safer-streets-small.png", size="large")


def main() -> None:
    st.title("Crime Demographics Explorer")

    with st.expander("Help"):
        st.markdown(f"""
The app overlays police.uk public crime data, showing counts of crimes of a given type over the last 3 years, and demographic data from the 2021 UK census.

The interactive map displays the "hot" crime areas extruded and shaded from yellow to red. The colour of each area is given by the count of crimes in
that area, with red being the highest.

#### Data sources

- Demographic data is from the 2021 UK census - specifically **TS021 - Population by ethnic group (OA)** - remapped to the
                    selected spatial unit
- Crime data is from police.uk archive from {all_months[0]} to {all_months[-1]}

#### Ethnicity explanations:

- {"\n - ".join(f"{k}: {v}" for k, v in ethnicities.items())}
""")

    st.sidebar.header("Demographics")

    force = st.sidebar.selectbox("Force Area", get_args(Force), index=43)  # default="West Yorkshire"
    category = st.sidebar.selectbox("Crime type", CATEGORIES, index=1)
    spatial_unit_name = st.sidebar.selectbox("Spatial Unit", geographies.keys(), index=0)
    elevation_scale = st.sidebar.slider(
        "Elevation scale", min_value=10, max_value=100, value=50, step=10, help="Adjust the vertical scale"
    )

    try:
        raw_data, boundary = cache_crime_data(force, category)

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
        population = cache_population(force).to_crs(epsg=4326)
        # and aggregate - annualised rate
        counts = get_monthly_crime_counts(crime_data, features).sum(axis=1) / 3

        ethnicity = (
            get_demographics(population, features)
            .groupby(["spatial_unit", "C2021_ETH_20_NAME"], observed=True)["count"]
            .sum()
            .unstack(level="C2021_ETH_20_NAME")
        ).rename(columns={v: k for k, v in ethnicities.items()})

        total = ethnicity.sum(axis=1)
        ethnicity = ethnicity.div(total, axis=0)
        ethnicity["population"] = total
        ethnicity = features[["geometry"]].join(ethnicity.join(counts.rename("count"))).fillna(0)
        ethnicity = ethnicity[ethnicity["count"] > 0]

        # round/reformat for tooltips
        for eth in ethnicities:
            ethnicity[eth] = ethnicity[eth].map(lambda x: f"{x:.1%}")
        ethnicity["count"] = ethnicity["count"].round(1)
        ethnicity["name"] = ethnicity.index

        st.toast("Data loaded")

        view_state = pdk.ViewState(
            latitude=centroid_lat,
            longitude=centroid_lon,
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
            get_line_color=[64, 64, 192, 255],
        )

        layers = [
            boundary_layer,
            pdk.Layer(
                "GeoJsonLayer",
                ethnicity.__geo_interface__,
                opacity=1.0,
                stroked=True,
                filled=True,
                extruded=True,
                wireframe=True,
                get_fill_color="""[
                        255,
                        255 - properties.count / 3,
                        0,
                        160
                    ]""",
                get_line_color=[255, 255, 255, 255],
                pickable=True,
                elevation_scale=elevation_scale,
                get_elevation="properties.count",
            ),
        ]

        tooltip = {
            "html": "Feature {name}: population {population}, crime rate {count}/year<br/>"
            "Ethnicity breakdown (2021 census):<br/>" + "<br/>".join(f"{eth}: {{{eth}}}" for eth in ethnicities)
        }

        st.pydeck_chart(
            pdk.Deck(map_style=st.context.theme.type, layers=layers, initial_view_state=view_state, tooltip=tooltip),
            height=960,
        )

        with st.expander("Table View"):
            st.dataframe(ethnicity.drop("geometry", axis=1))

    except Exception as e:
        st.error(e)


if __name__ == "__main__":
    main()
