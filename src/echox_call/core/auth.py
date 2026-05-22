"""API client configuration and authentication helpers."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for runtime.
    load_dotenv = None


DEFAULT_CLIENTS_CONFIG_PATH = "config/clients.yaml"


class ClientConfigError(RuntimeError):
    """Raised when the API client configuration is invalid."""


class ApiAuthenticationError(RuntimeError):
    """Raised when an API key is missing or invalid."""


class ApiClientDisabledError(ApiAuthenticationError):
    """Raised when the matched API client is disabled."""


@dataclass(frozen=True)
class ApiClient:
    client_id: str
    name: str
    api_key: str
    source_system: str
    enabled: bool
    allow_debug: bool


def _load_dotenv_if_available() -> None:
    if load_dotenv is not None:
        load_dotenv()


def get_clients_config_path(env: Mapping[str, str] | None = None) -> Path:
    _load_dotenv_if_available()
    source = os.environ if env is None else env
    return Path(source.get("CLIENTS_CONFIG_PATH", DEFAULT_CLIENTS_CONFIG_PATH))


def _require_string(raw_client: Mapping[str, Any], field: str) -> str:
    value = raw_client.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ClientConfigError(f"client field {field!r} must be a non-empty string")
    return value.strip()


def _require_bool(raw_client: Mapping[str, Any], field: str) -> bool:
    value = raw_client.get(field)
    if not isinstance(value, bool):
        raise ClientConfigError(f"client field {field!r} must be a boolean")
    return value


def load_api_clients(path: str | Path | None = None) -> list[ApiClient]:
    config_path = Path(path) if path is not None else get_clients_config_path()
    if not config_path.exists():
        raise ClientConfigError(f"API client config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}

    raw_clients = document.get("clients")
    if not isinstance(raw_clients, list):
        raise ClientConfigError("API client config must contain a clients list")

    clients: list[ApiClient] = []
    seen_client_ids: set[str] = set()
    seen_api_keys: set[str] = set()

    for raw_client in raw_clients:
        if not isinstance(raw_client, dict):
            raise ClientConfigError("each client entry must be a mapping")

        client = ApiClient(
            client_id=_require_string(raw_client, "client_id"),
            name=_require_string(raw_client, "name"),
            api_key=_require_string(raw_client, "api_key"),
            source_system=_require_string(raw_client, "source_system"),
            enabled=_require_bool(raw_client, "enabled"),
            allow_debug=_require_bool(raw_client, "allow_debug"),
        )

        if client.client_id in seen_client_ids:
            raise ClientConfigError(f"duplicate client_id: {client.client_id}")
        if client.api_key in seen_api_keys:
            raise ClientConfigError(f"duplicate api_key for client_id: {client.client_id}")

        seen_client_ids.add(client.client_id)
        seen_api_keys.add(client.api_key)
        clients.append(client)

    if not clients:
        raise ClientConfigError("API client config must define at least one client")

    return clients


def authenticate_api_key(
    api_key: str | None,
    clients: list[ApiClient] | None = None,
) -> ApiClient:
    """Authenticate X-API-Key and return the matched enabled client."""

    if not api_key or not api_key.strip():
        raise ApiAuthenticationError("missing X-API-Key")

    configured_clients = clients if clients is not None else load_api_clients()
    submitted_key = api_key.strip()

    for client in configured_clients:
        if secrets.compare_digest(submitted_key, client.api_key):
            if not client.enabled:
                raise ApiClientDisabledError(f"API client is disabled: {client.client_id}")
            return client

    raise ApiAuthenticationError("invalid X-API-Key")

