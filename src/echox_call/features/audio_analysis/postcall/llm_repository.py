"""Database access for the LLM worker queue."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from echox_call.core.db import connect
from echox_call.features.audio_analysis.postcall.llm_worker_models import (
    ClaimedLlmJob,
    LlmAnalysisOutput,
)
from echox_call.features.audio_analysis.postcall.schemas import (
    ATTENTION_LEVEL_NAMES,
    InputSnapshot,
    OverallResult,
    PostcallJobResultData,
    PostcallReviewSegment,
    RiskPerson,
    VoiceResult,
)


INSERT_LLM_JOB_SQL = """
INSERT INTO postcall_llm_jobs (postcall_job_id, job_id)
VALUES (%(postcall_job_id)s, %(job_id)s)
ON CONFLICT (postcall_job_id) DO NOTHING
RETURNING id
"""

RESET_LLM_JOB_SQL = """
UPDATE postcall_llm_jobs
SET
    state         = 'queued',
    attempt_count = 0,
    locked_by     = NULL,
    locked_at     = NULL,
    locked_until  = NULL,
    next_run_at   = now(),
    started_at    = NULL,
    completed_at  = NULL,
    failed_at     = NULL,
    error_code    = NULL,
    error_message = NULL,
    llm_model     = NULL,
    llm_output    = NULL,
    updated_at    = now()
WHERE postcall_job_id = %s
RETURNING id
"""

CLAIM_NEXT_LLM_JOB_SQL = """
WITH candidate AS (
    SELECT id
    FROM postcall_llm_jobs
    WHERE state = 'queued'
      AND next_run_at <= now()
      AND attempt_count < max_attempts
      AND (locked_until IS NULL OR locked_until < now())
    ORDER BY next_run_at ASC, created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE postcall_llm_jobs AS llm
SET
    state         = 'processing',
    locked_by     = %(worker_id)s,
    locked_at     = now(),
    locked_until  = now() + make_interval(secs => %(lock_seconds)s),
    started_at    = COALESCE(llm.started_at, now()),
    attempt_count = llm.attempt_count + 1,
    updated_at    = now()
FROM candidate
WHERE llm.id = candidate.id
RETURNING
    llm.id,
    llm.postcall_job_id,
    llm.job_id,
    llm.attempt_count,
    llm.max_attempts
"""

SELECT_LLM_JOB_INPUT_SQL = """
SELECT
    jjdh,
    bjsj,
    callback_url,
    asr_result,
    raw_payload
FROM postcall_jobs
WHERE id = %s
"""

RECOVER_EXPIRED_LLM_JOBS_SQL = """
WITH expired AS (
    UPDATE postcall_llm_jobs
    SET
        state = CASE
            WHEN attempt_count < max_attempts THEN 'queued'
            ELSE 'failed'
        END,
        failed_at = CASE
            WHEN attempt_count < max_attempts THEN NULL
            ELSE now()
        END,
        error_code    = 'WORKER_LOCK_EXPIRED',
        error_message = concat(
            'LLM worker lock expired; previousWorker=',
            COALESCE(locked_by, ''),
            '; attempt=', attempt_count, '/', max_attempts
        ),
        locked_by    = NULL,
        locked_at    = NULL,
        locked_until = NULL,
        next_run_at  = CASE
            WHEN attempt_count < max_attempts THEN now()
            ELSE next_run_at
        END,
        updated_at = now()
    WHERE state = 'processing'
      AND locked_until IS NOT NULL
      AND locked_until < now()
    RETURNING state
)
SELECT
    count(*)::integer                                          AS recovered_count,
    count(*) FILTER (WHERE state = 'queued')::integer         AS requeued_count,
    count(*) FILTER (WHERE state = 'failed')::integer         AS failed_count
FROM expired
"""

RECORD_LLM_FAILURE_SQL = """
UPDATE postcall_llm_jobs
SET
    state = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts THEN 'queued'
        ELSE 'failed'
    END,
    failed_at = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts THEN NULL
        ELSE now()
    END,
    error_code    = %(error_code)s,
    error_message = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts
            THEN concat(%(error_message)s::text, '; retryScheduled=true; retryDelaySecs=', %(retry_delay_seconds)s::integer)
        ELSE concat(%(error_message)s::text, '; retryScheduled=false; attempt=', attempt_count, '/', max_attempts)
    END,
    locked_by    = NULL,
    locked_at    = NULL,
    locked_until = NULL,
    next_run_at  = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts
            THEN now() + make_interval(secs => %(retry_delay_seconds)s)
        ELSE next_run_at
    END,
    updated_at = now()
WHERE id = %(llm_job_id)s
RETURNING state, attempt_count, max_attempts
"""

MARK_OVERALL_JOB_FAILED_SQL = """
UPDATE postcall_jobs
SET
    state       = 'failed',
    failed_at   = now(),
    error_code  = %(error_code)s,
    error_message = %(error_message)s,
    locked_by   = NULL,
    locked_at   = NULL,
    locked_until = NULL,
    updated_at  = now()
WHERE id = %(postcall_job_id)s
  AND state NOT IN ('completed', 'failed', 'failed_cancelled')
"""

RECORD_LLM_SUCCESS_SQL = """
UPDATE postcall_llm_jobs
SET
    state        = 'completed',
    completed_at = now(),
    locked_by    = NULL,
    locked_at    = NULL,
    locked_until = NULL,
    llm_model    = %(llm_model)s,
    llm_output   = %(llm_output)s,
    updated_at   = now()
WHERE id = %(llm_job_id)s
RETURNING id
"""

TRY_MARK_OVERALL_COMPLETED_SQL = """
WITH locked AS (
    SELECT
        pj.id,
        pj.job_id,
        pj.jjdh,
        pj.duplicate_count,
        pj.audio_analysis_data,
        pj.raw_payload,
        pj.state,
        plj.llm_output
    FROM postcall_jobs AS pj
    JOIN postcall_llm_jobs AS plj ON plj.postcall_job_id = pj.id
    WHERE pj.id = %(postcall_job_id)s
      AND pj.audio_completed_at IS NOT NULL
      AND plj.state = 'completed'
      AND pj.state NOT IN ('completed', 'failed', 'failed_cancelled')
    FOR UPDATE OF pj
)
UPDATE postcall_jobs AS pj
SET
    state        = 'completed',
    completed_at = now(),
    error_code   = NULL,
    error_message = NULL,
    locked_by    = NULL,
    locked_at    = NULL,
    locked_until = NULL,
    updated_at   = now()
FROM locked
WHERE pj.id = locked.id
RETURNING
    pj.id,
    locked.job_id,
    locked.jjdh,
    locked.duplicate_count,
    locked.audio_analysis_data,
    locked.raw_payload,
    locked.llm_output
"""

INSERT_FINAL_ANALYSIS_RESULT_SQL = """
INSERT INTO postcall_analysis_results (
    postcall_job_id,
    attention_level,
    attention_level_name,
    model_versions,
    audio_processing,
    rule_version,
    matched_rule_codes,
    fusion_trace,
    api_result_payload,
    api_result_version,
    api_result_generated_at
)
VALUES (
    %(postcall_job_id)s,
    %(attention_level)s,
    %(attention_level_name)s,
    %(model_versions)s,
    %(audio_processing)s,
    %(rule_version)s,
    %(matched_rule_codes)s,
    %(fusion_trace)s,
    %(api_result_payload)s,
    %(api_result_version)s,
    now()
)
ON CONFLICT (postcall_job_id) DO UPDATE
SET
    attention_level         = EXCLUDED.attention_level,
    attention_level_name    = EXCLUDED.attention_level_name,
    model_versions          = EXCLUDED.model_versions,
    audio_processing        = EXCLUDED.audio_processing,
    rule_version            = EXCLUDED.rule_version,
    matched_rule_codes      = EXCLUDED.matched_rule_codes,
    fusion_trace            = EXCLUDED.fusion_trace,
    api_result_payload      = EXCLUDED.api_result_payload,
    api_result_version      = EXCLUDED.api_result_version,
    api_result_generated_at = now(),
    updated_at              = now()
RETURNING id
"""

LINK_SEGMENTS_TO_ANALYSIS_SQL = """
UPDATE postcall_timeline_segments
SET analysis_result_id = %s
WHERE postcall_job_id = %s
  AND analysis_result_id IS NULL;

UPDATE postcall_review_segments
SET analysis_result_id = %s
WHERE postcall_job_id = %s
  AND analysis_result_id IS NULL;
"""

API_RESULT_VERSION = "postcall_job_result_v2"


class PostcallLlmJobStaleError(RuntimeError):
    """Raised when an LLM worker tries to write for a stale job version."""


class PostcallLlmJobRepository:
    """Manage the LLM worker queue and final result assembly."""

    def insert_llm_job(self, conn: Any, *, postcall_job_id: Any, job_id: str) -> None:
        conn.execute(INSERT_LLM_JOB_SQL, {"postcall_job_id": postcall_job_id, "job_id": job_id})

    def reset_llm_job(self, conn: Any, *, postcall_job_id: Any) -> None:
        conn.execute(RESET_LLM_JOB_SQL, (postcall_job_id,))

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lock_seconds: int,
    ) -> ClaimedLlmJob | None:
        with connect(autocommit=False) as conn:
            with conn.transaction():
                row = conn.execute(
                    CLAIM_NEXT_LLM_JOB_SQL,
                    {"worker_id": worker_id, "lock_seconds": lock_seconds},
                ).fetchone()
        if row is None:
            return None

        with connect() as conn:
            job_row = conn.execute(SELECT_LLM_JOB_INPUT_SQL, (row["postcall_job_id"],)).fetchone()

        if job_row is None:
            return None

        raw = job_row["raw_payload"] or {}
        risk_person_raw = raw.get("riskPerson")
        return ClaimedLlmJob(
            internal_id=row["id"],
            postcall_job_id=row["postcall_job_id"],
            job_id=row["job_id"],
            jjdh=job_row["jjdh"],
            bjsj=job_row["bjsj"],
            callback_url=job_row["callback_url"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            asr_result=job_row["asr_result"] or [],
            alarm_content=raw.get("alarmContent"),
            alarm_address=raw.get("alarmAddress"),
            is_high_incident_address=raw.get("isHighIncidentAddress"),
            risk_person=risk_person_raw if isinstance(risk_person_raw, dict) else None,
        )

    def recover_expired_jobs(self) -> dict[str, int]:
        with connect(autocommit=True) as conn:
            row = conn.execute(RECOVER_EXPIRED_LLM_JOBS_SQL).fetchone()
        if row is None:
            return {"recovered_count": 0, "requeued_count": 0, "failed_count": 0}
        return {
            "recovered_count": row["recovered_count"],
            "requeued_count": row["requeued_count"],
            "failed_count": row["failed_count"],
        }

    def record_failure(
        self,
        *,
        job: ClaimedLlmJob,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int,
    ) -> None:
        with connect(autocommit=True) as conn:
            row = conn.execute(
                RECORD_LLM_FAILURE_SQL,
                {
                    "llm_job_id": job.internal_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "retryable": retryable,
                    "retry_delay_seconds": retry_delay_seconds,
                },
            ).fetchone()
        if row is None:
            return

        is_terminal = row["state"] == "failed"
        if is_terminal:
            with connect(autocommit=True) as conn:
                conn.execute(
                    MARK_OVERALL_JOB_FAILED_SQL,
                    {
                        "postcall_job_id": job.postcall_job_id,
                        "error_code": f"LLM_{error_code}",
                        "error_message": f"LLM worker failed: {error_message}",
                    },
                )

    def persist_success(self, *, job: ClaimedLlmJob, output: LlmAnalysisOutput) -> None:
        llm_output_json = {
            "level": output.level,
            "levelName": output.level_name,
            "caseTypeSummary": output.case_type_summary,
            "caseTypeDetails": output.case_type_details,
            "highRiskAddressSummary": output.high_risk_address_summary,
            "highRiskPersonSummary": output.high_risk_person_summary,
        }

        with connect(autocommit=False) as conn:
            with conn.transaction():
                row = conn.execute(
                    RECORD_LLM_SUCCESS_SQL,
                    {
                        "llm_job_id": job.internal_id,
                        "llm_model": output.llm_model,
                        "llm_output": Jsonb(llm_output_json),
                    },
                ).fetchone()
                if row is None:
                    raise PostcallLlmJobStaleError(
                        f"LLM job {job.job_id} disappeared before recording success"
                    )

        self.try_mark_overall_completed(job.postcall_job_id)

    def try_mark_overall_completed(self, postcall_job_id: Any) -> None:
        with connect(autocommit=False) as conn:
            with conn.transaction():
                merged = conn.execute(
                    TRY_MARK_OVERALL_COMPLETED_SQL,
                    {"postcall_job_id": postcall_job_id},
                ).fetchone()
        if merged is not None:
            self._write_final_analysis_result(merged)

    def _write_final_analysis_result(self, merged: Any) -> None:
        audio_data: dict[str, Any] = merged["audio_analysis_data"] or {}
        llm_out: dict[str, Any] = merged["llm_output"] or {}
        raw: dict[str, Any] = merged["raw_payload"] or {}

        overall_level: int = llm_out.get("level", 3)
        overall_level_name: str = llm_out.get("levelName", ATTENTION_LEVEL_NAMES[3])

        voice_level: int | None = audio_data.get("attentionLevel")
        voice_level_name: str | None = audio_data.get("attentionLevelName")
        raw_review_segments: list[dict[str, Any]] = audio_data.get("reviewSegments") or []

        voice_review_segments: list[PostcallReviewSegment] | None = None
        if voice_level in {1, 2} and raw_review_segments:
            voice_review_segments = [
                PostcallReviewSegment(
                    startSec=seg["startSec"],
                    endSec=seg["endSec"],
                    result=seg["result"],
                )
                for seg in raw_review_segments
            ]

        voice_result = VoiceResult(
            level=voice_level,
            levelName=voice_level_name,
            reviewSegments=voice_review_segments,
        )

        input_snapshot = InputSnapshot(
            alarmContent=raw.get("alarmContent"),
            alarmAddress=raw.get("alarmAddress"),
            isHighIncidentAddress=raw.get("isHighIncidentAddress"),
        )

        risk_person_raw = raw.get("riskPerson")
        risk_person: RiskPerson | None = None
        if isinstance(risk_person_raw, dict):
            try:
                risk_person = RiskPerson.model_validate(risk_person_raw)
            except ValidationError:
                pass

        summary = _build_summary(llm_out, overall_level_name, voice_level_name)

        overall_result = OverallResult(
            level=overall_level,
            levelName=overall_level_name,
            summary=summary,
            voiceResult=voice_result,
            inputSnapshot=input_snapshot,
            riskPerson=risk_person,
        )

        api_result = PostcallJobResultData(
            jobId=merged["job_id"],
            jjdh=merged["jjdh"],
            state="completed",
            overallResult=overall_result,
        )
        api_payload = api_result.model_dump(mode="json", exclude_none=True)

        model_versions = audio_data.get("modelVersions") or {}
        audio_processing = audio_data.get("audioProcessing") or {}
        rule_version = audio_data.get("ruleVersion") or "unknown"
        matched_rule_codes = audio_data.get("matchedRuleCodes") or []
        fusion_trace = audio_data.get("fusionTrace") or {}

        with connect(autocommit=False) as conn:
            with conn.transaction():
                result_row = conn.execute(
                    INSERT_FINAL_ANALYSIS_RESULT_SQL,
                    {
                        "postcall_job_id": merged["id"],
                        "attention_level": overall_level,
                        "attention_level_name": overall_level_name,
                        "model_versions": Jsonb(model_versions),
                        "audio_processing": Jsonb(audio_processing),
                        "rule_version": rule_version,
                        "matched_rule_codes": Jsonb(matched_rule_codes),
                        "fusion_trace": Jsonb(fusion_trace),
                        "api_result_payload": Jsonb(api_payload),
                        "api_result_version": API_RESULT_VERSION,
                    },
                ).fetchone()
                if result_row is not None:
                    analysis_result_id = result_row["id"]
                    conn.execute(
                        "UPDATE postcall_timeline_segments SET analysis_result_id = %s WHERE postcall_job_id = %s AND analysis_result_id IS NULL",
                        (analysis_result_id, merged["id"]),
                    )
                    conn.execute(
                        "UPDATE postcall_review_segments SET analysis_result_id = %s WHERE postcall_job_id = %s AND analysis_result_id IS NULL",
                        (analysis_result_id, merged["id"]),
                    )


def _build_summary(
    llm_out: dict[str, Any],
    overall_level_name: str,
    voice_level_name: str | None,
) -> list[str]:
    detail_items = _case_type_detail_summary_items(llm_out.get("caseTypeDetails"))
    if detail_items:
        analysis_summary = _build_case_type_summary_from_details(
            llm_out.get("caseTypeDetails"),
            overall_level_name,
        )
    else:
        analysis_summary = _normalize_analysis_summary(
            llm_out.get("caseTypeSummary"),
            overall_level_name,
        )
        detail_items = _legacy_case_type_detail_items(llm_out, voice_level_name)
    return [analysis_summary, *detail_items]


def _normalize_analysis_summary(value: Any, overall_level_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return f"分析总结：根据现有案件信息，综合判定为“{overall_level_name}”。"
    if text.startswith("分析总结："):
        return text
    return f"分析总结：{text}"


def _case_type_detail_summary_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    items: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        case_type = str(item.get("caseType") or "").strip()
        reason = _compact_reason(item.get("reason"))
        if not case_type or not reason:
            continue
        items.append(f"{case_type}：{reason}")
    return items


def _build_case_type_summary_from_details(value: Any, overall_level_name: str) -> str:
    case_types = _case_type_names(value)
    if not case_types or case_types == ["未命中二级以上警情"]:
        return "分析总结：未发现明确二级以上警情。"
    if overall_level_name == "建议复核":
        return f"分析总结：疑似涉及{'、'.join(case_types)}，建议复核。"
    return f"分析总结：涉及{'、'.join(case_types)}。"


def _case_type_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        case_type = str(item.get("caseType") or "").strip()
        if not case_type or case_type in seen:
            continue
        seen.add(case_type)
        names.append(case_type)
    return names


def _compact_reason(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    for delimiter in ("。", "；", ";", "\n"):
        if delimiter in text:
            head = text.split(delimiter, 1)[0].strip()
            if head:
                text = head
                break
    max_length = 80
    if len(text) > max_length:
        text = text[:max_length].rstrip("，,、；;。") + "..."
    elif text and text[-1] not in "。！？.!?...":
        text = f"{text}。"
    return text


def _legacy_case_type_detail_items(
    llm_out: dict[str, Any],
    voice_level_name: str | None,
) -> list[str]:
    items: list[str] = []
    case_type = str(llm_out.get("caseTypeSummary") or "").strip()
    if case_type:
        items.append(f"综合风险分析：{case_type}")

    voice_text = voice_level_name or "暂无音频分析结果"
    items.append(f"音频识别：{voice_text}")

    addr = str(llm_out.get("highRiskAddressSummary") or "").strip()
    if addr:
        items.append(f"高发案地址：{addr}")

    person = str(llm_out.get("highRiskPersonSummary") or "").strip()
    if person:
        items.append(f"涉案人员风险：{person}")

    if not items:
        items.append("未命中二级以上警情：现有信息未出现明确二级及以上警情线索。")
    return items
