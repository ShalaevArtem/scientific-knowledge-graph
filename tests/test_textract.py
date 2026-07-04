from __future__ import annotations

from pathlib import Path

import pytest

from skg.ingest import textract


def _make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Опыты"
    ws.append(["материал", "температура, C", "выход, %"])
    ws.append(["никель", 65, 94.0])
    ws.append([None, None, None])  # пустая строка отбрасывается
    ws.append(["медь", 80, 88.5])
    wb.save(str(path))


def test_xlsx_extraction_sheet_unit_and_content(tmp_path: Path):
    xlsx = tmp_path / "catalog.xlsx"
    _make_xlsx(xlsx)

    units = textract.extract_text(xlsx)

    assert len(units) == 1
    unit = units[0]
    assert unit.unit_kind == "sheet"
    assert unit.unit_no == 1
    # имя листа как заголовок делает лист находимым по названию
    assert "Опыты" in unit.text
    # целый float печатается без ".0", дробный сохраняется
    assert "94" in unit.text and "94.0" not in unit.text
    assert "88.5" in unit.text
    # пустая строка не попадает
    assert unit.text.count("\n") == 3  # заголовок + 3 непустые строки


def test_xls_and_xlsx_are_supported():
    assert {".xls", ".xlsx", ".xlsm"} <= textract.SUPPORTED


def test_images_supported_but_skipped_without_ocr(tmp_path: Path, monkeypatch):
    from PIL import Image

    img = tmp_path / "scan.png"
    Image.new("RGB", (32, 32), "white").save(str(img))

    monkeypatch.delenv("SKG_OCR", raising=False)
    assert not textract.ocr_enabled()
    # без OCR изображение поддерживается форматом, но текста не даёт (пустой список)
    assert textract.extract_text(img) == []


def test_ocr_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("SKG_OCR", "1")
    assert textract.ocr_enabled()
    monkeypatch.setenv("SKG_OCR", "off")
    assert not textract.ocr_enabled()
