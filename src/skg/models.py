"""Pydantic-модели входных записей структурированных источников.

Здесь же — обязательность полей и доменные проверки (диапазоны температур,
уверенность 0..1), потому что Neo4j Community не поддерживает NOT NULL-ограничения.
Правило системы «нет источника — нет факта» обеспечивают загрузчики: каждое ребро
получает поля source / record / confidence / loaded_at (см. loaders/base.py).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_name(name: str) -> str:
    """Ключ нормализации марок материалов: «Ti-6Al-4V», «ti 6al 4v» и «TI6AL4V» совпадают."""
    return re.sub(r"[\s\-_/]+", "", name).casefold().replace("ё", "е")


class MaterialRecord(BaseModel):
    id: str
    grade: str
    synonyms: list[str] = Field(default_factory=list)
    composition: dict[str, float | str] = Field(default_factory=dict)

    @property
    def lookup_keys(self) -> set[str]:
        return {normalize_name(self.grade), *(normalize_name(s) for s in self.synonyms)}


class EquipmentRecord(BaseModel):
    id: str
    type: str
    lab: str


class PersonRecord(BaseModel):
    id: str
    name: str
    lab: str


class PropertyRecord(BaseModel):
    name: str
    category: str


class DocumentRecord(BaseModel):
    id: str
    doc_type: str
    date: date
    title: str
    authors: list[str] = Field(default_factory=list)


class RegimeSpec(BaseModel):
    type: str
    temp_min: float | None = None
    temp_max: float | None = None
    duration_h: float | None = None
    medium: str | None = None
    params: dict[str, float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_temp_range(self) -> "RegimeSpec":
        if self.temp_min is not None and self.temp_max is not None:
            if self.temp_min > self.temp_max:
                raise ValueError(f"temp_min ({self.temp_min}) > temp_max ({self.temp_max})")
        return self

    @property
    def key(self) -> str:
        """Детерминированный ключ: одинаковые режимы схлопываются в один узел графа."""
        canonical = json.dumps(
            {
                "type": self.type.strip().casefold(),
                "temp_min": self.temp_min,
                "temp_max": self.temp_max,
                "duration_h": self.duration_h,
                "medium": (self.medium or "").strip().casefold() or None,
                "params": self.params,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


class MeasurementSpec(BaseModel):
    property: str
    value: float
    unit: str
    method: str
    uncertainty: float | None = None


class ConclusionRelation(BaseModel):
    type: Literal["supports", "contradicts"]
    target: str  # id вывода, на который ссылаемся


class ConclusionSpec(BaseModel):
    id: str | None = None  # если не задан, загрузчик сгенерирует "<exp_id>:C<n>"
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    date: date
    doc_id: str | None = None  # документ, где вывод описан (ребро DESCRIBED_IN)
    fragment: str | None = None  # цитата/локатор фрагмента внутри документа
    relates: list[ConclusionRelation] = Field(default_factory=list)


class ExperimentRecord(BaseModel):
    id: str
    date: date
    goal: str
    status: str
    material: str  # марка или синоним — нормализуется по справочнику материалов
    equipment_id: str
    regime: RegimeSpec
    measurements: list[MeasurementSpec] = Field(default_factory=list)
    conclusions: list[ConclusionSpec] = Field(default_factory=list)

    @field_validator("material", "equipment_id")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("пустое значение")
        return v.strip()
