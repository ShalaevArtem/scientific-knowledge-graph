"""Полное LLM-извлечение по приоритету категорий (обзоры → статьи → доклады →
конференции → журналы). Возобновляемо: обработанные чанки пропускаются.

Запуск: .venv\\Scripts\\python infra\\extract_all.py [workers]
"""

from __future__ import annotations

import sys

from skg.db import session_scope
from skg.extract.extractor import extract_chunks
from skg.extract.llm import get_llm

ORDER = ["обзор", "статья", "доклад", "материалы конференции", "журнальная статья"]

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    llm = get_llm()
    for cat in ORDER:
        print(f"=== категория: {cat} ===", flush=True)
        with session_scope() as session:
            report = extract_chunks(
                session, llm, limit=999_999, category=cat, workers=workers
            )
        print(report.render(), flush=True)
    print("=== извлечение завершено ===", flush=True)
