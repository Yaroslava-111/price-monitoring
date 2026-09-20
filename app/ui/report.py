from __future__ import annotations

import streamlit as st


def render() -> None:
    st.header("Отчёт: конкуренты")
    st.caption("Отклонения цен конкурентов выше порога N%.")
    st.info("Раздел будет реализован на этапе 6.")