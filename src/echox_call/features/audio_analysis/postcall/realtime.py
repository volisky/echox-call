"""Realtime audio stream ingestion and 10-second window analysis."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

import librosa
import numpy as np
import soundfile as sf
from psycopg.types.json import Jsonb

from echox_call.core.db import connect
from echox_call.core.settings import (
    PostcallWorkerSettings,
    RealtimeWorkerSettings,
    load_postcall_worker_settings,
    load_realtime_worker_settings,
)
from echox_call.features.audio_analysis.postcall.audio_processing import TARGET_SAMPLE_RATE
from echox_call.features.audio_analysis.postcall.attention_rules import AttentionEvaluation
from echox_call.features.audio_analysis.postcall.repository import PostcallJobRepository
from echox_call.features.audio_analysis.postcall.worker import (
    PostcallWorker,
    _model_run,
    _timeline_segment_payload,
)
from echox_call.features.audio_analysis.postcall.worker_models import (
    ModelRunRecord,
    TimelineSegmentRecord,
)


class RealtimeAudioError(RuntimeError):
    """Raised when realtime stream ingestion or analysis fails."""


PCM16LE_SAMPLE_RATE = 8000


@dataclass(frozen=True)
class VendorParams:
    raw: str
    values: dict[str, str]

    @property
    def call_id(self) -> str | None:
        return self.values.get("callid")

    @property
    def agent_id(self) -> str | None:
        return self.values.get("agentid")

    @property
    def usrdn(self) -> str | None:
        return self.values.get("usrdn")


@dataclass(frozen=True)
class ChunkAudioInfo:
    duration_sec: float | None
    sample_rate: int | None
    channels: int | None


@dataclass(frozen=True)
class RealtimeChunkRecord:
    stream_id: str
    call_id: str
    voice_id: str | None
    seq: int
    duplicate: bool
    received_duration_sec: float
    ended: bool


@dataclass(frozen=True)
class RealtimeWindowClaim:
    stream_id: str
    call_id: str
    window_index: int
    start_sec: float
    end_sec: float
    ended: bool


def parse_vendor_specific_param(value: str | None) -> VendorParams:
    raw = value or ""
    normalized = raw.strip()
    if normalized.startswith("{") and normalized.endswith("}"):
        normalized = normalized[1:-1]
    parsed: dict[str, str] = {}
    for item in normalized.replace(",", ";").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, item_value = item.split("=", 1)
        key = key.strip().lower()
        item_value = item_value.strip()
        if key and item_value:
            parsed[key] = item_value
    return VendorParams(raw=raw, values=parsed)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_wav(path: Path) -> ChunkAudioInfo:
    try:
        info = sf.info(path)
    except Exception:
        return ChunkAudioInfo(duration_sec=None, sample_rate=None, channels=None)
    return ChunkAudioInfo(
        duration_sec=float(info.duration),
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
    )


def save_upload_chunk(
    *,
    storage_dir: Path,
    call_id: str,
    seq: int,
    voice_format: int,
    source: BinaryIO,
) -> tuple[Path, int, str, ChunkAudioInfo]:
    chunk_dir = storage_dir / _safe_id(call_id) / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    path = chunk_dir / f"{seq:08d}.wav"
    if path.exists() and path.stat().st_size > 0:
        existing_info = inspect_wav(path)
        if existing_info.duration_sec is not None:
            return path, path.stat().st_size, _sha256_file(path), existing_info

    if voice_format == 1:
        _write_pcm16le_as_wav(source, path)
    else:
        with path.open("wb") as output:
            shutil.copyfileobj(source, output)
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise RealtimeAudioError("uploaded audio chunk is empty")
    return path, size_bytes, _sha256_file(path), inspect_wav(path)


def _write_pcm16le_as_wav(source: BinaryIO, path: Path) -> None:
    data = source.read()
    if not data:
        raise RealtimeAudioError("uploaded audio chunk is empty")
    if len(data) % 2 != 0:
        data += b"\x00"
    samples = np.frombuffer(data, dtype="<i2")
    if samples.size == 0:
        raise RealtimeAudioError("uploaded PCM audio chunk has no samples")
    sf.write(path, samples, PCM16LE_SAMPLE_RATE, subtype="PCM_16")


class RealtimeAudioRepository:
    def __init__(self, *, settings: RealtimeWorkerSettings | None = None) -> None:
        self.settings = settings or load_realtime_worker_settings()

    def record_chunk(
        self,
        *,
        call_id: str,
        voice_id: str | None,
        seq: int,
        path: Path,
        sha256: str,
        size_bytes: int,
        audio_info: ChunkAudioInfo,
        vendor: VendorParams,
        ended: bool,
    ) -> RealtimeChunkRecord:
        with connect(autocommit=False) as conn:
            with conn.transaction():
                stream = conn.execute(
                    """
                    INSERT INTO postcall_realtime_streams (
                        call_id,
                        voice_id,
                        agent_id,
                        usrdn,
                        vendor_specific_param,
                        state,
                        ended_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
                    ON CONFLICT (call_id) DO UPDATE
                    SET
                        voice_id = COALESCE(EXCLUDED.voice_id, postcall_realtime_streams.voice_id),
                        agent_id = COALESCE(EXCLUDED.agent_id, postcall_realtime_streams.agent_id),
                        usrdn = COALESCE(EXCLUDED.usrdn, postcall_realtime_streams.usrdn),
                        vendor_specific_param = COALESCE(
                            NULLIF(EXCLUDED.vendor_specific_param, ''),
                            postcall_realtime_streams.vendor_specific_param
                        ),
                        state = CASE
                            WHEN postcall_realtime_streams.state = 'failed' THEN 'failed'
                            WHEN EXCLUDED.ended_at IS NOT NULL THEN 'ended'
                            ELSE postcall_realtime_streams.state
                        END,
                        ended_at = COALESCE(postcall_realtime_streams.ended_at, EXCLUDED.ended_at),
                        updated_at = now()
                    RETURNING id, call_id, voice_id, received_duration_sec, ended_at
                    """,
                    (
                        call_id,
                        voice_id,
                        vendor.agent_id,
                        vendor.usrdn,
                        vendor.raw,
                        "ended" if ended else "receiving",
                        ended,
                    ),
                ).fetchone()
                if stream is None:
                    raise RealtimeAudioError(f"failed to create realtime stream: {call_id}")

                linked = conn.execute(
                    """
                    UPDATE postcall_realtime_streams AS stream
                    SET
                        linked_postcall_job_id = job.id,
                        updated_at = now()
                    FROM postcall_jobs AS job
                    WHERE stream.id = %s
                      AND job.call_id = stream.call_id
                      AND stream.linked_postcall_job_id IS NULL
                    RETURNING stream.linked_postcall_job_id
                    """,
                    (stream["id"],),
                ).fetchone()
                _ = linked

                inserted = conn.execute(
                    """
                    INSERT INTO postcall_realtime_chunks (
                        stream_id,
                        seq,
                        file_path,
                        sha256,
                        size_bytes,
                        duration_sec,
                        sample_rate,
                        channels,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stream_id, seq) DO NOTHING
                    RETURNING id
                    """,
                    (
                        stream["id"],
                        seq,
                        str(path),
                        sha256,
                        size_bytes,
                        audio_info.duration_sec,
                        audio_info.sample_rate,
                        audio_info.channels,
                        Jsonb({"voiceId": voice_id, "vendorSpecificParam": vendor.raw}),
                    ),
                ).fetchone()

                if inserted is not None and audio_info.duration_sec is not None:
                    stream = conn.execute(
                        """
                        UPDATE postcall_realtime_streams
                        SET
                            received_duration_sec = received_duration_sec + %s,
                            state = CASE
                                WHEN state = 'failed' THEN 'failed'
                                WHEN ended_at IS NOT NULL THEN 'ended'
                                ELSE 'receiving'
                            END,
                            updated_at = now()
                        WHERE id = %s
                        RETURNING id, call_id, voice_id, received_duration_sec, ended_at
                        """,
                        (audio_info.duration_sec, stream["id"]),
                    ).fetchone()
                if stream is None:
                    raise RealtimeAudioError(f"failed to update realtime stream: {call_id}")

        return RealtimeChunkRecord(
            stream_id=str(stream["id"]),
            call_id=stream["call_id"],
            voice_id=stream["voice_id"],
            seq=seq,
            duplicate=inserted is None,
            received_duration_sec=float(stream["received_duration_sec"]),
            ended=stream["ended_at"] is not None,
        )

    def claim_next_window(self) -> RealtimeWindowClaim | None:
        window_sec = self.settings.window_sec
        tail_min_sec = self.settings.tail_min_sec
        with connect(autocommit=False) as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT
                        id,
                        call_id,
                        received_duration_sec,
                        analyzed_duration_sec,
                        ended_at
                    FROM postcall_realtime_streams
                    WHERE state IN ('receiving', 'alerting', 'ended')
                      AND (
                        received_duration_sec - analyzed_duration_sec >= %s
                        OR (
                            ended_at IS NOT NULL
                            AND received_duration_sec - analyzed_duration_sec >= %s
                        )
                      )
                    ORDER BY updated_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (window_sec, tail_min_sec),
                ).fetchone()
                if row is None:
                    return None
                start_sec = float(row["analyzed_duration_sec"])
                remaining = float(row["received_duration_sec"]) - start_sec
                end_sec = start_sec + window_sec if remaining >= window_sec else float(row["received_duration_sec"])
                window_index = int(start_sec // window_sec)
                conn.execute(
                    """
                    UPDATE postcall_realtime_streams
                    SET analyzed_duration_sec = %s, state = 'analyzing', updated_at = now()
                    WHERE id = %s
                    """,
                    (end_sec, row["id"]),
                )
        return RealtimeWindowClaim(
            stream_id=str(row["id"]),
            call_id=row["call_id"],
            window_index=window_index,
            start_sec=start_sec,
            end_sec=end_sec,
            ended=row["ended_at"] is not None,
        )

    def load_window_waveform(self, claim: RealtimeWindowClaim) -> np.ndarray:
        with connect() as conn:
            chunks = conn.execute(
                """
                SELECT file_path
                FROM postcall_realtime_chunks
                WHERE stream_id = %s
                ORDER BY seq ASC
                """,
                (claim.stream_id,),
            ).fetchall()
        if not chunks:
            raise RealtimeAudioError(f"stream has no chunks: {claim.call_id}")

        waveforms: list[np.ndarray] = []
        for chunk in chunks:
            audio, sample_rate = sf.read(chunk["file_path"], always_2d=True, dtype="float32")
            mono = audio.mean(axis=1).astype(np.float32)
            if sample_rate != TARGET_SAMPLE_RATE:
                mono = librosa.resample(
                    mono,
                    orig_sr=sample_rate,
                    target_sr=TARGET_SAMPLE_RATE,
                ).astype(np.float32)
            waveforms.append(mono)
        full = np.concatenate(waveforms) if waveforms else np.array([], dtype=np.float32)
        start = int(round(claim.start_sec * TARGET_SAMPLE_RATE))
        end = int(round(claim.end_sec * TARGET_SAMPLE_RATE))
        window = full[start:end]
        if len(window) == 0:
            raise RealtimeAudioError(
                f"window has no samples: {claim.call_id} {claim.start_sec}-{claim.end_sec}"
            )
        return window.astype(np.float32)

    def record_window_success(
        self,
        *,
        claim: RealtimeWindowClaim,
        timeline: list[dict[str, Any]],
        attention_evaluation: AttentionEvaluation,
        emotion_types_zh: list[str],
    ) -> None:
        review_segments = attention_evaluation.review_segments
        with connect(autocommit=False) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO postcall_realtime_windows (
                        stream_id,
                        window_index,
                        start_sec,
                        end_sec,
                        state,
                        timeline_segments,
                        attention_level,
                        attention_level_name,
                        emotion_types_zh,
                        review_segments,
                        matched_rule_codes,
                        completed_at
                    )
                    VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (stream_id, window_index) DO UPDATE
                    SET
                        state = 'completed',
                        timeline_segments = EXCLUDED.timeline_segments,
                        attention_level = EXCLUDED.attention_level,
                        attention_level_name = EXCLUDED.attention_level_name,
                        emotion_types_zh = EXCLUDED.emotion_types_zh,
                        review_segments = EXCLUDED.review_segments,
                        matched_rule_codes = EXCLUDED.matched_rule_codes,
                        completed_at = now()
                    """,
                    (
                        claim.stream_id,
                        claim.window_index,
                        claim.start_sec,
                        claim.end_sec,
                        Jsonb(timeline),
                        attention_evaluation.level,
                        attention_evaluation.level_name,
                        emotion_types_zh,
                        Jsonb(review_segments),
                        attention_evaluation.matched_rule_codes,
                    ),
                )
                state = "alerting" if attention_evaluation.level in {1, 2} else "receiving"
                if claim.ended:
                    state = "ended"
                conn.execute(
                    """
                    UPDATE postcall_realtime_streams
                    SET state = %s, updated_at = now()
                    WHERE id = %s
                      AND state <> 'failed'
                    """,
                    (state, claim.stream_id),
                )

    def record_window_failure(self, *, claim: RealtimeWindowClaim, error: Exception) -> None:
        with connect(autocommit=False) as conn:
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO postcall_realtime_windows (
                        stream_id,
                        window_index,
                        start_sec,
                        end_sec,
                        state,
                        error_code,
                        error_message,
                        completed_at
                    )
                    VALUES (%s, %s, %s, %s, 'failed', %s, %s, now())
                    ON CONFLICT (stream_id, window_index) DO UPDATE
                    SET
                        state = 'failed',
                        error_code = EXCLUDED.error_code,
                        error_message = EXCLUDED.error_message,
                        completed_at = now()
                    """,
                    (
                        claim.stream_id,
                        claim.window_index,
                        claim.start_sec,
                        claim.end_sec,
                        error.__class__.__name__,
                        str(error),
                    ),
                )
                conn.execute(
                    """
                    UPDATE postcall_realtime_streams
                    SET state = 'failed', error_code = %s, error_message = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (error.__class__.__name__, str(error), claim.stream_id),
                )

    def load_completed_timeline(self, call_id: str) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT win.timeline_segments
                FROM postcall_realtime_windows AS win
                JOIN postcall_realtime_streams AS stream ON stream.id = win.stream_id
                WHERE stream.call_id = %s
                  AND win.state = 'completed'
                ORDER BY win.start_sec ASC, win.end_sec ASC
                """,
                (call_id,),
            ).fetchall()
        timeline: list[dict[str, Any]] = []
        for row in rows:
            segments = row["timeline_segments"] if isinstance(row["timeline_segments"], list) else []
            timeline.extend(segment for segment in segments if isinstance(segment, dict))
        return timeline

    def list_finalized_streams_with_jobs(self, *, limit: int) -> list[str]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT stream.call_id
                FROM postcall_realtime_streams AS stream
                JOIN postcall_jobs AS job ON job.call_id = stream.call_id
                WHERE stream.state = 'finalized'
                  AND stream.linked_postcall_job_id IS NULL
                ORDER BY stream.finalized_at ASC NULLS LAST, stream.updated_at ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [row["call_id"] for row in rows]

    def list_ended_streams_ready_to_finalize(self, *, limit: int) -> list[str]:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT call_id
                FROM postcall_realtime_streams
                WHERE state = 'ended'
                  AND ended_at IS NOT NULL
                  AND received_duration_sec - analyzed_duration_sec < %s
                ORDER BY ended_at ASC, updated_at ASC
                LIMIT %s
                """,
                (self.settings.tail_min_sec, limit),
            ).fetchall()
        return [row["call_id"] for row in rows]

    def close_idle_streams(self, *, limit: int) -> int:
        idle_end_seconds = self.settings.idle_end_seconds
        if idle_end_seconds <= 0:
            return 0
        with connect(autocommit=False) as conn:
            with conn.transaction():
                rows = conn.execute(
                    """
                    WITH candidate AS (
                        SELECT id, call_id, received_duration_sec
                        FROM postcall_realtime_streams
                        WHERE state IN ('receiving', 'alerting')
                          AND ended_at IS NULL
                          AND updated_at <= now() - make_interval(secs => %s)
                        ORDER BY updated_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE postcall_realtime_streams AS stream
                    SET
                        state = 'failed',
                        ended_at = COALESCE(stream.ended_at, now()),
                        error_code = CASE
                            WHEN candidate.received_duration_sec < %s THEN 'REALTIME_IDLE_TOO_SHORT'
                            ELSE 'REALTIME_MISSING_END'
                        END,
                        error_message = CASE
                            WHEN candidate.received_duration_sec < %s THEN
                                'stream idle timed out before minimum duration: callId='
                                || candidate.call_id
                                || ' receivedDurationSec='
                                || candidate.received_duration_sec::text
                                || ' minDurationSec='
                                || %s::text
                            ELSE
                                'stream idle timed out without end flag; falling back to non-realtime audio analysis: callId='
                                || candidate.call_id
                                || ' receivedDurationSec='
                                || candidate.received_duration_sec::text
                                || ' idleEndSeconds='
                                || %s::text
                        END,
                        updated_at = now()
                    FROM candidate
                    WHERE stream.id = candidate.id
                    RETURNING stream.call_id
                    """,
                    (
                        idle_end_seconds,
                        limit,
                        self.settings.min_duration_sec,
                        self.settings.min_duration_sec,
                        self.settings.min_duration_sec,
                        idle_end_seconds,
                    ),
                ).fetchall()
        return len(rows)

    def mark_stream_finalized(
        self,
        *,
        call_id: str,
        job_internal_id: Any | None,
        attention_evaluation: AttentionEvaluation,
        emotion_types_zh: list[str],
    ) -> None:
        payload = {
            "jjdh": None,
            "callId": call_id,
            "level": attention_evaluation.level,
            "levelName": attention_evaluation.level_name,
            "emotionTypes": emotion_types_zh,
        }
        with connect(autocommit=False) as conn:
            with conn.transaction():
                stream = conn.execute(
                    """
                    UPDATE postcall_realtime_streams
                    SET
                        state = 'finalized',
                        linked_postcall_job_id = COALESCE(linked_postcall_job_id, %s),
                        finalized_at = now(),
                        updated_at = now()
                    WHERE call_id = %s
                    RETURNING id
                    """,
                    (job_internal_id, call_id),
                ).fetchone()
                if stream is None or attention_evaluation.level not in {1, 2}:
                    return
                job_row = None
                if job_internal_id is not None:
                    job_row = conn.execute(
                        "SELECT jjdh FROM postcall_jobs WHERE id = %s",
                        (job_internal_id,),
                    ).fetchone()
                if job_row is not None:
                    payload["jjdh"] = job_row["jjdh"]
                conn.execute(
                    """
                    INSERT INTO postcall_realtime_alert_notifications (
                        stream_id,
                        postcall_job_id,
                        jjdh,
                        call_id,
                        level,
                        level_name,
                        emotion_types_zh,
                        payload,
                        state
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'skipped')
                    """,
                    (
                        stream["id"],
                        job_internal_id,
                        payload["jjdh"],
                        call_id,
                        attention_evaluation.level,
                        attention_evaluation.level_name,
                        emotion_types_zh,
                        Jsonb(payload),
                    ),
                )


class RealtimeAudioWorker:
    def __init__(
        self,
        *,
        realtime_settings: RealtimeWorkerSettings | None = None,
        postcall_settings: PostcallWorkerSettings | None = None,
        repository: RealtimeAudioRepository | None = None,
        postcall_repository: PostcallJobRepository | None = None,
    ) -> None:
        self.realtime_settings = realtime_settings or load_realtime_worker_settings()
        self.postcall_worker = PostcallWorker(settings=postcall_settings or load_postcall_worker_settings())
        self.repository = repository or RealtimeAudioRepository(settings=self.realtime_settings)
        self.postcall_repository = postcall_repository or PostcallJobRepository()

    def run_once(self, *, batch_size: int | None = None) -> int:
        limit = batch_size or self.realtime_settings.batch_size
        processed = self.repository.close_idle_streams(limit=max(1, limit))
        for _ in range(limit):
            claim = self.repository.claim_next_window()
            if claim is None:
                break
            self.process_window(claim)
            processed += 1
        for call_id in self.repository.list_finalized_streams_with_jobs(limit=max(1, limit)):
            self._persist_final_stream(call_id=call_id, model_runs=[])
            processed += 1
        for call_id in self.repository.list_ended_streams_ready_to_finalize(limit=max(1, limit)):
            self._persist_final_stream(call_id=call_id, model_runs=[])
            processed += 1
        return processed

    def process_window(self, claim: RealtimeWindowClaim) -> None:
        try:
            waveform = self.repository.load_window_waveform(claim)
            timeline, model_runs = self._analyze_window(claim, waveform)
            attention_evaluation = self.postcall_worker.attention_rules.evaluate(timeline)
            emotion_types_zh = _emotion_types_zh(timeline)
            self.repository.record_window_success(
                claim=claim,
                timeline=timeline,
                attention_evaluation=attention_evaluation,
                emotion_types_zh=emotion_types_zh,
            )
            self._persist_final_if_ready(claim, model_runs)
        except Exception as exc:
            self.repository.record_window_failure(claim=claim, error=exc)

    def _analyze_window(
        self,
        claim: RealtimeWindowClaim,
        waveform: np.ndarray,
    ) -> tuple[list[dict[str, Any]], list[ModelRunRecord]]:
        beats_segments, beats_run = self.postcall_worker._run_model(
            model_name=self.postcall_worker.beats_model.model_name,
            model_version=self.postcall_worker.beats_model.model_version,
            model_role="audio_event",
            fn=lambda: self.postcall_worker.beats_model.predict(waveform),
        )
        wavlm_segments, wavlm_run = self.postcall_worker._run_model(
            model_name=self.postcall_worker.wavlm_model.model_name,
            model_version=self.postcall_worker.wavlm_model.model_version,
            model_role="voice_emotion",
            fn=lambda: self.postcall_worker.wavlm_model.predict(waveform),
        )
        shifted = [
            _shift_segment(segment, claim.start_sec)
            for segment in [*beats_segments, *wavlm_segments]
        ]
        shifted.sort(key=lambda item: (item.start_sec, item.end_sec, item.segment_id))
        timeline = [_timeline_segment_payload(segment) for segment in shifted]
        return timeline, [beats_run, wavlm_run]

    def _persist_final_if_ready(
        self,
        claim: RealtimeWindowClaim,
        model_runs: list[ModelRunRecord],
    ) -> None:
        if not claim.ended:
            return
        self._persist_final_stream(call_id=claim.call_id, model_runs=model_runs)

    def _persist_final_stream(
        self,
        *,
        call_id: str,
        model_runs: list[ModelRunRecord],
    ) -> None:
        timeline = self.repository.load_completed_timeline(call_id)
        job = self.postcall_repository.get_claimed_job_by_call_id(call_id)
        attention_rule_profile = "default"
        if job is None:
            attention_evaluation = self.postcall_worker.attention_rules.evaluate(timeline)
        else:
            (
                attention_evaluation,
                attention_rule_profile,
            ) = self.postcall_worker.evaluate_attention_for_job(
                job=job,
                timeline=timeline,
            )
        if job is None:
            self.repository.mark_stream_finalized(
                call_id=call_id,
                job_internal_id=None,
                attention_evaluation=attention_evaluation,
                emotion_types_zh=_emotion_types_zh(timeline),
            )
            return
        segments = [
            _segment_from_timeline(item, index=index)
            for index, item in enumerate(timeline, start=1)
        ]
        self.postcall_repository.persist_success(
            job=job,
            segments=segments,
            model_runs=model_runs,
            model_versions={
                "analysisProfile": "realtime",
                "audioEvent": self.postcall_worker.beats_model.model_version,
                "voiceEmotion": self.postcall_worker.wavlm_model.model_version,
            },
            audio_processing={
                "analysisProfile": "realtime",
                "source": "realtime_stream",
                "windowSec": self.realtime_settings.window_sec,
                "tailMinSec": self.realtime_settings.tail_min_sec,
                "timelineSegmentCount": len(segments),
                "attentionRuleVersion": attention_evaluation.rule_version,
                "attentionRuleProfile": attention_rule_profile,
                "level": attention_evaluation.level,
                "levelName": attention_evaluation.level_name,
                "matchedRuleCodes": attention_evaluation.matched_rule_codes,
            },
            attention_evaluation=attention_evaluation,
        )
        self.repository.mark_stream_finalized(
            call_id=call_id,
            job_internal_id=job.internal_id,
            attention_evaluation=attention_evaluation,
            emotion_types_zh=_emotion_types_zh(timeline),
        )


def _shift_segment(segment: TimelineSegmentRecord, offset_sec: float) -> TimelineSegmentRecord:
    return TimelineSegmentRecord(
        segment_id=segment.segment_id,
        start_sec=round(segment.start_sec + offset_sec, 3),
        end_sec=round(segment.end_sec + offset_sec, 3),
        speaker_label=segment.speaker_label,
        speaker_role=segment.speaker_role,
        role_source=segment.role_source,
        audio_event_scores=segment.audio_event_scores,
        voice_emotion_scores=segment.voice_emotion_scores,
        voice_detailed_scores=segment.voice_detailed_scores,
        voice_emotion_dimensions=segment.voice_emotion_dimensions,
        internal_payload={
            **segment.internal_payload,
            "sourceRole": segment.internal_payload.get("sourceRole", "realtime_window"),
            "timeOffsetSec": offset_sec,
        },
    )


def _segment_from_timeline(item: dict[str, Any], *, index: int) -> TimelineSegmentRecord:
    raw_segment_id = str(item.get("segmentId") or "").strip()
    return TimelineSegmentRecord(
        segment_id=raw_segment_id or f"realtime_segment_{index:04d}",
        start_sec=float(item["startSec"]),
        end_sec=float(item["endSec"]),
        speaker_label=item.get("speakerLabel"),
        speaker_role=item.get("speakerRole"),
        role_source=item.get("roleSource"),
        audio_event_scores=item.get("audioEventScores") or [],
        voice_emotion_scores=item.get("voiceEmotionScores") or [],
        voice_emotion_dimensions=item.get("voiceEmotionDimensions") or {},
        internal_payload={"source": "realtime_stream"},
    )


def _emotion_types_zh(timeline: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for segment in timeline:
        for score in segment.get("voiceEmotionScores") or []:
            if not isinstance(score, dict):
                continue
            name = score.get("emotionNameZh")
            value = score.get("score")
            if isinstance(name, str) and isinstance(value, int | float) and value >= 0.4:
                names.add(name)
    return sorted(names)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
