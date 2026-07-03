"""Загрузка корпуса документов в граф: Document + Chunk (+ авторы докладов).

Потоковая: документы обрабатываются и пишутся порциями, чтобы корпус любого
размера не собирался в памяти. Заголовок — из свойств файла (docx/pptx core
properties, PDF metadata), иначе имя файла. Язык — эвристикой по кириллице.
Идемпотентно (MERGE по id), повторный прогон обновляет метаданные.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from neo4j import Session

from ..ingest.chunker import chunk_units
from ..ingest.registry import CorpusDoc, detect_language, scan_corpus
from ..ingest.textract import ExtractionError, extract_text
from ..models import normalize_name
from .base import LoadReport, provenance, run_batched

FLUSH_EVERY_DOCS = 20

MERGE_CORPUS_DOC = """
UNWIND $rows AS row
MERGE (d:Document {id: row.id})
SET d.title = row.title, d.category = row.category, d.doc_type = row.category,
    d.journal = row.journal, d.conference = row.conference,
    d.year = row.year, d.language = row.language,
    d.rel_path = row.rel_path, d.units = row.units, d.chars = row.chars
"""

MERGE_CHUNKS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (c:Chunk {id: row.id})
SET c.seq = row.seq, c.unit_no = row.unit_no, c.unit_kind = row.unit_kind,
    c.text = row.text, c.doc_id = row.doc_id
MERGE (d)-[:HAS_CHUNK]->(c)
"""

MERGE_DOC_AUTHORS = """
UNWIND $rows AS row
MATCH (d:Document {id: row.doc_id})
MERGE (p:Person {id: row.person_id})
ON CREATE SET p.name = row.name
MERGE (d)-[a:AUTHORED_BY]->(p)
SET a += row.prov
"""


def _file_title(path: Path) -> str | None:
    try:
        suffix = path.suffix.lower()
        if suffix in (".docx", ".docm"):
            import docx

            t = docx.Document(str(path)).core_properties.title
        elif suffix == ".pptx":
            from pptx import Presentation

            t = Presentation(str(path)).core_properties.title
        elif suffix == ".pdf":
            import fitz

            with fitz.open(path) as doc:
                t = doc.metadata.get("title")
        else:
            return None
    except Exception:
        return None
    t = (t or "").strip()
    # артефакты конвертации Word→PDF и расширения в заголовке
    t = re.sub(r"(?i)^microsoft (word|powerpoint) - ", "", t)
    t = re.sub(r"(?i)\.(docx?|docm|pdf|pptx?)$", "", t).strip()
    # мусорные заголовки из шаблонов офиса не берём
    if len(t) < 8 or re.fullmatch(r"(?i)(presentation|document|слайд|титул).*", t):
        return None
    return t


class _Buffers:
    def __init__(self, session: Session):
        self.session = session
        self.docs: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.authors: list[dict[str, Any]] = []

    def flush(self) -> None:
        run_batched(self.session, MERGE_CORPUS_DOC, self.docs)
        run_batched(self.session, MERGE_CHUNKS, self.chunks)
        run_batched(self.session, MERGE_DOC_AUTHORS, self.authors)
        self.docs, self.chunks, self.authors = [], [], []


def _process_doc(doc: CorpusDoc, buffers: _Buffers, report: LoadReport) -> int:
    """Извлекает текст документа в буферы; возвращает число чанков."""
    try:
        units = extract_text(doc.path)
    except ExtractionError as exc:
        report.reject(doc.rel_path, str(exc))
        return 0
    if not units:
        report.reject(doc.rel_path, "текст не извлечён (возможно, скан без текстового слоя)")
        return 0
    chunks = chunk_units(doc.doc_id, units)
    buffers.docs.append(
        {
            "id": doc.doc_id,
            "title": _file_title(doc.path) or doc.path.stem,
            "category": doc.category,
            "journal": doc.journal,
            "conference": doc.conference,
            "year": doc.year,
            "language": detect_language(units[0].text),
            "rel_path": doc.rel_path,
            "units": len(units),
            "chars": sum(len(u.text) for u in units),
        }
    )
    buffers.chunks.extend(
        {
            "id": c.chunk_id,
            "doc_id": c.doc_id,
            "seq": c.seq,
            "unit_no": c.unit_no,
            "unit_kind": c.unit_kind,
            "text": c.text,
        }
        for c in chunks
    )
    for author in doc.authors or []:
        buffers.authors.append(
            {
                "doc_id": doc.doc_id,
                "person_id": "P:" + normalize_name(author),
                "name": author,
                "prov": provenance(doc.rel_path, doc.doc_id),
            }
        )
    return len(chunks)


def load_corpus(
    session: Session,
    root: Path,
    limit: int | None = None,
    category: str | None = None,
    resume: bool = False,
) -> LoadReport:
    report = LoadReport(source=str(root))
    docs = scan_corpus(root)
    if category:
        docs = [d for d in docs if d.category == category]
    if resume:
        loaded_ids = {
            r["id"] for r in session.run("MATCH (d:Document) RETURN d.id AS id")
        }
        skipped = len([d for d in docs if d.doc_id in loaded_ids])
        docs = [d for d in docs if d.doc_id not in loaded_ids]
        print(f"resume: пропущено уже загруженных: {skipped}")
    if limit:
        docs = docs[:limit]
    print(f"к обработке: {len(docs)} документов")

    buffers = _Buffers(session)
    total_chunks = 0
    for i, doc in enumerate(docs, start=1):
        n = _process_doc(doc, buffers, report)
        if n:
            report.loaded += 1
            total_chunks += n
        if i % FLUSH_EVERY_DOCS == 0:
            buffers.flush()
            print(f"  {i}/{len(docs)} документов, чанков: {total_chunks}", flush=True)
    buffers.flush()
    report.warn(f"чанков создано: {total_chunks}")
    return report
