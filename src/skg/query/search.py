"""Гибридный поиск по корпусу: вектор (семантика) + BM25 (термины) с RRF-слиянием.

Векторный поиск ловит перефразировки и RU/EN-переключения, полнотекстовый —
точные термины и марки («12Х18Н10Т», «SO2»). Reciprocal Rank Fusion не требует
калибровки скорингов между индексами.
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import Session

from ..embeddings import embed_texts

VECTOR_QUERY = """
CALL db.index.vector.queryNodes('chunk_embedding', $k, $vector)
YIELD node, score
RETURN node.id AS chunk_id, score
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('chunk_search', $q)
YIELD node, score
RETURN node.id AS chunk_id, score
LIMIT $k
"""

FETCH_CHUNKS = """
UNWIND $ids AS cid
MATCH (c:Chunk {id: cid})<-[:HAS_CHUNK]-(d:Document)
WHERE ($category IS NULL OR d.category = $category)
  AND ($language IS NULL OR d.language = $language)
  AND ($year_from IS NULL OR d.year >= $year_from)
  AND ($year_to IS NULL OR d.year <= $year_to)
RETURN c.id AS chunk_id, c.text AS text, c.unit_kind AS unit_kind, c.unit_no AS unit_no,
       d.id AS doc_id, d.title AS title, d.category AS category, d.journal AS journal,
       d.conference AS conference, d.year AS year, d.language AS language, d.rel_path AS rel_path
"""

DOC_ENTITIES = """
UNWIND $doc_ids AS did
MATCH (d:Document {id: did})-[m:MENTIONS]->(e)
WITH did, labels(e)[0] AS label, coalesce(e.name, e.grade, e.id) AS name, sum(m.count) AS cnt
ORDER BY cnt DESC
WITH did, collect({label: label, name: name, mentions: cnt})[..12] AS entities
RETURN did AS doc_id, entities
"""


_ARTIFACT_TITLE = re.compile(r"(?i)\.(qxd|indd|pmd|p65|cdr|fm)$|^[A-Za-z]{2,4}[_-]?\d")


def display_title(row: dict[str, Any]) -> str:
    """Титул для цитат: у журнальных подшивок PDF-metadata — имя файла вёрстки
    («CM_7_03.qxd»); тогда понятнее «Цветные металлы, 2003»."""
    title = row.get("title") or ""
    if row.get("journal") and _ARTIFACT_TITLE.search(title):
        year = f", {row['year']}" if row.get("year") else ""
        return f"{row['journal']}{year}"
    return title


def _fulltext_escape(query: str) -> str:
    """Экранирует спецсимволы Lucene, сохраняя слова запроса."""
    out = []
    for ch in query:
        if ch in '+-&|!(){}[]^"~*?:\\/':
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip()


def hybrid_search(
    session: Session,
    query: str,
    k: int = 10,
    category: str | None = None,
    language: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    fetch_k = max(k * 5, 40)

    vector = embed_texts([query])[0]
    vec_hits = session.run(VECTOR_QUERY, k=fetch_k, vector=vector).data()

    ft_query = _fulltext_escape(query)
    ft_hits = (
        session.run(FULLTEXT_QUERY, q=ft_query, k=fetch_k).data() if ft_query else []
    )

    # Reciprocal Rank Fusion
    scores: dict[str, float] = {}
    for hits in (vec_hits, ft_hits):
        for rank, hit in enumerate(hits):
            scores[hit["chunk_id"]] = scores.get(hit["chunk_id"], 0.0) + 1.0 / (rrf_k + rank + 1)

    if not scores:
        return []
    ranked_ids = sorted(scores, key=scores.get, reverse=True)

    rows = session.run(
        FETCH_CHUNKS,
        ids=ranked_ids,
        category=category,
        language=language,
        year_from=year_from,
        year_to=year_to,
    ).data()
    by_id = {r["chunk_id"]: r for r in rows}

    results = []
    for cid in ranked_ids:
        row = by_id.get(cid)
        if row is None:  # отфильтрован по метаданным
            continue
        row["rrf_score"] = round(scores[cid], 5)
        row["display_title"] = display_title(row)
        results.append(row)
        if len(results) >= k:
            break
    return results


def attach_doc_entities(session: Session, results: list[dict[str, Any]]) -> None:
    """Дополняет результаты сущностями, извлечёнными из их документов (если extract прогнан)."""
    doc_ids = sorted({r["doc_id"] for r in results})
    if not doc_ids:
        return
    entities = {r["doc_id"]: r["entities"] for r in session.run(DOC_ENTITIES, doc_ids=doc_ids)}
    for r in results:
        r["entities"] = entities.get(r["doc_id"], [])
