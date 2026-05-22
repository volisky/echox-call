"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.conninfo import make_conninfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for runtime.
    load_dotenv = None


class DatabaseConfigError(RuntimeError):
    """Raised when PostgreSQL configuration is missing or invalid."""


class PostcallWorkerConfigError(RuntimeError):
    """Raised when postcall worker configuration is invalid."""


class LlmWorkerConfigError(RuntimeError):
    """Raised when LLM worker configuration is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    conninfo: str
    connect_timeout: int
    pool_min_size: int
    pool_max_size: int


@dataclass(frozen=True)
class LlmWorkerSettings:
    api_key: str
    base_url: str
    model: str
    max_tokens: int
    batch_size: int
    lock_seconds: int
    retry_base_delay_seconds: int
    retry_max_delay_seconds: int


@dataclass(frozen=True)
class PostcallWorkerSettings:
    analysis_profile: str
    storage_dir: Path
    storage_timezone: ZoneInfo
    batch_size: int
    lock_seconds: int
    retry_base_delay_seconds: int
    retry_max_delay_seconds: int
    audio_max_bytes: int
    audio_max_duration_sec: int
    audio_download_timeout_sec: int
    audio_event_top_k: int
    device: str
    torch_num_threads: int | None
    torch_interop_threads: int | None
    beats_checkpoint_path: Path
    beats_labels_path: Path
    diarization_model_dir: Path
    diarization_num_speakers: int | None
    wavlm_emotion_model_dir: Path
    wavlm_backbone_dir: Path
    wavlm_labels_path: Path
    attention_rules_path: Path


def _load_dotenv_if_available() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw_value = env.get(name)
    if raw_value is None or raw_value == "":
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise DatabaseConfigError(f"{name} must be an integer, got {raw_value!r}") from exc

    if value <= 0:
        raise DatabaseConfigError(f"{name} must be greater than 0, got {value}")

    return value


def _env_path(env: Mapping[str, str], name: str, default: str) -> Path:
    raw_value = env.get(name)
    value = raw_value if raw_value not in (None, "") else default
    return Path(value)


def _env_optional_int(env: Mapping[str, str], name: str, default: int | None) -> int | None:
    raw_value = env.get(name)
    if raw_value is None or raw_value == "":
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise DatabaseConfigError(f"{name} must be an integer, got {raw_value!r}") from exc

    if value <= 0:
        raise DatabaseConfigError(f"{name} must be greater than 0, got {value}")

    return value


def _env_choice(
    env: Mapping[str, str],
    name: str,
    default: str,
    allowed: set[str],
) -> str:
    raw_value = env.get(name)
    value = raw_value.strip().lower() if raw_value not in (None, "") else default
    if value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise PostcallWorkerConfigError(f"{name} must be one of {allowed_text}, got {value!r}")
    return value


def _env_timezone(env: Mapping[str, str], name: str, default: str) -> ZoneInfo:
    raw_value = env.get(name)
    value = raw_value.strip() if raw_value not in (None, "") else default
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise PostcallWorkerConfigError(f"{name} is not a valid IANA timezone: {value}") from exc


def load_database_settings(env: Mapping[str, str] | None = None) -> DatabaseSettings:
    """Load PostgreSQL settings.

    DATABASE_URL is preferred. If it is absent, standard PG* variables are used.
    The function intentionally fails fast when no database target is configured.
    """

    _load_dotenv_if_available()
    source = os.environ if env is None else env

    connect_timeout = _env_int(source, "DB_CONNECT_TIMEOUT", 5)
    pool_min_size = _env_int(source, "DB_POOL_MIN_SIZE", 1)
    pool_max_size = _env_int(source, "DB_POOL_MAX_SIZE", 5)

    if pool_min_size > pool_max_size:
        raise DatabaseConfigError(
            "DB_POOL_MIN_SIZE must be less than or equal to DB_POOL_MAX_SIZE"
        )

    direct_conninfo = (
        source.get("DATABASE_URL")
        or source.get("POSTGRES_DSN")
        or source.get("PG_DSN")
    )
    if direct_conninfo:
        return DatabaseSettings(
            conninfo=direct_conninfo,
            connect_timeout=connect_timeout,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
        )

    database = source.get("PGDATABASE")
    user = source.get("PGUSER")
    if not database or not user:
        raise DatabaseConfigError(
            "PostgreSQL is not configured. Set DATABASE_URL, or set both "
            "PGDATABASE and PGUSER. See .env.example."
        )

    conninfo = make_conninfo(
        "",
        host=source.get("PGHOST", "localhost"),
        port=source.get("PGPORT", "5432"),
        dbname=database,
        user=user,
        password=source.get("PGPASSWORD"),
        sslmode=source.get("PGSSLMODE"),
    )

    return DatabaseSettings(
        conninfo=conninfo,
        connect_timeout=connect_timeout,
        pool_min_size=pool_min_size,
        pool_max_size=pool_max_size,
    )


def load_postcall_worker_settings(
    env: Mapping[str, str] | None = None,
) -> PostcallWorkerSettings:
    """Load postcall worker settings from environment variables."""

    _load_dotenv_if_available()
    source = os.environ if env is None else env

    return PostcallWorkerSettings(
        analysis_profile=_env_choice(
            source,
            "POSTCALL_ANALYSIS_PROFILE",
            "full",
            {"full", "fast"},
        ),
        storage_dir=_env_path(source, "POSTCALL_STORAGE_DIR", "data/postcall"),
        storage_timezone=_env_timezone(source, "POSTCALL_STORAGE_TIMEZONE", "Asia/Shanghai"),
        batch_size=_env_int(source, "POSTCALL_WORKER_BATCH_SIZE", 1),
        lock_seconds=_env_int(source, "POSTCALL_WORKER_LOCK_SECONDS", 600),
        retry_base_delay_seconds=_env_int(source, "POSTCALL_WORKER_RETRY_BASE_DELAY_SECONDS", 60),
        retry_max_delay_seconds=_env_int(source, "POSTCALL_WORKER_RETRY_MAX_DELAY_SECONDS", 600),
        audio_max_bytes=_env_int(source, "POSTCALL_AUDIO_MAX_BYTES", 104857600),
        audio_max_duration_sec=_env_int(source, "POSTCALL_AUDIO_MAX_DURATION_SEC", 600),
        audio_download_timeout_sec=_env_int(source, "POSTCALL_AUDIO_DOWNLOAD_TIMEOUT_SEC", 30),
        audio_event_top_k=_env_int(source, "POSTCALL_AUDIO_EVENT_TOP_K", 20),
        device=source.get("POSTCALL_DEVICE", "cpu").strip() or "cpu",
        torch_num_threads=_env_optional_int(source, "POSTCALL_TORCH_NUM_THREADS", None),
        torch_interop_threads=_env_optional_int(
            source,
            "POSTCALL_TORCH_INTEROP_THREADS",
            None,
        ),
        beats_checkpoint_path=_env_path(
            source,
            "POSTCALL_BEATS_CHECKPOINT_PATH",
            "models/BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2/"
            "BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2.pt",
        ),
        beats_labels_path=_env_path(
            source,
            "POSTCALL_BEATS_LABELS_PATH",
            "docs/postcall-beats-audioset-labels.csv",
        ),
        diarization_model_dir=_env_path(
            source,
            "POSTCALL_DIARIZATION_MODEL_DIR",
            "models/speaker-diarization-community-1",
        ),
        diarization_num_speakers=_env_optional_int(
            source,
            "POSTCALL_DIARIZATION_NUM_SPEAKERS",
            2,
        ),
        wavlm_emotion_model_dir=_env_path(
            source,
            "POSTCALL_WAVLM_EMOTION_MODEL_DIR",
            "models/wavlm-large-categorical-emotion",
        ),
        wavlm_backbone_dir=_env_path(
            source,
            "POSTCALL_WAVLM_BACKBONE_DIR",
            "models/wavlm-large",
        ),
        wavlm_labels_path=_env_path(
            source,
            "POSTCALL_WAVLM_LABELS_PATH",
            "docs/postcall-wavlm-output-labels.csv",
        ),
        attention_rules_path=_env_path(
            source,
            "POSTCALL_ATTENTION_RULES_PATH",
            "config/postcall_attention_rules.yaml",
        ),
    )


def load_llm_worker_settings(
    env: Mapping[str, str] | None = None,
) -> LlmWorkerSettings:
    """Load LLM worker settings from environment variables."""

    _load_dotenv_if_available()
    source = os.environ if env is None else env

    api_key = (
        source.get("LLM_API_KEY", "").strip()
        or source.get("ANTHROPIC_API_KEY", "").strip()
    )
    if not api_key:
        raise LlmWorkerConfigError(
            "LLM_API_KEY is not set. The LLM worker requires a valid API key."
        )

    raw_base_url = source.get("LLM_BASE_URL", "").strip()
    base_url = raw_base_url or "https://api.modelarts-maas.com/openai/v1"

    return LlmWorkerSettings(
        api_key=api_key,
        base_url=base_url,
        model=source.get("LLM_WORKER_MODEL", "deepseek-v4-flash").strip() or "deepseek-v4-flash",
        max_tokens=_env_int(source, "LLM_WORKER_MAX_TOKENS", 1024),
        batch_size=_env_int(source, "LLM_WORKER_BATCH_SIZE", 3),
        lock_seconds=_env_int(source, "LLM_WORKER_LOCK_SECONDS", 120),
        retry_base_delay_seconds=_env_int(source, "LLM_WORKER_RETRY_BASE_DELAY_SECONDS", 30),
        retry_max_delay_seconds=_env_int(source, "LLM_WORKER_RETRY_MAX_DELAY_SECONDS", 300),
    )
