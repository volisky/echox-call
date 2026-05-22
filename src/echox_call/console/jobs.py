"""Read-only postcall job queries for the management console."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import Any

from echox_call.core.db import connect


JOB_STATES_PENDING = {"processing_queued"}
JOB_STATES_ACTIVE = {"processing_downloading", "processing_analyzing"}


@dataclass(frozen=True)
class JobListFilters:
    keyword: str | None
    state: str | None
    source_system: str | None
    page: int
    page_size: int


@dataclass(frozen=True)
class JobListResult:
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
    total_pages: int
    state_options: list[str]
    source_system_options: list[str]
    summary: dict[str, int]


@dataclass(frozen=True)
class JobDetailResult:
    job: dict[str, Any]
    basic_fields: list[dict[str, str]]
    runtime_fields: list[dict[str, str]]
    error_fields: list[dict[str, str]]
    json_sections: list[dict[str, str]]
    rule_summary_fields: list[dict[str, str]]
    rule_insights: list[dict[str, str]]
    analysis_fields: list[dict[str, str]]
    analysis_json_sections: list[dict[str, str]]
    model_runs: list[dict[str, str]]
    timeline_segments: list[dict[str, str]]


@dataclass(frozen=True)
class JobAudioAsset:
    path: Path
    asset_type: str
    content_type: str
    size_bytes: int | None


class ConsoleJobRepository:
    """Query postcall jobs for read-only operational pages."""

    def get_summary(self) -> dict[str, int]:
        with connect() as conn:
            return _load_summary(conn)

    def list_jobs(self, filters: JobListFilters) -> JobListResult:
        where_sql, params = _build_where(filters)
        count_sql = f"SELECT count(*) AS count FROM postcall_jobs {where_sql}"
        select_sql = f"""
            SELECT
                job_id,
                jjdh,
                state,
                source_system,
                bjsj,
                created_at,
                updated_at,
                started_at,
                completed_at,
                failed_at,
                error_code,
                error_message,
                duplicate_count,
                attempt_count,
                max_attempts,
                result.attention_level AS analysis_level,
                result.attention_level_name AS analysis_level_name,
                COALESCE(
                    result.api_result_payload->'overallResult'->>'level',
                    result.api_result_payload->>'level'
                ) AS api_result_level,
                COALESCE(
                    result.api_result_payload->'overallResult'->>'levelName',
                    result.api_result_payload->>'levelName'
                ) AS api_result_level_name,
                jqlbdm,
                jqlxdm
            FROM postcall_jobs
            LEFT JOIN LATERAL (
                SELECT
                    attention_level,
                    attention_level_name,
                    api_result_payload
                FROM postcall_analysis_results
                WHERE postcall_job_id = postcall_jobs.id
                ORDER BY created_at DESC
                LIMIT 1
            ) AS result ON TRUE
            {where_sql}
            ORDER BY postcall_jobs.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """

        with connect() as conn:
            total = int(conn.execute(count_sql, params).fetchone()["count"])
            page_size = filters.page_size
            total_pages = max(1, ceil(total / page_size))
            page = min(max(filters.page, 1), total_pages)

            query_params = params | {
                "limit": page_size,
                "offset": (page - 1) * page_size,
            }
            row_offset = int(query_params["offset"])
            rows = [
                _present_job_row(dict(row), sequence=row_offset + index + 1)
                for index, row in enumerate(conn.execute(select_sql, query_params).fetchall())
            ]

            state_options = [
                row["state"]
                for row in conn.execute(
                    """
                    SELECT state
                    FROM postcall_jobs
                    GROUP BY state
                    ORDER BY state
                    """
                ).fetchall()
            ]
            source_system_options = [
                row["source_system"]
                for row in conn.execute(
                    """
                    SELECT source_system
                    FROM postcall_jobs
                    GROUP BY source_system
                    ORDER BY source_system
                    """
                ).fetchall()
            ]
            summary = _load_summary(conn)

        return JobListResult(
            rows=rows,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            state_options=state_options,
            source_system_options=source_system_options,
            summary=summary,
        )

    def get_job_detail(self, job_id: str) -> JobDetailResult | None:
        select_sql = """
            SELECT
                id,
                job_id,
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
                source_system,
                state,
                priority,
                duplicate_count,
                locked_by,
                locked_at,
                started_at,
                completed_at,
                failed_at,
                error_code,
                error_message,
                created_at,
                updated_at,
                locked_until,
                attempt_count,
                max_attempts,
                next_run_at,
                last_heartbeat_at
            FROM postcall_jobs
            WHERE job_id = %(job_id)s
            LIMIT 1
        """

        with connect() as conn:
            job_row = conn.execute(select_sql, {"job_id": job_id}).fetchone()
            if job_row is None:
                return None

            job = dict(job_row)
            analysis_result = conn.execute(
                """
                SELECT
                    id,
                    attention_level,
                    attention_level_name,
                    model_versions,
                    audio_processing,
                    rule_version,
                    matched_rule_codes,
                    api_result_payload,
                    api_result_version,
                    api_result_generated_at,
                    created_at,
                    updated_at
                FROM postcall_analysis_results
                WHERE postcall_job_id = %(postcall_job_id)s
                LIMIT 1
                """,
                {"postcall_job_id": job["id"]},
            ).fetchone()
            model_runs = conn.execute(
                """
                SELECT
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
                    error_message,
                    created_at
                FROM postcall_model_runs
                WHERE postcall_job_id = %(postcall_job_id)s
                ORDER BY started_at ASC NULLS LAST, created_at ASC, model_role ASC
                """,
                {"postcall_job_id": job["id"]},
            ).fetchall()
            timeline_segments = conn.execute(
                """
                SELECT
                    segment_id,
                    start_sec::double precision AS start_sec,
                    end_sec::double precision AS end_sec,
                    speaker_label,
                    speaker_role,
                    role_source,
                    audio_event_scores,
                    voice_emotion_scores,
                    voice_detailed_scores,
                    voice_emotion_dimensions,
                    internal_payload
                FROM postcall_timeline_segments
                WHERE postcall_job_id = %(postcall_job_id)s
                ORDER BY start_sec ASC, end_sec ASC, segment_id ASC
                """,
                {"postcall_job_id": job["id"]},
            ).fetchall()
            review_segments = conn.execute(
                """
                SELECT payload
                FROM postcall_review_segments
                WHERE postcall_job_id = %(postcall_job_id)s
                ORDER BY start_sec ASC, end_sec ASC, segment_id ASC
                """,
                {"postcall_job_id": job["id"]},
            ).fetchall()
            audio_assets = conn.execute(
                """
                SELECT
                    asset_type,
                    uri,
                    content_type,
                    size_bytes
                FROM postcall_audio_assets
                WHERE postcall_job_id = %(postcall_job_id)s
                ORDER BY
                    CASE asset_type
                        WHEN 'normalized' THEN 0
                        WHEN 'source' THEN 1
                        ELSE 2
                    END,
                    created_at DESC
                """,
                {"postcall_job_id": job["id"]},
            ).fetchall()

        if job is None:
            return None

        return _present_job_detail(
            job,
            dict(analysis_result) if analysis_result else None,
            [dict(row) for row in model_runs],
            [dict(row) for row in timeline_segments],
            [dict(row) for row in review_segments],
            _select_existing_audio_asset([dict(row) for row in audio_assets]),
        )

    def get_job_audio_asset(self, job_id: str) -> JobAudioAsset | None:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    asset.asset_type,
                    asset.uri,
                    asset.content_type,
                    asset.size_bytes
                FROM postcall_audio_assets AS asset
                JOIN postcall_jobs AS job ON job.id = asset.postcall_job_id
                WHERE job.job_id = %(job_id)s
                ORDER BY
                    CASE asset.asset_type
                        WHEN 'normalized' THEN 0
                        WHEN 'source' THEN 1
                        ELSE 2
                    END,
                    asset.created_at DESC
                """,
                {"job_id": job_id},
            ).fetchall()

        return _select_existing_audio_asset([dict(row) for row in rows])


def _build_where(filters: JobListFilters) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.state:
        clauses.append("state = %(state)s")
        params["state"] = filters.state

    if filters.source_system:
        clauses.append("source_system = %(source_system)s")
        params["source_system"] = filters.source_system

    if filters.keyword:
        clauses.append(
            """
            (
                jjdh ILIKE %(keyword)s ESCAPE '\\'
                OR job_id ILIKE %(keyword)s ESCAPE '\\'
                OR source_system ILIKE %(keyword)s ESCAPE '\\'
            )
            """
        )
        params["keyword"] = f"%{_escape_like(filters.keyword)}%"

    if not clauses:
        return "", params

    return "WHERE " + " AND ".join(clauses), params


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _load_summary(conn: Any) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT state, count(*) AS count
        FROM postcall_jobs
        GROUP BY state
        """
    ).fetchall()
    by_state = {row["state"]: int(row["count"]) for row in rows}
    return {
        "total": sum(by_state.values()),
        "pending": sum(by_state.get(state, 0) for state in JOB_STATES_PENDING),
        "active": sum(by_state.get(state, 0) for state in JOB_STATES_ACTIVE),
        "completed": by_state.get("completed", 0),
        "failed": by_state.get("failed", 0),
        "today_completed": _load_today_completed_count(conn),
    }


def _load_today_completed_count(conn: Any) -> int:
    row = conn.execute(
        """
        SELECT count(*) AS count
        FROM postcall_jobs
        WHERE completed_at >= CURRENT_DATE
          AND completed_at < CURRENT_DATE + INTERVAL '1 day'
        """
    ).fetchone()
    return int(row["count"])


def _present_job_row(row: dict[str, Any], *, sequence: int) -> dict[str, Any]:
    created_at = row.get("created_at")
    completed_at = row.get("completed_at")
    failed_at = row.get("failed_at")
    finished_at = completed_at or failed_at
    duration_text = _format_duration(created_at, finished_at)
    error_text = _summarize_error(row.get("error_code"), row.get("error_message"))
    state = str(row.get("state") or "unknown")
    analysis_level = (
        row.get("analysis_level")
        if row.get("analysis_level") is not None
        else row.get("api_result_level")
    )
    analysis_level_name = row.get("analysis_level_name") or row.get("api_result_level_name")

    return row | {
        "sequence": sequence,
        "state_label": _state_label(state),
        "state_key": state.replace("_", "-"),
        "analysis_result_text": _analysis_result_text(analysis_level, analysis_level_name),
        "analysis_result_key": _analysis_result_key(analysis_level, analysis_level_name),
        "analysis_result_title": _analysis_result_title(analysis_level, analysis_level_name),
        "bjsj_text": _format_datetime(row.get("bjsj")),
        "created_at_text": _format_datetime(created_at),
        "updated_at_text": _format_datetime(row.get("updated_at")),
        "duration_text": duration_text,
        "error_text": error_text,
        "error_title": error_text if error_text != "-" else "",
    }


def _present_job_detail(
    row: dict[str, Any],
    analysis_result: dict[str, Any] | None,
    model_runs: list[dict[str, Any]],
    timeline_segments: list[dict[str, Any]],
    review_segments: list[dict[str, Any]],
    audio_asset: JobAudioAsset | None,
) -> JobDetailResult:
    state = str(row.get("state") or "unknown")
    job = row | {
        "state_label": _state_label(state),
        "state_key": state.replace("_", "-"),
        "attempt_text": _format_attempts(row.get("attempt_count"), row.get("max_attempts")),
        "has_analysis_output": bool(analysis_result or timeline_segments),
        "has_local_audio": audio_asset is not None,
        "local_audio_asset_type": audio_asset.asset_type if audio_asset else "",
        "local_audio_asset_label": _audio_asset_label(audio_asset.asset_type)
        if audio_asset
        else "",
    }

    basic_fields = [
        _detail_field("jobId", row.get("job_id"), mono=True),
        _detail_field("接警单号", row.get("jjdh"), mono=True),
        _detail_field("报警时间", _format_datetime(row.get("bjsj"))),
        _detail_field("接警系统推送单位", row.get("jcjxtjsdwmc")),
        _detail_field("接警单位", row.get("jjdwmc")),
        _detail_field("管辖单位", row.get("gxdwmc")),
        _detail_field("报警电话", row.get("bjdh")),
        _detail_field("报警人", row.get("bjrmc")),
        _detail_field("报警人性别代码", row.get("bjrxbdm")),
        _detail_field("联系电话", row.get("lxdh")),
        _detail_field("警情地址", row.get("jqdz"), wide=True),
        _detail_field("报警内容", row.get("bjnr"), wide=True),
        _detail_field("警情等级", row.get("jqdj")),
        _detail_field("警情类别代码", row.get("jqlbdm"), mono=True),
        _detail_field("警情类型代码", row.get("jqlxdm"), mono=True),
        _detail_field("警情细类代码", row.get("jqxldm"), mono=True),
        _detail_field("警情子类代码", row.get("jqzldm"), mono=True),
    ]

    runtime_fields = [
        _detail_field("状态", f"{_state_label(state)} / {state}"),
        _detail_field("优先级", row.get("priority")),
        _detail_field("重复提交次数", row.get("duplicate_count")),
        _detail_field("尝试次数", job["attempt_text"]),
        _detail_field("锁定者", row.get("locked_by"), mono=True),
        _detail_field("锁定时间", _format_datetime(row.get("locked_at"))),
        _detail_field("锁过期时间", _format_datetime(row.get("locked_until"))),
        _detail_field("下次执行时间", _format_datetime(row.get("next_run_at"))),
        _detail_field("最近心跳时间", _format_datetime(row.get("last_heartbeat_at"))),
        _detail_field("开始时间", _format_datetime(row.get("started_at"))),
        _detail_field("完成时间", _format_datetime(row.get("completed_at"))),
        _detail_field("失败时间", _format_datetime(row.get("failed_at"))),
        _detail_field("创建时间", _format_datetime(row.get("created_at"))),
        _detail_field("更新时间", _format_datetime(row.get("updated_at"))),
        _detail_field("内部 ID", row.get("id"), mono=True),
    ]

    error_fields = [
        _detail_field("错误码", row.get("error_code"), mono=True),
        _detail_field("错误信息", row.get("error_message"), wide=True),
    ]

    json_sections = [
        {
            "title": "ASR 结果",
            "description": "postcall_jobs.asr_result",
            "content": _format_json(row.get("asr_result")),
        },
        {
            "title": "原始请求体",
            "description": "postcall_jobs.raw_payload",
            "content": _format_json(row.get("raw_payload")),
        },
    ]

    analysis_fields: list[dict[str, str]] = []
    analysis_json_sections: list[dict[str, str]] = []
    rule_summary_fields: list[dict[str, str]] = []
    rule_insights: list[dict[str, str]] = []
    if analysis_result:
        api_result_payload = analysis_result.get("api_result_payload")
        if not isinstance(api_result_payload, dict):
            api_result_payload = {}
        overall_result = api_result_payload.get("overallResult")
        if not isinstance(overall_result, dict):
            overall_result = {}
        matched_rule_codes = analysis_result.get("matched_rule_codes")
        review_segment_payloads = [
            row["payload"]
            for row in review_segments
            if isinstance(row["payload"], dict)
        ]
        if not review_segment_payloads:
            voice_result = overall_result.get("voiceResult") or {}
            api_review_segments = (
                voice_result.get("reviewSegments")
                or api_result_payload.get("reviewSegments")
            )
            review_segment_payloads = api_review_segments if isinstance(api_review_segments, list) else []
        rule_summary_fields = [
            _detail_field("规则判断完成", _format_bool(True)),
            _detail_field(
                "综合关注等级",
                _format_attention_level(
                    analysis_result.get("attention_level")
                    or overall_result.get("level")
                    or api_result_payload.get("level"),
                    analysis_result.get("attention_level_name")
                    or overall_result.get("levelName")
                    or api_result_payload.get("levelName"),
                ),
            ),
            _detail_field("规则版本", analysis_result.get("rule_version"), mono=True),
            _detail_field("命中规则", _format_string_list(matched_rule_codes), mono=True, wide=True),
        ]
        rule_insights = [
            _present_review_segment(segment)
            for segment in review_segment_payloads
            if isinstance(segment, dict)
        ]
        analysis_fields = [
            _detail_field("规则版本", analysis_result.get("rule_version"), mono=True),
            _detail_field("模型运行记录", len(model_runs)),
            _detail_field("时间线片段", len(timeline_segments)),
            _detail_field("API 结果版本", analysis_result.get("api_result_version")),
            _detail_field(
                "API 结果生成时间",
                _format_datetime(analysis_result.get("api_result_generated_at")),
            ),
            _detail_field("结果创建时间", _format_datetime(analysis_result.get("created_at"))),
            _detail_field("结果更新时间", _format_datetime(analysis_result.get("updated_at"))),
        ]
        analysis_json_sections = [
            {
                "title": "模型版本",
                "description": "postcall_analysis_results.model_versions",
                "content": _format_json(analysis_result.get("model_versions")),
            },
            {
                "title": "音频处理摘要",
                "description": "postcall_analysis_results.audio_processing",
                "content": _format_json(analysis_result.get("audio_processing")),
            },
            {
                "title": "API 返回 data 快照",
                "description": "postcall_analysis_results.api_result_payload",
                "content": _format_json(analysis_result.get("api_result_payload")),
            },
            {
                "title": "命中规则",
                "description": "postcall_analysis_results.matched_rule_codes",
                "content": _format_json(analysis_result.get("matched_rule_codes")),
            },
        ]

    return JobDetailResult(
        job=job,
        basic_fields=basic_fields,
        runtime_fields=runtime_fields,
        error_fields=error_fields,
        json_sections=json_sections,
        rule_summary_fields=rule_summary_fields,
        rule_insights=rule_insights,
        analysis_fields=analysis_fields,
        analysis_json_sections=analysis_json_sections,
        model_runs=[_present_model_run(row) for row in model_runs],
        timeline_segments=[_present_timeline_segment(row) for row in timeline_segments],
    )


def _state_label(state: str) -> str:
    labels = {
        "processing_queued": "待处理",
        "processing_downloading": "下载中",
        "processing_analyzing": "分析中",
        "completed": "已完成",
        "failed": "失败",
        "failed_cancelled": "已取消",
    }
    return labels.get(state, state)


def _analysis_result_text(level: Any, level_name: Any) -> str:
    name_text = _format_value(level_name)
    if name_text != "-":
        return name_text

    level_text = _format_value(level)
    if level_text == "-":
        return "-"
    return f"level {level_text}"


def _analysis_result_title(level: Any, level_name: Any) -> str:
    level_text = _format_value(level)
    name_text = _format_value(level_name)
    if level_text == "-" and name_text == "-":
        return "暂无分析结果"
    return f"level={level_text}，levelName={name_text}"


def _analysis_result_key(level: Any, level_name: Any) -> str:
    try:
        level_number = int(level)
    except (TypeError, ValueError):
        level_number = None

    if level_number == 1:
        return "attention"
    if level_number == 2:
        return "review"
    if level_number == 3:
        return "clear"

    name_text = _format_value(level_name)
    if name_text == "-":
        return "pending"
    if "暂无" in name_text or "无明显" in name_text:
        return "clear"
    if "复核" in name_text:
        return "review"
    if "关注" in name_text:
        return "attention"
    return "default"


def _format_datetime(value: Any) -> str:
    if not isinstance(value, datetime):
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_attempts(attempt_count: Any, max_attempts: Any) -> str:
    attempt_text = _format_value(attempt_count)
    max_attempt_text = _format_value(max_attempts)
    if attempt_text == "-" and max_attempt_text == "-":
        return "-"
    return f"{attempt_text} / {max_attempt_text}"


def _format_bool(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "-"


def _format_attention_level(level: Any, level_name: Any) -> str:
    if level is None and level_name is None:
        return "-"
    level_text = _format_value(level)
    name_text = _format_value(level_name)
    if level_text == "-":
        return name_text
    if name_text == "-":
        return level_text
    return f"{level_text} / {name_text}"


def _format_duration(start: Any, end: Any) -> str:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return "-"
    seconds = max(int((end - start).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _format_duration_ms(value: Any) -> str:
    if value is None:
        return "-"
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return _format_value(value)
    if milliseconds < 1000:
        return f"{milliseconds}ms"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{int(minutes)}m {remaining_seconds:.0f}s"


def _summarize_error(error_code: Any, error_message: Any) -> str:
    code = str(error_code).strip() if error_code else ""
    message = str(error_message).strip() if error_message else ""
    if code and message:
        return _truncate(f"{code}: {message}", 72)
    if code:
        return code
    if message:
        return _truncate(message, 72)
    return "-"


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def _detail_field(
    label: str,
    value: Any,
    *,
    mono: bool = False,
    wide: bool = False,
) -> dict[str, str]:
    return {
        "label": label,
        "value": _format_value(value),
        "class_name": " is-mono" if mono else "",
        "span_class": " is-wide" if wide else "",
    }


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, Decimal):
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    text = str(value).strip()
    return text or "-"


def _format_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return _format_value(value)


def _present_model_run(row: dict[str, Any]) -> dict[str, str]:
    error_text = _summarize_error(row.get("error_code"), row.get("error_message"))
    return {
        "model_name": _format_value(row.get("model_name")),
        "model_version": _format_value(row.get("model_version")),
        "model_role": _format_value(row.get("model_role")),
        "status": _format_value(row.get("status")),
        "status_key": _format_value(row.get("status")).replace("_", "-"),
        "duration_text": _format_duration_ms(row.get("duration_ms")),
        "started_at_text": _format_datetime(row.get("started_at")),
        "completed_at_text": _format_datetime(row.get("completed_at")),
        "output_summary": _format_compact_json(row.get("output_summary")),
        "metrics": _format_compact_json(row.get("metrics")),
        "error_text": error_text,
        "error_title": error_text if error_text != "-" else "",
    }


def _present_timeline_segment(row: dict[str, Any]) -> dict[str, str]:
    start_sec = row.get("start_sec")
    end_sec = row.get("end_sec")
    return {
        "segment_id": _format_value(row.get("segment_id")),
        "start_sec": _format_data_seconds(start_sec),
        "end_sec": _format_data_seconds(end_sec),
        "time_range": f"{_format_seconds(start_sec)} - {_format_seconds(end_sec)}",
        "speaker_text": _format_speaker(row),
        "role_source": _format_value(row.get("role_source")),
        "audio_event_top": _format_score_items(
            row.get("audio_event_scores"),
            primary_name_keys=("eventNameZh", "eventNameEn", "name", "label"),
            limit=3,
        ),
        "voice_emotion_top": _format_score_items(
            row.get("voice_emotion_scores"),
            primary_name_keys=("emotionNameZh", "emotionNameEn", "label", "name"),
            limit=3,
        ),
        "dimension_text": _format_dimensions(row.get("voice_emotion_dimensions")),
    }


def _present_review_segment(row: dict[str, Any]) -> dict[str, str]:
    matched_rule_codes = row.get("matchedRuleCodes")
    start_sec = row.get("startSec")
    end_sec = row.get("endSec")
    source_segments = row.get("sourceSegments")
    scores = _review_segment_scores(row)
    return {
        "insight_name": _format_value(row.get("title") or row.get("result")),
        "insight_type": _format_value(row.get("segmentId")),
        "time_range": f"{_format_seconds(start_sec)} - {_format_seconds(end_sec)}",
        "occurrence_count": _format_value(len(source_segments) if isinstance(source_segments, list) else None),
        "duration_text": _format_seconds(_duration_seconds(start_sec, end_sec)),
        "max_score": _format_score_value(max(scores) if scores else None),
        "avg_score": _format_score_value(sum(scores) / len(scores) if scores else None),
        "confidence": _format_value(row.get("confidence")),
        "matched_rule_codes": _format_string_list(matched_rule_codes),
        "reason": _format_value(row.get("reason")),
    }


def _review_segment_scores(row: dict[str, Any]) -> list[float]:
    scores: list[float] = []
    for field_name in ("audioEvents", "voiceStates"):
        values = row.get(field_name)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            score = _parse_float(item.get("score"))
            if score is not None:
                scores.append(score)
    return scores


def _duration_seconds(start: Any, end: Any) -> float | None:
    start_number = _parse_float(start)
    end_number = _parse_float(end)
    if start_number is None or end_number is None:
        return None
    return max(0.0, end_number - start_number)


def _format_compact_json(value: Any) -> str:
    if not value:
        return "-"
    text = _format_json(value).replace("\n", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    return _truncate(text, 120)


def _format_seconds(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _format_value(value)
    return f"{number:.3f}".rstrip("0").rstrip(".") + "s"


def _format_data_seconds(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""


def _format_string_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    return " / ".join(_format_value(item) for item in value if _format_value(item) != "-") or "-"


def _format_score_value(value: Any) -> str:
    try:
        return _format_score(float(value))
    except (TypeError, ValueError):
        return _format_value(value)


def _select_existing_audio_asset(rows: list[dict[str, Any]]) -> JobAudioAsset | None:
    for row in rows:
        uri = _format_value(row.get("uri"))
        if uri == "-":
            continue
        path = _resolve_audio_path(uri)
        if path is None:
            continue
        content_type = _format_value(row.get("content_type"))
        return JobAudioAsset(
            path=path,
            asset_type=_format_value(row.get("asset_type")),
            content_type=content_type if content_type != "-" else "audio/wav",
            size_bytes=int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
        )
    return None


def _resolve_audio_path(uri: str) -> Path | None:
    raw_path = Path(uri).expanduser()
    candidates = (
        [raw_path]
        if raw_path.is_absolute()
        else [
            Path.cwd() / raw_path,
            Path(__file__).resolve().parents[3] / raw_path,
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    return None


def _audio_asset_label(asset_type: str) -> str:
    if asset_type == "normalized":
        return "本地规范化音频"
    if asset_type == "source":
        return "本地下载音频"
    return "本地音频"


def _format_speaker(row: dict[str, Any]) -> str:
    speaker_label = _format_value(row.get("speaker_label"))
    speaker_role = _format_value(row.get("speaker_role"))
    if speaker_label == "-" and speaker_role == "-":
        return "-"
    if speaker_label == "-":
        return speaker_role
    if speaker_role == "-":
        return speaker_label
    return f"{speaker_label} / {speaker_role}"


def _format_score_items(
    value: Any,
    *,
    primary_name_keys: tuple[str, ...],
    limit: int,
) -> str:
    if not isinstance(value, list) or not value:
        return "-"

    scored_items: list[tuple[float, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        score = _parse_float(item.get("score"))
        if score is None:
            continue
        name = _first_text_value(item, primary_name_keys)
        if not name:
            name = _format_value(item.get("index"))
        scored_items.append((score, name))

    if not scored_items:
        return "-"

    scored_items.sort(key=lambda item: item[0], reverse=True)
    return " / ".join(
        f"{name} {_format_score(score)}"
        for score, name in scored_items[:limit]
    )


def _format_dimensions(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"

    parts: list[str] = []
    for key in ("arousal", "valence", "dominance"):
        item = value.get(key)
        if not isinstance(item, dict):
            continue
        number = _parse_float(item.get("value"))
        if number is None:
            continue
        name = _first_text_value(item, ("dimensionNameZh", "dimensionNameEn")) or key
        parts.append(f"{name} {_format_value(number)}")

    if not parts:
        for key, item in value.items():
            if not isinstance(item, dict):
                continue
            number = _parse_float(item.get("value"))
            if number is None:
                continue
            name = _first_text_value(item, ("dimensionNameZh", "dimensionNameEn")) or str(key)
            parts.append(f"{name} {_format_value(number)}")

    return " / ".join(parts) if parts else "-"


def _first_text_value(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_score(value: float) -> str:
    if 0 <= value <= 1:
        return f"{value * 100:.1f}%"
    return _format_value(value)
