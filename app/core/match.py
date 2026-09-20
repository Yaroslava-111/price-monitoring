from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from app.core.normalize import normalize_name, normalize_sku

METHOD_EXACT = "exact"
METHOD_FUZZY = "fuzzy"
METHOD_MANUAL = "manual"

STATUS_AUTO = "auto"
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"


@dataclass
class Candidate:
    product_id: int
    sku: str
    name: str
    similarity: float


@dataclass
class MatchResult:
    matched: bool
    product_id: int | None = None
    method: str | None = None
    status: str | None = None
    similarity: float = 0.0
    candidates: list[Candidate] | None = None


def find_exact_sku(external_sku: str, products: list[dict]) -> int | None:
    norm_external = normalize_sku(external_sku)
    if not norm_external:
        return None
    for product in products:
        if normalize_sku(product["sku"]) == norm_external and product.get(
            "is_active", False
        ):
            return product["id"]
    return None


def fuzzy_match(
    external_name: str,
    products: list[dict],
    auto_threshold: float,
    manual_threshold: float,
    rejected_pairs: set[tuple[int, str]] | None = None,
) -> MatchResult:
    rejected_pairs = rejected_pairs or set()
    norm_name = normalize_name(external_name)
    if not norm_name:
        return MatchResult(matched=False)

    candidates: list[Candidate] = []
    for product in products:
        if not product.get("is_active", False):
            continue
        if (product["id"], norm_name) in rejected_pairs:
            continue
        similarity = float(
            fuzz.token_set_ratio(norm_name, normalize_name(product["name"]))
        )
        if similarity >= manual_threshold:
            candidates.append(
                Candidate(
                    product_id=product["id"],
                    sku=product["sku"],
                    name=product["name"],
                    similarity=similarity,
                )
            )

    if not candidates:
        return MatchResult(matched=False)

    candidates.sort(key=lambda c: c.similarity, reverse=True)
    best = candidates[0]

    if best.similarity >= auto_threshold:
        auto_candidates = [c for c in candidates if c.similarity >= auto_threshold]
        if len(auto_candidates) == 1:
            return MatchResult(
                matched=True,
                product_id=best.product_id,
                method=METHOD_FUZZY,
                status=STATUS_AUTO,
                similarity=best.similarity,
                candidates=candidates,
            )
        return MatchResult(
            matched=True,
            product_id=best.product_id,
            method=METHOD_FUZZY,
            status=STATUS_PENDING,
            similarity=best.similarity,
            candidates=candidates,
        )

    return MatchResult(
        matched=True,
        product_id=best.product_id,
        method=METHOD_FUZZY,
        status=STATUS_PENDING,
        similarity=best.similarity,
        candidates=candidates,
    )


def match(
    external_sku: str,
    external_name: str,
    products: list[dict],
    auto_threshold: float,
    manual_threshold: float,
    rejected_pairs: set[tuple[int, str]] | None = None,
) -> MatchResult:
    exact_id = find_exact_sku(external_sku, products)
    if exact_id is not None:
        return MatchResult(
            matched=True,
            product_id=exact_id,
            method=METHOD_EXACT,
            status=STATUS_AUTO,
            similarity=100.0,
        )
    return fuzzy_match(
        external_name,
        products,
        auto_threshold,
        manual_threshold,
        rejected_pairs,
    )