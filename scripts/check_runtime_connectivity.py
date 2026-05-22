"""Check PostgreSQL and OpenAI-compatible LLM connectivity.

Run from the project root:
    python scripts/check_runtime_connectivity.py

Recommended in Docker deployments:
    docker-compose run api python scripts/check_runtime_connectivity.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from echox_call.core.db import ping_database
from echox_call.core.settings import (
    DatabaseConfigError,
    LlmWorkerConfigError,
    load_llm_worker_settings,
)


def main() -> int:
    _load_env_file(Path(".env.docker"))
    _load_env_file(Path(".env"))

    ok = True
    ok = _check_postgres_tcp() and ok
    ok = _check_postgres_login() and ok
    ok = _check_llm_http() and ok
    return 0 if ok else 1


def _check_postgres_tcp() -> bool:
    host = os.environ.get("POSTGRES_HOST", "").strip()
    port_text = os.environ.get("POSTGRES_PORT", "5432").strip() or "5432"
    if not host:
        database_url = os.environ.get("DATABASE_URL", "")
        parsed = urlparse(database_url)
        host = parsed.hostname or ""
        port = parsed.port or 5432
    else:
        try:
            port = int(port_text)
        except ValueError:
            print(f"[FAIL] PG TCP: POSTGRES_PORT is not an integer: {port_text!r}")
            return False

    if not host:
        print("[FAIL] PG TCP: POSTGRES_HOST or DATABASE_URL host is missing")
        return False

    try:
        with socket.create_connection((host, port), timeout=5):
            pass
    except OSError as exc:
        print(f"[FAIL] PG TCP: cannot connect to {host}:{port}: {exc}")
        return False

    print(f"[ OK ] PG TCP: connected to {host}:{port}")
    return True


def _check_postgres_login() -> bool:
    try:
        result = ping_database()
    except (DatabaseConfigError, RuntimeError) as exc:
        print(f"[FAIL] PG login/query: {exc}")
        return False

    print(
        "[ OK ] PG login/query: "
        f"database={result.get('database')} user={result.get('user')} "
        f"server={result.get('host')}:{result.get('port')}"
    )
    return True


def _check_llm_http() -> bool:
    try:
        settings = load_llm_worker_settings()
    except LlmWorkerConfigError as exc:
        print(f"[FAIL] LLM config: {exc}")
        return False

    url = settings.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "请回复 OK"}],
        "stream": False,
        "max_tokens": min(settings.max_tokens, 32),
    }
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=30)
    except httpx.HTTPError as exc:
        print(f"[FAIL] LLM HTTP: cannot call {url}: {exc}")
        return False

    if response.status_code >= 400:
        print(f"[FAIL] LLM HTTP: status={response.status_code} url={url}")
        print(_truncate(response.text))
        return False

    try:
        data = response.json()
    except json.JSONDecodeError:
        print(f"[FAIL] LLM HTTP: non-JSON response from {url}")
        print(_truncate(response.text))
        return False

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        print(f"[FAIL] LLM HTTP: response has no choices from {url}")
        print(_truncate(json.dumps(data, ensure_ascii=False)))
        return False

    content = (
        choices[0]
        .get("message", {})
        .get("content")
    )
    print(
        "[ OK ] LLM HTTP: "
        f"model={settings.model} base_url={settings.base_url} "
        f"reply={_truncate(str(content), limit=120)!r}"
    )
    return True


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def _truncate(value: str, *, limit: int = 500) -> str:
    return value if len(value) <= limit else value[:limit] + "...<truncated>"


if __name__ == "__main__":
    raise SystemExit(main())
