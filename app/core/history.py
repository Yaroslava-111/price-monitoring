from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def series_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if "price_date" in df.columns:
        df["price_date"] = pd.to_datetime(df["price_date"])
    return df.sort_values(["price_date", "source_name"]).reset_index(drop=True)


def build_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if df.empty or "price" not in df.columns:
        return fig
    for source_name, group in df.groupby("source_name", sort=True):
        fig.add_trace(
            go.Scatter(
                x=group["price_date"],
                y=group["price"],
                mode="lines+markers",
                name=str(source_name),
                hovertemplate=(
                    "<b>%{y:,.2f} ₽</b><br>"
                    "Дата: %{x|%d.%m.%Y}<br>"
                    f"Источник: {source_name}"
                    "<extra></extra>"
                ),
            )
        )
    fig.update_layout(
        title="Динамика цены",
        xaxis_title="Дата",
        yaxis_title="Цена, ₽",
        legend_title="Источник",
        hovermode="x unified",
        margin=dict(l=10, r=10, t=60, b=10),
        yaxis_tickformat=",.0f",
    )
    return fig


def history_csv(df: pd.DataFrame) -> str:
    buf = []
    for col in ("price", "price_date", "source_name"):
        if col not in df.columns:
            df = df.copy()
            break
    view = df.copy()
    if "price" in view.columns and "source_name" not in view.columns:
        pass
    view = view[["price", "price_date", "source_name"]].rename(
        columns={
            "price": "Цена, ₽",
            "price_date": "Дата цены",
            "source_name": "Источник",
        }
    )
    return view.to_csv(index=False)