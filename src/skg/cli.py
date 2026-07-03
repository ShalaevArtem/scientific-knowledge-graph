"""CLI: skg ping | skg schema | skg load <kind> <path> | skg load-all <dir> | skg stats."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import session_scope
from .loaders import (
    load_documents,
    load_equipment,
    load_experiments,
    load_materials,
    load_properties,
    load_staff,
)
from .schema import apply_schema

LOADERS = {
    "materials": (load_materials, "materials.csv"),
    "equipment": (load_equipment, "equipment.csv"),
    "staff": (load_staff, "staff.csv"),
    "properties": (load_properties, "properties.csv"),
    "documents": (load_documents, "documents.csv"),
    "experiments": (load_experiments, "experiments.jsonl"),
}

# Порядок важен: эксперименты ссылаются на справочники, документы — на сотрудников
LOAD_ORDER = ["materials", "equipment", "staff", "properties", "documents", "experiments"]


def cmd_ping(_: argparse.Namespace) -> int:
    with session_scope() as session:
        value = session.run("RETURN 1 AS ok").single()["ok"]
    print(f"Neo4j доступен (RETURN 1 -> {value})")
    return 0


def cmd_schema(_: argparse.Namespace) -> int:
    with session_scope() as session:
        applied = apply_schema(session)
    print(f"Применено выражений схемы: {len(applied)}")
    for line in applied:
        print(f"  {line}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    loader, _ = LOADERS[args.kind]
    with session_scope() as session:
        report = loader(session, Path(args.path))
    print(report.render())
    return 1 if report.rejected else 0


def cmd_load_all(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    exit_code = 0
    with session_scope() as session:
        for kind in LOAD_ORDER:
            loader, filename = LOADERS[kind]
            path = directory / filename
            if not path.exists():
                print(f"[{filename}] нет файла — пропущено")
                continue
            report = loader(session, path)
            print(report.render())
            if report.rejected:
                exit_code = 1
    return exit_code


def cmd_seed(args: argparse.Namespace) -> int:
    from .loaders.seed import load_seed

    with session_scope() as session:
        report = load_seed(session, Path(args.dir))
    print(report.render())
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from .extract.extractor import extract_chunks
    from .extract.llm import get_llm

    llm = get_llm()
    with session_scope() as session:
        report = extract_chunks(
            session, llm, limit=args.limit, doc_id=args.doc,
            category=args.category, workers=args.workers,
        )
    print(report.render())
    return 1 if report.rejected else 0


def cmd_wipe(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Полная очистка графа. Подтвердите флагом --yes")
        return 1
    with session_scope() as session:
        while True:
            deleted = session.run(
                "MATCH (n) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS n"
            ).single()["n"]
            if deleted == 0:
                break
            print(f"  удалено узлов: {deleted}")
    print("Граф пуст. Схема (constraints/indexes) сохранена.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .loaders.corpus import load_corpus

    with session_scope() as session:
        report = load_corpus(
            session, Path(args.dir), limit=args.limit, category=args.category,
            resume=args.resume,
        )
    print(report.render())
    return 1 if report.rejected else 0


def cmd_embed(args: argparse.Namespace) -> int:
    from .embeddings import embed_missing

    with session_scope() as session:
        total = embed_missing(session, max_total=args.limit)
    print(f"Готово: эмбеддинги посчитаны для {total} чанков")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from .query.search import hybrid_search

    with session_scope() as session:
        hits = hybrid_search(
            session,
            args.query,
            k=args.k,
            category=args.category,
            language=args.language,
            year_from=args.year_from,
        )
    for h in hits:
        loc = f"{h['unit_kind']} {h['unit_no']}"
        print(f"[{h['rrf_score']:.4f}] {h['title']} ({h['category']}, {h['year'] or '—'}, {loc})")
        print(f"        {h['text'][:220].replace(chr(10), ' ')}…")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("skg.web.app:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    with session_scope() as session:
        print("Узлы:")
        for rec in session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"
        ):
            print(f"  {rec['label']}: {rec['n']}")
        print("Связи:")
        for rec in session.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type"
        ):
            print(f"  {rec['type']}: {rec['n']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # консоль Windows по умолчанию не UTF-8, а вывод у нас русскоязычный
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="skg", description="Граф знаний по исследованиям")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="проверить подключение к Neo4j").set_defaults(func=cmd_ping)
    sub.add_parser("schema", help="применить ограничения и индексы").set_defaults(func=cmd_schema)

    p_load = sub.add_parser("load", help="загрузить один источник")
    p_load.add_argument("kind", choices=LOADERS.keys())
    p_load.add_argument("path")
    p_load.set_defaults(func=cmd_load)

    p_all = sub.add_parser("load-all", help="загрузить все источники из каталога")
    p_all.add_argument("dir")
    p_all.set_defaults(func=cmd_load_all)

    p_seed = sub.add_parser("seed", help="загрузить seed-справочники доменных терминов")
    p_seed.add_argument("dir", nargs="?", default="data/seed")
    p_seed.set_defaults(func=cmd_seed)

    p_extract = sub.add_parser("extract", help="LLM-извлечение сущностей из чанков")
    p_extract.add_argument("--limit", type=int, default=100)
    p_extract.add_argument("--doc", default=None, help="только один документ (id)")
    p_extract.add_argument("--category", default=None, help="только одна категория")
    p_extract.add_argument("--workers", type=int, default=1, help="параллельные LLM-вызовы")
    p_extract.set_defaults(func=cmd_extract)

    p_wipe = sub.add_parser("wipe", help="полная очистка графа (данные, не схема)")
    p_wipe.add_argument("--yes", action="store_true")
    p_wipe.set_defaults(func=cmd_wipe)

    p_ingest = sub.add_parser("ingest", help="загрузить корпус документов (Document+Chunk)")
    p_ingest.add_argument("dir")
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.add_argument("--category", default=None)
    p_ingest.add_argument("--resume", action="store_true",
                          help="пропустить документы, уже загруженные в граф")
    p_ingest.set_defaults(func=cmd_ingest)

    p_embed = sub.add_parser("embed", help="посчитать эмбеддинги для чанков без них")
    p_embed.add_argument("--limit", type=int, default=None,
                         help="максимум чанков за прогон (возобновляемо)")
    p_embed.set_defaults(func=cmd_embed)

    p_search = sub.add_parser("search", help="семантический поиск по корпусу")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=8)
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--language", default=None)
    p_search.add_argument("--year-from", type=int, default=None, dest="year_from")
    p_search.set_defaults(func=cmd_search)

    p_serve = sub.add_parser("serve", help="запустить веб-интерфейс")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("stats", help="счётчики узлов и связей").set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
