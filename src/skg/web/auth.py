"""RBAC и аудит.

Роли (из ТЗ кейса): исследователь, аналитик, руководитель проекта, администратор,
внешний партнёр. Токены задаются в .env одной JSON-строкой:

  AUTH_TOKENS={"tok-admin": {"name": "Иванов И.И.", "role": "admin"},
               "tok-ext": {"name": "Партнёр", "role": "external"}}

Без AUTH_TOKENS (или без токена в запросе) действует открытый режим: роль
AUTH_OPEN_ROLE (по умолчанию researcher). AUTH_OPEN_ROLE=deny — анонимный
доступ запрещён (401).

Разграничение: «внешний партнёр» не видит внутренние категории документов
(обзоры НИИ и доклады) — фильтр применяется в Cypher-запросах поиска и при
просмотре фрагментов/графа. Аудит: каждый запрос данных пишется в
logs/audit.jsonl (время, пользователь, роль, действие, параметры).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Header, HTTPException

ROLE_LABELS = {
    "researcher": "исследователь",
    "analyst": "аналитик",
    "lead": "руководитель проекта",
    "admin": "администратор",
    "external": "внешний партнёр",
}

# внутренние материалы НИИ, закрытые для внешних партнёров
RESTRICTED_FOR_EXTERNAL = ["обзор", "доклад"]
ALL_CATEGORIES = ["статья", "обзор", "доклад", "журнальная статья", "материалы конференции"]

AUDIT_PATH = Path(__file__).resolve().parents[3] / "logs" / "audit.jsonl"


@dataclass(frozen=True)
class User:
    name: str
    role: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    def allowed_categories(self) -> list[str] | None:
        """None — без ограничений; список — только перечисленные категории."""
        if self.role == "external":
            return [c for c in ALL_CATEGORIES if c not in RESTRICTED_FOR_EXTERNAL]
        return None

    @property
    def can_view_audit(self) -> bool:
        return self.role == "admin"


def parse_tokens(raw: str) -> dict[str, User]:
    if not raw.strip():
        return {}
    data = json.loads(raw)
    out: dict[str, User] = {}
    for token, u in data.items():
        role = u.get("role", "researcher")
        if role not in ROLE_LABELS:
            raise ValueError(f"неизвестная роль в AUTH_TOKENS: {role}")
        out[token] = User(name=u.get("name", "без имени"), role=role)
    return out


_tokens_cache: dict[str, User] | None = None


def _tokens() -> dict[str, User]:
    global _tokens_cache
    if _tokens_cache is None:
        _tokens_cache = parse_tokens(os.environ.get("AUTH_TOKENS", ""))
    return _tokens_cache


def get_user(x_auth_token: str | None = Header(None, alias="X-Auth-Token")) -> User:
    """FastAPI-зависимость: пользователь по токену либо анонимная роль."""
    tokens = _tokens()
    if x_auth_token:
        user = tokens.get(x_auth_token)
        if user is None:
            raise HTTPException(401, "неизвестный токен доступа")
        return user
    open_role = os.environ.get("AUTH_OPEN_ROLE", "researcher")
    if open_role not in ROLE_LABELS:  # deny или опечатка — анонимный доступ закрыт
        raise HTTPException(401, "нужен заголовок X-Auth-Token")
    return User(name="гость", role=open_role)


def audit_log(user: User, action: str, path: Path = AUDIT_PATH, **details) -> None:
    """Строка аудита: время (UTC), пользователь, роль, действие, параметры."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": user.name,
        "role": user.role,
        "action": action,
        **{k: v for k, v in details.items() if v is not None},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200, path: Path = AUDIT_PATH) -> list[dict]:
    """Последние записи аудита (для администратора), новые сверху."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
