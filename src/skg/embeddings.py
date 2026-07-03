"""Локальные эмбеддинги (fastembed/ONNX, без GPU и внешних API) и семантический поиск.

Модель мультиязычная (RU/EN) — терминология корпуса двуязычная
(«электроэкстракция» / «electrowinning»). Размерность зашита в vector-индекс
chunk_embedding (schema/003_corpus.cypher) — при смене модели пересоздать индекс.
"""

from __future__ import annotations

import os
from typing import Iterable

from neo4j import Session

from .loaders.base import batched

EMBED_MODEL = os.environ.get(
    "EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
EMBED_DIMS = 384

_model = None


def get_model():
    global _model
    if _model is None:
        from pathlib import Path

        from fastembed import TextEmbedding

        cache = Path(__file__).resolve().parents[2] / ".cache" / "fastembed"
        _model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(cache))
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in get_model().embed(texts)]


SET_EMBEDDINGS = """
UNWIND $rows AS row
MATCH (c:Chunk {id: row.id})
CALL db.create.setNodeVectorProperty(c, 'embedding', row.embedding)
"""


MISSING_IDS = """
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
WHERE c.embedding IS NULL
RETURN c.id AS id,
       CASE WHEN d.category = 'журнальная статья' THEN 1 ELSE 0 END AS prio
ORDER BY prio, id
"""

FETCH_TEXTS = """
UNWIND $ids AS cid
MATCH (c:Chunk {id: cid})
RETURN c.id AS id, c.text AS text
"""


def embed_missing(
    session: Session, batch_size: int = 200, max_total: int | None = None
) -> int:
    """Считает эмбеддинги для чанков, у которых их ещё нет. Возобновляемо;
    max_total ограничивает объём прогона. Журнальные подшивки (самая объёмная
    и наименее приоритетная часть корпуса) эмбеддятся в последнюю очередь."""
    ids = [r["id"] for r in session.run(MISSING_IDS)]
    if max_total:
        ids = ids[:max_total]
    print(f"чанков без эмбеддинга к обработке: {len(ids)}", flush=True)
    total = 0
    for i in range(0, len(ids), batch_size):
        chunk_ids = ids[i : i + batch_size]
        records = session.run(FETCH_TEXTS, ids=chunk_ids).data()
        vectors = embed_texts([r["text"] for r in records])
        rows = [{"id": r["id"], "embedding": v} for r, v in zip(records, vectors)]
        for batch in batched(rows, 100):
            session.execute_write(lambda tx, b=batch: tx.run(SET_EMBEDDINGS, rows=b).consume())
        total += len(rows)
        if total % 2000 < batch_size:
            print(f"  эмбеддинги: {total}/{len(ids)}", flush=True)
    return total


VECTOR_SEARCH = """
CALL db.index.vector.queryNodes('chunk_embedding', $k, $vector)
YIELD node, score
MATCH (d:Document {id: node.doc_id})
WHERE ($category IS NULL OR d.category = $category)
  AND ($language IS NULL OR d.language = $language)
  AND ($year_from IS NULL OR d.year >= $year_from)
RETURN node.id AS chunk_id, node.text AS text, node.unit_kind AS unit_kind,
       node.unit_no AS unit_no, score,
       d.id AS doc_id, d.title AS title, d.category AS category,
       d.journal AS journal, d.year AS year, d.language AS language, d.rel_path AS rel_path
ORDER BY score DESC
"""


def semantic_search(
    session: Session,
    query: str,
    k: int = 8,
    category: str | None = None,
    language: str | None = None,
    year_from: int | None = None,
) -> list[dict]:
    vector = embed_texts([query])[0]
    # запрашиваем с запасом: часть кандидатов отфильтруется по метаданным
    return session.run(
        VECTOR_SEARCH,
        k=max(k * 4, 20),
        vector=vector,
        category=category,
        language=language,
        year_from=year_from,
    ).data()[:k]
