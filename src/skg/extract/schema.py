"""Pydantic-схемы результата LLM-извлечения из чанка документа.

Схема сознательно плоская и «упоминание-центричная»: из литературы надёжно
извлекаются упоминания сущностей, численные результаты с условиями и выводы.
Полноценные Experiment-узлы создаются только из структурированных протоколов.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

# «200-240», «60–65» (не путать дефис диапазона с минусом «-0,25»)
_RANGE_RE = re.compile(
    r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*[–—-]\s*(\d+(?:[.,]\d+)?)\s*(.*)$"
)
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_numeric(value) -> tuple[float, float | None, str | None]:
    """«60°C» → (60, None, "°C"); «200-240» → (200, 240, None); «-0,25 В» → (-0.25, None, "В").
    Не число — ValueError: измерение отбрасывается, значения не выдумываем."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), None, None
    s = str(value).strip().replace("−", "-")
    m = _RANGE_RE.match(s)
    if m:
        return _to_float(m.group(1)), _to_float(m.group(2)), m.group(3).strip() or None
    m = _NUM_RE.search(s)
    if not m:
        raise ValueError(f"не число: {value!r}")
    tail = s[m.end():].strip(" .;:")
    return _to_float(m.group(0)), None, tail or None


def _coerce_mention_list(v):
    """Слабые модели отдают списки строк вместо объектов — принимаем оба вида."""
    if isinstance(v, list):
        return [{"name": item} if isinstance(item, str) else item for item in v]
    return v


def _coerce_facility_list(v):
    """«Nikkelverk (Норвегия)» → name + country из скобок."""
    if not isinstance(v, list):
        return v
    out = []
    for item in v:
        if isinstance(item, str):
            m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", item.strip())
            out.append(
                {"name": m.group(1), "country": m.group(2)} if m else {"name": item}
            )
        else:
            out.append(item)
    return out


class XMention(BaseModel):
    name: str
    name_en: str | None = None  # английский эквивалент, если упомянут рядом

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("пустое имя")
        return v


class XFacility(XMention):
    country: str | None = None


class XCondition(BaseModel):
    """Числовое ограничение/условие: «сульфаты ≤ 300 мг/л», «t = 60 °C»."""

    parameter: str
    op: str = "="  # = | <= | >= | < | > | range
    value: float | None = None
    value_max: float | None = None  # для диапазонов
    unit: str | None = None
    raw: str | None = None  # исходная формулировка, если структуризация неточна

    @model_validator(mode="before")
    @classmethod
    def coerce_value(cls, data):
        if isinstance(data, dict) and isinstance(data.get("value"), str):
            raw = data["value"]
            try:
                v, vmax, unit_tail = parse_numeric(raw)
            except ValueError:
                data["raw"] = raw
                data["value"] = None
                return data
            data["value"] = v
            if vmax is not None:
                data.setdefault("value_max", vmax)
                data.setdefault("op", "range")
            if unit_tail and not data.get("unit"):
                data["unit"] = unit_tail
        return data


class XMeasurement(BaseModel):
    """Численный результат: «извлечение никеля 95,5 %»."""

    property: str
    value: float
    value_max: float | None = None  # «200–240 А/м²» — диапазон
    unit: str | None = None
    conditions: list[XCondition] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_value(cls, data):
        if isinstance(data, dict):
            if isinstance(data.get("value"), str):
                v, vmax, unit_tail = parse_numeric(data["value"])  # ValueError → отброс
                data["value"] = v
                if vmax is not None:
                    data.setdefault("value_max", vmax)
                if unit_tail and not data.get("unit"):
                    data["unit"] = unit_tail
            if isinstance(data.get("value_max"), str):
                try:
                    v, vmax, _ = parse_numeric(data["value_max"])
                    data["value_max"] = vmax if vmax is not None else v
                except ValueError:
                    data["value_max"] = None
            if not isinstance(data.get("conditions"), list):
                data["conditions"] = []  # None или {} от слабых моделей
        return data


class XConclusion(BaseModel):
    """Вывод/рекомендация с привязкой к сущностям и географии."""

    text: str
    materials: list[str] = Field(default_factory=list)
    processes: list[str] = Field(default_factory=list)
    geography: str | None = None  # "РФ" | "зарубежная" | конкретная страна
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class ChunkExtraction(BaseModel):
    materials: list[XMention] = Field(default_factory=list)
    processes: list[XMention] = Field(default_factory=list)
    equipment: list[XMention] = Field(default_factory=list)
    facilities: list[XFacility] = Field(default_factory=list)
    measurements: list[XMeasurement] = Field(default_factory=list)
    conclusions: list[XConclusion] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)  # только с титульных фрагментов

    @field_validator("materials", "processes", "equipment", mode="before")
    @classmethod
    def strings_to_mentions(cls, v):
        return _coerce_mention_list(v)

    @field_validator("facilities", mode="before")
    @classmethod
    def strings_to_facilities(cls, v):
        return _coerce_facility_list(v)

    @field_validator("conclusions", mode="before")
    @classmethod
    def strings_to_conclusions(cls, v):
        if isinstance(v, list):
            return [{"text": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("measurements", mode="before")
    @classmethod
    def drop_unparsable_measurements(cls, v):
        """Измерение с непарсимым числом отбрасывается целиком (не выдумываем),
        остальные элементы списка сохраняются."""
        if not isinstance(v, list):
            return v
        kept = []
        for item in v:
            if isinstance(item, dict):
                if item.get("value") is None:  # «измерение» без числа — не измерение
                    continue
                try:
                    parse_numeric(item["value"])
                except (ValueError, TypeError):
                    continue
            kept.append(item)
        return kept

    @field_validator("authors", mode="before")
    @classmethod
    def authors_to_strings(cls, v):
        if isinstance(v, list):
            return [
                item.get("name", "") if isinstance(item, dict) else item for item in v
            ]
        return v
