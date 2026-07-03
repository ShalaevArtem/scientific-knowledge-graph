"""Подключение к Neo4j."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import Driver, GraphDatabase, Session

from .config import Settings, get_settings


def create_driver(settings: Settings | None = None) -> Driver:
    settings = settings or get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    driver.verify_connectivity()
    return driver


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    settings = settings or get_settings()
    driver = create_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            yield session
    finally:
        driver.close()
