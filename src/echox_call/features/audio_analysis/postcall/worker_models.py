"""Internal data structures for postcall worker processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ClaimedPostcallJob:
    internal_id: UUID
    job_id: str
    jjdh: str
    audio_url: str
    bjsj: datetime
    callback_url: str | None
    attempt_count: int
    max_attempts: int
    duplicate_count: int


@dataclass(frozen=True)
class AudioAssetRecord:
    asset_type: str
    uri: str
    content_type: str | None = None
    sha256: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    duration_sec: float | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRunRecord:
    model_name: str
    model_role: str
    status: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    model_version: str | None = None
    input_ref: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TimelineSegmentRecord:
    segment_id: str
    start_sec: float
    end_sec: float
    speaker_label: str | None = None
    speaker_role: str | None = None
    role_source: str | None = None
    audio_event_scores: list[dict[str, Any]] = field(default_factory=list)
    voice_emotion_scores: list[dict[str, Any]] = field(default_factory=list)
    voice_detailed_scores: list[dict[str, Any]] = field(default_factory=list)
    voice_emotion_dimensions: dict[str, Any] = field(default_factory=dict)
    internal_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpeakerSegmentRecord:
    start_sec: float
    end_sec: float
    speaker_label: str
    speaker_role: str = "未知"
    role_source: str = "diarization_only"
