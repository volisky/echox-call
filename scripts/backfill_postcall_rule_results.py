"""Generate missing rule-evaluated results from persisted timeline segments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from echox_call.core.db import connect
from echox_call.features.audio_analysis.postcall.attention_rules import load_attention_rules
from echox_call.features.audio_analysis.postcall.repository import API_RESULT_VERSION
from echox_call.features.audio_analysis.postcall.schemas import PostcallJobResultData


RULES_PATH = Path("config/postcall_attention_rules.yaml")


def main() -> int:
    rules = load_attention_rules(RULES_PATH)
    generated = 0
    skipped = 0

    with connect(autocommit=False) as conn:
        jobs = conn.execute(
            """
            SELECT
                job.id,
                job.job_id,
                job.jjdh,
                job.state
            FROM postcall_jobs AS job
            WHERE job.state = 'completed'
              AND NOT EXISTS (
                  SELECT 1
                  FROM postcall_analysis_results AS result
                  WHERE result.postcall_job_id = job.id
              )
            ORDER BY job.created_at ASC
            """
        ).fetchall()

        for job_row in jobs:
            job = dict(job_row)
            timeline_rows = conn.execute(
                """
                SELECT
                    id,
                    segment_payload
                FROM postcall_timeline_segments
                WHERE postcall_job_id = %(job_id)s
                ORDER BY start_sec ASC, end_sec ASC, segment_id ASC
                """,
                {"job_id": job["id"]},
            ).fetchall()
            timeline = [
                row["segment_payload"]
                for row in timeline_rows
                if isinstance(row["segment_payload"], dict)
            ]
            if not timeline:
                skipped += 1
                print(f"skip no timeline: {job['job_id']}")
                continue

            evaluation = rules.evaluate(timeline)
            api_payload = {
                "jobId": job["job_id"],
                "jjdh": job["jjdh"],
                "state": job["state"],
                "needAttention": evaluation.need_attention,
                "timeline": timeline,
                "insights": evaluation.insights,
            }
            validated_payload = PostcallJobResultData.model_validate(api_payload).model_dump(
                mode="json"
            )
            fusion_trace = {
                "ruleVersion": evaluation.rule_version,
                "matchedRuleCodes": evaluation.matched_rule_codes,
            }
            audio_processing = {
                "timelineSegmentCount": len(timeline),
                "attentionRuleVersion": evaluation.rule_version,
                "needAttention": evaluation.need_attention,
                "insightCount": len(evaluation.insights),
                "matchedRuleCodes": evaluation.matched_rule_codes,
            }
            model_versions = _load_model_versions(conn, job["id"])

            with conn.transaction():
                result = conn.execute(
                    """
                    INSERT INTO postcall_analysis_results (
                        postcall_job_id,
                        risk_level,
                        need_attention,
                        confidence,
                        risk_types,
                        summary,
                        recommended_actions,
                        model_versions,
                        audio_processing,
                        fusion_trace,
                        api_result_payload,
                        api_result_version,
                        api_result_generated_at,
                        analysis_mode,
                        risk_evaluated
                    )
                    VALUES (
                        %s,
                        'unknown',
                        %s,
                        NULL,
                        ARRAY[]::text[],
                        '',
                        ARRAY[]::text[],
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        now(),
                        'rule_evaluated',
                        true
                    )
                    RETURNING id
                    """,
                    (
                        job["id"],
                        evaluation.need_attention,
                        Jsonb(model_versions),
                        Jsonb(audio_processing),
                        Jsonb(fusion_trace),
                        Jsonb(validated_payload),
                        API_RESULT_VERSION,
                    ),
                ).fetchone()
                result_id = result["id"]

                conn.execute(
                    """
                    UPDATE postcall_timeline_segments
                    SET analysis_result_id = %s
                    WHERE postcall_job_id = %s
                    """,
                    (result_id, job["id"]),
                )
                conn.execute(
                    """
                    UPDATE postcall_timeline_events
                    SET analysis_result_id = %s
                    WHERE postcall_job_id = %s
                    """,
                    (result_id, job["id"]),
                )
                _insert_evidence_segments(
                    conn,
                    job_id=job["id"],
                    result_id=result_id,
                    rule_version=evaluation.rule_version,
                    insights=evaluation.insights,
                )
            generated += 1
            print(
                "generated rule result: "
                f"{job['job_id']} needAttention={evaluation.need_attention} "
                f"insights={len(evaluation.insights)}"
            )

    print(f"done generated={generated} skipped={skipped}")
    return 0


def _load_model_versions(conn: Any, postcall_job_id: Any) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            model_role,
            model_name,
            model_version
        FROM postcall_model_runs
        WHERE postcall_job_id = %s
        ORDER BY created_at ASC, model_role ASC
        """,
        (postcall_job_id,),
    ).fetchall()
    return {
        row["model_role"]: {
            "modelName": row["model_name"],
            "modelVersion": row["model_version"],
        }
        for row in rows
    }


def _insert_evidence_segments(
    conn: Any,
    *,
    job_id: Any,
    result_id: Any,
    rule_version: str,
    insights: list[dict[str, Any]],
) -> None:
    index = 1
    for insight in insights:
        if not insight.get("needAttention"):
            continue
        payload = {
            "ruleVersion": rule_version,
            "matchedRuleCodes": insight.get("matchedRuleCodes", []),
            "evidence": insight.get("evidence", []),
            "insight": insight,
        }
        conn.execute(
            """
            INSERT INTO postcall_evidence_segments (
                postcall_job_id,
                analysis_result_id,
                segment_id,
                start_sec,
                end_sec,
                risk_level,
                reason,
                recommended_action,
                clip_uri,
                payload
            )
            VALUES (%s, %s, %s, %s, %s, 'unknown', %s, NULL, NULL, %s)
            """,
            (
                job_id,
                result_id,
                f"insight_{index:06d}",
                insight["startSec"],
                insight["endSec"],
                insight["reason"],
                Jsonb(payload),
            ),
        )
        index += 1


if __name__ == "__main__":
    raise SystemExit(main())
