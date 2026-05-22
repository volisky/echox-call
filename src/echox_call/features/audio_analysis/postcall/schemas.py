"""Schemas for postcall audio analysis jobs."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ASR_SPEAKERS = ("接警员", "报警人")
PostcallJobState = Literal[
    "processing_queued",
    "processing_downloading",
    "processing_analyzing",
    "completed",
    "failed",
    "failed_cancelled",
]
VoiceEmotionDimensionKey = Literal["arousal", "valence", "dominance"]
SpeakerRole = Literal["未知", "报警人", "接警员"]
SpeakerRoleSource = Literal[
    "global_audio",
    "diarization_only",
    "energy_vad",
    "asr_timestamp",
    "voiceprint",
    "channel",
    "manual",
]


def _strip_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be blank")
    return value.strip()


def _strip_optional_non_blank(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _strip_non_blank(value, field_name)


def _validate_public_http_url(value: str, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https")
    if not parsed.hostname:
        raise ValueError(f"{field_name} must contain a hostname")

    # ALLOW_LOCAL_AUDIO_URL=1 bypasses private/loopback checks for local testing.
    if os.environ.get("ALLOW_LOCAL_AUDIO_URL", "").strip() in ("1", "true", "yes"):
        return value

    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError(f"{field_name} cannot point to localhost")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError(f"{field_name} cannot point to a non-public IP address")

    return value


class AsrSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal["接警员", "报警人"]
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _strip_non_blank(value, "asrResult[].text")


class RiskPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idcard: str | None = None
    tags: list[str] = Field(default_factory=list)
    report: str | None = None

    @field_validator("idcard", "report")
    @classmethod
    def validate_optional_text(cls, value: str | None, info: Any) -> str | None:
        return _strip_optional_non_blank(value, f"riskPerson.{info.field_name}")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return [
            _strip_non_blank(tag, "riskPerson.tags[]")
            for tag in value
        ]


class CreatePostcallJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jjdh: str
    audioUrl: str
    bjsj: datetime
    JCJXTJSDWMC: str
    JJDWMC: str
    GXDWMC: str
    bjdh: str
    bjrmc: str
    bjrxbdm: int = Field(ge=0, le=2)
    lxdh: str
    jqdz: str
    bjnr: str
    jqlbdm: str
    jqlxdm: str
    jqxldm: str | None = None
    jqzldm: str | None = None
    jqdj: str
    callbackUrl: str | None = None
    asrResult: list[AsrSegment] | None = None
    alarmContent: str | None = None
    alarmAddress: str | None = None
    isHighIncidentAddress: bool | None = None
    riskPerson: RiskPerson | None = None

    @field_validator(
        "jjdh",
        "audioUrl",
        "JCJXTJSDWMC",
        "JJDWMC",
        "GXDWMC",
        "bjdh",
        "bjrmc",
        "lxdh",
        "jqdz",
        "bjnr",
        "jqlbdm",
        "jqlxdm",
        "jqdj",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)

    @field_validator("jqxldm", "jqzldm", "callbackUrl", "alarmContent", "alarmAddress")
    @classmethod
    def validate_optional_text(cls, value: str | None, info: Any) -> str | None:
        return _strip_optional_non_blank(value, info.field_name)

    @field_validator("audioUrl")
    @classmethod
    def validate_audio_url(cls, value: str) -> str:
        return _validate_public_http_url(value, "audioUrl")

    @field_validator("callbackUrl")
    @classmethod
    def validate_callback_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("callbackUrl must be a valid http or https URL")
        return value

    def asr_result_json(self) -> list[dict[str, str]]:
        if not self.asrResult:
            return []
        return [segment.model_dump(mode="json") for segment in self.asrResult]

    def raw_payload_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class PostcallJobCreateResult:
    job_id: str
    jjdh: str
    state: PostcallJobState
    duplicate: bool
    duplicate_count: int


class CreatePostcallJobData(BaseModel):
    jobId: str
    jjdh: str
    state: PostcallJobState
    duplicate: bool


CreatePostcallJobResponse = CreatePostcallJobData


class AudioEventScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventNameEn: str
    eventNameZh: str
    score: float = Field(ge=0, le=1)

    @field_validator("eventNameEn", "eventNameZh")
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)


class VoiceEmotionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotionNameEn: str
    emotionNameZh: str
    score: float = Field(ge=0, le=1)

    @field_validator("emotionNameEn", "emotionNameZh")
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)


class VoiceEmotionDimensionValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimensionNameEn: str
    dimensionNameZh: str
    value: float = Field(ge=0, le=1)

    @field_validator("dimensionNameEn", "dimensionNameZh")
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)


class PostcallTimelineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segmentId: str
    startSec: float = Field(ge=0)
    endSec: float = Field(ge=0)
    speakerLabel: str | None = None
    speakerRole: SpeakerRole | None = None
    roleSource: SpeakerRoleSource | None = None
    audioEventScores: list[AudioEventScore]
    voiceEmotionScores: list[VoiceEmotionScore]
    voiceEmotionDimensions: dict[VoiceEmotionDimensionKey, VoiceEmotionDimensionValue]

    @field_validator("segmentId")
    @classmethod
    def validate_segment_id(cls, value: str) -> str:
        return _strip_non_blank(value, "segmentId")

    @field_validator("speakerLabel")
    @classmethod
    def validate_speaker_label(cls, value: str | None) -> str | None:
        return _strip_optional_non_blank(value, "speakerLabel")

    @field_validator("endSec")
    @classmethod
    def validate_end_sec(cls, value: float, info: Any) -> float:
        start_sec = info.data.get("startSec")
        if isinstance(start_sec, int | float) and value < start_sec:
            raise ValueError("endSec cannot be earlier than startSec")
        return value

    @field_validator("voiceEmotionDimensions")
    @classmethod
    def validate_voice_emotion_dimensions(
        cls,
        value: dict[VoiceEmotionDimensionKey, VoiceEmotionDimensionValue],
    ) -> dict[VoiceEmotionDimensionKey, VoiceEmotionDimensionValue]:
        if value and set(value) != {"arousal", "valence", "dominance"}:
            raise ValueError(
                "voiceEmotionDimensions must be empty or contain arousal, valence, and dominance"
            )
        return value


InsightEvidenceSourceField = Literal[
    "audioEventScores",
    "voiceEmotionScores",
    "voiceEmotionDimensions",
]
InsightConfidence = Literal["low", "medium", "high"]
AttentionLevel = Literal[1, 2, 3]
ATTENTION_LEVEL_NAMES = {
    1: "需要关注",
    2: "建议复核",
    3: "暂无明显线索",
}
LlmJobState = Literal["queued", "processing", "completed", "failed"]


class PostcallInsightEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segmentId: str
    startSec: float = Field(ge=0)
    endSec: float = Field(ge=0)
    sourceField: InsightEvidenceSourceField
    nameEn: str
    nameZh: str
    score: float = Field(ge=0, le=1)

    @field_validator("segmentId", "nameEn", "nameZh")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)

    @field_validator("endSec")
    @classmethod
    def validate_end_sec(cls, value: float, info: Any) -> float:
        start_sec = info.data.get("startSec")
        if isinstance(start_sec, int | float) and value < start_sec:
            raise ValueError("endSec cannot be earlier than startSec")
        return value


class PostcallInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insightType: str
    insightName: str
    startSec: float = Field(ge=0)
    endSec: float = Field(ge=0)
    occurrenceCount: int = Field(ge=1)
    totalDurationSec: float = Field(ge=0)
    maxScore: float = Field(ge=0, le=1)
    avgScore: float = Field(ge=0, le=1)
    confidence: InsightConfidence
    reason: str
    matchedRuleCodes: list[str]
    evidence: list[PostcallInsightEvidence]

    @field_validator("insightType", "insightName", "reason")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)

    @field_validator("matchedRuleCodes")
    @classmethod
    def validate_rule_codes(cls, value: list[str]) -> list[str]:
        for code in value:
            _strip_non_blank(code, "matchedRuleCodes[]")
        return value

    @field_validator("endSec")
    @classmethod
    def validate_end_sec(cls, value: float, info: Any) -> float:
        start_sec = info.data.get("startSec")
        if isinstance(start_sec, int | float) and value < start_sec:
            raise ValueError("endSec cannot be earlier than startSec")
        return value


class ReviewSegmentEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nameEn: str
    nameZh: str
    score: float = Field(ge=0, le=1)

    @field_validator("nameEn", "nameZh")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)


class PostcallReviewSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startSec: float = Field(ge=0)
    endSec: float = Field(ge=0)
    result: str

    @field_validator("result")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)

    @field_validator("endSec")
    @classmethod
    def validate_end_sec(cls, value: float, info: Any) -> float:
        start_sec = info.data.get("startSec")
        if isinstance(start_sec, int | float) and value < start_sec:
            raise ValueError("endSec cannot be earlier than startSec")
        return value


class VoiceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: AttentionLevel | None
    levelName: str | None
    reviewSegments: list[PostcallReviewSegment] | None = None

    @model_validator(mode="after")
    def validate_voice_level(self) -> "VoiceResult":
        if self.level is None or self.levelName is None:
            if self.level is not None or self.levelName is not None:
                raise ValueError("level and levelName must both be null or both be present")
            self.reviewSegments = None
            return self
        expected = ATTENTION_LEVEL_NAMES[self.level]
        if self.levelName != expected:
            raise ValueError(f"levelName must be {expected!r} when level is {self.level}")
        if self.level == 3:
            self.reviewSegments = None
        return self


class InputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alarmContent: str | None = None
    alarmAddress: str | None = None
    isHighIncidentAddress: bool | None = None


class OverallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: AttentionLevel
    levelName: str
    summary: list[str]
    voiceResult: VoiceResult
    inputSnapshot: InputSnapshot
    riskPerson: RiskPerson | None = None

    @field_validator("levelName")
    @classmethod
    def validate_level_name(cls, value: str, info: Any) -> str:
        level = info.data.get("level")
        if isinstance(level, int):
            expected = ATTENTION_LEVEL_NAMES.get(level)
            if expected and value != expected:
                raise ValueError(f"levelName must be {expected!r} when level is {level}")
        return value


class PostcallJobResultData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    jjdh: str
    state: PostcallJobState
    overallResult: OverallResult | None = None

    @field_validator("jobId", "jjdh")
    @classmethod
    def validate_required_text(cls, value: str, info: Any) -> str:
        return _strip_non_blank(value, info.field_name)
