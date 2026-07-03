"""Извлечение текста из PDF / DOCX / DOCM / PPTX.

Возвращает список юнитов (страница, слайд или блок абзацев) с их текстом —
номер юнита попадает в provenance чанка, чтобы цитату можно было найти в оригинале.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".docm", ".pptx"}


@dataclass
class TextUnit:
    unit_no: int  # страница PDF, номер слайда, порядковый блок DOCX
    unit_kind: str  # "page" | "slide" | "block"
    text: str


class ExtractionError(Exception):
    pass


def extract_text(path: Path) -> list[TextUnit]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix in (".docx", ".docm"):
            return _extract_docx(path)
        if suffix == ".pptx":
            return _extract_pptx(path)
    except Exception as exc:  # повреждённые файлы в корпусе не должны валить пайплайн
        raise ExtractionError(f"{path.name}: {exc}") from exc
    raise ExtractionError(f"{path.name}: неподдерживаемый формат {suffix}")


def _extract_pdf(path: Path) -> list[TextUnit]:
    import fitz  # pymupdf

    units = []
    with fitz.open(path) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                units.append(TextUnit(page_no, "page", text))
    return units


def _open_word(path: Path):
    """Открывает docx/docm. python-docx отказывает macro-enabled документам по
    content-type — подменяем тип в [Content_Types].xml на обычный (макросы не нужны,
    текст тот же)."""
    import docx

    try:
        return docx.Document(str(path))
    except ValueError:
        import io
        import zipfile

        src = zipfile.ZipFile(path)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as dst:
            for item in src.infolist():
                data = src.read(item)
                if item.filename == "[Content_Types].xml":
                    data = data.replace(
                        b"application/vnd.ms-word.document.macroEnabled.main+xml",
                        b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                    )
                dst.writestr(item, data)
        buf.seek(0)
        return docx.Document(buf)


def _extract_docx(path: Path, block_paragraphs: int = 40) -> list[TextUnit]:
    doc = _open_word(path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    # таблицы тоже несут данные (составы, параметры) — добавляем построчно
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    units = []
    for i in range(0, len(paragraphs), block_paragraphs):
        block = "\n".join(paragraphs[i : i + block_paragraphs])
        units.append(TextUnit(i // block_paragraphs + 1, "block", block))
    return units


def _extract_pptx(path: Path) -> list[TextUnit]:
    from pptx import Presentation

    prs = Presentation(str(path))
    units = []
    for slide_no, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    texts.append(t)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        texts.append(" | ".join(cells))
        if texts:
            units.append(TextUnit(slide_no, "slide", "\n".join(texts)))
    return units
