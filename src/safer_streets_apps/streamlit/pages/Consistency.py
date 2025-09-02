import streamlit as st


def main() -> None:
    st.title("Crime Consistency Explorer")

    with st.expander("Help"):
        st.markdown(
            """
The app uses police.uk public crime data to determine, how consistently spatial units feature in "top N" rankings each
month over a number of years.
"""
        )

    st.sidebar.header("Consistency")

    st.markdown("TODO")


if __name__ == "__main__":
    main()
