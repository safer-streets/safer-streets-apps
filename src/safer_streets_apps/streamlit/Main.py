import streamlit as st

st.set_page_config(
    page_title="Welcome",
    page_icon="👋",
)


st.logo("./assets/safer-streets-small.png", size="large")
st.image("./assets/safer-streets-small.png")

st.markdown("# Welcome to Safer Streets @ Leeds!")

st.markdown("""
This site hosts some interactive demo apps allowing practitioners and policymakers to explore historical crime patterns
interactively.

Please note that to keep costs down the apps are hosted on a budget service plan, and may respond slowly. We can
temporarily adjust the service plan if necessary.

Feedback and suggestions for improvements or new features will be gratefully received, and can be submitted
[here](https://github.com/safer-streets/safer-streets-apps/issues).
""")

st.sidebar.markdown("Select one of the apps above.")

st.markdown(
    """
    ## Resources

    - [blog](https://safer-streets.github.io)
    - [API documentation](https://uol-a011-prd-uks-wkld025-asp1-api1-acdkeudzafe8dtc9.uksouth-01.azurewebsites.net/docs)
    - [github](https://github.com/safer-streets)
"""
)
