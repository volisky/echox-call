"""Console user configuration and signed-cookie authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for runtime.
    load_dotenv = None


DEFAULT_CONSOLE_USERS_CONFIG_PATH = "config/console_users.yaml"
CONSOLE_SESSION_COOKIE = "echox_call_console_session"


class ConsoleAuthConfigError(RuntimeError):
    """Raised when console user configuration is invalid."""


class ConsoleAuthenticationError(RuntimeError):
    """Raised when console login credentials are invalid."""


@dataclass(frozen=True)
class ConsoleUser:
    username: str
    name: str
    password: str
    enabled: bool


@dataclass(frozen=True)
class ConsoleAuthConfig:
    users: list[ConsoleUser]
    session_secret: str
    max_age_seconds: int


def _load_dotenv_if_available() -> None:
    if load_dotenv is not None:
        load_dotenv()


def get_console_users_config_path(env: Mapping[str, str] | None = None) -> Path:
    _load_dotenv_if_available()
    source = os.environ if env is None else env
    return Path(source.get("CONSOLE_USERS_CONFIG_PATH", DEFAULT_CONSOLE_USERS_CONFIG_PATH))


def load_console_auth_config(path: str | Path | None = None) -> ConsoleAuthConfig:
    config_path = Path(path) if path is not None else get_console_users_config_path()
    if not config_path.exists():
        raise ConsoleAuthConfigError(f"console user config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}

    if not isinstance(document, dict):
        raise ConsoleAuthConfigError("console user config must be a mapping")

    raw_session = document.get("session") or {}
    if not isinstance(raw_session, dict):
        raise ConsoleAuthConfigError("console user config field 'session' must be a mapping")

    session_secret = _require_string(raw_session, "secret", owner="session")
    max_age_seconds = _optional_positive_int(raw_session, "max_age_seconds", default=28800)

    raw_users = document.get("users")
    if not isinstance(raw_users, list):
        raise ConsoleAuthConfigError("console user config must contain a users list")

    users: list[ConsoleUser] = []
    seen_usernames: set[str] = set()
    for raw_user in raw_users:
        if not isinstance(raw_user, dict):
            raise ConsoleAuthConfigError("each console user entry must be a mapping")
        user = ConsoleUser(
            username=_require_string(raw_user, "username", owner="user"),
            name=_require_string(raw_user, "name", owner="user"),
            password=_require_password(raw_user, "password"),
            enabled=_require_bool(raw_user, "enabled", owner="user"),
        )
        if user.username in seen_usernames:
            raise ConsoleAuthConfigError(f"duplicate console username: {user.username}")
        seen_usernames.add(user.username)
        users.append(user)

    if not users:
        raise ConsoleAuthConfigError("console user config must define at least one user")

    return ConsoleAuthConfig(
        users=users,
        session_secret=session_secret,
        max_age_seconds=max_age_seconds,
    )


def authenticate_console_user(
    username: str | None,
    password: str | None,
    config: ConsoleAuthConfig | None = None,
) -> ConsoleUser:
    submitted_username = (username or "").strip()
    submitted_password = password or ""
    if not submitted_username or not submitted_password:
        raise ConsoleAuthenticationError("请输入用户名和密码。")

    auth_config = config if config is not None else load_console_auth_config()
    for user in auth_config.users:
        if not secrets.compare_digest(submitted_username, user.username):
            continue
        if not user.enabled:
            raise ConsoleAuthenticationError("该用户已停用。")
        if not secrets.compare_digest(submitted_password, user.password):
            break
        return user

    raise ConsoleAuthenticationError("用户名或密码不正确。")


def create_console_session_cookie(user: ConsoleUser, config: ConsoleAuthConfig) -> str:
    payload = {
        "username": user.username,
        "name": user.name,
        "iat": int(time.time()),
    }
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload_b64 = _urlsafe_b64encode(payload_text.encode("utf-8"))
    signature = _sign(payload_b64, config.session_secret)
    return f"{payload_b64}.{signature}"


def get_console_session_user(
    cookie_value: str | None,
    config: ConsoleAuthConfig | None = None,
) -> ConsoleUser | None:
    if not cookie_value:
        return None

    auth_config = config if config is not None else load_console_auth_config()
    try:
        payload_b64, signature = cookie_value.split(".", 1)
    except ValueError:
        return None

    expected_signature = _sign(payload_b64, auth_config.session_secret)
    if not secrets.compare_digest(signature, expected_signature):
        return None

    try:
        payload = json.loads(_urlsafe_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    username = payload.get("username")
    issued_at = payload.get("iat")
    if not isinstance(username, str) or not isinstance(issued_at, int):
        return None
    if issued_at + auth_config.max_age_seconds < int(time.time()):
        return None

    for user in auth_config.users:
        if user.enabled and secrets.compare_digest(username, user.username):
            return user
    return None


def _require_string(raw: Mapping[str, Any], field: str, *, owner: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConsoleAuthConfigError(f"{owner} field {field!r} must be a non-empty string")
    return value.strip()


def _require_bool(raw: Mapping[str, Any], field: str, *, owner: str) -> bool:
    value = raw.get(field)
    if not isinstance(value, bool):
        raise ConsoleAuthConfigError(f"{owner} field {field!r} must be a boolean")
    return value


def _require_password(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if isinstance(value, bool) or value is None:
        raise ConsoleAuthConfigError(f"user field {field!r} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise ConsoleAuthConfigError(f"user field {field!r} must be a non-empty string")
    return text


def _optional_positive_int(raw: Mapping[str, Any], field: str, *, default: int) -> int:
    value = raw.get(field, default)
    if not isinstance(value, int) or value <= 0:
        raise ConsoleAuthConfigError(f"session field {field!r} must be a positive integer")
    return value


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _urlsafe_b64encode(digest)


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
