"""Ответ на вопрос естественным языком: разбор → гибридный поиск → синтез с цитатами.

Без LLM-ключа слой деградирует до гибридного поиска: пользователь получает
релевантные фрагменты с источниками, но без сводки. С ключом:
1) вопрос разбирается в структурированные фильтры (период, категория, язык);
2) поверх найденных фрагментов синтезируется сводка, каждое утверждение — с [n]-цитатой;
3) модель обязана отмечать противоречия источников и разделять практику РФ/мира.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from neo4j import Session

from ..extract.llm import LLMClient, LLMError
from .search import attach_doc_entities, hybrid_search

PARSE_PROMPT_TEMPLATE = """Разбери вопрос к базе знаний горно-металлургического НИИ в JSON-фильтры:
{{
  "query": "переформулированный поисковый запрос (термины, без вопросительных слов)",
  "year_from": null | int, "year_to": null | int,
  "language": null | "ru" | "en",
  "category": null | "статья" | "обзор" | "доклад" | "журнальная статья" | "материалы конференции",
  "geography": null | "РФ" | "мир"
}}
Только явные ограничения из вопроса; сомневаешься — null. «за последние N лет» считай от {current_year} года.
Ответ — только JSON."""

SYNTH_PROMPT = """Ты — аналитик горно-металлургического НИИ. Ответь на вопрос по фрагментам
внутренней базы знаний. Правила:
- Каждое фактическое утверждение подкрепляй ссылкой [n] на фрагмент. Без ссылки — не утверждай.
- Числа (концентрации, температуры, скорости) приводи точно как в источнике, с единицами.
- Если источники расходятся — покажи расхождение явно («источник [2] утверждает…, тогда как [5]…»).
- Различай отечественную и зарубежную практику, где это видно из источников.
- Если фрагментов недостаточно для ответа — скажи прямо, что данных мало, и что нашлось рядом.
- Структура: краткий вывод (2–3 предложения), затем детали по пунктам, в конце — «Пробелы/что уточнить».
Пиши по-русски, компактно."""


def _parse_prompt(current_year: int | None = None) -> str:
    return PARSE_PROMPT_TEMPLATE.format(current_year=current_year or date.today().year)


def parse_question(
    llm: LLMClient, question: str, current_year: int | None = None
) -> dict[str, Any]:
    try:
        parsed = llm.complete_json(_parse_prompt(current_year), question, max_tokens=500)
    except LLMError:
        return {"query": question}
    return {
        "query": parsed.get("query") or question,
        "year_from": parsed.get("year_from"),
        "year_to": parsed.get("year_to"),
        "language": parsed.get("language"),
        "category": parsed.get("category"),
        "geography": parsed.get("geography"),
    }


def _format_fragments(hits: list[dict[str, Any]]) -> str:
    blocks = []
    for i, h in enumerate(hits, start=1):
        src = h["journal"] or h["conference"] or h["category"]
        year = f", {h['year']}" if h["year"] else ""
        title = h.get("display_title") or h["title"]
        blocks.append(
            f"[{i}] «{title}» ({src}{year}; {h['unit_kind']} {h['unit_no']})\n{h['text']}"
        )
    return "\n\n".join(blocks)


def answer_question(
    session: Session,
    question: str,
    llm: LLMClient | None = None,
    k: int = 10,
    category: str | None = None,
    language: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"query": question}
    if llm is not None:
        filters = parse_question(llm, question)
    if category:
        filters["category"] = category
    if language:
        filters["language"] = language
    if year_from is not None:
        filters["year_from"] = year_from
    if year_to is not None:
        filters["year_to"] = year_to

    hits = hybrid_search(
        session,
        filters["query"],
        k=k,
        category=filters.get("category"),
        language=filters.get("language"),
        year_from=filters.get("year_from"),
        year_to=filters.get("year_to"),
    )
    attach_doc_entities(session, hits)

    result: dict[str, Any] = {
        "question": question,
        "filters": {k_: v for k_, v in filters.items() if v},
        "citations": hits,
        "answer": None,
    }
    if llm is None or not hits:
        return result

    user = (
        f"Вопрос: {question}\n"
        + (f"Фильтры запроса: {json.dumps(result['filters'], ensure_ascii=False)}\n" if result["filters"] else "")
        + f"\nФрагменты базы знаний:\n\n{_format_fragments(hits)}"
    )
    try:
        result["answer"] = llm.complete_text(SYNTH_PROMPT, user, max_tokens=2000)
    except LLMError as exc:
        result["answer_error"] = str(exc)
    return result
