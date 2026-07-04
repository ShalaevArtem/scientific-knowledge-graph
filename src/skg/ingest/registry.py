"""Реестр корпуса: метаданные документов из структуры папок выгрузки кейса.

Ожидаемая структура: <корень>/Источники информации/<Категория>/...
Категории: Доклады, Журналы/<журнал>/<год>, Материалы конференций/<конференция>,
Обзоры, Статьи. Метаданные, которых нет в пути (год статьи, география, заголовок),
дополняются позже: заголовок — из свойств файла, остальное — LLM-извлечением.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .textract import SUPPORTED

CATEGORY_BY_FOLDER = {
    "Доклады": "доклад",
    "Журналы": "журнальная статья",
    "Материалы конференций": "материалы конференции",
    "Обзоры": "обзор",
    "Статьи": "статья",
}

YEAR_RE = re.compile(r"(?:19[5-9]\d|20[0-2]\d)")  # 1950–2029: номера вроде «PO 1901» — не годы
REPORT_AUTHOR_RE = re.compile(r"^Доклад_(.+?)\.(pdf|docx|pptx|docm|xlsx?|xlsm)$", re.IGNORECASE)


@dataclass
class CorpusDoc:
    doc_id: str
    path: Path
    rel_path: str
    category: str
    journal: str | None = None
    conference: str | None = None
    year: int | None = None
    authors: list[str] | None = None


def _doc_id(rel_path: str) -> str:
    return "D" + hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:12]


def _find_year(*names: str) -> int | None:
    for name in names:
        m = YEAR_RE.search(name)
        if m:
            return int(m.group(0))
    return None


def scan_corpus(root: Path) -> list[CorpusDoc]:
    """Обходит выгрузку, возвращает документы поддерживаемых форматов с метаданными пути."""
    docs: list[CorpusDoc] = []
    base = root / "Источники информации"
    if not base.exists():
        # выгрузка может содержать обёртку («Задача 2. …/Источники информации»)
        nested = sorted(root.glob("*/Источники информации"))
        base = nested[0] if nested else root  # либо указана сразу папка с категориями
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED:
            continue
        rel = path.relative_to(base)
        parts = rel.parts
        top = parts[0] if parts else ""
        category = CATEGORY_BY_FOLDER.get(top)
        if category is None:
            continue
        doc = CorpusDoc(
            doc_id=_doc_id(str(rel)),
            path=path,
            rel_path=str(rel).replace("\\", "/"),
            category=category,
        )
        if top == "Журналы" and len(parts) >= 3:
            doc.journal = parts[1]
            doc.year = _find_year(parts[2])
        elif top == "Материалы конференций":
            if len(parts) >= 3:
                doc.conference = parts[1]
            doc.year = _find_year(*parts[1:])
        else:
            doc.year = _find_year(path.stem)
        m = REPORT_AUTHOR_RE.match(path.name)
        if m:
            doc.authors = [m.group(1).replace("_", " ").strip()]
        docs.append(doc)
    return docs


def detect_language(text: str, sample: int = 4000) -> str:
    """Грубая, но достаточная эвристика: доля кириллицы в начале текста."""
    head = text[:sample]
    cyr = sum(1 for ch in head if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    alpha = sum(1 for ch in head if ch.isalpha())
    if alpha == 0:
        return "unknown"
    return "ru" if cyr / alpha > 0.4 else "en"
