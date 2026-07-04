"""Версионирование выводов: повторное извлечение с изменившимся результатом
архивирует прежнее состояние в ConclusionVersion. Требует запущенный Neo4j.
"""

from __future__ import annotations

import pytest

from skg.db import session_scope
from skg.extract.extractor import extract_chunks

DOC_ID = "TEST-DOC-VERSIONING"
CHUNK_ID = f"{DOC_ID}:1"


def fake_response(text: str, confidence: float) -> dict:
    return {
        "materials": [], "processes": [], "equipment": [], "facilities": [],
        "measurements": [], "topics": [], "authors": [],
        "conclusions": [{
            "text": text, "materials": [], "processes": [],
            "geography": "РФ", "confidence": confidence,
        }],
    }


class FakeLLM:
    model = "fake-model"

    def __init__(self, text: str, confidence: float):
        self.response = fake_response(text, confidence)

    def complete_json(self, system: str, user: str, max_tokens: int = 4096) -> dict:
        return self.response


@pytest.fixture()
def graph_session():
    with session_scope() as session:
        session.run(
            "MERGE (d:Document {id: $id}) "
            "SET d.title = 'Тест версий', d.category = 'статья', d.year = 2025 "
            "MERGE (c:Chunk {id: $cid}) "
            "SET c.text = 'текст', c.unit_kind = 'page', c.unit_no = 1, "
            "    c.seq = 1, c.doc_id = $id, c.extracted_at = NULL "
            "MERGE (d)-[:HAS_CHUNK]->(c)",
            id=DOC_ID, cid=CHUNK_ID,
        )
        yield session
        session.run(
            "MATCH (c:Conclusion) WHERE c.id STARTS WITH $cid "
            "OPTIONAL MATCH (c)-[:HAD_VERSION]->(v) DETACH DELETE c, v",
            cid=CHUNK_ID,
        )
        session.run(
            "MATCH (d:Document {id: $id})-[:HAS_CHUNK]->(c) DETACH DELETE d, c", id=DOC_ID
        )


def test_changed_conclusion_is_versioned(graph_session):
    # первичное извлечение: версия 1, архива нет
    extract_chunks(graph_session, FakeLLM("Скорость 30 л/мин оптимальна.", 0.6),
                   limit=10, doc_id=DOC_ID)
    first = graph_session.run(
        "MATCH (c:Conclusion {id: $cid}) RETURN c.version AS v, c.text AS t",
        cid=f"{CHUNK_ID}:C1",
    ).single()
    assert first["v"] == 1

    # повторное извлечение новой «моделью» с изменившимся выводом (--force)
    extract_chunks(graph_session, FakeLLM("Скорость 20-25 л/мин оптимальна.", 0.9),
                   limit=10, doc_id=DOC_ID, force=True)
    current = graph_session.run(
        "MATCH (c:Conclusion {id: $cid}) RETURN c.version AS v, c.text AS t, "
        "c.confidence AS conf",
        cid=f"{CHUNK_ID}:C1",
    ).single()
    assert current["v"] == 2
    assert "20-25" in current["t"]
    assert current["conf"] == 0.9

    archived = graph_session.run(
        "MATCH (c:Conclusion {id: $cid})-[:HAD_VERSION]->(v:ConclusionVersion) "
        "RETURN v.version AS v, v.text AS t, v.confidence AS conf",
        cid=f"{CHUNK_ID}:C1",
    ).single()
    assert archived["v"] == 1
    assert "30 л/мин" in archived["t"]
    assert archived["conf"] == 0.6


def test_unchanged_conclusion_not_versioned(graph_session):
    llm = FakeLLM("Вывод стабилен.", 0.7)
    extract_chunks(graph_session, llm, limit=10, doc_id=DOC_ID)
    extract_chunks(graph_session, llm, limit=10, doc_id=DOC_ID, force=True)
    rec = graph_session.run(
        "MATCH (c:Conclusion {id: $cid}) "
        "OPTIONAL MATCH (c)-[:HAD_VERSION]->(v) "
        "RETURN c.version AS ver, count(v) AS archived",
        cid=f"{CHUNK_ID}:C1",
    ).single()
    assert rec["ver"] == 1, "без изменений версия не растёт"
    assert rec["archived"] == 0, "архив не создаётся"
