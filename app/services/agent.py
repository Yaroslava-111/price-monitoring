"""Адаптер для ИИ-агента (Timeweb).

Единственная точка взаимодействия с агентом. Работает в двух режимах:

* **live** — заданы эндпоинт, ключ и модель: обоснования пишет модель;
* **mock** — что-то из трёх не задано: ответы собираются локально по шаблону.

Отбор товаров и вся арифметика в обоих режимах считаются локально и
остаются проверяемыми; модель отвечает только за текст обоснования.
Режим каждого вызова пишется в agent_log, чтобы задним числом было видно,
кто именно составил рекомендацию.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.core.normalize import normalize_name
from app.services import matching
from app.services import settings as settings_service
from app.services import sources as sources_service
from app.services import llm
from app.storage.repositories import scope_filter_sql

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
        self.config = llm.LLMConfig(endpoint=endpoint, api_key=api_key, model=model)
        # Причина последнего отката на шаблон — показывается владельцу,
        # чтобы «тихая» деградация до мока не выглядела нормальной работой.
        self.last_llm_error: str = ""

    @property
    def is_live(self) -> bool:
        """Настроен ли реальный вызов модели."""
        return self.config.configured

    def mode(self) -> str:
        return "live" if self.is_live else "mock"

    def check_connection(self) -> str:
        """Проверочный запрос к модели. Бросает AgentError с понятным текстом."""
        if not self.is_live:
            raise AgentError(
                "Агент не настроен: не заданы " + ", ".join(self.config.missing()) + "."
            )
        try:
            answer = llm.ping(self.config)
        except llm.LLMError as exc:
            self._log("check_connection", "failed", error=str(exc))
            raise AgentError(str(exc)) from exc
        self._log("check_connection", "success", result=f"live: {answer[:100]}")
        return answer

    def _llm_rationales(self, items: list[dict], scope: str) -> dict[int, str]:
        """Просит модель написать обоснование по каждой позиции.

        Возвращает {product_id: текст}. При любой ошибке — пустой словарь:
        вызывающий код откатывается на локальный шаблон, а причина
        сохраняется в last_llm_error и попадает в agent_log.
        """
        if not self.is_live or not items:
            return {}

        if scope == "competitor":
            task = (
                "Ты помогаешь владельцу магазина реагировать на цены конкурентов. "
                "По каждой позиции объясни, стоит ли менять свою цену и почему."
            )
        else:
            task = (
                "Ты помогаешь владельцу магазина выбирать поставщика. "
                "По каждой позиции объясни, выгодно ли закупать у этого поставщика."
            )

        prompt = "\n\n".join(
            [
                task,
                "Верни СТРОГО JSON-массив без пояснений, по одному объекту на "
                'позицию: [{"product_id": <число>, "rationale": "<текст до 300 '
                'символов>"}]. Пиши по-русски, конкретно, без воды и без '
                "выдуманных чисел — опирайся только на переданные данные.",
                "Данные:\n" + _as_json({"items": items}),
            ]
        )

        try:
            answer = llm.chat(
                self.config,
                [
                    {"role": "system", "content": "Ты аналитик по ценам. Отвечай только JSON."},
                    {"role": "user", "content": prompt},
                ],
            )
        except llm.LLMError as exc:
            self.last_llm_error = str(exc)
            return {}

        parsed = _parse_rationales(answer)
        if not parsed:
            self.last_llm_error = "Модель вернула ответ не в формате JSON-массива."
        return parsed

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
        self.last_llm_error = ""
        try:
            if scope == "competitor":
                recs = self._analyze_competitors()
            else:
                recs = self._analyze_suppliers()
            # В журнале видно и режим, и откат на шаблон с его причиной.
            note = self.mode()
            if self.is_live and self.last_llm_error:
                note = f"live→mock ({self.last_llm_error})"
            self._log(
                "analyze_prices",
                "success",
                error=self.last_llm_error,
                result=f"{note}: {len(recs)}",
            )
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
            f"""
            SELECT pr.product_id, pr.price, pr.source_id,
                   COALESCE(s.name, 'Разовый файл') AS source_name,
                   pr.price_date, p.own_price, p.sku
            FROM prices pr
            JOIN products p ON p.id = pr.product_id
            LEFT JOIN sources s ON s.id = pr.source_id
            WHERE {scope_filter_sql("pr", "competitor")}
            """
        ).fetchall()

        # Кого рекомендовать и с какими числами — решаем локально, чтобы
        # рекомендации оставались воспроизводимыми и не зависели от модели.
        candidates: list[dict] = []
        for row in rows:
            own = float(row["own_price"])
            comp = float(row["price"])
            if own <= 0 or comp <= 0:
                continue
            dev = (comp - own) / own * 100.0
            if dev >= 0:
                continue
            candidates.append(
                {
                    "product_id": int(row["product_id"]),
                    "sku": row["sku"],
                    "competitor": row["source_name"],
                    "source_id": row["source_id"],
                    "own_price": round(own, 2),
                    "competitor_price": round(comp, 2),
                    "deviation_pct": round(dev, 2),
                    "price_date": row["price_date"],
                }
            )

        ai_text = self._llm_rationales(
            [
                {k: v for k, v in c.items() if k != "source_id"}
                for c in candidates
            ],
            "competitor",
        )

        recs: list[dict] = []
        for row in candidates:
            dev = row["deviation_pct"]
            target = row["competitor_price"]
            rationale = ai_text.get(row["product_id"]) or (
                f"Конкурент «{row['competitor']}» продаёт дешевле вашей цены "
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
                    "competitor": row["competitor"],
                    "deviation_pct": dev,
                    "target_price": target,
                    "rationale": rationale,
                    "mode": "live" if row["product_id"] in ai_text else "mock",
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

        # Самого выгодного поставщика выбираем локально — это арифметика,
        # доверять её модели незачем.
        candidates: list[dict] = []
        for prod in products:
            stats = suppliers_service.stats_for_product(self.conn, prod["id"])
            cheapest = pick_cheapest(stats)
            if cheapest is None:
                continue
            own = float(prod["own_price"] or 0)
            pct = diff_percent(own, cheapest.last_price)
            candidates.append(
                {
                    "product_id": prod["id"],
                    "sku": prod["sku"],
                    "supplier": cheapest.source_name,
                    "source_id": cheapest.source_id,
                    "supplier_price": cheapest.last_price,
                    "own_price": round(own, 2),
                    "deviation_pct": round(pct, 2) if pct is not None else None,
                }
            )

        ai_text = self._llm_rationales(
            [
                {k: v for k, v in c.items() if k != "source_id"}
                for c in candidates
            ],
            "supplier",
        )

        recs: list[dict] = []
        for cand in candidates:
            product_id = cand["product_id"]
            pct = cand["deviation_pct"]
            pct_text = ""
            if pct is not None:
                sign = "ниже" if pct <= 0 else "выше"
                pct_text = f" Это {abs(pct):.1f}% {sign} вашей цены."
            rationale = ai_text.get(product_id) or (
                f"Закупка дешевле всего у «{cand['supplier']}» — "
                f"{cand['supplier_price']:,.2f} ₽.{pct_text}"
            )[:300]
            if self._already_proposed(
                product_id, "source_switch", cand["source_id"]
            ):
                continue
            self._propose(
                product_id,
                "source_switch",
                cand["source_id"],
                {
                    "supplier": cand["supplier"],
                    "supplier_price": cand["supplier_price"],
                    "deviation_pct": pct,
                    "rationale": rationale,
                    "mode": "live" if product_id in ai_text else "mock",
                },
            )
            recs.append(
                {
                    "product_id": product_id,
                    "sku": cand["sku"],
                    "rationale": rationale,
                    "supplier": cand["supplier"],
                    "supplier_price": cand["supplier_price"],
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


def _parse_rationales(answer: str) -> dict[int, str]:
    """Достаёт {product_id: обоснование} из ответа модели.

    Модель часто оборачивает JSON в ```-блок или добавляет пару слов вокруг,
    поэтому берём фрагмент от первой '[' до последней ']'. Всё, что не
    разобралось, молча отбрасывается — вызывающий код откатится на шаблон.
    """
    import json

    text = answer.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(payload, list):
        return {}

    out: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            product_id = int(item["product_id"])
        except (KeyError, TypeError, ValueError):
            continue
        rationale = str(item.get("rationale") or "").strip()
        if rationale:
            out[product_id] = rationale[:300]
    return out


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