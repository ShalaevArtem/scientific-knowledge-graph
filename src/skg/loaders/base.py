"""Общая инфраструктура загрузчиков: provenance, батчи, отчёт о загрузке."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from neo4j import Session

BATCH_SIZE = 500


def provenance(source_file: Path | str, record_id: str, confidence: float = 1.0) -> dict[str, Any]:
    """Поля provenance для рёбер. Структурированные источники загружаются с confidence=1.0;
    факты, извлечённые LLM из текста (этап 2), придут со своей уверенностью."""
    return {
        "source": f"{Path(source_file).name}#{record_id}",
        "confidence": confidence,
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass
class LoadReport:
    source: str
    loaded: int = 0
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def reject(self, record_id: str, reason: str) -> None:
        self.rejected.append(f"{record_id}: {reason}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def render(self) -> str:
        lines = [f"[{self.source}] загружено записей: {self.loaded}"]
        if self.rejected:
            lines.append(f"  отклонено: {len(self.rejected)}")
            lines.extend(f"    - {r}" for r in self.rejected)
        if self.warnings:
            lines.append(f"  предупреждения: {len(self.warnings)}")
            lines.extend(f"    - {w}" for w in self.warnings)
        return "\n".join(lines)


def batched(rows: Iterable[dict[str, Any]], size: int = BATCH_SIZE) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def run_batched(session: Session, query: str, rows: Iterable[dict[str, Any]]) -> int:
    """Выполняет UNWIND-запрос батчами, возвращает число обработанных строк."""
    total = 0
    for batch in batched(rows):
        session.execute_write(lambda tx, b=batch: tx.run(query, rows=b).consume())
        total += len(batch)
    return total


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def split_list(raw: str | None, sep: str = "|") -> list[str]:
    """Разбирает списковое поле CSV вида 'a|b|c'."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(sep) if item.strip()]
