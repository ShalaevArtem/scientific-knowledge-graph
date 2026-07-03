"""Загрузчик каталога экспериментов (JSONL: одна запись — один эксперимент).

Правила:
- материал указывается маркой или синонимом и нормализуется по справочнику;
  неизвестный материал / установка → запись отклоняется целиком (сначала пополните справочник);
- свойство измерения должно быть в справочнике свойств — иначе отклоняется
  только это измерение (дисциплина канонических имён);
- одинаковые режимы схлопываются в один узел Regime по детерминированному ключу;
- каждое ребро несёт provenance: source (файл#запись), confidence, loaded_at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo4j import Session
from pydantic import ValidationError

from ..models import ExperimentRecord, normalize_name
from .base import LoadReport, provenance, run_batched

MERGE_EXPERIMENT = """
UNWIND $rows AS row
MERGE (e:Experiment {id: row.id})
SET e.date = date(row.date), e.goal = row.goal, e.status = row.status
WITH e, row
MATCH (m:Material {id: row.material_id})
MERGE (e)-[uses:USES]->(m)
SET uses += row.prov
WITH e, row
MERGE (r:Regime {key: row.regime.key})
SET r.type = row.regime.type,
    r.temp_min = row.regime.temp_min,
    r.temp_max = row.regime.temp_max,
    r.duration_h = row.regime.duration_h,
    r.medium = row.regime.medium,
    r.params = row.regime.params
MERGE (e)-[ap:APPLIES]->(r)
SET ap += row.prov
WITH e, row
MATCH (u:Equipment {id: row.equipment_id})
MERGE (e)-[oe:ON_EQUIPMENT]->(u)
SET oe += row.prov
"""

MERGE_MEASUREMENTS = """
UNWIND $rows AS row
MATCH (e:Experiment {id: row.exp_id})
MATCH (p:Property {name: row.property})
MERGE (ms:Measurement {id: row.id})
SET ms.value = row.value, ms.unit = row.unit,
    ms.method = row.method, ms.uncertainty = row.uncertainty
MERGE (e)-[y:YIELDS]->(ms)
SET y += row.prov
MERGE (ms)-[op:OF_PROPERTY]->(p)
SET op += row.prov
"""

MERGE_CONCLUSIONS = """
UNWIND $rows AS row
MATCH (e:Experiment {id: row.exp_id})
MERGE (c:Conclusion {id: row.id})
SET c.text = row.text, c.confidence = row.confidence, c.date = date(row.date)
MERGE (e)-[lt:LEADS_TO]->(c)
SET lt += row.prov
"""

MERGE_DESCRIBED_IN = """
UNWIND $rows AS row
MATCH (c:Conclusion {id: row.conclusion_id})
MATCH (d:Document {id: row.doc_id})
MERGE (c)-[di:DESCRIBED_IN]->(d)
SET di += row.prov, di.fragment = row.fragment
"""

# Тип ребра нельзя параметризовать — по запросу на каждый тип связи выводов
MERGE_SUPPORTS = """
UNWIND $rows AS row
MATCH (a:Conclusion {id: row.from_id})
MATCH (b:Conclusion {id: row.to_id})
MERGE (a)-[r:SUPPORTS]->(b)
SET r += row.prov
"""

MERGE_CONTRADICTS = MERGE_SUPPORTS.replace(":SUPPORTS", ":CONTRADICTS")


def _material_lookup(session: Session) -> dict[str, str]:
    """Нормализованное имя (марка или синоним) → id материала."""
    lookup: dict[str, str] = {}
    for rec in session.run("MATCH (m:Material) RETURN m.id AS id, m.grade AS grade, m.synonyms AS synonyms"):
        for name in [rec["grade"], *(rec["synonyms"] or [])]:
            lookup[normalize_name(name)] = rec["id"]
    return lookup


def load_experiments(session: Session, path: Path) -> LoadReport:
    report = LoadReport(source=path.name)
    materials = _material_lookup(session)
    equipment = {r["id"] for r in session.run("MATCH (u:Equipment) RETURN u.id AS id")}
    properties = {r["name"] for r in session.run("MATCH (p:Property) RETURN p.name AS name")}
    documents = {r["id"] for r in session.run("MATCH (d:Document) RETURN d.id AS id")}

    exp_rows: list[dict[str, Any]] = []
    meas_rows: list[dict[str, Any]] = []
    concl_rows: list[dict[str, Any]] = []
    described_rows: list[dict[str, Any]] = []
    relation_rows: dict[str, list[dict[str, Any]]] = {"supports": [], "contradicts": []}
    conclusion_ids: set[str] = set()

    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rec = ExperimentRecord(**json.loads(line))
            except (ValidationError, json.JSONDecodeError) as exc:
                report.reject(f"строка {line_no}", str(exc))
                continue

            material_id = materials.get(normalize_name(rec.material))
            if material_id is None:
                report.reject(rec.id, f"материал «{rec.material}» не найден в справочнике")
                continue
            if rec.equipment_id not in equipment:
                report.reject(rec.id, f"установка «{rec.equipment_id}» не найдена в справочнике")
                continue

            prov = provenance(path, rec.id)
            exp_rows.append(
                {
                    "id": rec.id,
                    "date": rec.date.isoformat(),
                    "goal": rec.goal,
                    "status": rec.status,
                    "material_id": material_id,
                    "equipment_id": rec.equipment_id,
                    "regime": {
                        "key": rec.regime.key,
                        "type": rec.regime.type,
                        "temp_min": rec.regime.temp_min,
                        "temp_max": rec.regime.temp_max,
                        "duration_h": rec.regime.duration_h,
                        "medium": rec.regime.medium,
                        "params": json.dumps(rec.regime.params, ensure_ascii=False),
                    },
                    "prov": prov,
                }
            )

            for i, m in enumerate(rec.measurements, start=1):
                if m.property not in properties:
                    report.warn(
                        f"{rec.id}: свойство «{m.property}» не в справочнике — измерение пропущено"
                    )
                    continue
                meas_rows.append(
                    {
                        "id": f"{rec.id}:M{i}",
                        "exp_id": rec.id,
                        "property": m.property,
                        "value": m.value,
                        "unit": m.unit,
                        "method": m.method,
                        "uncertainty": m.uncertainty,
                        "prov": prov,
                    }
                )

            for i, c in enumerate(rec.conclusions, start=1):
                concl_id = c.id or f"{rec.id}:C{i}"
                conclusion_ids.add(concl_id)
                concl_rows.append(
                    {
                        "id": concl_id,
                        "exp_id": rec.id,
                        "text": c.text,
                        "confidence": c.confidence,
                        "date": c.date.isoformat(),
                        "prov": prov,
                    }
                )
                if c.doc_id is not None:
                    if c.doc_id not in documents:
                        report.warn(
                            f"{rec.id}: документ {c.doc_id} не найден — ребро DESCRIBED_IN пропущено"
                        )
                    else:
                        described_rows.append(
                            {
                                "conclusion_id": concl_id,
                                "doc_id": c.doc_id,
                                "fragment": c.fragment,
                                "prov": prov,
                            }
                        )
                for rel in c.relates:
                    relation_rows[rel.type].append(
                        {"from_id": concl_id, "to_id": rel.target, "prov": prov}
                    )

    report.loaded = run_batched(session, MERGE_EXPERIMENT, exp_rows)
    run_batched(session, MERGE_MEASUREMENTS, meas_rows)
    run_batched(session, MERGE_CONCLUSIONS, concl_rows)
    run_batched(session, MERGE_DESCRIBED_IN, described_rows)

    # связи между выводами применяем после создания всех выводов файла;
    # цель может быть и выводом, загруженным ранее из другого источника
    existing = {
        r["id"] for r in session.run("MATCH (c:Conclusion) RETURN c.id AS id")
    } | conclusion_ids
    for rel_type, query in (("supports", MERGE_SUPPORTS), ("contradicts", MERGE_CONTRADICTS)):
        valid_rows = []
        for row in relation_rows[rel_type]:
            if row["to_id"] not in existing:
                report.warn(
                    f"связь {rel_type}: вывод {row['to_id']} не найден — ребро пропущено"
                )
                continue
            valid_rows.append(row)
        run_batched(session, query, valid_rows)

    return report
