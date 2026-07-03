"""Загрузчики справочников: материалы, установки, сотрудники, свойства, реестр документов.

Все загрузки идемпотентны (MERGE по уникальному ключу), повторный прогон обновляет атрибуты.
"""

from __future__ import annotations

import json
from pathlib import Path

from neo4j import Session
from pydantic import ValidationError

from ..models import (
    DocumentRecord,
    EquipmentRecord,
    MaterialRecord,
    PersonRecord,
    PropertyRecord,
)
from .base import LoadReport, provenance, read_csv, run_batched, split_list

MERGE_MATERIALS = """
UNWIND $rows AS row
MERGE (m:Material {id: row.id})
SET m.grade = row.grade,
    m.synonyms = row.synonyms,
    m.composition = row.composition
"""

MERGE_EQUIPMENT = """
UNWIND $rows AS row
MERGE (u:Equipment {id: row.id})
SET u.type = row.type, u.lab = row.lab
"""

MERGE_PERSONS = """
UNWIND $rows AS row
MERGE (p:Person {id: row.id})
SET p.name = row.name, p.lab = row.lab
"""

MERGE_PROPERTIES = """
UNWIND $rows AS row
MERGE (p:Property {name: row.name})
SET p.category = row.category
"""

MERGE_DOCUMENTS = """
UNWIND $rows AS row
MERGE (d:Document {id: row.id})
SET d.doc_type = row.doc_type, d.date = date(row.date), d.title = row.title
"""

MERGE_AUTHORSHIP = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MATCH (p:Person {id: row.person_id})
MERGE (d)-[a:AUTHORED_BY]->(p)
SET a += row.prov
"""


def load_materials(session: Session, path: Path) -> LoadReport:
    report = LoadReport(source=path.name)
    rows: list[dict] = []
    seen_keys: dict[str, str] = {}
    for raw in read_csv(path):
        try:
            rec = MaterialRecord(
                id=raw["id"],
                grade=raw["grade"],
                synonyms=split_list(raw.get("synonyms")),
                composition=json.loads(raw["composition"]) if raw.get("composition") else {},
            )
        except (ValidationError, json.JSONDecodeError, KeyError) as exc:
            report.reject(raw.get("id", "<без id>"), str(exc))
            continue
        # два материала с общим синонимом сломают нормализацию — ловим на входе
        for key in rec.lookup_keys:
            if key in seen_keys and seen_keys[key] != rec.id:
                report.warn(
                    f"синоним «{key}» у {rec.id} уже занят материалом {seen_keys[key]}"
                )
            seen_keys[key] = rec.id
        rows.append(
            {
                "id": rec.id,
                "grade": rec.grade,
                "synonyms": rec.synonyms,
                "composition": json.dumps(rec.composition, ensure_ascii=False),
            }
        )
    report.loaded = run_batched(session, MERGE_MATERIALS, rows)
    return report


def load_equipment(session: Session, path: Path) -> LoadReport:
    return _load_simple(session, path, EquipmentRecord, MERGE_EQUIPMENT)


def load_staff(session: Session, path: Path) -> LoadReport:
    return _load_simple(session, path, PersonRecord, MERGE_PERSONS)


def load_properties(session: Session, path: Path) -> LoadReport:
    return _load_simple(session, path, PropertyRecord, MERGE_PROPERTIES)


def _load_simple(session: Session, path: Path, model, query: str) -> LoadReport:
    report = LoadReport(source=path.name)
    rows = []
    for raw in read_csv(path):
        try:
            rows.append(model(**raw).model_dump())
        except ValidationError as exc:
            report.reject(raw.get("id") or raw.get("name") or "<без ключа>", str(exc))
    report.loaded = run_batched(session, query, rows)
    return report


def load_documents(session: Session, path: Path) -> LoadReport:
    report = LoadReport(source=path.name)
    known_persons = {
        r["id"] for r in session.run("MATCH (p:Person) RETURN p.id AS id")
    }
    doc_rows, author_rows = [], []
    for raw in read_csv(path):
        try:
            rec = DocumentRecord(
                id=raw["id"],
                doc_type=raw["doc_type"],
                date=raw["date"],
                title=raw["title"],
                authors=split_list(raw.get("authors")),
            )
        except (ValidationError, KeyError) as exc:
            report.reject(raw.get("id", "<без id>"), str(exc))
            continue
        doc_rows.append(
            {
                "id": rec.id,
                "doc_type": rec.doc_type,
                "date": rec.date.isoformat(),
                "title": rec.title,
            }
        )
        for person_id in rec.authors:
            if person_id not in known_persons:
                report.warn(
                    f"{rec.id}: автор {person_id} не найден в справочнике сотрудников — ребро пропущено"
                )
                continue
            author_rows.append(
                {"doc_id": rec.id, "person_id": person_id, "prov": provenance(path, rec.id)}
            )
    report.loaded = run_batched(session, MERGE_DOCUMENTS, doc_rows)
    run_batched(session, MERGE_AUTHORSHIP, author_rows)
    return report
