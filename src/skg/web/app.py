"""Веб-приложение: поиск с ответом и цитатами, карточки сущностей, граф, пробелы.

Запуск: skg serve  (или uvicorn skg.web.app:app)
LLM-ключ не обязателен: без него /api/answer возвращает фрагменты без сводки.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from neo4j import Driver

from ..config import get_settings
from ..db import create_driver
from ..extract.llm import LLMClient, LLMError, get_llm
from ..query.answer import answer_question
from ..query.search import attach_doc_entities, hybrid_search

app = FastAPI(title="Научный клубок — карта знаний R&D")
STATIC = Path(__file__).parent / "static"

_driver: Driver | None = None
_llm: LLMClient | None | str = "unset"  # ленивое разрешение, None = ключа нет


def driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = create_driver()
    return _driver


def llm() -> LLMClient | None:
    global _llm
    if _llm == "unset":
        try:
            _llm = get_llm(role="synth")
        except LLMError:
            _llm = None
    return _llm


def _session():
    return driver().session(database=get_settings().neo4j_database)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    with _session() as s:
        s.run("RETURN 1").consume()
    return {"neo4j": True, "llm": llm() is not None}


@app.get("/api/answer")
def api_answer(
    q: str = Query(..., min_length=2, max_length=1000),
    k: int = Query(10, ge=1, le=20),
    category: str | None = None,
    language: str | None = Query(None, pattern="^(ru|en)$"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "год от не может быть больше года до")
    with _session() as s:
        return answer_question(
            s,
            q,
            llm=llm(),
            k=k,
            category=category,
            language=language,
            year_from=year_from,
            year_to=year_to,
        )


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=2, max_length=1000),
    k: int = Query(12, ge=1, le=30),
    category: str | None = None,
    language: str | None = Query(None, pattern="^(ru|en)$"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "год от не может быть больше года до")
    with _session() as s:
        hits = hybrid_search(
            s, q, k=k, category=category, language=language,
            year_from=year_from, year_to=year_to,
        )
        attach_doc_entities(s, hits)
    return {"hits": hits}


@app.get("/api/review")
def api_review(limit: int = Query(50, ge=1, le=200)):
    with _session() as s:
        rows = s.run(
            """
            MATCH (n)
            WHERE n.needs_review = true
            RETURN labels(n)[0] AS label, coalesce(n.id, n.name) AS key,
                   coalesce(n.name, n.grade, n.text, n.id) AS name,
                   n.confidence AS confidence
            ORDER BY label, name
            LIMIT $limit
            """,
            limit=limit,
        ).data()
    return {"items": rows}


@app.get("/api/stats")
def api_stats():
    with _session() as s:
        nodes = s.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"
        ).data()
        rels = s.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC"
        ).data()
        docs = s.run(
            "MATCH (d:Document) RETURN d.category AS category, count(*) AS n, "
            "min(d.year) AS year_min, max(d.year) AS year_max ORDER BY n DESC"
        ).data()
        review = s.run(
            "MATCH (n) WHERE n.needs_review = true "
            "RETURN labels(n)[0] AS label, count(*) AS n"
        ).data()
    return {"nodes": nodes, "relations": rels, "documents": docs, "needs_review": review}


ENTITY_CARD = """
MATCH (e:{label}) WHERE coalesce(e.id, e.name) = $key
OPTIONAL MATCH (d:Document)-[m:MENTIONS]->(e)
WITH e, d, m ORDER BY m.count DESC
WITH e, collect({{id: d.id, title: d.title, category: d.category, year: d.year,
                 mentions: m.count}})[..25] AS docs
OPTIONAL MATCH (c:Conclusion)-[:ABOUT]->(e)
OPTIONAL MATCH (c)-[:DESCRIBED_IN]->(cd:Document)
WITH e, docs, collect(DISTINCT {{text: c.text, confidence: c.confidence,
     geography: c.geography, doc: cd.title}})[..25] AS conclusions
RETURN properties(e) AS props, docs, conclusions
"""


@app.get("/api/entity/{label}/{key}")
def api_entity(label: str, key: str):
    if label not in {"Material", "Process", "Equipment", "Facility", "TopicTag", "Person"}:
        raise HTTPException(400, "неизвестный тип сущности")
    with _session() as s:
        rec = s.run(ENTITY_CARD.format(label=label), key=key).single()
    if rec is None:
        raise HTTPException(404, "сущность не найдена")
    return {"label": label, **rec.data()}


CHUNK_CONTEXT = """
MATCH (c:Chunk {id: $chunk_id})<-[:HAS_CHUNK]-(d:Document)
MATCH (d)-[:HAS_CHUNK]->(n:Chunk)
WHERE n.seq >= c.seq - 2 AND n.seq <= c.seq + 2
RETURN d.title AS title, d.rel_path AS rel_path, d.category AS category,
       n.seq AS seq, n.unit_kind AS unit_kind, n.unit_no AS unit_no, n.text AS text
ORDER BY n.seq
"""


@app.get("/api/context")
def api_context(chunk_id: str):
    """Фрагмент ± два соседних: прочитать цитату в её окружении."""
    with _session() as s:
        rows = s.run(CHUNK_CONTEXT, chunk_id=chunk_id).data()
    if not rows:
        raise HTTPException(404, "чанк не найден")
    return {
        "title": rows[0]["title"],
        "rel_path": rows[0]["rel_path"],
        "category": rows[0]["category"],
        "chunks": rows,
        "focus": chunk_id,
    }


GRAPH_AROUND_DOC = """
MATCH (d:Document {id: $doc_id})
OPTIONAL MATCH (d)-[m:MENTIONS]->(e)
WITH d, e, m ORDER BY m.count DESC LIMIT 30
OPTIONAL MATCH (c:Conclusion)-[:DESCRIBED_IN]->(d)
RETURN d, collect(DISTINCT {entity: e, mentions: m.count}) AS ents,
       collect(DISTINCT c) AS concls
"""

GRAPH_AROUND_ENTITY = """
MATCH (e:{label}) WHERE coalesce(e.id, e.name) = $key
MATCH (d:Document)-[m:MENTIONS]->(e)
WITH e, d, m ORDER BY m.count DESC LIMIT 15
OPTIONAL MATCH (d)-[m2:MENTIONS]->(other)
WHERE other <> e
WITH e, d, other, m2 ORDER BY m2.count DESC
WITH e, d, collect({{entity: other, mentions: m2.count}})[..8] AS others
RETURN e, collect({{doc: d, others: others}}) AS neighborhood
"""


def _node_payload(node) -> dict:
    label = list(node.labels)[0]
    props = dict(node)
    return {
        "id": f"{label}:{props.get('id') or props.get('name')}",
        "label": label,
        "name": props.get("title") or props.get("name") or props.get("grade") or props.get("id"),
        "needs_review": props.get("needs_review", False),
    }


@app.get("/api/graph")
def api_graph(doc_id: str | None = None, label: str | None = None, key: str | None = None):
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    with _session() as s:
        if doc_id:
            rec = s.run(GRAPH_AROUND_DOC, doc_id=doc_id).single()
            if rec is None:
                raise HTTPException(404, "документ не найден")
            d = _node_payload(rec["d"])
            nodes[d["id"]] = d
            for item in rec["ents"]:
                if item["entity"] is None:
                    continue
                e = _node_payload(item["entity"])
                nodes[e["id"]] = e
                edges.append({"from": d["id"], "to": e["id"], "type": "MENTIONS",
                              "weight": item["mentions"]})
            for c in rec["concls"]:
                cn = _node_payload(c)
                nodes[cn["id"]] = cn
                edges.append({"from": cn["id"], "to": d["id"], "type": "DESCRIBED_IN", "weight": 1})
        elif label and key:
            if label not in {"Material", "Process", "Equipment", "Facility"}:
                raise HTTPException(400, "label должен быть Material|Process|Equipment|Facility")
            rec = s.run(GRAPH_AROUND_ENTITY.format(label=label), key=key).single()
            if rec is None:
                raise HTTPException(404, "сущность не найдена")
            e = _node_payload(rec["e"])
            nodes[e["id"]] = e
            for item in rec["neighborhood"]:
                d = _node_payload(item["doc"])
                nodes[d["id"]] = d
                edges.append({"from": d["id"], "to": e["id"], "type": "MENTIONS", "weight": 1})
                for o in item["others"]:
                    if o["entity"] is None:
                        continue
                    on = _node_payload(o["entity"])
                    nodes[on["id"]] = on
                    edges.append({"from": d["id"], "to": on["id"], "type": "MENTIONS",
                                  "weight": o["mentions"] or 1})
        else:
            raise HTTPException(400, "нужен doc_id либо label+key")
    return {"nodes": list(nodes.values()), "edges": edges}


GAPS_QUERY = """
MATCH (m:Material) WHERE m.needs_review IS NULL OR m.needs_review = false
MATCH (p:Process) WHERE p.needs_review IS NULL OR p.needs_review = false
OPTIONAL MATCH (d:Document)-[:MENTIONS]->(m)
WHERE (d)-[:MENTIONS]->(p)
WITH m.name AS material, p.name AS process, count(DISTINCT d) AS docs
RETURN material, process, docs
"""


@app.get("/api/gaps")
def api_gaps(top_materials: int = 20, top_processes: int = 20):
    with _session() as s:
        rows = s.run(GAPS_QUERY).data()
    # берём наиболее упоминаемые материалы/процессы, чтобы матрица была обозримой
    mat_totals: dict[str, int] = {}
    proc_totals: dict[str, int] = {}
    for r in rows:
        mat_totals[r["material"]] = mat_totals.get(r["material"], 0) + r["docs"]
        proc_totals[r["process"]] = proc_totals.get(r["process"], 0) + r["docs"]
    materials = sorted(mat_totals, key=mat_totals.get, reverse=True)[:top_materials]
    processes = sorted(proc_totals, key=proc_totals.get, reverse=True)[:top_processes]
    cells = {
        (r["material"], r["process"]): r["docs"]
        for r in rows
        if r["material"] in materials and r["process"] in processes
    }
    matrix = [
        {"material": m, "cells": [cells.get((m, p), 0) for p in processes]} for m in materials
    ]
    return {"processes": processes, "matrix": matrix}


@app.get("/api/experts")
def api_experts(topic: str):
    """Носители экспертизы: авторы документов, релевантных теме."""
    with _session() as s:
        hits = hybrid_search(s, topic, k=25)
        doc_ids = sorted({h["doc_id"] for h in hits})
        rows = s.run(
            """
            UNWIND $doc_ids AS did
            MATCH (d:Document {id: did})-[:AUTHORED_BY]->(p:Person)
            RETURN p.name AS name, p.lab AS lab, count(DISTINCT d) AS docs,
                   collect(DISTINCT d.title)[..5] AS titles
            ORDER BY docs DESC LIMIT 15
            """,
            doc_ids=doc_ids,
        ).data()
    return {"experts": rows}
