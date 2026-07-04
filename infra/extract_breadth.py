"""Широтное LLM-извлечение под лимит времени: ~N чанков с КАЖДОГО документа,
равномерно по всему объёму тома (stride), а не подряд с начала. Цель — за
ограниченное время (общая квота Yandex) представить в графе как можно больше
документов, зацепив аннотации/выводы разных работ внутри сшитых томов.

Приоритет категорий: журнальные статьи (0 извлечено) → материалы конференции.
Возобновляемо: берутся только чанки без extracted_at; перезапуск безопасен.

Запуск: .venv\\Scripts\\python infra\\extract_breadth.py [per_doc=20] [workers=6]
"""

from __future__ import annotations

import sys
import time

from skg.db import session_scope
from skg.extract.extractor import extract_chunks
from skg.extract.llm import get_llm

ORDER = ["журнальная статья", "материалы конференции"]


def stride_sample(ids: list[str], n: int) -> list[str]:
    """Равномерная выборка n элементов из отсортированного по seq списка ids."""
    if len(ids) <= n:
        return ids
    step = (len(ids) - 1) / (n - 1)
    return [ids[round(i * step)] for i in range(n)]


def sample_category(session, category: str, per_doc: int) -> list[str]:
    """Собирает stride-выборку непройденных чанков по всем документам категории."""
    rows = session.run(
        """
        MATCH (d:Document {category: $category})-[:HAS_CHUNK]->(c:Chunk)
        WHERE c.extracted_at IS NULL
        WITH d, c ORDER BY c.seq
        RETURN d.id AS doc, collect(c.id) AS ids
        """,
        category=category,
    ).data()
    picked: list[str] = []
    for r in rows:
        picked.extend(stride_sample(r["ids"], per_doc))
    return picked


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    per_doc = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    llm = get_llm()
    print(f"=== широтное извлечение: per_doc={per_doc}, workers={workers} ===", flush=True)

    for cat in ORDER:
        t0 = time.time()
        with session_scope() as session:
            ids = sample_category(session, cat, per_doc)
            print(f"=== {cat}: выбрано {len(ids)} чанков ===", flush=True)
            if not ids:
                continue
            report = extract_chunks(
                session, llm, limit=len(ids) + 1, category=cat,
                workers=workers, chunk_ids=ids,
            )
        dt = time.time() - t0
        print(report.render(), flush=True)
        print(f"=== {cat}: готово за {dt/60:.1f} мин ===", flush=True)
    print("=== широтное извлечение завершено ===", flush=True)
