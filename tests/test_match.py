import pytest

from app.core import match
from app.core.match import MatchResult
from app.core.normalize import normalize_name, normalize_sku

PRODUCTS = [
    {"id": 1, "sku": "COF-100", "name": "Кофе Арабика 250г зерно", "is_active": True},
    {"id": 2, "sku": "COF-200", "name": "Кофе Робуста 500г молотый", "is_active": True},
    {"id": 3, "sku": "TEA-050", "name": "Чай зелёный листовой 100г", "is_active": True},
]


def test_normalize_sku():
    assert normalize_sku("  cof-100  ") == "COF-100"
    assert normalize_sku("Ё-123") == "Е-123"
    assert normalize_sku("много   пробелов   внутри") == "МНОГО ПРОБЕЛОВ ВНУТРИ"


def test_normalize_name():
    assert normalize_name("  Кофе   Арабика ") == "кофе арабика"
    assert normalize_name("Чай зелёный листовой") == "чай зеленый листовой"
    assert normalize_name("Товар №1") == "товар no1"


def test_exact_sku_matches_auto():
    result = match.match(
        external_sku="cof-100",
        external_name="",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.matched is True
    assert result.method == match.METHOD_EXACT
    assert result.status == match.STATUS_AUTO
    assert result.product_id == 1
    assert result.similarity == 100.0


def test_unknown_sku_falls_to_fuzzy():
    result = match.match(
        external_sku="NOPE",
        external_name="Кофе Арабика 250г",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.matched is True
    assert result.method == match.METHOD_FUZZY


def test_high_similarity_single_candidate_is_auto():
    result = match.match(
        external_sku="",
        external_name="Кофе Арабика 250г зерно",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.status == match.STATUS_AUTO
    assert result.product_id == 1
    assert result.similarity >= 90


def test_grayzone_requires_pending():
    result = match.match(
        external_sku="",
        external_name="Кофе Арабика зерно премиум",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.status == match.STATUS_PENDING
    assert 70 <= result.similarity < 90
    assert result.matched is True
    assert result.candidates is not None


def test_low_similarity_no_match():
    result = match.match(
        external_sku="",
        external_name="Шоколад молочный 90г",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.matched is False
    assert result.product_id is None


def test_rejected_pair_not_offered():
    rejected = {(1, normalize_name("Кофе Арабика 250г зерно"))}
    result = match.match(
        external_sku="",
        external_name="Кофе Арабика 250г зерно",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
        rejected_pairs=rejected,
    )
    assert result.matched is False


def test_ambiguous_two_auto_candidates_is_pending():
    products = [
        {"id": 1, "sku": "A", "name": "Молоко 1л свежее", "is_active": True},
        {"id": 2, "sku": "B", "name": "Молоко свежее 1л", "is_active": True},
    ]
    result = match.match(
        external_sku="",
        external_name="Молоко 1л свежее",
        products=products,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.status == match.STATUS_PENDING


def test_inactive_products_not_matched():
    products = [
        {"id": 1, "sku": "COF-100", "name": "Кофе Арабика 250г", "is_active": False},
    ]
    result = match.match(
        external_sku="cof-100",
        external_name="Кофе Арабика",
        products=products,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.matched is False


def test_empty_external_name_no_match():
    result = match.match(
        external_sku="",
        external_name="   ",
        products=PRODUCTS,
        auto_threshold=90,
        manual_threshold=70,
    )
    assert result.matched is False