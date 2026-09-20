"""Streamlit entry point for the Turnover Analysis & Alert Tool."""
import streamlit as st

st.set_page_config(page_title="Turnover Analysis & Alert Tool", layout="wide")

st.title("Turnover Analysis & Alert Tool")
st.write(
    "Use the pages in the sidebar to manage clients, import Tally data, "
    "enter figures manually, configure thresholds, and view reports and alerts."
)
