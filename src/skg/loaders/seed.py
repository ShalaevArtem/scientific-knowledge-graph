"""Загрузка seed-справочников доменных терминов (data/seed/*.csv).

Создаёт канонические узлы Material / Process / Equipment с RU/EN-синонимами —
базу нормализации для LLM-извлечения. Эти узлы верифицированы (needs_review = false),
в отличие от сущностей, впервые встреченных в текстах.
"""

from __future__ import annotations

from pathlib import Path

from neo4j import Session

from ..models import normalize_name
from .base import LoadReport, read_csv, run_batched, split_list

MERGE_SEED_TPL = """
UNWIND $rows AS row
MERGE (n:{label} {{id: row.id}})
SET n.name = row.name, n.name_en = row.name_en, n.synonyms = row.synonyms,
    n.{extra_prop} = row.extra, n.needs_review = false
"""

SEED_FILES = {
    "materials.csv": ("Material", "kind"),
    "processes.csv": ("Process", "category"),
    "equipment.csv": ("Equipment", "type"),
}


def load_seed(session: Session, seed_dir: Path) -> LoadReport:
    report = LoadReport(source=str(seed_dir))
    for filename, (label, extra_prop) in SEED_FILES.items():
        path = seed_dir / filename
        if not path.exists():
            report.warn(f"{filename}: файла нет — пропущено")
            continue
        rows = []
        for raw in read_csv(path):
            name = raw["name"].strip()
            if not name:
                continue
            synonyms = split_list(raw.get("synonyms"))
            name_en = (raw.get("name_en") or "").strip() or None
            rows.append(
                {
                    "id": f"{label[0]}:{normalize_name(name)}",
                    "name": name,
                    "name_en": name_en,
                    "synonyms": [name] + ([name_en] if name_en else []) + synonyms,
                    "extra": (raw.get(extra_prop) or "").strip() or None,
                }
            )
        count = run_batched(session, MERGE_SEED_TPL.format(label=label, extra_prop=extra_prop), rows)
        report.loaded += count
        report.warn(f"{filename}: {count} канонических узлов {label}")
    return report
