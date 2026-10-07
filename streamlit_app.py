"""Trading Desk web app (Streamlit). Research and paper trading only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from webapp import pages  # noqa: E402

st.set_page_config(page_title="Trading Desk", page_icon="📈", layout="wide")
st.markdown("<style>.block-container{padding-top:2rem;max-width:1300px}</style>", unsafe_allow_html=True)
nav = st.navigation([
    st.Page(pages.today_page, title="Today", icon="📋", default=True),
    st.Page(pages.portfolio_page, title="Portfolio", icon="💼", url_path="portfolio"),
    st.Page(pages.scan_page, title="Market scan", icon="🛰️", url_path="scan"),
    st.Page(pages.options_page, title="Options lab", icon="🧪", url_path="options"),
    st.Page(pages.sizer_page, title="Position sizer", icon="📏", url_path="sizer"),
])
with st.sidebar:
    st.caption("Paper trading only · not financial advice")
nav.run()
