"""Database access for postcall audio analysis jobs."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from echox_call.core.auth import ApiClient
from echox_call.core.db import connect
from echox_call.features.audio_analysis.postcall.attention_rules import AttentionEvaluation
from echox_call.features.audio_analysis.postcall.llm_repository import (
    PostcallLlmJobRepository,
    _build_summary,
)
from echox_call.features.audio_analysis.postcall.schemas import (
    CreatePostcallJobRequest,
    InputSnapshot,
    OverallResult,
    PostcallJobCreateResult,
    PostcallJobResultData,
    PostcallReviewSegment,
    RiskPerson,
    VoiceResult,
)
from echox_call.features.audio_analysis.postcall.worker_models import (
    AudioAssetRecord,
    ClaimedPostcallJob,
    ModelRunRecord,
    TimelineSegmentRecord,
)


INSERT_JOB_SQL = """
INSERT INTO postcall_jobs (
    jjdh,
    audio_url,
    bjsj,
    jcjxtjsdwmc,
    jjdwmc,
    gxdwmc,
    bjdh,
    bjrmc,
    bjrxbdm,
    lxdh,
    jqdz,
    bjnr,
    jqlbdm,
    jqlxdm,
    jqxldm,
    jqzldm,
    jqdj,
    callback_url,
    asr_result,
    raw_payload,
    client_id,
    source_system
)
VALUES (
    %(jjdh)s,
    %(audio_url)s,
    %(bjsj)s,
    %(jcjxtjsdwmc)s,
    %(jjdwmc)s,
    %(gxdwmc)s,
    %(bjdh)s,
    %(bjrmc)s,
    %(bjrxbdm)s,
    %(lxdh)s,
    %(jqdz)s,
    %(bjnr)s,
    %(jqlbdm)s,
    %(jqlxdm)s,
    %(jqxldm)s,
    %(jqzldm)s,
    %(jqdj)s,
    %(callback_url)s,
    %(asr_result)s,
    %(raw_payload)s,
    %(client_id)s,
    %(source_system)s
)
ON CONFLICT (jjdh) DO NOTHING
RETURNING id, job_id, jjdh, state, audio_url, duplicate_count
"""


SELECT_JOB_FOR_UPDATE_SQL = """
SELECT id, job_id, jjdh, state, audio_url, duplicate_count
FROM postcall_jobs
WHERE jjdh = %s
FOR UPDATE
"""


REQUEUE_DUPLICATE_JOB_SQL = """
UPDATE postcall_jobs
SET
    audio_url = %(audio_url)s,
    bjsj = %(bjsj)s,
    jcjxtjsdwmc = %(jcjxtjsdwmc)s,
    jjdwmc = %(jjdwmc)s,
    gxdwmc = %(gxdwmc)s,
    bjdh = %(bjdh)s,
    bjrmc = %(bjrmc)s,
    bjrxbdm = %(bjrxbdm)s,
    lxdh = %(lxdh)s,
    jqdz = %(jqdz)s,
    bjnr = %(bjnr)s,
    jqlbdm = %(jqlbdm)s,
    jqlxdm = %(jqlxdm)s,
    jqxldm = %(jqxldm)s,
    jqzldm = %(jqzldm)s,
    jqdj = %(jqdj)s,
    callback_url = %(callback_url)s,
    asr_result = %(asr_result)s,
    raw_payload = %(raw_payload)s,
    client_id = %(client_id)s,
    source_system = %(source_system)s,
    state = 'processing_queued',
    duplicate_count = duplicate_count + 1,
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    attempt_count = 0,
    next_run_at = now(),
    last_heartbeat_at = NULL,
    started_at = NULL,
    completed_at = NULL,
    audio_completed_at = NULL,
    audio_analysis_data = '{}'::jsonb,
    failed_at = NULL,
    error_code = NULL,
    error_message = NULL,
    updated_at = now()
WHERE id = %(job_internal_id)s
RETURNING id, job_id, jjdh, state, audio_url, duplicate_count
"""


SELECT_JOB_RESULT_SQL = """
SELECT
    pj.id,
    pj.job_id,
    pj.jjdh,
    pj.state,
    pj.raw_payload,
    pj.audio_completed_at,
    pj.audio_analysis_data,
    plj.state AS llm_state,
    plj.llm_output,
    par.api_result_payload
FROM postcall_jobs AS pj
LEFT JOIN postcall_llm_jobs AS plj ON plj.postcall_job_id = pj.id
LEFT JOIN postcall_analysis_results AS par ON par.postcall_job_id = pj.id
WHERE pj.job_id = %s
  AND pj.client_id = %s
"""


SELECT_COMPLETED_JOBS_FOR_ATTENTION_RECOMPUTE_SQL = """
SELECT
    job.id,
    job.job_id,
    job.jjdh,
    job.audio_url,
    job.bjsj,
    job.callback_url,
    job.attempt_count,
    job.max_attempts,
    job.duplicate_count,
    result.id AS analysis_result_id,
    result.api_result_payload,
    result.model_versions,
    result.audio_processing,
    result.rule_version,
    result.matched_rule_codes
FROM postcall_jobs AS job
JOIN postcall_analysis_results AS result
  ON result.postcall_job_id = job.id
WHERE job.state = 'completed'
  AND (%(job_id)s::text IS NULL OR job.job_id = %(job_id)s)
ORDER BY job.created_at ASC, job.id ASC
"""


SELECT_TIMELINE_PAYLOADS_FOR_RECOMPUTE_SQL = """
SELECT
    segment_id,
    start_sec,
    end_sec,
    speaker_label,
    speaker_role,
    role_source,
    audio_event_scores,
    voice_emotion_scores,
    voice_emotion_dimensions
FROM postcall_timeline_segments
WHERE postcall_job_id = %s
ORDER BY start_sec ASC, end_sec ASC, segment_id ASC
"""


CLAIM_NEXT_JOB_SQL = """
WITH candidate AS (
    SELECT id
    FROM postcall_jobs
    WHERE state = 'processing_queued'
      AND next_run_at <= now()
      AND attempt_count < max_attempts
      AND (locked_until IS NULL OR locked_until < now())
    ORDER BY priority DESC, next_run_at ASC, created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE postcall_jobs AS job
SET
    state = 'processing_downloading',
    locked_by = %(worker_id)s,
    locked_at = now(),
    locked_until = now() + make_interval(secs => %(lock_seconds)s),
    started_at = COALESCE(job.started_at, now()),
    attempt_count = job.attempt_count + 1,
    updated_at = now()
FROM candidate
WHERE job.id = candidate.id
RETURNING
    job.id,
    job.job_id,
    job.jjdh,
    job.audio_url,
    job.bjsj,
    job.callback_url,
    job.attempt_count,
    job.max_attempts,
    job.duplicate_count
"""


RECOVER_EXPIRED_JOBS_SQL = """
WITH expired AS (
    UPDATE postcall_jobs
    SET
        state = CASE
            WHEN attempt_count < max_attempts THEN 'processing_queued'
            ELSE 'failed'
        END,
        failed_at = CASE
            WHEN attempt_count < max_attempts THEN NULL
            ELSE now()
        END,
        error_code = 'WORKER_LOCK_EXPIRED',
        error_message = concat(
            'worker lock expired while state=',
            state,
            '; previousWorker=',
            COALESCE(locked_by, ''),
            '; attempt=',
            attempt_count,
            '/',
            max_attempts
        ),
        locked_by = NULL,
        locked_at = NULL,
        locked_until = NULL,
        next_run_at = CASE
            WHEN attempt_count < max_attempts THEN now()
            ELSE next_run_at
        END,
        updated_at = now()
    WHERE state IN ('processing_downloading', 'processing_analyzing')
      AND locked_until IS NOT NULL
      AND locked_until < now()
    RETURNING state
)
SELECT
    count(*)::integer AS recovered_count,
    count(*) FILTER (WHERE state = 'processing_queued')::integer AS requeued_count,
    count(*) FILTER (WHERE state = 'failed')::integer AS failed_count
FROM expired
"""


MARK_ANALYZING_SQL = """
UPDATE postcall_jobs
SET
    state = 'processing_analyzing',
    locked_until = now() + make_interval(secs => %(lock_seconds)s),
    updated_at = now()
WHERE id = %(job_internal_id)s
  AND duplicate_count = %(duplicate_count)s
RETURNING id
"""


RECORD_FAILURE_SQL = """
UPDATE postcall_jobs
SET
    state = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts THEN 'processing_queued'
        ELSE 'failed'
    END,
    failed_at = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts THEN NULL
        ELSE now()
    END,
    error_code = %(error_code)s,
    error_message = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts THEN concat(
            %(error_message)s::text,
            '; retryScheduled=true; nextAttempt=',
            attempt_count + 1,
            '/',
            max_attempts,
            '; retryDelaySeconds=',
            %(retry_delay_seconds)s::integer
        )
        ELSE concat(
            %(error_message)s::text,
            '; retryScheduled=false; attempt=',
            attempt_count,
            '/',
            max_attempts
        )
    END,
    locked_by = NULL,
    locked_at = NULL,
    locked_until = NULL,
    next_run_at = CASE
        WHEN %(retryable)s AND attempt_count < max_attempts
            THEN now() + make_interval(secs => %(retry_delay_seconds)s)
        ELSE next_run_at
    END,
    updated_at = now()
WHERE id = %(job_internal_id)s
  AND duplicate_count = %(duplicate_count)s
RETURNING state, attempt_count, max_attempts, next_run_at
"""


INSERT_AUDIO_ASSET_SQL = """
INSERT INTO postcall_audio_assets (
    postcall_job_id,
    asset_type,
    uri,
    content_type,
    sha256,
    sample_rate,
    channels,
    duration_sec,
    size_bytes,
    metadata
)
SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
WHERE EXISTS (
    SELECT 1
    FROM postcall_jobs
    WHERE id = %s
      AND duplicate_count = %s
)
RETURNING id
"""


INSERT_MODEL_RUN_SQL = """
INSERT INTO postcall_model_runs (
    postcall_job_id,
    model_name,
    model_version,
    model_role,
    status,
    started_at,
    completed_at,
    duration_ms,
    input_ref,
    metrics,
    output_summary,
    error_code,
    error_message
)
SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
WHERE EXISTS (
    SELECT 1
    FROM postcall_jobs
    WHERE id = %s
      AND duplicate_count = %s
)
RETURNING id
"""


DELETE_TIMELINE_SEGMENTS_SQL = """
DELETE FROM postcall_timeline_segments
WHERE postcall_job_id = %s
"""


DELETE_ANALYSIS_RESULT_SQL = """
DELETE FROM postcall_analysis_results
WHERE postcall_job_id = %s
"""


DELETE_REVIEW_SEGMENTS_SQL = """
DELETE FROM postcall_review_segments
WHERE postcall_job_id = %s
"""


DELETE_AUDIO_ASSETS_SQL = """
DELETE FROM postcall_audio_assets
WHERE postcall_job_id = %s
"""


DELETE_MODEL_RUNS_SQL = """
DELETE FROM postcall_model_runs
WHERE postcall_job_id = %s
"""


SELECT_CURRENT_JOB_VERSION_FOR_UPDATE_SQL = """
SELECT id
FROM postcall_jobs
WHERE id = %s
  AND duplicate_count = %s
FOR UPDATE
"""


INSERT_ANALYSIS_RESULT_SQL = """
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
    %s,
    %s,
    %s,
    %s,
    %s,
    %s,
    %s,
    %s,
    %s,
    %s,
    now()
)
RETURNING id
"""


UPDATE_RECOMPUTED_ANALYSIS_RESULT_SQL = """
UPDATE postcall_analysis_results
SET
    attention_level = %s,
    attention_level_name = %s,
    rule_version = %s,
    matched_rule_codes = %s,
    fusion_trace = %s,
    api_result_payload = %s,
    api_result_version = %s,
    api_result_generated_at = now(),
    updated_at = now()
WHERE id = %s
"""


INSERT_REVIEW_SEGMENT_SQL = """
INSERT INTO postcall_review_segments (
    postcall_job_id,
    analysis_result_id,
    segment_id,
    start_sec,
    end_sec,
    level,
    level_name,
    result,
    reason,
    confidence,
    matched_rule_codes,
    audio_events,
    voice_states,
    source_segments,
    payload
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


INSERT_TIMELINE_SEGMENT_SQL = """
INSERT INTO postcall_timeline_segments (
    postcall_job_id,
    analysis_result_id,
    segment_id,
    start_sec,
    end_sec,
    speaker_label,
    speaker_role,
    role_source,
    audio_event_scores,
    voice_emotion_scores,
    voice_detailed_scores,
    voice_emotion_dimensions,
    internal_payload
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""


SET_AUDIO_DONE_SQL = """
UPDATE postcall_jobs
SET
    audio_completed_at  = now(),
    audio_analysis_data = %(audio_analysis_data)s,
    locked_by    = NULL,
    locked_at    = NULL,
    locked_until = NULL,
    updated_at   = now()
WHERE id = %(job_internal_id)s
  AND duplicate_count = %(duplicate_count)s
RETURNING id
"""


class PostcallJobRepositoryError(RuntimeError):
    """Raised when a postcall job cannot be persisted."""


class PostcallJobNotFoundError(RuntimeError):
    """Raised when the current API client cannot access a postcall job."""


class PostcallResultContractError(RuntimeError):
    """Raised when stored postcall result JSON does not match the public API contract."""


class PostcallJobStaleError(RuntimeError):
    """Raised when a worker tries to write a previous submission version."""


class PostcallJobRepository:
    """Persist postcall jobs and requeue same-jjdh submissions for analysis."""

    def __init__(self, llm_repository: PostcallLlmJobRepository | None = None) -> None:
        self._llm_repo = llm_repository or PostcallLlmJobRepository()

    def create_or_requeue(
        self,
        request: CreatePostcallJobRequest,
        client: ApiClient,
    ) -> PostcallJobCreateResult:
        params = _build_insert_params(request, client)

        with connect(autocommit=False) as conn:
            with conn.transaction():
                inserted = conn.execute(INSERT_JOB_SQL, params).fetchone()
                if inserted is not None:
                    self._llm_repo.insert_llm_job(
                        conn,
                        postcall_job_id=inserted["id"],
                        job_id=inserted["job_id"],
                    )
                    return PostcallJobCreateResult(
                        job_id=inserted["job_id"],
                        jjdh=inserted["jjdh"],
                        state=inserted["state"],
                        duplicate=False,
                        duplicate_count=inserted["duplicate_count"],
                    )

                existing = conn.execute(SELECT_JOB_FOR_UPDATE_SQL, (request.jjdh,)).fetchone()
                if existing is None:
                    raise PostcallJobRepositoryError(
                        f"postcall job disappeared during duplicate handling: {request.jjdh}"
                    )

                _delete_job_outputs(conn, existing["id"])

                params["job_internal_id"] = existing["id"]
                updated = conn.execute(REQUEUE_DUPLICATE_JOB_SQL, params).fetchone()
                if updated is None:
                    raise PostcallJobRepositoryError(
                        f"failed to requeue duplicate postcall job: {request.jjdh}"
                    )

                self._llm_repo.reset_llm_job(conn, postcall_job_id=existing["id"])

                return PostcallJobCreateResult(
                    job_id=updated["job_id"],
                    jjdh=updated["jjdh"],
                    state=updated["state"],
                    duplicate=True,
                    duplicate_count=updated["duplicate_count"],
                )

    def get_result(
        self,
        job_id: str,
        client: ApiClient,
    ) -> PostcallJobResultData:
        with connect() as conn:
            row = conn.execute(
                SELECT_JOB_RESULT_SQL,
                (job_id, client.client_id),
            ).fetchone()
        if row is None:
            raise PostcallJobNotFoundError("postcall job not found")

        if row["state"] == "completed" and isinstance(row["api_result_payload"], dict):
            payload = row["api_result_payload"]
            if "level" in payload and "overallResult" not in payload:
                # Pre-migration legacy row; return completed state without overallResult.
                try:
                    return PostcallJobResultData.model_validate({
                        "jobId": payload.get("jobId") or row["job_id"],
                        "jjdh": payload.get("jjdh") or row["jjdh"],
                        "state": "completed",
                    })
                except ValidationError as exc:
                    raise PostcallResultContractError(
                        f"stored API result payload is invalid for jobId {job_id}"
                    ) from exc
            try:
                return PostcallJobResultData.model_validate(payload)
            except ValidationError as exc:
                raise PostcallResultContractError(
                    f"stored API result payload is invalid for jobId {job_id}"
                ) from exc

        partial_result = _build_partial_overall_result(row)
        try:
            payload: dict[str, Any] = {
                "jobId": row["job_id"],
                "jjdh": row["jjdh"],
                "state": row["state"],
            }
            if partial_result is not None:
                payload["overallResult"] = partial_result
            return PostcallJobResultData.model_validate(payload)
        except ValidationError as exc:
            raise PostcallResultContractError(
                f"stored postcall result is invalid for jobId {job_id}"
            ) from exc

    def list_completed_jobs_for_attention_recompute(
        self,
        *,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List completed jobs whose saved timeline can be re-evaluated by rules only."""

        with connect() as conn:
            rows = conn.execute(
                SELECT_COMPLETED_JOBS_FOR_ATTENTION_RECOMPUTE_SQL,
                {"job_id": job_id},
            ).fetchall()
        return [dict(row) for row in rows]

    def get_timeline_payloads_for_attention_recompute(
        self,
        *,
        job_internal_id: Any,
    ) -> list[dict[str, Any]]:
        """Rebuild the public timeline snapshot from structured segment columns."""

        with connect() as conn:
            rows = conn.execute(
                SELECT_TIMELINE_PAYLOADS_FOR_RECOMPUTE_SQL,
                (job_internal_id,),
            ).fetchall()
        timeline: list[dict[str, Any]] = []
        for row in rows:
            timeline.append(_timeline_payload_from_row(row))
        return timeline

    def persist_recomputed_attention(
        self,
        *,
        job_row: dict[str, Any],
        timeline: list[dict[str, Any]],
        attention_evaluation: AttentionEvaluation,
    ) -> dict[str, Any]:
        """Update stored audio analysis data after rule-only recomputation.

        NOTE: The overall level/levelName in the public API result is determined by the
        LLM worker. This method only updates the audio analysis portion (voice level,
        review segments). The final api_result_payload is NOT updated here — a re-run
        of the LLM worker would be needed to regenerate the overall result.
        """

        claimed_job = ClaimedPostcallJob(
            internal_id=job_row["id"],
            job_id=job_row["job_id"],
            jjdh=job_row["jjdh"],
            audio_url=job_row["audio_url"],
            bjsj=job_row["bjsj"],
            callback_url=job_row["callback_url"],
            attempt_count=job_row["attempt_count"],
            max_attempts=job_row["max_attempts"],
            duplicate_count=job_row["duplicate_count"],
        )

        audio_analysis_data = _build_audio_analysis_data(
            model_versions=job_row.get("model_versions") or {},
            audio_processing=job_row.get("audio_processing") or {},
            attention_evaluation=attention_evaluation,
        )

        with connect(autocommit=False) as conn:
            with conn.transaction():
                _lock_current_job_version(conn, claimed_job)
                conn.execute(DELETE_REVIEW_SEGMENTS_SQL, (job_row["id"],))
                conn.execute(
                    "UPDATE postcall_jobs SET audio_analysis_data = %s, updated_at = now() WHERE id = %s",
                    (Jsonb(audio_analysis_data), job_row["id"]),
                )
                _insert_review_segments(
                    conn,
                    job=claimed_job,
                    analysis_result_id=job_row.get("analysis_result_id"),
                    attention_evaluation=attention_evaluation,
                )
        return {
            "jobId": job_row["job_id"],
            "jjdh": job_row["jjdh"],
            "voiceLevel": attention_evaluation.level,
            "voiceLevelName": attention_evaluation.level_name,
            "reviewSegmentCount": len(attention_evaluation.review_segments),
            "matchedRuleCodes": attention_evaluation.matched_rule_codes,
            "ruleVersion": attention_evaluation.rule_version,
        }

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lock_seconds: int,
    ) -> ClaimedPostcallJob | None:
        with connect(autocommit=False) as conn:
            with conn.transaction():
                row = conn.execute(
                    CLAIM_NEXT_JOB_SQL,
                    {
                        "worker_id": worker_id,
                        "lock_seconds": lock_seconds,
                    },
                ).fetchone()
        if row is None:
            return None
        return ClaimedPostcallJob(
            internal_id=row["id"],
            job_id=row["job_id"],
            jjdh=row["jjdh"],
            audio_url=row["audio_url"],
            bjsj=row["bjsj"],
            callback_url=row["callback_url"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            duplicate_count=row["duplicate_count"],
        )

    def recover_expired_jobs(self) -> dict[str, int]:
        """Recover jobs left in processing states after their worker lock expired."""

        with connect(autocommit=True) as conn:
            row = conn.execute(RECOVER_EXPIRED_JOBS_SQL).fetchone()
        if row is None:
            return {"recovered_count": 0, "requeued_count": 0, "failed_count": 0}
        return {
            "recovered_count": row["recovered_count"],
            "requeued_count": row["requeued_count"],
            "failed_count": row["failed_count"],
        }

    def mark_analyzing(
        self,
        *,
        job: ClaimedPostcallJob,
        lock_seconds: int,
    ) -> None:
        with connect(autocommit=True) as conn:
            row = conn.execute(
                MARK_ANALYZING_SQL,
                {
                    "job_internal_id": job.internal_id,
                    "duplicate_count": job.duplicate_count,
                    "lock_seconds": lock_seconds,
                },
            ).fetchone()
        if row is None:
            raise PostcallJobStaleError(
                f"postcall job {job.job_id} was superseded before analyzing"
            )

    def record_failure(
        self,
        *,
        job: ClaimedPostcallJob,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int,
    ) -> None:
        with connect(autocommit=True) as conn:
            row = conn.execute(
                RECORD_FAILURE_SQL,
                {
                    "job_internal_id": job.internal_id,
                    "duplicate_count": job.duplicate_count,
                    "error_code": error_code,
                    "error_message": error_message,
                    "retryable": retryable,
                    "retry_delay_seconds": retry_delay_seconds,
                },
            ).fetchone()
        if row is None:
            raise PostcallJobStaleError(
                f"postcall job {job.job_id} was superseded before recording failure"
            )

    def mark_failed(
        self,
        *,
        job: ClaimedPostcallJob,
        error_code: str,
        error_message: str,
    ) -> None:
        self.record_failure(
            job=job,
            error_code=error_code,
            error_message=error_message,
            retryable=False,
            retry_delay_seconds=0,
        )

    def insert_audio_asset(
        self,
        *,
        job: ClaimedPostcallJob,
        asset: AudioAssetRecord,
    ) -> None:
        with connect(autocommit=True) as conn:
            row = conn.execute(
                INSERT_AUDIO_ASSET_SQL,
                (
                    job.internal_id,
                    asset.asset_type,
                    asset.uri,
                    asset.content_type,
                    asset.sha256,
                    asset.sample_rate,
                    asset.channels,
                    asset.duration_sec,
                    asset.size_bytes,
                    Jsonb(asset.metadata),
                    job.internal_id,
                    job.duplicate_count,
                ),
            ).fetchone()
        if row is None:
            raise PostcallJobStaleError(
                f"postcall job {job.job_id} was superseded before recording audio asset"
            )

    def persist_success(
        self,
        *,
        job: ClaimedPostcallJob,
        segments: list[TimelineSegmentRecord],
        model_runs: list[ModelRunRecord],
        model_versions: dict[str, Any],
        audio_processing: dict[str, Any],
        attention_evaluation: AttentionEvaluation,
    ) -> None:
        audio_analysis_data = _build_audio_analysis_data(
            model_versions=model_versions,
            audio_processing=audio_processing,
            attention_evaluation=attention_evaluation,
        )

        with connect(autocommit=False) as conn:
            with conn.transaction():
                _lock_current_job_version(conn, job)
                conn.execute(DELETE_REVIEW_SEGMENTS_SQL, (job.internal_id,))
                conn.execute(DELETE_TIMELINE_SEGMENTS_SQL, (job.internal_id,))

                for model_run in model_runs:
                    _insert_model_run(conn, job, model_run)

                _insert_review_segments(
                    conn,
                    job=job,
                    analysis_result_id=None,
                    attention_evaluation=attention_evaluation,
                )
                for segment in segments:
                    conn.execute(
                        INSERT_TIMELINE_SEGMENT_SQL,
                        (
                            job.internal_id,
                            None,
                            segment.segment_id,
                            segment.start_sec,
                            segment.end_sec,
                            segment.speaker_label,
                            segment.speaker_role,
                            segment.role_source,
                            Jsonb(segment.audio_event_scores),
                            Jsonb(segment.voice_emotion_scores),
                            Jsonb(segment.voice_detailed_scores),
                            Jsonb(segment.voice_emotion_dimensions),
                            Jsonb(segment.internal_payload),
                        ),
                    )

                done = conn.execute(
                    SET_AUDIO_DONE_SQL,
                    {
                        "job_internal_id": job.internal_id,
                        "duplicate_count": job.duplicate_count,
                        "audio_analysis_data": Jsonb(audio_analysis_data),
                    },
                ).fetchone()
                if done is None:
                    raise PostcallJobStaleError(
                        f"postcall job {job.job_id} was superseded before audio completion"
                    )

        self._llm_repo.try_mark_overall_completed(job.internal_id)

def _build_audio_analysis_data(
    *,
    model_versions: dict[str, Any],
    audio_processing: dict[str, Any],
    attention_evaluation: AttentionEvaluation,
) -> dict[str, Any]:
    review_segments: list[dict[str, Any]] = []
    if attention_evaluation.level in {1, 2}:
        review_segments = [
            {
                "startSec": seg["startSec"],
                "endSec": seg["endSec"],
                "result": seg.get("result") or seg.get("title", ""),
            }
            for seg in attention_evaluation.review_segments
        ]
    return {
        "attentionLevel": attention_evaluation.level,
        "attentionLevelName": attention_evaluation.level_name,
        "ruleVersion": attention_evaluation.rule_version,
        "matchedRuleCodes": attention_evaluation.matched_rule_codes,
        "modelVersions": model_versions,
        "audioProcessing": audio_processing,
        "reviewSegments": review_segments,
        "fusionTrace": {
            "ruleVersion": attention_evaluation.rule_version,
            "attentionConclusion": attention_evaluation.attention_conclusion,
            "priority": attention_evaluation.priority,
            "keyRiskFactors": attention_evaluation.key_risk_factors,
            "matchedRuleCodes": attention_evaluation.matched_rule_codes,
            "matchedCompositeInsights": attention_evaluation.matched_composite_insights,
            "suppressedInsights": attention_evaluation.suppressed_insights,
            "conflictStatus": attention_evaluation.conflict_status,
            "conflictReason": attention_evaluation.conflict_reason,
            "uncertaintyStatus": attention_evaluation.uncertainty_status,
            "audioQualityStatus": attention_evaluation.audio_quality_status,
            "highRiskTimeRanges": attention_evaluation.high_risk_time_ranges,
            "recommendedReviewTimeRanges": attention_evaluation.recommended_review_time_ranges,
            "evidenceSummary": attention_evaluation.evidence_summary,
            "confidenceSummary": attention_evaluation.confidence_summary,
            "debugInfo": attention_evaluation.debug_info,
        },
    }


def _build_partial_overall_result(row: Any) -> OverallResult | None:
    audio_data = row["audio_analysis_data"] if isinstance(row["audio_analysis_data"], dict) else {}
    llm_out = row["llm_output"] if isinstance(row["llm_output"], dict) else {}
    raw = row["raw_payload"] if isinstance(row["raw_payload"], dict) else {}

    audio_completed = row["audio_completed_at"] is not None
    llm_completed = row["llm_state"] == "completed" and bool(llm_out)
    if not audio_completed and not llm_completed:
        return None

    voice_result = _build_partial_voice_result(audio_data) if audio_completed else None
    if voice_result is None and not llm_completed:
        return None

    risk_person_raw = raw.get("riskPerson")
    risk_person: RiskPerson | None = None
    if isinstance(risk_person_raw, dict):
        try:
            risk_person = RiskPerson.model_validate(risk_person_raw)
        except ValidationError:
            pass

    input_snapshot = InputSnapshot(
        alarmContent=raw.get("alarmContent"),
        alarmAddress=raw.get("alarmAddress"),
        isHighIncidentAddress=raw.get("isHighIncidentAddress"),
    )

    if llm_completed:
        return _build_llm_partial_overall_result(
            llm_out=llm_out,
            voice_result=voice_result,
            input_snapshot=input_snapshot,
            risk_person=risk_person,
        )
    if voice_result is None or voice_result.level is None or voice_result.levelName is None:
        return None
    return OverallResult(
        level=voice_result.level,
        levelName=voice_result.levelName,
        summary=_build_voice_partial_summary(voice_result),
        voiceResult=voice_result,
        inputSnapshot=input_snapshot,
        riskPerson=risk_person,
    )


def _build_partial_voice_result(audio_data: dict[str, Any]) -> VoiceResult | None:
    voice_level = audio_data.get("attentionLevel")
    voice_level_name = audio_data.get("attentionLevelName")
    if voice_level not in {1, 2, 3} or not isinstance(voice_level_name, str):
        return None

    raw_review_segments: list[dict[str, Any]] = audio_data.get("reviewSegments") or []
    review_segments: list[PostcallReviewSegment] | None = None
    if voice_level in {1, 2} and raw_review_segments:
        review_segments = [
            PostcallReviewSegment(
                startSec=segment["startSec"],
                endSec=segment["endSec"],
                result=segment["result"],
            )
            for segment in raw_review_segments
        ]

    return VoiceResult(
        level=voice_level,
        levelName=voice_level_name,
        reviewSegments=review_segments,
    )


def _build_llm_partial_overall_result(
    *,
    llm_out: dict[str, Any],
    voice_result: VoiceResult | None,
    input_snapshot: InputSnapshot,
    risk_person: RiskPerson | None,
) -> OverallResult | None:
    level = llm_out.get("level")
    level_name = llm_out.get("levelName")
    if level not in {1, 2, 3} or not isinstance(level_name, str):
        return None

    voice_level_name = voice_result.levelName if voice_result is not None else None
    return OverallResult(
        level=level,
        levelName=level_name,
        summary=_build_summary(llm_out, level_name, voice_level_name),
        voiceResult=voice_result or VoiceResult(level=None, levelName=None),
        inputSnapshot=input_snapshot,
        riskPerson=risk_person,
    )


def _build_voice_partial_summary(voice_result: VoiceResult) -> list[str]:
    if voice_result.levelName is None:
        return ["音频识别：暂无音频分析结果。"]

    if voice_result.reviewSegments:
        segment_results = [
            segment.result
            for segment in voice_result.reviewSegments
            if segment.result
        ]
        if segment_results:
            return [
                f"音频识别：综合判定为“{voice_result.levelName}”。",
                *[f"音频片段：{result}" for result in segment_results],
            ]

    return [f"音频识别：综合判定为“{voice_result.levelName}”。"]


def _timeline_payload_from_row(row: Any) -> dict[str, Any]:
    return {
        "segmentId": row["segment_id"],
        "startSec": float(row["start_sec"]),
        "endSec": float(row["end_sec"]),
        "speakerLabel": row["speaker_label"],
        "speakerRole": row["speaker_role"],
        "roleSource": row["role_source"],
        "audioEventScores": row["audio_event_scores"],
        "voiceEmotionScores": row["voice_emotion_scores"],
        "voiceEmotionDimensions": row["voice_emotion_dimensions"],
    }


def _insert_review_segments(
    conn: Any,
    *,
    job: ClaimedPostcallJob,
    analysis_result_id: Any,
    attention_evaluation: AttentionEvaluation,
) -> None:
    if attention_evaluation.level not in {1, 2}:
        return
    for index, segment in enumerate(attention_evaluation.review_segments, start=1):
        segment_id = str(segment.get("segmentId") or f"review_{index:06d}")
        conn.execute(
            INSERT_REVIEW_SEGMENT_SQL,
            (
                job.internal_id,
                analysis_result_id,
                segment_id,
                segment["startSec"],
                segment["endSec"],
                attention_evaluation.level,
                attention_evaluation.level_name,
                segment["title"],
                segment["reason"],
                segment["confidence"],
                Jsonb(segment.get("matchedRuleCodes", [])),
                Jsonb(segment.get("audioEvents", [])),
                Jsonb(segment.get("voiceStates", [])),
                Jsonb(segment.get("sourceSegments", [])),
                Jsonb(segment),
            ),
        )


def _delete_job_outputs(conn: Any, job_internal_id: Any) -> None:
    conn.execute(DELETE_REVIEW_SEGMENTS_SQL, (job_internal_id,))
    conn.execute(DELETE_TIMELINE_SEGMENTS_SQL, (job_internal_id,))
    conn.execute(DELETE_ANALYSIS_RESULT_SQL, (job_internal_id,))
    conn.execute(DELETE_MODEL_RUNS_SQL, (job_internal_id,))
    conn.execute(DELETE_AUDIO_ASSETS_SQL, (job_internal_id,))


def _lock_current_job_version(conn: Any, job: ClaimedPostcallJob) -> None:
    row = conn.execute(
        SELECT_CURRENT_JOB_VERSION_FOR_UPDATE_SQL,
        (job.internal_id, job.duplicate_count),
    ).fetchone()
    if row is None:
        raise PostcallJobStaleError(
            f"postcall job {job.job_id} was superseded before writing result"
        )


def _insert_model_run(
    conn: Any,
    job: ClaimedPostcallJob,
    model_run: ModelRunRecord,
) -> None:
    row = conn.execute(
        INSERT_MODEL_RUN_SQL,
        (
            job.internal_id,
            model_run.model_name,
            model_run.model_version,
            model_run.model_role,
            model_run.status,
            model_run.started_at,
            model_run.completed_at,
            model_run.duration_ms,
            Jsonb(model_run.input_ref),
            Jsonb(model_run.metrics),
            Jsonb(model_run.output_summary),
            model_run.error_code,
            model_run.error_message,
            job.internal_id,
            job.duplicate_count,
        ),
    ).fetchone()
    if row is None:
        raise PostcallJobStaleError(
            f"postcall job {job.job_id} was superseded before recording model run"
        )


def _build_insert_params(
    request: CreatePostcallJobRequest,
    client: ApiClient,
) -> dict[str, Any]:
    return {
        "jjdh": request.jjdh,
        "audio_url": request.audioUrl,
        "bjsj": request.bjsj,
        "jcjxtjsdwmc": request.JCJXTJSDWMC,
        "jjdwmc": request.JJDWMC,
        "gxdwmc": request.GXDWMC,
        "bjdh": request.bjdh,
        "bjrmc": request.bjrmc,
        "bjrxbdm": request.bjrxbdm,
        "lxdh": request.lxdh,
        "jqdz": request.jqdz,
        "bjnr": request.bjnr,
        "jqlbdm": request.jqlbdm,
        "jqlxdm": request.jqlxdm,
        "jqxldm": request.jqxldm,
        "jqzldm": request.jqzldm,
        "jqdj": request.jqdj,
        "callback_url": request.callbackUrl,
        "asr_result": Jsonb(request.asr_result_json()),
        "raw_payload": Jsonb(request.raw_payload_json()),
        "client_id": client.client_id,
        "source_system": client.source_system,
    }
