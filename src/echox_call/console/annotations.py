"""Persistence and validation helpers for the WavLM emotion annotation console."""

from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4

from echox_call.console.upload import ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES, UploadedFile
from echox_call.core.db import connect


DEFAULT_SERVER_AUDIO_ROOT = Path("data/emotion_annotation_server_audio")
ANNOTATION_UPLOAD_SUBDIRECTORY = "_uploaded"
EMOTION_LABELS = (
    ("Anger", "愤怒 / 激动"),
    ("Contempt", "轻蔑 / 不满"),
    ("Disgust", "厌恶"),
    ("Fear", "恐惧 / 惊恐"),
    ("Happiness", "高兴 / 愉悦"),
    ("Neutral", "中性 / 平稳"),
    ("Sadness", "悲伤 / 低落"),
    ("Surprise", "惊讶 / 突发反应"),
    ("Other", "其他 / 无法归类"),
)
EMOTION_LABEL_ZH = dict(EMOTION_LABELS)
EMOTION_LABEL_VALUES = {item[0] for item in EMOTION_LABELS}
EMOTION_LABEL_ORDER = tuple(item[0] for item in EMOTION_LABELS)
SECONDARY_EMOTION_VALUES = EMOTION_LABEL_VALUES - {"Neutral", "Other"}
MAX_SERVER_AUDIO_ITEMS = 2000


class EmotionAnnotationError(ValueError):
    """Raised when annotation input cannot safely be saved."""


@dataclass(frozen=True)
class AnnotationAudioFile:
    id: str
    source_type: str
    source_reference: str
    stored_path: Path
    original_filename: str
    content_type: str
    size_bytes: int | None
    duration_sec: float | None
    imported_by: str
    created_at: datetime


@dataclass(frozen=True)
class AnnotationSegmentInput:
    start_sec: float
    end_sec: float
    speaker_label: str | None
    primary_emotion: str
    secondary_emotions: tuple[str, ...]
    is_overlapping_speech: bool
    annotator_confidence: int
    needs_review: bool
    note: str


@dataclass(frozen=True)
class AnnotationSubmission:
    audio_usable: bool
    overall_note: str
    segments: tuple[AnnotationSegmentInput, ...]


def build_soft_label_intervals(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Split temporal overlaps and average hard labels using confidence weights."""

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        audio_id = str(row.get("audio_id") or "")
        primary_emotion = str(row.get("primary_emotion") or "")
        try:
            start_sec = float(row.get("start_sec"))
            end_sec = float(row.get("end_sec"))
            confidence = int(row.get("annotator_confidence") or 3)
        except (TypeError, ValueError):
            continue
        if (
            not audio_id
            or primary_emotion not in EMOTION_LABEL_VALUES
            or not isfinite(start_sec)
            or not isfinite(end_sec)
            or end_sec <= start_sec
        ):
            continue
        confidence = max(1, min(5, confidence))
        group = grouped.setdefault(
            audio_id,
            {
                "audio_id": audio_id,
                "audio_path": str(row.get("audio_path") or ""),
                "original_filename": str(row.get("original_filename") or ""),
                "segments": [],
            },
        )
        group["segments"].append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "primary_emotion": primary_emotion,
                "confidence": confidence,
                "annotator_username": str(row.get("annotator_username") or ""),
                "annotation_id": str(row.get("annotation_id") or ""),
            }
        )

    output: list[dict[str, Any]] = []
    for audio_id in sorted(grouped):
        group = grouped[audio_id]
        segments = group["segments"]
        boundaries = sorted(
            {value for segment in segments for value in (segment["start_sec"], segment["end_sec"])}
        )
        for index in range(len(boundaries) - 1):
            interval_start = boundaries[index]
            interval_end = boundaries[index + 1]
            if interval_end <= interval_start:
                continue
            covering = [
                segment
                for segment in segments
                if segment["start_sec"] <= interval_start and segment["end_sec"] >= interval_end
            ]
            if not covering:
                continue
            weighted_scores = {label: 0.0 for label in EMOTION_LABEL_ORDER}
            total_weight = 0.0
            for segment in covering:
                weight = float(segment["confidence"])
                weighted_scores[segment["primary_emotion"]] += weight
                total_weight += weight
            soft_labels = {
                label: round(weighted_scores[label] / total_weight, 8)
                for label in EMOTION_LABEL_ORDER
            }
            average_confidence = sum(segment["confidence"] for segment in covering) / len(covering)
            output.append(
                {
                    "audio_id": group["audio_id"],
                    "audio_path": group["audio_path"],
                    "original_filename": group["original_filename"],
                    "start_sec": round(interval_start, 3),
                    "end_sec": round(interval_end, 3),
                    "label_order": list(EMOTION_LABEL_ORDER),
                    "target_distribution": [soft_labels[label] for label in EMOTION_LABEL_ORDER],
                    "soft_labels": soft_labels,
                    "sample_weight": round(average_confidence / 5.0, 6),
                    "average_confidence": round(average_confidence, 3),
                    "annotator_count": len(
                        {segment["annotator_username"] for segment in covering if segment["annotator_username"]}
                    ),
                    "vote_count": len(covering),
                    "source_annotation_ids": sorted(
                        {segment["annotation_id"] for segment in covering if segment["annotation_id"]}
                    ),
                }
            )
    return output


def get_server_audio_root(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    return Path(source.get("EMOTION_ANNOTATION_SERVER_AUDIO_ROOT", DEFAULT_SERVER_AUDIO_ROOT)).expanduser()


def scan_server_audio_files(root: Path | None = None) -> list[dict[str, str]]:
    safe_root = (root or get_server_audio_root()).resolve()
    if not safe_root.exists():
        return []
    if not safe_root.is_dir():
        raise EmotionAnnotationError("服务器音频目录不是有效目录，请检查 EMOTION_ANNOTATION_SERVER_AUDIO_ROOT。")

    files: list[dict[str, str]] = []
    for path in sorted(safe_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        relative = path.relative_to(safe_root).as_posix()
        files.append(
            {
                "relative_path": relative,
                "display_name": relative,
                "size_text": _format_bytes(path.stat().st_size),
            }
        )
        if len(files) >= MAX_SERVER_AUDIO_ITEMS:
            break
    return files


def save_annotation_upload(uploaded_file: UploadedFile) -> AnnotationAudioFile:
    _validate_uploaded_audio(uploaded_file)
    server_root = get_server_audio_root().resolve()
    upload_root = server_root / ANNOTATION_UPLOAD_SUBDIRECTORY
    upload_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_file.filename).suffix.lower()
    stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"
    path = (upload_root / stored_name).resolve()
    path.write_bytes(uploaded_file.content)
    content_type = uploaded_file.content_type
    if not content_type.startswith("audio/"):
        content_type = mimetypes.guess_type(stored_name)[0] or "audio/*"
    return AnnotationAudioFile(
        id="",
        source_type="server",
        source_reference=path.relative_to(server_root).as_posix(),
        stored_path=path,
        original_filename=uploaded_file.filename,
        content_type=content_type,
        size_bytes=len(uploaded_file.content),
        duration_sec=None,
        imported_by="",
        created_at=datetime.now(),
    )


def server_audio_file_from_relative_path(relative_path: str, root: Path | None = None) -> AnnotationAudioFile:
    safe_root = (root or get_server_audio_root()).resolve()
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        raise EmotionAnnotationError("服务器音频路径不合法。")
    path = (safe_root / normalized).resolve()
    if safe_root not in path.parents or not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise EmotionAnnotationError("所选服务器音频不存在或格式不受支持。")
    return AnnotationAudioFile(
        id="",
        source_type="server",
        source_reference=normalized,
        stored_path=path,
        original_filename=path.name,
        content_type=mimetypes.guess_type(path.name)[0] or "audio/*",
        size_bytes=path.stat().st_size,
        duration_sec=None,
        imported_by="",
        created_at=datetime.now(),
    )


def parse_annotation_submission(form: Mapping[str, list[str]]) -> AnnotationSubmission:
    audio_usable = _form_value(form, "audio_usable") == "1"
    overall_note = _clean_text(_form_value(form, "overall_note"), 2000)
    count = _parse_int(_form_value(form, "segment_count"), field="标注片段数量", minimum=0, maximum=100)
    segments: list[AnnotationSegmentInput] = []
    for index in range(count):
        prefix = f"segment_{index}_"
        start_value = _form_value(form, f"{prefix}start")
        end_value = _form_value(form, f"{prefix}end")
        primary = _form_value(form, f"{prefix}primary") or ""
        if not any((start_value, end_value, primary, _form_value(form, f"{prefix}speaker"))):
            continue
        start_sec = _parse_float(start_value, field=f"第 {index + 1} 段开始时间")
        end_sec = _parse_float(end_value, field=f"第 {index + 1} 段结束时间")
        if end_sec <= start_sec:
            raise EmotionAnnotationError(f"第 {index + 1} 段的结束时间必须大于开始时间。")
        if primary not in EMOTION_LABEL_VALUES:
            raise EmotionAnnotationError(f"第 {index + 1} 段请选择一个主情绪。")
        secondary = tuple(dict.fromkeys(value for value in form.get(f"{prefix}secondary", []) if value))
        invalid_secondary = set(secondary) - SECONDARY_EMOTION_VALUES
        if invalid_secondary or primary in secondary:
            raise EmotionAnnotationError(f"第 {index + 1} 段的辅助情绪不符合标注规则。")
        if len(secondary) > 2:
            raise EmotionAnnotationError(f"第 {index + 1} 段最多选择两个辅助情绪。")
        segments.append(
            AnnotationSegmentInput(
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                speaker_label=_clean_optional_text(_form_value(form, f"{prefix}speaker"), 120),
                primary_emotion=primary,
                secondary_emotions=secondary,
                is_overlapping_speech=_form_value(form, f"{prefix}overlap") == "1",
                annotator_confidence=_parse_int(
                    _form_value(form, f"{prefix}confidence"),
                    field=f"第 {index + 1} 段把握程度",
                    minimum=1,
                    maximum=5,
                ),
                needs_review=_form_value(form, f"{prefix}needs_review") == "1",
                note=_clean_text(_form_value(form, f"{prefix}note"), 500),
            )
        )
    if audio_usable and not segments:
        raise EmotionAnnotationError("可用音频至少需要保存一个情绪片段；若无有效人声，请选择“音频不适合标注”。")
    if not audio_usable and segments:
        raise EmotionAnnotationError("标记为不适合标注时，请删除所有情绪片段后再保存。")
    return AnnotationSubmission(
        audio_usable=audio_usable,
        overall_note=overall_note,
        segments=tuple(segments),
    )


class EmotionAnnotationRepository:
    """Database access for imported audio and immutable annotation revisions."""

    def get_summary(self) -> dict[str, int]:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM emotion_annotation_audio_files) AS audio_count,
                    (SELECT count(*) FROM emotion_annotation_sessions) AS annotation_count,
                    (SELECT count(*) FROM emotion_annotation_audio_files AS audio
                     WHERE NOT EXISTS (
                         SELECT 1 FROM emotion_annotation_sessions AS session
                         WHERE session.audio_file_id = audio.id
                     )) AS unannotated_count,
                    (SELECT count(DISTINCT annotator_username) FROM emotion_annotation_sessions) AS annotator_count
                """
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

    def list_audio_files(self) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    audio.id::text AS id,
                    audio.source_type,
                    audio.source_reference,
                    audio.original_filename,
                    audio.content_type,
                    audio.size_bytes,
                    audio.duration_sec,
                    audio.imported_by,
                    audio.created_at,
                    count(session.id) AS annotation_count,
                    count(DISTINCT session.annotator_username) AS annotator_count,
                    max(session.created_at) AS last_annotated_at
                FROM emotion_annotation_audio_files AS audio
                LEFT JOIN emotion_annotation_sessions AS session ON session.audio_file_id = audio.id
                GROUP BY audio.id
                ORDER BY audio.created_at DESC
                """
            ).fetchall()
        return [_present_audio_row(dict(row)) for row in rows]

    def get_server_audio_statuses(self) -> dict[str, dict[str, int | bool]]:
        """Return import and annotation counts keyed by safe server-relative path."""
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    audio.source_reference,
                    count(session.id) AS annotation_count,
                    count(DISTINCT session.annotator_username) AS annotator_count
                FROM emotion_annotation_audio_files AS audio
                LEFT JOIN emotion_annotation_sessions AS session ON session.audio_file_id = audio.id
                WHERE audio.source_type = 'server'
                GROUP BY audio.source_reference
                """
            ).fetchall()
        return {
            str(row["source_reference"]): {
                "is_imported": True,
                "annotation_count": int(row["annotation_count"] or 0),
                "annotator_count": int(row["annotator_count"] or 0),
            }
            for row in rows
        }

    def get_audio_file(self, audio_id: str) -> AnnotationAudioFile | None:
        try:
            parsed_id = UUID(audio_id)
        except ValueError:
            return None
        with connect() as conn:
            row = conn.execute(
                """
                SELECT id::text, source_type, source_reference, stored_path, original_filename,
                       content_type, size_bytes, duration_sec, imported_by, created_at
                FROM emotion_annotation_audio_files
                WHERE id = %(id)s
                """,
                {"id": parsed_id},
            ).fetchone()
        return _row_to_audio_file(dict(row)) if row else None

    def get_annotation_count(self, audio_id: str) -> int:
        try:
            parsed_id = UUID(audio_id)
        except ValueError:
            return 0
        with connect() as conn:
            row = conn.execute(
                "SELECT count(*) AS count FROM emotion_annotation_sessions WHERE audio_file_id = %(audio_id)s",
                {"audio_id": parsed_id},
            ).fetchone()
        return int(row["count"] or 0) if row else 0

    def add_audio_file(self, audio: AnnotationAudioFile, *, imported_by: str) -> tuple[AnnotationAudioFile, bool]:
        with connect() as conn:
            row = conn.execute(
                """
                INSERT INTO emotion_annotation_audio_files (
                    source_type, source_reference, stored_path, original_filename,
                    content_type, size_bytes, imported_by
                ) VALUES (
                    %(source_type)s, %(source_reference)s, %(stored_path)s, %(original_filename)s,
                    %(content_type)s, %(size_bytes)s, %(imported_by)s
                )
                ON CONFLICT (stored_path) DO NOTHING
                RETURNING id::text, source_type, source_reference, stored_path, original_filename,
                          content_type, size_bytes, duration_sec, imported_by, created_at
                """,
                {
                    "source_type": audio.source_type,
                    "source_reference": audio.source_reference,
                    "stored_path": str(audio.stored_path),
                    "original_filename": audio.original_filename,
                    "content_type": audio.content_type,
                    "size_bytes": audio.size_bytes,
                    "imported_by": imported_by,
                },
            ).fetchone()
            if row:
                return _row_to_audio_file(dict(row)), True
        existing = self._get_audio_by_stored_path(audio.stored_path)
        if existing is None:
            raise EmotionAnnotationError("导入音频时发生并发冲突，请重新尝试。")
        return existing, False

    def save_annotation(
        self,
        *,
        audio_file_id: str,
        annotator_username: str,
        annotator_name: str,
        submission: AnnotationSubmission,
        duration_sec: float | None,
    ) -> str:
        try:
            audio_id = UUID(audio_file_id)
        except ValueError as exc:
            raise EmotionAnnotationError("音频标识不正确。") from exc
        if duration_sec is not None and duration_sec >= 0:
            max_end = max((segment.end_sec for segment in submission.segments), default=0.0)
            if max_end > duration_sec + 0.25:
                raise EmotionAnnotationError("存在片段超出音频时长，请检查开始和结束时间。")
        with connect(autocommit=False) as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"{audio_id}:{annotator_username}",),
                )
                audio_exists = conn.execute(
                    "SELECT 1 FROM emotion_annotation_audio_files WHERE id = %s",
                    (audio_id,),
                ).fetchone()
                if not audio_exists:
                    raise EmotionAnnotationError("该音频不存在，可能已被移除。")
                row = conn.execute(
                    """
                    INSERT INTO emotion_annotation_sessions (
                        audio_file_id, annotator_username, annotator_name, revision_no,
                        audio_usable, overall_note
                    )
                    SELECT
                        %(audio_file_id)s, %(annotator_username)s, %(annotator_name)s,
                        COALESCE(max(revision_no), 0) + 1,
                        %(audio_usable)s, %(overall_note)s
                    FROM emotion_annotation_sessions
                    WHERE audio_file_id = %(audio_file_id)s
                      AND annotator_username = %(annotator_username)s
                    RETURNING id::text
                    """,
                    {
                        "audio_file_id": audio_id,
                        "annotator_username": annotator_username,
                        "annotator_name": annotator_name,
                        "audio_usable": submission.audio_usable,
                        "overall_note": submission.overall_note,
                    },
                ).fetchone()
                session_id = str(row["id"])
                if submission.segments:
                    with conn.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO emotion_annotation_segments (
                                session_id, start_sec, end_sec, speaker_label, primary_emotion,
                                secondary_emotions, is_overlapping_speech, annotator_confidence,
                                needs_review, note
                            ) VALUES (
                                %(session_id)s, %(start_sec)s, %(end_sec)s, %(speaker_label)s,
                                %(primary_emotion)s, %(secondary_emotions)s, %(is_overlapping_speech)s,
                                %(annotator_confidence)s, %(needs_review)s, %(note)s
                            )
                            """,
                            [
                                {
                                    "session_id": session_id,
                                    "start_sec": segment.start_sec,
                                    "end_sec": segment.end_sec,
                                    "speaker_label": segment.speaker_label,
                                    "primary_emotion": segment.primary_emotion,
                                    "secondary_emotions": list(segment.secondary_emotions),
                                    "is_overlapping_speech": segment.is_overlapping_speech,
                                    "annotator_confidence": segment.annotator_confidence,
                                    "needs_review": segment.needs_review,
                                    "note": segment.note,
                                }
                                for segment in submission.segments
                            ],
                        )
                if duration_sec is not None and duration_sec >= 0:
                    conn.execute(
                        """
                        UPDATE emotion_annotation_audio_files
                        SET duration_sec = COALESCE(duration_sec, %(duration_sec)s)
                        WHERE id = %(audio_file_id)s
                        """,
                        {"duration_sec": round(duration_sec, 3), "audio_file_id": audio_id},
                    )
        return session_id

    def list_annotation_history(self, audio_file_id: str) -> list[dict[str, Any]]:
        try:
            parsed_id = UUID(audio_file_id)
        except ValueError:
            return []
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    session.id::text AS id,
                    session.annotator_name,
                    session.annotator_username,
                    session.revision_no,
                    session.audio_usable,
                    session.overall_note,
                    session.created_at,
                    count(segment.id) AS segment_count,
                    COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'start_sec', segment.start_sec,
                                'end_sec', segment.end_sec,
                                'primary_emotion', segment.primary_emotion,
                                'secondary_emotions', segment.secondary_emotions,
                                'speaker_label', segment.speaker_label,
                                'is_overlapping_speech', segment.is_overlapping_speech,
                                'annotator_confidence', segment.annotator_confidence,
                                'needs_review', segment.needs_review,
                                'note', segment.note
                            )
                            ORDER BY segment.start_sec
                        ) FILTER (WHERE segment.id IS NOT NULL),
                        '[]'::jsonb
                    ) AS segment_items
                FROM emotion_annotation_sessions AS session
                LEFT JOIN emotion_annotation_segments AS segment ON segment.session_id = session.id
                WHERE session.audio_file_id = %(audio_file_id)s
                GROUP BY session.id
                ORDER BY session.created_at DESC
                """,
                {"audio_file_id": parsed_id},
            ).fetchall()
        return [_present_history_row(dict(row)) for row in rows]

    def export_jsonl(self) -> str:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    audio.id::text AS audio_id,
                    audio.stored_path,
                    audio.original_filename,
                    session.id::text AS annotation_id,
                    session.annotator_username,
                    session.revision_no,
                    session.audio_usable,
                    session.overall_note,
                    session.created_at AS annotated_at,
                    segment.start_sec,
                    segment.end_sec,
                    segment.speaker_label,
                    segment.primary_emotion,
                    segment.secondary_emotions,
                    segment.is_overlapping_speech,
                    segment.annotator_confidence,
                    segment.needs_review,
                    segment.note
                FROM emotion_annotation_sessions AS session
                JOIN emotion_annotation_audio_files AS audio ON audio.id = session.audio_file_id
                LEFT JOIN emotion_annotation_segments AS segment ON segment.session_id = session.id
                ORDER BY audio.created_at, session.created_at, segment.start_sec NULLS FIRST
                """
            ).fetchall()
        lines: list[str] = []
        for row in rows:
            item = dict(row)
            item["audio_path"] = item.pop("stored_path")
            item["secondary_emotions"] = item.get("secondary_emotions") or []
            item["annotated_at"] = item["annotated_at"].isoformat()
            for key in ("start_sec", "end_sec"):
                if item.get(key) is not None:
                    item[key] = float(item[key])
            lines.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(lines) + ("\n" if lines else "")

    def export_soft_label_jsonl(self) -> str:
        """Export confidence-weighted soft targets from each annotator's latest revision."""

        with connect() as conn:
            rows = conn.execute(
                """
                WITH latest_sessions AS (
                    SELECT DISTINCT ON (audio_file_id, annotator_username)
                        id,
                        audio_file_id,
                        annotator_username,
                        revision_no,
                        created_at
                    FROM emotion_annotation_sessions
                    WHERE audio_usable = true
                    ORDER BY audio_file_id, annotator_username, revision_no DESC, created_at DESC
                )
                SELECT
                    audio.id::text AS audio_id,
                    audio.stored_path AS audio_path,
                    audio.original_filename,
                    session.id::text AS annotation_id,
                    session.annotator_username,
                    segment.start_sec,
                    segment.end_sec,
                    segment.primary_emotion,
                    segment.annotator_confidence
                FROM latest_sessions AS session
                JOIN emotion_annotation_audio_files AS audio ON audio.id = session.audio_file_id
                JOIN emotion_annotation_segments AS segment ON segment.session_id = session.id
                WHERE segment.needs_review = false
                  AND segment.is_overlapping_speech = false
                ORDER BY audio.id, segment.start_sec, segment.end_sec, session.annotator_username
                """
            ).fetchall()
        intervals = build_soft_label_intervals([dict(row) for row in rows])
        return "".join(
            json.dumps(item, ensure_ascii=False, default=str) + "\n"
            for item in intervals
        )

    def _get_audio_by_stored_path(self, stored_path: Path) -> AnnotationAudioFile | None:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT id::text, source_type, source_reference, stored_path, original_filename,
                       content_type, size_bytes, duration_sec, imported_by, created_at
                FROM emotion_annotation_audio_files
                WHERE stored_path = %(stored_path)s
                """,
                {"stored_path": str(stored_path)},
            ).fetchone()
        return _row_to_audio_file(dict(row)) if row else None


def _validate_uploaded_audio(uploaded_file: UploadedFile) -> None:
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise EmotionAnnotationError("仅支持 WAV、MP3、M4A、FLAC、OGG、AAC 格式的音频。")
    if not uploaded_file.content:
        raise EmotionAnnotationError("上传的音频文件为空。")
    if len(uploaded_file.content) > MAX_UPLOAD_BYTES:
        raise EmotionAnnotationError(f"音频文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB。")


def _row_to_audio_file(row: Mapping[str, Any]) -> AnnotationAudioFile:
    return AnnotationAudioFile(
        id=str(row["id"]),
        source_type=str(row["source_type"]),
        source_reference=str(row["source_reference"]),
        stored_path=Path(str(row["stored_path"])),
        original_filename=str(row["original_filename"]),
        content_type=str(row.get("content_type") or "audio/*"),
        size_bytes=int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
        duration_sec=float(row["duration_sec"]) if row.get("duration_sec") is not None else None,
        imported_by=str(row["imported_by"]),
        created_at=row["created_at"],
    )


def _present_audio_row(row: dict[str, Any]) -> dict[str, Any]:
    row["size_text"] = _format_bytes(row.get("size_bytes"))
    row["duration_text"] = _format_duration(row.get("duration_sec"))
    row["annotation_count"] = int(row.get("annotation_count") or 0)
    row["annotator_count"] = int(row.get("annotator_count") or 0)
    row["created_at_text"] = _format_datetime(row.get("created_at"))
    row["last_annotated_at_text"] = _format_datetime(row.get("last_annotated_at"))
    return row


def _present_history_row(row: dict[str, Any]) -> dict[str, Any]:
    row["segment_count"] = int(row.get("segment_count") or 0)
    row["created_at_text"] = _format_datetime(row.get("created_at"))
    segment_items = row.pop("segment_items", [])
    summaries: list[str] = []
    presented_segments: list[dict[str, Any]] = []
    if isinstance(segment_items, list):
        for item in segment_items:
            if not isinstance(item, dict):
                continue
            primary_emotion = str(item.get("primary_emotion") or "Other")
            start_sec = _format_integer_second(item.get("start_sec"))
            end_sec = _format_integer_second(item.get("end_sec"))
            emotion_label = EMOTION_LABEL_ZH.get(primary_emotion, primary_emotion)
            secondary_emotions = [
                str(value)
                for value in (item.get("secondary_emotions") or [])
                if str(value) in SECONDARY_EMOTION_VALUES
            ]
            summaries.append(
                f"{emotion_label} {start_sec}-{end_sec} 秒"
            )
            presented_segments.append(
                {
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "primary_emotion": primary_emotion,
                    "emotion_label": emotion_label,
                    "secondary_emotions": secondary_emotions,
                    "speaker_label": str(item.get("speaker_label") or "").strip(),
                    "is_overlapping_speech": bool(item.get("is_overlapping_speech")),
                    "annotator_confidence": int(item.get("annotator_confidence") or 2),
                    "needs_review": bool(item.get("needs_review")),
                    "note": str(item.get("note") or "").strip(),
                }
            )
    row["segments"] = presented_segments
    row["segment_summary"] = "；".join(summaries)
    return row


def _format_datetime(value: Any) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else "-"


def _format_duration(value: Any) -> str:
    if value is None:
        return "播放后自动获取"
    seconds = float(value)
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes:02d}:{remainder:02d}" if minutes else f"{seconds:.1f} 秒"


def _format_integer_second(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _format_bytes(value: Any) -> str:
    if value is None:
        return "-"
    size = int(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "-"


def _form_value(form: Mapping[str, list[str]], key: str) -> str | None:
    values = form.get(key)
    return values[-1] if values else None


def _parse_int(value: str | None, *, field: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError as exc:
        raise EmotionAnnotationError(f"{field}必须是整数。") from exc
    if not minimum <= parsed <= maximum:
        raise EmotionAnnotationError(f"{field}应在 {minimum} 到 {maximum} 之间。")
    return parsed


def _parse_float(value: str | None, *, field: str) -> float:
    try:
        parsed = float(value or "")
    except ValueError as exc:
        raise EmotionAnnotationError(f"{field}必须是秒数。") from exc
    if not isfinite(parsed) or parsed < 0 or parsed > 24 * 60 * 60:
        raise EmotionAnnotationError(f"{field}不在允许范围内。")
    return parsed


def _clean_optional_text(value: str | None, maximum: int) -> str | None:
    cleaned = _clean_text(value, maximum)
    return cleaned or None


def _clean_text(value: str | None, maximum: int) -> str:
    return (value or "").strip()[:maximum]
