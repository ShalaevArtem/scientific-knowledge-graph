"""Гибридный поиск по корпусу: вектор (семантика) + BM25 (термины) + граф, RRF-слияние.

Векторный поиск ловит перефразировки и RU/EN-переключения, полнотекстовый —
точные термины и марки («12Х18Н10Т», «SO2»). Reciprocal Rank Fusion не требует
калибровки скорингов между индексами.

Графовый источник кандидатов: канонические сущности (Material/Process/Equipment),
распознанные в вопросе, ведут через рёбра MENTIONS к документам, упоминающим
несколько сущностей сразу; чанки-источники этих упоминаний (provenance рёбер)
попадают в слияние третьим списком. Так многопараметрический запрос
«материал + процесс» отвечается структурой графа, а не только текстовой близостью.
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
  AND ($allowed IS NULL OR d.category IN $allowed)
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


CANONICAL_ENTITIES = """
MATCH (n) WHERE (n:Material OR n:Process OR n:Equipment)
  AND (n.needs_review IS NULL OR n.needs_review = false)
RETURN labels(n)[0] AS label, coalesce(n.id, n.name) AS key,
       coalesce(n.name, n.grade) AS name, n.name_en AS name_en, n.synonyms AS synonyms
"""

GRAPH_DOC_CHUNKS = """
UNWIND $keys AS k
MATCH (d:Document)-[m:MENTIONS]->(e)
WHERE (e:Material OR e:Process OR e:Equipment) AND coalesce(e.id, e.name) = k
WITH d, count(DISTINCT k) AS matched, sum(m.count) AS weight,
     collect(DISTINCT m.source) AS srcs
WHERE matched >= $need
WITH matched, weight, srcs ORDER BY matched DESC, weight DESC LIMIT 15
RETURN srcs
"""

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _stem(word: str) -> str:
    """Грубый стем под русские окончания: «электроэкстракции» ~ «электроэкстракция»."""
    w = word.casefold().replace("ё", "е")
    if len(w) >= 6:
        return w[:-2]
    if len(w) >= 4:
        return w[:-1]
    return w


def _contains_words(text: str, words: list[str]) -> bool:
    """Все ли слова (с точностью до окончаний) встречаются в тексте.

    Слово совпадает со словом текста, если то начинается с его стема и не длиннее
    стема более чем на 3 символа (отсекает «мед» ~ «медленный»).
    """
    twords = [w.casefold().replace("ё", "е") for w in _WORD_RE.findall(text)]
    return all(
        any(tw.startswith(st) and len(tw) <= len(st) + 3 for tw in twords for st in (_stem(w),))
        for w in words
    )


def match_entities(question: str, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Сущности, чьё имя (или синоним) встречается в вопросе с точностью до окончаний."""
    found = []
    for ent in entities:
        variants = [ent.get("name"), ent.get("name_en"), *(ent.get("synonyms") or [])]
        for v in variants:
            words = _WORD_RE.findall(v) if v else []
            if words and _contains_words(question, words):
                found.append(ent)
                break
    return found


def resolve_question_entities(session: Session, question: str) -> list[dict[str, Any]]:
    """Канонические сущности графа, упомянутые в вопросе (лексически, без LLM)."""
    return match_entities(question, session.run(CANONICAL_ENTITIES).data())


ALL_PROPERTIES = "MATCH (p:Property) RETURN p.name AS name"

MEASUREMENTS_FOR_PROPS = """
UNWIND $props AS pname
MATCH (d:Document)-[r:REPORTS]->(ms:Measurement)-[:OF_PROPERTY]->(p:Property {name: pname})
WHERE ms.value IS NOT NULL
RETURN ms.value AS value, ms.value_max AS value_max, ms.unit AS unit,
       r.source AS chunk_id
LIMIT 5000
"""


def _norm_unit(unit: str | None) -> str:
    """Нормализация единиц для сравнения: «мг/дм³» ≡ «мг/дм3» ≡ «мг/л»."""
    u = (unit or "").casefold().replace(" ", "").replace("·", "*").replace("³", "3")
    return u.replace("дм3", "л").replace("cм3", "мл").replace("%масс", "%")


def _interval(op: str, value: float, value_max: float | None) -> tuple[float, float]:
    """Ограничение из вопроса → интервал допустимых значений."""
    inf = float("inf")
    if op == "range":
        return (value, value_max if value_max is not None else value)
    if op in ("<=", "<"):
        return (-inf, value)
    if op in (">=", ">"):
        return (value, inf)
    delta = abs(value) * 0.1  # «=» с допуском ±10 %
    return (value - delta, value + delta)


def measurement_candidates(
    session: Session, constraints: list[dict[str, Any]], limit: int = 40
) -> list[str]:
    """Чанки, где зафиксированы измерения, удовлетворяющие числовым ограничениям вопроса.

    Показатель ограничения матчится по именам Property (стеммингом), значение —
    пересечением интервалов (у измерения диапазон value..value_max), единицы
    сверяются после нормализации; отсутствие единицы у одной из сторон не отсекает.
    Чанки ранжируются по числу удовлетворённых ограничений.
    """
    prop_names = [r["name"] for r in session.run(ALL_PROPERTIES)]
    hits: dict[str, int] = {}
    for c in constraints:
        cprop, value = (c.get("property") or "").strip(), c.get("value")
        if not cprop or value is None:
            continue
        words = _WORD_RE.findall(cprop)
        matched = [p for p in prop_names if p and words and _contains_words(p, words)]
        if not matched:
            continue
        lo, hi = _interval(c.get("op") or "=", float(value), c.get("value_max"))
        cunit = _norm_unit(c.get("unit"))
        for row in session.run(MEASUREMENTS_FOR_PROPS, props=matched[:200]):
            munit = _norm_unit(row["unit"])
            if cunit and munit and cunit != munit:
                continue
            m_lo = row["value"]
            m_hi = row["value_max"] if row["value_max"] is not None else m_lo
            if m_lo is not None and m_lo <= hi and m_hi >= lo and row["chunk_id"]:
                hits[row["chunk_id"]] = hits.get(row["chunk_id"], 0) + 1
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    return [cid for cid, _ in ranked[:limit]]


def graph_candidates(session: Session, entity_keys: list[str], limit: int) -> list[str]:
    """Чанки-источники упоминаний из документов, покрывающих несколько сущностей.

    Возвращает id чанков, ранжированные по числу покрытых сущностей и суммарной
    частоте упоминаний документа.
    """
    need = 2 if len(entity_keys) >= 2 else 1
    seen: set[str] = set()
    ranked: list[str] = []
    for row in session.run(GRAPH_DOC_CHUNKS, keys=entity_keys, need=need):
        for cid in row["srcs"]:
            if cid and cid not in seen:
                seen.add(cid)
                ranked.append(cid)
    return ranked[:limit]


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
    entity_keys: list[str] | None = None,
    measure_chunk_ids: list[str] | None = None,
    allowed_categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    fetch_k = max(k * 5, 40)

    vector = embed_texts([query])[0]
    vec_hits = session.run(VECTOR_QUERY, k=fetch_k, vector=vector).data()

    ft_query = _fulltext_escape(query)
    ft_hits = (
        session.run(FULLTEXT_QUERY, q=ft_query, k=fetch_k).data() if ft_query else []
    )

    graph_ids = graph_candidates(session, entity_keys, fetch_k) if entity_keys else []
    graph_hits = [{"chunk_id": cid} for cid in graph_ids]
    measure_hits = [{"chunk_id": cid} for cid in (measure_chunk_ids or [])]

    # Reciprocal Rank Fusion
    scores: dict[str, float] = {}
    for hits in (vec_hits, ft_hits, graph_hits, measure_hits):
        for rank, hit in enumerate(hits):
            scores[hit["chunk_id"]] = scores.get(hit["chunk_id"], 0.0) + 1.0 / (rrf_k + rank + 1)

    if not scores:
        return []
    ranked_ids = sorted(scores, key=scores.get, reverse=True)

    rows = session.run(
        FETCH_CHUNKS,
        ids=ranked_ids,
        category=category,
        allowed=allowed_categories,
        language=language,
        year_from=year_from,
        year_to=year_to,
    ).data()
    by_id = {r["chunk_id"]: r for r in rows}

    graph_set = set(graph_ids)
    measure_set = set(measure_chunk_ids or [])
    per_doc: dict[str, int] = {}  # диверсификация: топ не забивается чанками одного документа
    results = []
    for cid in ranked_ids:
        row = by_id.get(cid)
        if row is None:  # отфильтрован по метаданным
            continue
        if per_doc.get(row["doc_id"], 0) >= 2:
            continue
        per_doc[row["doc_id"]] = per_doc.get(row["doc_id"], 0) + 1
        row["rrf_score"] = round(scores[cid], 5)
        row["display_title"] = display_title(row)
        row["via_graph"] = cid in graph_set
        row["via_measure"] = cid in measure_set
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
