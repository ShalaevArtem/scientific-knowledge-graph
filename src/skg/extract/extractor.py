"""Извлечение сущностей и связей из чанков корпуса и запись в граф.

Нормализация: канонические узлы создаются из seed-справочников (data/seed/*.csv);
LLM-упоминания разрешаются в канон по нормализованному имени и синонимам.
Неизвестные сущности создаются с needs_review = true — очередь на проверку эксперта
(принцип: факт попадает в граф, но помечен как неверифицированный).

Provenance: каждое ребро несёт source (id чанка), unit (страница/слайд), confidence,
extracted_at и модель — «нет источника — нет факта».
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from neo4j import Session
from pydantic import ValidationError

from ..models import normalize_name
from ..loaders.base import LoadReport, run_batched
from .llm import LLMClient
from .schema import ChunkExtraction

SYSTEM_PROMPT = """Ты — система извлечения фактов для графа знаний горно-металлургического НИИ.
Домен: гидрометаллургия, пирометаллургия, обогащение, экология (очистка вод, газов, отходы).

Из фрагмента документа извлеки СТРОГО в JSON:
- materials: материалы и вещества (металлы, соединения, руды, штейн, шлак, католит, реагенты).
- processes: технологические процессы (выщелачивание, электроэкстракция, флотация, обжиг…).
- equipment: оборудование и установки (печь взвешенной плавки, ванна электроэкстракции…).
- facilities: предприятия/месторождения/проекты с указанием страны, если она ясна из текста.
- measurements: численные результаты — property (что измерено), value (число), unit,
  conditions (при каких условиях: parameter, op из «= <= >= < > range», value, value_max, unit).
- conclusions: выводы и рекомендации автора — text (кратко, 1–2 предложения своими словами),
  materials/processes (к чему относится), geography («РФ», «зарубежная» или страна),
  confidence 0..1 (насколько категорично утверждение в тексте).
- topics: 1–3 тега из списка: гидрометаллургия, пирометаллургия, обогащение, экология,
  переработка отходов, оборудование, экономика, геотехнология, автоматизация.
- authors: ФИО авторов документа — ТОЛЬКО если фрагмент титульный (шапка статьи/доклада
  с фамилиями и организациями); из текста ссылок и цитирований авторов НЕ брать.

Правила:
- names — на русском в канонической форме (ед. число, именительный падеж); name_en заполняй,
  только если в тексте есть английский эквивалент.
- НЕ выдумывай числа: value только из текста, числом (не строкой), десятичная точка.
  Диапазон «200–240» пиши как value=200, value_max=240. Ошибки в концентрациях недопустимы.
- Каждый элемент списков — ОБЪЕКТ, как в примере ниже, не строка.
- Пустые списки допустимы. Если фрагмент — ссылки/оглавление/шапка, верни пустые списки.

Пример формата ответа:
{"materials": [{"name": "никель", "name_en": "nickel"}],
 "processes": [{"name": "электроэкстракция"}],
 "equipment": [{"name": "диафрагменная ячейка"}],
 "facilities": [{"name": "Nikkelverk", "country": "Норвегия"}],
 "measurements": [{"property": "плотность тока", "value": 200, "value_max": 240,
   "unit": "А/м2", "conditions": [{"parameter": "температура", "op": "=", "value": 60, "unit": "°C"}]}],
 "conclusions": [{"text": "…", "materials": ["никель"], "processes": ["электроэкстракция"],
   "geography": "зарубежная", "confidence": 0.8}],
 "topics": ["гидрометаллургия"], "authors": []}"""

USER_TEMPLATE = """Документ: «{title}» ({category}{year}).
Фрагмент ({unit_kind} {unit_no}):

{text}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Normalizer:
    """Разрешение упоминаний в канонические узлы по нормализованному имени и синонимам."""

    LABELS = {"Material": "материал", "Process": "процесс", "Equipment": "оборудование"}

    def __init__(self, session: Session):
        self.maps: dict[str, dict[str, str]] = {}
        for label in self.LABELS:
            lookup: dict[str, str] = {}
            for rec in session.run(
                f"MATCH (n:{label}) RETURN coalesce(n.name, n.grade) AS name, "
                f"n.synonyms AS synonyms, coalesce(n.id, n.name) AS key"
            ):
                for variant in [rec["name"], *(rec["synonyms"] or [])]:
                    if variant:
                        lookup[normalize_name(variant)] = rec["key"]
            self.maps[label] = lookup

    def resolve(self, label: str, name: str, name_en: str | None = None) -> tuple[str, bool]:
        """→ (ключ канонического узла, найден_ли). Не найден → ключ нового узла."""
        lookup = self.maps[label]
        for variant in (name, name_en):
            if variant and normalize_name(variant) in lookup:
                return lookup[normalize_name(variant)], True
        key = f"{label[0]}:{normalize_name(name)}"
        lookup[normalize_name(name)] = key  # внутри прогона дубли схлопываются
        if name_en:
            lookup[normalize_name(name_en)] = key
        return key, False


# без apoc (Community из zip) — запрос на каждую метку из шаблона
MERGE_ENTITY_TPL = """
UNWIND $rows AS row
MERGE (n:{label} {{id: row.key}})
ON CREATE SET n.name = row.name, n.needs_review = row.needs_review,
              n.synonyms = row.synonyms, n.created_at = row.now
SET n.name_en = coalesce(n.name_en, row.name_en)
"""

MERGE_FACILITY = """
UNWIND $rows AS row
MERGE (f:Facility {name: row.name})
ON CREATE SET f.needs_review = row.needs_review, f.created_at = row.now
SET f.country = coalesce(f.country, row.country)
"""

MERGE_MENTIONS_TPL = """
UNWIND $rows AS row
MATCH (d:Document {{id: row.doc_id}})
MATCH (n:{label} {{{key_prop}: row.key}})
MERGE (d)-[m:MENTIONS]->(n)
ON CREATE SET m.count = 0
SET m.count = m.count + 1, m.source = row.source, m.unit = row.unit,
    m.confidence = row.confidence, m.extracted_at = row.now, m.model = row.model
"""

MERGE_TOPICS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (t:TopicTag {name: row.topic})
MERGE (d)-[r:TAGGED]->(t)
SET r.source = row.source, r.extracted_at = row.now
"""

MERGE_DOC_MEASUREMENTS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (p:Property {name: row.property})
ON CREATE SET p.category = 'не классифицировано', p.needs_review = true
MERGE (ms:Measurement {id: row.id})
SET ms.value = row.value, ms.value_max = row.value_max,
    ms.unit = row.unit, ms.conditions = row.conditions
MERGE (d)-[rp:REPORTS]->(ms)
SET rp.source = row.source, rp.unit = row.unit_ref, rp.confidence = row.confidence,
    rp.extracted_at = row.now, rp.model = row.model
MERGE (ms)-[:OF_PROPERTY]->(p)
"""

# Версионирование фактов: повторное извлечение (новая модель, обновлённый документ)
# не затирает вывод молча — прежнее состояние архивируется в ConclusionVersion,
# у актуального узла растёт version и обновляется updated_at.
MERGE_DOC_CONCLUSIONS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (c:Conclusion {id: row.id})
ON CREATE SET c.text = row.text, c.confidence = row.confidence, c.geography = row.geography,
    c.date = CASE WHEN row.year IS NULL THEN NULL ELSE date({year: row.year, month: 1, day: 1}) END,
    c.needs_review = row.needs_review, c.version = 1, c.updated_at = row.now
MERGE (c)-[di:DESCRIBED_IN]->(d)
SET di.fragment = row.fragment, di.source = row.source, di.confidence = row.confidence,
    di.extracted_at = row.now, di.model = row.model
WITH c, row
WHERE c.text <> row.text OR c.confidence <> row.confidence
   OR coalesce(c.geography, '') <> coalesce(row.geography, '')
CREATE (v:ConclusionVersion {conclusion_id: c.id, version: coalesce(c.version, 1),
    text: c.text, confidence: c.confidence, geography: c.geography,
    archived_at: row.now, archived_by_model: row.model})
MERGE (c)-[:HAD_VERSION]->(v)
SET c.text = row.text, c.confidence = row.confidence, c.geography = row.geography,
    c.needs_review = row.needs_review,
    c.version = coalesce(c.version, 1) + 1, c.updated_at = row.now
"""

MERGE_EXTRACTED_AUTHORS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (p:Person {id: row.person_id})
ON CREATE SET p.name = row.name, p.needs_review = true
MERGE (d)-[a:AUTHORED_BY]->(p)
SET a.source = row.source, a.confidence = row.confidence,
    a.extracted_at = row.now, a.model = row.model
"""

MERGE_CONCLUSION_ABOUT_TPL = """
UNWIND $rows AS row
MATCH (c:Conclusion {{id: row.conclusion_id}})
MATCH (n:{label} {{id: row.key}})
MERGE (c)-[a:ABOUT]->(n)
SET a.source = row.source, a.extracted_at = row.now
"""


def extract_chunks(
    session: Session,
    llm: LLMClient,
    limit: int = 100,
    doc_id: str | None = None,
    category: str | None = None,
    workers: int = 1,
    min_confidence_review: float = 0.6,
    force: bool = False,
) -> LoadReport:
    """Извлекает из чанков без метки processed, пишет факты в граф.

    LLM-вызовы идут параллельно (workers), разбор и запись — последовательно:
    нормализатор мутирует словарь соответствий и не потокобезопасен.

    force=True — повторная обработка уже извлечённых чанков (новая модель или
    обновлённый источник): изменившиеся выводы получают новую версию, прежнее
    состояние архивируется (см. MERGE_DOC_CONCLUSIONS).
    """
    report = LoadReport(source=f"extract:{llm.model}")
    normalizer = Normalizer(session)

    where = ""
    if doc_id:
        where += " AND c.doc_id = $doc_id"
    if category:
        where += " AND d.category = $category"
    freshness = "true" if force else "c.extracted_at IS NULL"
    chunks = session.run(
        f"""
        MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
        WHERE {freshness} {where}
        RETURN c.id AS id, c.text AS text, c.unit_kind AS unit_kind, c.unit_no AS unit_no,
               d.id AS doc_id, d.title AS title, d.category AS category, d.year AS year
        ORDER BY d.id, c.seq LIMIT $limit
        """,
        limit=limit,
        doc_id=doc_id,
        category=category,
    ).data()

    def _call(chunk: dict) -> tuple[dict, ChunkExtraction | Exception]:
        user = USER_TEMPLATE.format(
            title=chunk["title"],
            category=chunk["category"],
            year=f", {chunk['year']}" if chunk["year"] else "",
            unit_kind=chunk["unit_kind"],
            unit_no=chunk["unit_no"],
            text=chunk["text"],
        )
        try:
            return chunk, ChunkExtraction(**llm.complete_json(SYSTEM_PROMPT, user))
        except Exception as exc:  # noqa: BLE001 — ошибка одного чанка не валит прогон
            return chunk, exc

    # порциями: LLM-вызовы (параллельно) → разбор → запись; падение процесса
    # теряет максимум одну порцию, прогресс виден в логе
    processed = 0
    for wave in batched_list(chunks, WAVE_SIZE):
        if workers > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_call, wave))
        else:
            results = [_call(c) for c in wave]
        _write_wave(session, llm, normalizer, results, report, min_confidence_review)
        processed += len(wave)
        print(f"  извлечение: {processed}/{len(chunks)}", flush=True)
    return report


WAVE_SIZE = 300


def batched_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _write_wave(
    session: Session,
    llm: LLMClient,
    normalizer: Normalizer,
    results: list[tuple[dict, ChunkExtraction | Exception]],
    report: LoadReport,
    min_confidence_review: float,
) -> None:
    now = _now()
    entity_rows: dict[str, list[dict[str, Any]]] = {"Material": [], "Process": [], "Equipment": []}
    facility_rows: list[dict[str, Any]] = []
    mention_rows: dict[str, list[dict[str, Any]]] = {
        "Material": [], "Process": [], "Equipment": [], "Facility": []
    }
    topic_rows: list[dict[str, Any]] = []
    author_rows: list[dict[str, Any]] = []
    measurement_rows: list[dict[str, Any]] = []
    conclusion_rows: list[dict[str, Any]] = []
    about_rows: dict[str, list[dict[str, Any]]] = {"Material": [], "Process": []}
    done_chunk_ids: list[str] = []

    for chunk, extraction in results:
        if isinstance(extraction, Exception):
            report.reject(chunk["id"], f"{type(extraction).__name__}: {extraction}")
            continue

        base = {
            "doc_id": chunk["doc_id"],
            "source": chunk["id"],
            "unit": f"{chunk['unit_kind']} {chunk['unit_no']}",
            "confidence": 0.8,
            "now": now,
            "model": llm.model,
        }

        for label, mentions in (
            ("Material", extraction.materials),
            ("Process", extraction.processes),
            ("Equipment", extraction.equipment),
        ):
            for m in mentions:
                key, known = normalizer.resolve(label, m.name, m.name_en)
                if not known:
                    entity_rows[label].append(
                        {
                            "key": key, "name": m.name, "name_en": m.name_en,
                            "synonyms": [m.name] + ([m.name_en] if m.name_en else []),
                            "needs_review": True, "now": now,
                        }
                    )
                mention_rows[label].append({**base, "key": key})

        for f in extraction.facilities:
            facility_rows.append(
                {"name": f.name, "country": f.country, "needs_review": True, "now": now}
            )
            mention_rows["Facility"].append({**base, "key": f.name})

        for topic in extraction.topics:
            topic_rows.append({**base, "topic": topic.strip().casefold()})

        for author in extraction.authors:
            author = author.strip()
            if author:
                author_rows.append(
                    {**base, "person_id": "P:" + normalize_name(author), "name": author}
                )

        for i, ms in enumerate(extraction.measurements, start=1):
            measurement_rows.append(
                {
                    **base,
                    "id": f"{chunk['id']}:M{i}",
                    "property": ms.property.strip(),
                    "value": ms.value,
                    "value_max": ms.value_max,
                    "unit": ms.unit,
                    "unit_ref": base["unit"],
                    "conditions": json.dumps(
                        [c.model_dump() for c in ms.conditions], ensure_ascii=False
                    ),
                }
            )

        for i, concl in enumerate(extraction.conclusions, start=1):
            concl_id = f"{chunk['id']}:C{i}"
            conclusion_rows.append(
                {
                    **base,
                    "id": concl_id,
                    "text": concl.text,
                    "confidence": concl.confidence,
                    "geography": concl.geography,
                    "year": chunk["year"],
                    "fragment": base["unit"],
                    "needs_review": concl.confidence < min_confidence_review,
                }
            )
            for label, names in (("Material", concl.materials), ("Process", concl.processes)):
                for name in names:
                    key, known = normalizer.resolve(label, name)
                    if not known:
                        entity_rows[label].append(
                            {
                                "key": key, "name": name, "name_en": None,
                                "synonyms": [name], "needs_review": True, "now": now,
                            }
                        )
                    about_rows[label].append(
                        {"conclusion_id": concl_id, "key": key, **base}
                    )

        done_chunk_ids.append(chunk["id"])
        report.loaded += 1

    # запись в граф: сначала узлы, затем рёбра
    for label, rows in entity_rows.items():
        run_batched(session, MERGE_ENTITY_TPL.format(label=label), rows)
    run_batched(session, MERGE_FACILITY, facility_rows)
    for label, rows in mention_rows.items():
        key_prop = "name" if label == "Facility" else "id"
        run_batched(session, MERGE_MENTIONS_TPL.format(label=label, key_prop=key_prop), rows)
    run_batched(session, MERGE_TOPICS, topic_rows)
    run_batched(session, MERGE_EXTRACTED_AUTHORS, author_rows)
    run_batched(session, MERGE_DOC_MEASUREMENTS, measurement_rows)
    run_batched(session, MERGE_DOC_CONCLUSIONS, conclusion_rows)
    for label, rows in about_rows.items():
        run_batched(session, MERGE_CONCLUSION_ABOUT_TPL.format(label=label), rows)
    if done_chunk_ids:
        run_batched(
            session,
            "UNWIND $rows AS row MATCH (c:Chunk {id: row.id}) SET c.extracted_at = row.now",
            [{"id": cid, "now": now} for cid in done_chunk_ids],
        )
    new_entities = sum(len(v) for v in entity_rows.values())
    report.warn(
        f"новых сущностей на проверку: {new_entities}, выводов: {len(conclusion_rows)}, "
        f"измерений: {len(measurement_rows)}"
    )
