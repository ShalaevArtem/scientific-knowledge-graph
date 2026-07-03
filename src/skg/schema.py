"""Применение схемы: выполняет .cypher-файлы из каталога schema/ по порядку.

Все выражения идемпотентны (IF NOT EXISTS), так что команду можно гонять повторно.
"""

from __future__ import annotations

import re
from pathlib import Path

from neo4j import Session

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def split_statements(text: str) -> list[str]:
    """Режет cypher-файл на выражения по ';', выбрасывая строчные комментарии."""
    no_comments = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def apply_schema(session: Session, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    applied: list[str] = []
    for path in sorted(schema_dir.glob("*.cypher")):
        for statement in split_statements(path.read_text(encoding="utf-8")):
            session.run(statement).consume()
            first_line = statement.splitlines()[0]
            applied.append(f"{path.name}: {first_line}")
    return applied
