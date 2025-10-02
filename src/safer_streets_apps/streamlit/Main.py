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
interactively

Feedback and suggestions for improvements or new features will be gratefully received
""")

st.sidebar.markdown("Select one of the apps above.")

st.markdown(
    """
    ## Resources

    - [blog](https://safer-streets.github.io)
    - [github](https://github.com/safer-streets)
"""
)
