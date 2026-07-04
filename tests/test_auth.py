"""Юнит-тесты RBAC и аудита (без сервера и Neo4j)."""

from __future__ import annotations

import pytest

from skg.web.auth import User, audit_log, parse_tokens, read_audit


def test_external_partner_restricted():
    ext = User(name="Партнёр", role="external")
    allowed = ext.allowed_categories()
    assert allowed is not None
    assert "обзор" not in allowed and "доклад" not in allowed
    assert "журнальная статья" in allowed and "статья" in allowed


def test_internal_roles_unrestricted():
    for role in ("researcher", "analyst", "lead", "admin"):
        assert User(name="x", role=role).allowed_categories() is None


def test_only_admin_views_audit():
    assert User(name="x", role="admin").can_view_audit
    assert not User(name="x", role="lead").can_view_audit


def test_parse_tokens():
    tokens = parse_tokens('{"t1": {"name": "Иванов", "role": "admin"}}')
    assert tokens["t1"].name == "Иванов" and tokens["t1"].role == "admin"
    assert parse_tokens("") == {}
    with pytest.raises(ValueError):
        parse_tokens('{"t": {"role": "superuser"}}')


def test_audit_roundtrip(tmp_path):
    path = tmp_path / "audit.jsonl"
    user = User(name="Тестов", role="analyst")
    audit_log(user, "answer", path=path, q="вопрос про никель", hits=7)
    audit_log(user, "view_context", path=path, chunk_id="D1:5")
    records = read_audit(limit=10, path=path)
    assert len(records) == 2
    assert records[0]["action"] == "view_context", "новые записи сверху"
    assert records[1]["q"] == "вопрос про никель"
    assert all(r["user"] == "Тестов" and r["role"] == "analyst" and r["ts"] for r in records)
