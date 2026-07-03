from __future__ import annotations

from skg.query import answer as answer_mod
from skg.query.search import _fulltext_escape, _text_signature


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.system_prompt = ""

    def complete_json(self, system: str, user: str, max_tokens: int = 500) -> dict:
        self.system_prompt = system
        return self.payload


def test_parse_question_uses_current_year_in_prompt():
    llm = FakeLLM({"query": "никель электроэкстракция", "year_from": 2024})

    parsed = answer_mod.parse_question(llm, "за последние 2 года", current_year=2030)

    assert parsed["query"] == "никель электроэкстракция"
    assert parsed["year_from"] == 2024
    assert "2030 года" in llm.system_prompt


def test_answer_question_explicit_filters_override_llm_filters(monkeypatch):
    captured = {}

    def fake_search(session, query, **kwargs):
        captured.update({"query": query, **kwargs})
        return []

    monkeypatch.setattr(answer_mod, "hybrid_search", fake_search)
    monkeypatch.setattr(answer_mod, "attach_doc_entities", lambda session, hits: None)
    llm = FakeLLM({"query": "из LLM", "category": "статья", "language": "en"})

    result = answer_mod.answer_question(
        object(),
        "исходный вопрос",
        llm=llm,
        category="доклад",
        language="ru",
        year_from=2020,
        year_to=2025,
    )

    assert result["filters"]["category"] == "доклад"
    assert result["filters"]["language"] == "ru"
    assert captured["category"] == "доклад"
    assert captured["language"] == "ru"
    assert captured["year_from"] == 2020
    assert captured["year_to"] == 2025


def test_fulltext_escape_removes_lucene_operators():
    escaped = _fulltext_escape('Ni+Co AND "католит":30')

    assert "+" not in escaped
    assert '"' not in escaped
    assert ":" not in escaped
    assert "Ni" in escaped
    assert "католит" in escaped


def test_text_signature_normalizes_whitespace_and_case():
    assert _text_signature("  Никель\nкатолит  ") == _text_signature("никель католит")
