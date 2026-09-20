from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierStats:
    source_id: int
    source_name: str
    last_price: float
    last_date: str
    min_price: float
    max_price: float
    avg_price: float
    first_date: str


def compute_avg(prices: list[float]) -> float:
    if not prices:
        return 0.0
    return sum(prices) / len(prices)


def diff_percent(own_price: float, supplier_price: float) -> float | None:
    if own_price is None or own_price <= 0:
        return None
    return (supplier_price - own_price) / own_price * 100.0


def pick_cheapest(stats: list[SupplierStats]) -> SupplierStats | None:
    if not stats:
        return None
    return min(stats, key=lambda s: s.last_price)


def as_matrix(rows: list[dict]) -> list[dict]:
    """Строки «product × supplier» из последних цен превращает в файл-структуру
    для сводной таблицы (по одному значению на пару)."""
    return rows


def to_stats(rows: list[dict]) -> list[SupplierStats]:
    return [
        SupplierStats(
            source_id=r["source_id"],
            source_name=r["source_name"],
            last_price=float(r["last_price"]),
            last_date=r["last_date"],
            min_price=float(r["min_price"]),
            max_price=float(r["max_price"]),
            avg_price=float(r["avg_price"]),
            first_date=r["first_date"],
        )
        for r in rows
    ]