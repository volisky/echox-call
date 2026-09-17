"""Internal data structures for LLM worker processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


CASE_REASON_SEPARATOR = "；"


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
    secondary_review_confidence: int | None
    secondary_review_reason: str | None
    llm_model: str

    @property
    def if_case(self) -> int:
        """1 when the LLM text verdict is level 1, otherwise 0.

        Derived from ``level`` so the flag can never drift from the verdict,
        including after the level-1 -> level-2 secondary-review downgrade.
        """

        return 1 if self.level == 1 else 0

    @property
    def case_reason(self) -> str | None:
        """Every matched case-type reason, joined by the Chinese semicolon.

        Populated for any level; ``None`` only when the model produced no
        usable case-type detail. With a single detail this is byte-identical
        to ``caseTypeDetails[0].reason``.
        """

        reasons = [
            detail["reason"] for detail in self.case_type_details if detail.get("reason")
        ]
        return CASE_REASON_SEPARATOR.join(reasons) if reasons else None
