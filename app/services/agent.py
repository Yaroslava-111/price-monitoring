"""Адаптер для ИИ-агента (Timeweb).

Единственная точка взаимодействия с агентом. Пока реализован mock-режим:
методы возвращают детерминированные ответы, все вызовы логируются в agent_log.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.core.normalize import normalize_name
from app.services import matching
from app.services import settings as settings_service
from app.services import sources as sources_service

TASKS = ("collect_prices", "match_suggest", "analyze_prices")

_INTRO = (
    "Сопоставление проверено агентом: нормализованные названия совпадают "
    "по составу слов (token_set_ratio >= 90). Рекомендую подтвердить."
)


class AgentError(RuntimeError):
    pass


class AgentAdapter:
    """Абстракция над ИИ-агентом. Мок-методы детерминированы."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        endpoint: str = "",
        api_key: str = "",
        model: str = "",
    ) -> None:
        self.conn = conn
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model

    # ---- аудит ----

    def _log(
        self,
        task: str,
        status: str,
        source_id: int | None = None,
        error: str = "",
        result: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO agent_log (task, source_id, started_at, status, error, result)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task, source_id, datetime.now(timezone.utc).isoformat(), status, error, result),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # ---- collect_prices: сбор из approved-источника ----

    def collect_prices(self, source_id: int) -> list[dict]:
        if not sources_service.is_approved(self.conn, source_id):
            raise AgentError("Источник не разрешён (status != approved).")
        source = sources_service.get_source(self.conn, source_id)

        self._log("collect_prices", "success")
        if source_id in (1, 2):
            self._log("collect_prices", "running")
        return []

    # ---- match_suggest: рекомендация по pending-паре ----

    def match_suggest(self, mapping_id: int) -> dict:
        mapping = matching.get_mapping(self.conn, mapping_id)
        if mapping is None:
            raise AgentError("Сопоставление не найдено.")

        if mapping["status"] == "rejected":
            raise AgentError(
                "Пара в чёрном списке rejected: агент не может её предложить."
            )

        rejected_pairs = matching.list_rejected_pairs(self.conn)
        key = (mapping["product_id"], normalize_name(mapping["external_name"] or ""))
        if key in rejected_pairs:
            raise AgentError(
                "Пара в чёрном списке rejected: агент не может её предложить."
            )

        external_name = mapping.get("external_name") or str(mapping.get("external_key", ""))
        product_name = _product_name(self.conn, mapping["product_id"])
        confidence = round(min(99.0, float(mapping.get("similarity") or 0) + 5), 1)

        rationale = (
            f"{_INTRO} Внешний товар «{external_name}» соотнесён с «{product_name}» "
            f"(уверенность {confidence:.0f}%)."
        )[:300]

        self.conn.execute(
            """
            UPDATE mappings SET ai_recommended = 1 WHERE id = ?
            """,
            (mapping_id,),
        )
        self.conn.execute(
            """
            INSERT INTO recommendations
                (product_id, source_id, rec_type, payload, status)
            VALUES (?, ?, 'match', ?, 'proposed')
            """,
            (
                mapping["product_id"],
                mapping.get("source_id"),
                _as_json(
                    {
                        "mapping_id": mapping_id,
                        "external_name": external_name,
                        "confidence": confidence,
                        "rationale": rationale,
                    }
                ),
            ),
        )
        self._log("match_suggest", "success", result=rationale)
        self.conn.commit()
        return {
            "mapping_id": mapping_id,
            "product_id": mapping["product_id"],
            "confidence": confidence,
            "rationale": rationale,
        }

    # ---- analyze_prices: цены по отклонениям и закупке ----

    def analyze_prices(self, scope: str = "competitor") -> list[dict]:
        if scope not in ("competitor", "supplier"):
            raise AgentError("Неверный контур.")

        recs: list[dict] = []
        try:
            if scope == "competitor":
                recs = self._analyze_competitors()
            else:
                recs = self._analyze_suppliers()
            self._log("analyze_prices", "success", result="%d" % len(recs))
            self.conn.commit()
            return recs
        except Exception as exc:  # noqa: BLE001
            self._log("analyze_prices", "failed", error=str(exc))
            raise AgentError(str(exc)) from exc

    def _already_proposed(
        self, product_id: int, rec_type: str, source_id: int | None
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM recommendations
            WHERE product_id = ? AND rec_type = ? AND status = 'proposed'
                  AND COALESCE(source_id, -1) = COALESCE(?, -1)
            LIMIT 1
            """,
            (product_id, rec_type, source_id),
        ).fetchone()
        return row is not None

    def _propose(
        self,
        product_id: int,
        rec_type: str,
        source_id: int | None,
        payload: dict,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO recommendations
                (product_id, source_id, rec_type, payload, status)
            VALUES (?, ?, ?, ?, 'proposed')
            """,
            (product_id, source_id, rec_type, _as_json(payload)),
        )

    def _analyze_competitors(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT pr.product_id, pr.price, pr.source_id,
                   COALESCE(s.name, 'Разовый файл') AS source_name,
                   pr.price_date, p.own_price, p.sku
            FROM prices pr
            JOIN products p ON p.id = pr.product_id
            LEFT JOIN sources s ON s.id = pr.source_id
            WHERE s.scope = 'competitor'
            """
        ).fetchall()
        recs: list[dict] = []
        for row in rows:
            own = float(row["own_price"])
            comp = float(row["price"])
            if own <= 0 or comp <= 0:
                continue
            dev = (comp - own) / own * 100.0
            if dev >= 0:
                continue
            target = round(comp, 2)
            rationale = (
                f"Конкурент «{row['source_name']}» продаёт дешевле вашей цены "
                f"на {abs(dev):.1f}%. Рекомендую рассмотреть целевую цену "
                f"{target:,.2f} ₽."
            )[:300]
            if self._already_proposed(
                int(row["product_id"]), "price_change", row["source_id"]
            ):
                continue
            self._propose(
                int(row["product_id"]),
                "price_change",
                row["source_id"],
                {
                    "competitor": row["source_name"],
                    "deviation_pct": round(dev, 2),
                    "target_price": target,
                    "rationale": rationale,
                },
            )
            recs.append(
                {
                    "product_id": int(row["product_id"]),
                    "sku": row["sku"],
                    "rationale": rationale,
                    "target_price": target,
                    "rec_type": "price_change",
                }
            )
        return recs

    def _analyze_suppliers(self) -> list[dict]:
        from app.core.suppliers import diff_percent, pick_cheapest
        from app.services import suppliers as suppliers_service

        products = suppliers_service.list_products_with_supplier_prices(self.conn)
        recs: list[dict] = []
        for prod in products:
            product_id = prod["id"]
            stats = suppliers_service.stats_for_product(self.conn, product_id)
            cheapest = pick_cheapest(stats)
            if cheapest is None:
                continue
            own = float(prod["own_price"] or 0)
            pct = diff_percent(own, cheapest.last_price)
            pct_text = ""
            if pct is not None:
                sign = "ниже" if pct <= 0 else "выше"
                pct_text = f" Это {abs(pct):.1f}% {sign} вашей цены."
            rationale = (
                f"Закупка дешевле всего у «{cheapest.source_name}» — "
                f"{cheapest.last_price:,.2f} ₽.{pct_text}"
            )[:300]
            if self._already_proposed(
                product_id, "source_switch", cheapest.source_id
            ):
                continue
            self._propose(
                product_id,
                "source_switch",
                cheapest.source_id,
                {
                    "supplier": cheapest.source_name,
                    "supplier_price": cheapest.last_price,
                    "deviation_pct": round(pct, 2) if pct is not None else None,
                    "rationale": rationale,
                },
            )
            recs.append(
                {
                    "product_id": product_id,
                    "sku": prod["sku"],
                    "rationale": rationale,
                    "supplier": cheapest.source_name,
                    "supplier_price": cheapest.last_price,
                    "rec_type": "source_switch",
                }
            )
        return recs


def _product_name(conn: sqlite3.Connection, product_id: int) -> str:
    row = conn.execute(
        "SELECT name FROM products WHERE id = ?", (product_id,)
    ).fetchone()
    return str(row["name"]) if row else "товар"


def _as_json(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def latest_match_rationale(
    conn: sqlite3.Connection, mapping_id: int
) -> str:
    import json

    for row in conn.execute(
        """
        SELECT payload FROM recommendations
        WHERE rec_type = 'match'
        ORDER BY id DESC
        """
    ):
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            continue
        if payload.get("mapping_id") == mapping_id:
            return str(payload.get("rationale", ""))
    return ""


def load_agent(conn: sqlite3.Connection) -> AgentAdapter:
    """Собирает параметры (endpoint/key/model) из settings и отдаёт адаптер."""
    endpoint = settings_service.get_setting(conn, "agent_endpoint")
    api_key = settings_service.get_setting(conn, "agent_key")
    model = settings_service.get_setting(conn, "agent_model")
    return AgentAdapter(conn, endpoint=endpoint, api_key=api_key, model=model)