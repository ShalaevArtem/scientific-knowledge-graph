"""Настройки подключения: читаются из окружения / .env в корне проекта."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str


def get_settings() -> Settings:
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD не задан. Скопируйте .env.example в .env и заполните."
        )
    return Settings(
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=password,
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )
