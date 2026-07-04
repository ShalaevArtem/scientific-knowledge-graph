"""Юнит-тесты лексического резолвера сущностей и числовых ограничений (без Neo4j и LLM)."""

from __future__ import annotations

from skg.query.search import _contains_words, _interval, _norm_unit, match_entities

ENTITIES = [
    {"key": "M:никель", "label": "Material", "name": "никель", "name_en": "nickel", "synonyms": []},
    {"key": "M:медь", "label": "Material", "name": "медь", "name_en": "copper", "synonyms": []},
    {"key": "P:электроэкстракция", "label": "Process", "name": "электроэкстракция",
     "name_en": "electrowinning", "synonyms": ["электролиз нерастворимыми анодами"]},
    {"key": "E:печьвзвешеннойплавки", "label": "Equipment", "name": "печь взвешенной плавки",
     "name_en": "flash smelting furnace", "synonyms": ["ПВП"]},
    {"key": "P:выщелачивание", "label": "Process", "name": "выщелачивание",
     "name_en": "leaching", "synonyms": []},
]


def keys(question: str) -> set[str]:
    return {e["key"] for e in match_entities(question, ENTITIES)}


def test_inflected_forms_match():
    q = "Какие решения циркуляции католита при электроэкстракции никеля описаны?"
    assert {"P:электроэкстракция", "M:никель"} <= keys(q)


def test_multiword_and_abbreviation():
    assert "E:печьвзвешеннойплавки" in keys("подача шихты в печи взвешенной плавки")
    assert "E:печьвзвешеннойплавки" in keys("режимы ПВП на никелевом заводе")


def test_english_name_matches():
    assert "P:электроэкстракция" in keys("nickel electrowinning cell design")
    assert "M:никель" in keys("nickel electrowinning cell design")


def test_no_false_positive_on_prefix_words():
    # «медленные» не должно распознаваться как материал «медь»
    assert "M:медь" not in keys("медленные решения замедляют работу")


def test_no_entities_in_unrelated_question():
    assert keys("какая погода в Норильске в декабре") == set()


def test_property_words_match_inflected():
    assert _contains_words("концентрация сульфатов в воде", ["сульфаты"])
    assert _contains_words("сухой остаток", ["сухой", "остаток"])
    assert not _contains_words("химический состав", ["сульфаты"])


def test_norm_unit_equivalences():
    assert _norm_unit("мг/дм³") == _norm_unit("мг/л")
    assert _norm_unit("МГ / ДМ3") == _norm_unit("мг/л")
    assert _norm_unit("г/т") != _norm_unit("мг/л")
    assert _norm_unit(None) == ""


def test_interval_semantics():
    assert _interval("<=", 300, None) == (float("-inf"), 300)
    assert _interval(">=", 100, None) == (100, float("inf"))
    assert _interval("range", 200, 300) == (200, 300)
    lo, hi = _interval("=", 1000, None)  # ±10 %
    assert lo == 900 and hi == 1100
