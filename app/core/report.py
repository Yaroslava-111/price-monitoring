from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Deviation:
    product_id: int
    sku: str
    product_name: str
    category: str
    own_price: float
    competitor_price: float
    deviation_pct: float
    delta_rub: float
    source_name: str
    source_id: int | None
    price_date: str


def clamp_threshold(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def compute_deviation(own_price: float, competitor_price: float) -> float | None:
    if own_price is None or own_price <= 0:
        return None
    return (competitor_price - own_price) / own_price * 100.0


def is_over_threshold(
    deviation_pct: float | None, threshold: float
) -> bool:
    if deviation_pct is None:
        return False
    return deviation_pct <= -clamp_threshold(threshold)


def to_deviation(row: dict) -> Deviation:
    own_price = float(row["own_price"])
    comp_price = float(row["price"])
    pct = compute_deviation(own_price, comp_price)
    return Deviation(
        product_id=row["product_id"],
        sku=row["sku"],
        product_name=row["product_name"],
        category=row["category"],
        own_price=own_price,
        competitor_price=comp_price,
        deviation_pct=pct if pct is not None else 0.0,
        delta_rub=comp_price - own_price,
        source_name=row["source_name"],
        source_id=row["source_id"],
        price_date=row["price_date"],
    )


def sort_by_deviation(rows: list[Deviation]) -> list[Deviation]:
    return sorted(rows, key=lambda d: d.deviation_pct)