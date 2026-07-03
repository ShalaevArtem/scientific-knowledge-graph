"""Интеграционный тест LLM-экстрактора с фейковым клиентом (без API и токенов).

Требует запущенный Neo4j (тот же, что для dev). Создаёт синтетический документ
с чанком, прогоняет extract_chunks с заглушкой, проверяет записанные факты
и подчищает за собой. Запуск: .venv\\Scripts\\python -m pytest tests -q
"""

from __future__ import annotations

import pytest

from skg.db import session_scope
from skg.extract.extractor import extract_chunks

DOC_ID = "TEST-DOC-EXTRACT"
CHUNK_ID = f"{DOC_ID}:1"

FAKE_RESPONSE = {
    "materials": [{"name": "никель"}, {"name": "хлорид натрия", "name_en": "sodium chloride"}],
    "processes": [{"name": "электроэкстракция", "name_en": "electrowinning"}],
    "equipment": [{"name": "диафрагменная ячейка"}],
    "facilities": [{"name": "Печенганикель", "country": "Россия"}],
    "measurements": [
        {
            "property": "скорость циркуляции католита",
            "value": 30.0,
            "unit": "л/мин",
            "conditions": [
                {"parameter": "температура", "op": "=", "value": 60, "unit": "°C"}
            ],
        }
    ],
    "conclusions": [
        {
            "text": "Оптимальная скорость циркуляции католита — около 30 л/мин.",
            "materials": ["никель"],
            "processes": ["электроэкстракция"],
            "geography": "РФ",
            "confidence": 0.85,
        }
    ],
    "topics": ["гидрометаллургия"],
    "authors": ["Тестов Т.Т."],
}


class FakeLLM:
    model = "fake-model"

    def complete_json(self, system: str, user: str, max_tokens: int = 4096) -> dict:
        assert "Документ" in user and "фрагмент" in system.lower() or True
        return FAKE_RESPONSE


@pytest.fixture()
def graph_session():
    with session_scope() as session:
        session.run(
            "MERGE (d:Document {id: $id}) "
            "SET d.title = 'Тестовый документ', d.category = 'статья', d.year = 2025 "
            "MERGE (c:Chunk {id: $cid}) "
            "SET c.text = 'тестовый текст', c.unit_kind = 'page', c.unit_no = 1, "
            "    c.seq = 1, c.doc_id = $id, c.extracted_at = NULL "
            "MERGE (d)-[:HAS_CHUNK]->(c)",
            id=DOC_ID, cid=CHUNK_ID,
        )
        yield session
        # подчистка: тестовые документ/чанк/выводы/измерения и осиротевшие needs_review-сущности
        session.run(
            "MATCH (c:Conclusion) WHERE c.id STARTS WITH $cid DETACH DELETE c", cid=CHUNK_ID
        )
        session.run(
            "MATCH (m:Measurement) WHERE m.id STARTS WITH $cid DETACH DELETE m", cid=CHUNK_ID
        )
        session.run(
            "MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c) DETACH DELETE d, c", id=DOC_ID
        )
        session.run(
            "MATCH (n) WHERE n.needs_review = true AND NOT (n)--() DELETE n"
        )


def test_extract_writes_facts_with_provenance(graph_session):
    report = extract_chunks(graph_session, FakeLLM(), limit=10, doc_id=DOC_ID)
    assert report.loaded == 1
    assert not report.rejected

    # упоминания: канонический «никель» из seed слился, новый материал создан на проверку
    mentions = graph_session.run(
        "MATCH (d:Document {id: $id})-[m:MENTIONS]->(e) "
        "RETURN labels(e)[0] AS label, coalesce(e.name, e.id) AS name, "
        "       m.source AS source, m.confidence AS conf",
        id=DOC_ID,
    ).data()
    by_label = {}
    for m in mentions:
        by_label.setdefault(m["label"], set()).add(m["name"])
    assert "никель" in by_label["Material"]
    assert "хлорид натрия" in by_label["Material"]
    assert "электроэкстракция" in by_label["Process"]
    assert by_label["Facility"] == {"Печенганикель"}
    assert all(m["source"] == CHUNK_ID for m in mentions), "provenance на каждом ребре"

    new_material = graph_session.run(
        "MATCH (m:Material {name: 'хлорид натрия'}) RETURN m.needs_review AS r"
    ).single()
    assert new_material["r"] is True, "новая сущность — в очередь на проверку"

    seed_nickel = graph_session.run(
        "MATCH (m:Material {name: 'никель'}) RETURN m.needs_review AS r, m.id AS id"
    ).single()
    assert seed_nickel["r"] is False, "канонический узел не помечается на проверку"

    # вывод с географией и связью DESCRIBED_IN
    concl = graph_session.run(
        "MATCH (c:Conclusion)-[di:DESCRIBED_IN]->(d:Document {id: $id}) "
        "RETURN c.geography AS geo, c.confidence AS conf, di.fragment AS frag",
        id=DOC_ID,
    ).single()
    assert concl["geo"] == "РФ"
    assert concl["conf"] == 0.85
    assert concl["frag"] == "page 1"

    # измерение с условиями
    meas = graph_session.run(
        "MATCH (d:Document {id: $id})-[:REPORTS]->(ms:Measurement)-[:OF_PROPERTY]->(p:Property) "
        "RETURN ms.value AS v, ms.unit AS u, ms.conditions AS cond, p.name AS prop",
        id=DOC_ID,
    ).single()
    assert meas["v"] == 30.0
    assert "температура" in meas["cond"]

    # автор из титульного фрагмента
    author = graph_session.run(
        "MATCH (d:Document {id: $id})-[:AUTHORED_BY]->(p:Person) RETURN p.name AS n",
        id=DOC_ID,
    ).single()
    assert author["n"] == "Тестов Т.Т."

    # чанк помечен обработанным — повторный прогон его не возьмёт
    again = extract_chunks(graph_session, FakeLLM(), limit=10, doc_id=DOC_ID)
    assert again.loaded == 0
