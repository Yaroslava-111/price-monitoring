from __future__ import annotations

import streamlit as st


def render() -> None:
    st.header("Каталог товаров")
    st.caption("Управление собственными товарами и ценами (SKU, название, категория, своя цена).")
    st.info("Раздел будет реализован на этапе 1.")