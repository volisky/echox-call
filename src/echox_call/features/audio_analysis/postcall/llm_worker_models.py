"""Internal data structures for LLM worker processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ClaimedLlmJob:
    internal_id: UUID
    postcall_job_id: UUID
    job_id: str
    jjdh: str
    callback_url: str | None
    attempt_count: int
    max_attempts: int
    asr_result: list[dict[str, Any]]
    alarm_content: str | None
    alarm_address: str | None
    is_high_incident_address: bool | None
    risk_person: dict[str, Any] | None
    bjsj: datetime


@dataclass(frozen=True)
class LlmAnalysisOutput:
    level: int
    level_name: str
    case_type_summary: str | None
    case_type_details: list[dict[str, str]]
    high_risk_address_summary: str | None
    high_risk_person_summary: str | None
    llm_model: str
