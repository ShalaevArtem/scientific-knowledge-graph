"""Чанкинг текста под семантический поиск и provenance.

Чанк ссылается на юнит документа (страницу/слайд/блок) — этого достаточно,
чтобы человек нашёл цитату в оригинале. Размер ~1200 символов согласован
с лимитом эмбеддинг-модели (512 токенов, см. config).
"""

from __future__ import annotations

from dataclasses import dataclass

from .textract import TextUnit

CHUNK_CHARS = 1200
OVERLAP_CHARS = 150


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    seq: int  # сквозной порядок в документе
    unit_no: int
    unit_kind: str
    text: str


def _split_long(text: str, limit: int, overlap: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            # стараемся резать по границе предложения/абзаца
            cut = max(text.rfind("\n", start + limit // 2, end), text.rfind(". ", start + limit // 2, end))
            if cut > start:
                end = cut + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


def chunk_units(doc_id: str, units: list[TextUnit]) -> list[Chunk]:
    chunks: list[Chunk] = []
    seq = 0
    for unit in units:
        for piece in _split_long(unit.text, CHUNK_CHARS, OVERLAP_CHARS):
            seq += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}:{seq}",
                    doc_id=doc_id,
                    seq=seq,
                    unit_no=unit.unit_no,
                    unit_kind=unit.unit_kind,
                    text=piece,
                )
            )
    return chunks
