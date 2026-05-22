"""Postcall worker orchestration for model outputs and attention insights."""

from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import torch

from echox_call.core.settings import PostcallWorkerSettings, load_postcall_worker_settings
from echox_call.features.audio_analysis.postcall.audio_processing import (
    AudioProcessingError,
    download_audio,
    normalize_audio,
)
from echox_call.features.audio_analysis.postcall.attention_rules import (
    AttentionRulesEngine,
    PostcallAttentionRuleError,
    load_attention_rules,
)
from echox_call.features.audio_analysis.postcall.model_runtime import (
    BeatsAudioEventModel,
    ModelRuntimeError,
    SpeakerDiarizationModel,
    WavLMEmotionModel,
    assign_segment_ids,
)
from echox_call.features.audio_analysis.postcall.repository import (
    PostcallJobStaleError,
    PostcallJobRepository,
    PostcallResultContractError,
)
from echox_call.features.audio_analysis.postcall.schemas import PostcallTimelineSegment
from echox_call.features.audio_analysis.postcall.worker_models import (
    AudioAssetRecord,
    ClaimedPostcallJob,
    ModelRunRecord,
    SpeakerSegmentRecord,
    TimelineSegmentRecord,
)


_TORCH_INTEROP_THREADS_CONFIGURED: int | None = None
RETRYABLE_WORKER_ERROR_CODES = {
    "AUDIO_DOWNLOAD_FAILED",
    "WORKER_FAILED",
}


class PostcallWorker:
    def __init__(
        self,
        *,
        settings: PostcallWorkerSettings | None = None,
        repository: PostcallJobRepository | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or load_postcall_worker_settings()
        _configure_torch_threads(self.settings)
        self.repository = repository or PostcallJobRepository()
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self._beats_model: BeatsAudioEventModel | None = None
        self._diarization_model: SpeakerDiarizationModel | None = None
        self._wavlm_model: WavLMEmotionModel | None = None
        self._attention_rules: AttentionRulesEngine | None = None

    def run_once(self, *, batch_size: int | None = None) -> int:
        limit = self.settings.batch_size if batch_size is None else batch_size
        self.repository.recover_expired_jobs()
        processed = 0
        for _ in range(limit):
            job = self.repository.claim_next_job(
                worker_id=self.worker_id,
                lock_seconds=self.settings.lock_seconds,
            )
            if job is None:
                break
            self.process_job(job)
            processed += 1
        return processed

    def run_loop(self, *, sleep_seconds: float = 5.0) -> None:
        while True:
            processed = self.run_once()
            if processed == 0:
                time.sleep(sleep_seconds)

    def process_job(self, job: ClaimedPostcallJob) -> None:
        try:
            self._process_job(job)
        except AudioProcessingError as exc:
            self._record_failure(
                job=job,
                error_code=exc.code,
                error_message=str(exc),
                retryable=exc.retryable,
            )
        except PostcallResultContractError as exc:
            self._record_failure(
                job=job,
                error_code="RESULT_CONTRACT_FAILED",
                error_message=str(exc),
                retryable=False,
            )
        except ModelRuntimeError as exc:
            self._record_failure(
                job=job,
                error_code=exc.code,
                error_message=str(exc),
                retryable=_is_retryable_error(exc.code),
            )
        except PostcallAttentionRuleError as exc:
            self._record_failure(
                job=job,
                error_code="ATTENTION_RULE_FAILED",
                error_message=str(exc),
                retryable=False,
            )
        except PostcallJobStaleError:
            return
        except Exception as exc:
            self._record_failure(
                job=job,
                error_code="WORKER_FAILED",
                error_message=f"{exc.__class__.__name__}: {exc}",
                retryable=True,
            )

    def _record_failure(
        self,
        *,
        job: ClaimedPostcallJob,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> None:
        try:
            self.repository.record_failure(
                job=job,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
                retry_delay_seconds=_retry_delay_seconds(
                    attempt_count=job.attempt_count,
                    base_delay_seconds=self.settings.retry_base_delay_seconds,
                    max_delay_seconds=self.settings.retry_max_delay_seconds,
                ),
            )
        except PostcallJobStaleError:
            return

    def _process_job(self, job: ClaimedPostcallJob) -> None:
        job_dir = _job_storage_dir(
            root_dir=self.settings.storage_dir,
            job=job,
            storage_timezone=self.settings.storage_timezone,
        )
        storage_partition = job_dir.relative_to(self.settings.storage_dir).parent.as_posix()
        preprocess_started = _utc_now()
        downloaded = download_audio(
            audio_url=job.audio_url,
            output_dir=job_dir,
            max_bytes=self.settings.audio_max_bytes,
            timeout_sec=self.settings.audio_download_timeout_sec,
        )
        self.repository.insert_audio_asset(
            job=job,
            asset=AudioAssetRecord(
                asset_type="source",
                uri=str(downloaded.path),
                content_type=downloaded.content_type,
                sha256=downloaded.sha256,
                size_bytes=downloaded.size_bytes,
                metadata={
                    "downloadUrl": job.audio_url,
                    "storagePartition": storage_partition,
                    "storagePartitionBasis": "bjsj",
                    "storageTimezone": self.settings.storage_timezone.key,
                },
            ),
        )

        normalized = normalize_audio(
            source_path=downloaded.path,
            output_dir=job_dir,
            max_duration_sec=self.settings.audio_max_duration_sec,
        )
        self.repository.insert_audio_asset(
            job=job,
            asset=AudioAssetRecord(
                asset_type="normalized",
                uri=str(normalized.path),
                content_type="audio/wav",
                sha256=normalized.sha256,
                sample_rate=normalized.sample_rate,
                channels=normalized.channels,
                duration_sec=normalized.duration_sec,
                size_bytes=normalized.size_bytes,
                metadata={
                    "sourceSha256": downloaded.sha256,
                    "storagePartition": storage_partition,
                    "storagePartitionBasis": "bjsj",
                    "storageTimezone": self.settings.storage_timezone.key,
                },
            ),
        )
        preprocess_completed = _utc_now()
        model_runs = [
            _model_run(
                model_name="audio_preprocess",
                model_version="audio_processing_v1",
                model_role="audio_preprocess",
                started_at=preprocess_started,
                completed_at=preprocess_completed,
                output_summary={
                    "durationSec": normalized.duration_sec,
                    "sampleRate": normalized.sample_rate,
                    "channels": normalized.channels,
                    "sourceSizeBytes": downloaded.size_bytes,
                },
            )
        ]

        self.repository.mark_analyzing(
            job=job,
            lock_seconds=self.settings.lock_seconds,
        )

        beats_segments, beats_run = self._run_model(
            model_name=self.beats_model.model_name,
            model_version=self.beats_model.model_version,
            model_role="audio_event",
            fn=lambda: self.beats_model.predict(normalized.waveform),
        )
        model_runs.append(beats_run)

        speaker_segments: list[SpeakerSegmentRecord] = []
        model_versions = {
            "analysisProfile": self.settings.analysis_profile,
            "audioEvent": self.beats_model.model_version,
            "voiceEmotion": self.wavlm_model.model_version,
        }
        if self.settings.analysis_profile == "full":
            speaker_segments, diarization_run = self._run_diarization(normalized.waveform)
            wavlm_segments, wavlm_run = self._run_model(
                model_name=self.wavlm_model.model_name,
                model_version=self.wavlm_model.model_version,
                model_role="voice_emotion",
                fn=lambda: self.wavlm_model.predict_for_speakers(
                    normalized.waveform,
                    speaker_segments,
                ),
            )
            model_runs.extend([diarization_run, wavlm_run])
            model_versions["speakerDiarization"] = self.diarization_model.model_version
        else:
            wavlm_segments, wavlm_run = self._run_model(
                model_name=self.wavlm_model.model_name,
                model_version=self.wavlm_model.model_version,
                model_role="voice_emotion",
                fn=lambda: self.wavlm_model.predict(normalized.waveform),
            )
            model_runs.append(wavlm_run)

        segments = assign_segment_ids(beats_segments + wavlm_segments)
        self._validate_public_segments(segments)
        attention_evaluation = self.attention_rules.evaluate(
            [_timeline_segment_payload(segment) for segment in segments]
        )
        self.repository.persist_success(
            job=job,
            segments=segments,
            model_runs=model_runs,
            model_versions=model_versions,
            audio_processing={
                "analysisProfile": self.settings.analysis_profile,
                "speakerDiarizationEnabled": self.settings.analysis_profile == "full",
                "durationSec": normalized.duration_sec,
                "sampleRate": normalized.sample_rate,
                "channels": normalized.channels,
                "timelineSegmentCount": len(segments),
                "attentionRuleVersion": attention_evaluation.rule_version,
                "level": attention_evaluation.level,
                "levelName": attention_evaluation.level_name,
                "insightCount": len(attention_evaluation.insights),
                "matchedRuleCodes": attention_evaluation.matched_rule_codes,
                "speakerSegmentCount": len(speaker_segments),
                "speakerSegments": [
                    {
                        "startSec": round(segment.start_sec, 3),
                        "endSec": round(segment.end_sec, 3),
                        "speakerLabel": segment.speaker_label,
                        "speakerRole": segment.speaker_role,
                        "roleSource": segment.role_source,
                    }
                    for segment in speaker_segments
                ],
                "audioEventSegmentCount": len(beats_segments),
                "voiceEmotionSegmentCount": len(wavlm_segments),
            },
            attention_evaluation=attention_evaluation,
        )

    @property
    def beats_model(self) -> BeatsAudioEventModel:
        if self._beats_model is None:
            self._beats_model = BeatsAudioEventModel(
                checkpoint_path=self.settings.beats_checkpoint_path,
                labels_path=self.settings.beats_labels_path,
                device=self.settings.device,
                top_k=self.settings.audio_event_top_k,
            )
        return self._beats_model

    @property
    def diarization_model(self) -> SpeakerDiarizationModel:
        if self._diarization_model is None:
            self._diarization_model = SpeakerDiarizationModel(
                model_dir=self.settings.diarization_model_dir,
                device=self.settings.device,
                num_speakers=self.settings.diarization_num_speakers,
            )
        return self._diarization_model

    @property
    def wavlm_model(self) -> WavLMEmotionModel:
        if self._wavlm_model is None:
            self._wavlm_model = WavLMEmotionModel(
                model_dir=self.settings.wavlm_emotion_model_dir,
                backbone_dir=self.settings.wavlm_backbone_dir,
                labels_path=self.settings.wavlm_labels_path,
                device=self.settings.device,
            )
        return self._wavlm_model

    @property
    def attention_rules(self) -> AttentionRulesEngine:
        if self._attention_rules is None:
            self._attention_rules = load_attention_rules(self.settings.attention_rules_path)
        return self._attention_rules

    def _run_diarization(
        self,
        waveform: np.ndarray,
    ) -> tuple[list[SpeakerSegmentRecord], ModelRunRecord]:
        started_at = _utc_now()
        speaker_segments = self.diarization_model.predict(waveform)
        completed_at = _utc_now()
        return speaker_segments, _model_run(
            model_name=self.diarization_model.model_name,
            model_version=self.diarization_model.model_version,
            model_role="speaker_diarization",
            started_at=started_at,
            completed_at=completed_at,
            output_summary={
                "speakerCount": len({segment.speaker_label for segment in speaker_segments}),
                "segmentCount": len(speaker_segments),
                "speakerSegments": [
                    {
                        "startSec": round(segment.start_sec, 3),
                        "endSec": round(segment.end_sec, 3),
                        "speakerLabel": segment.speaker_label,
                        "speakerRole": segment.speaker_role,
                        "roleSource": segment.role_source,
                    }
                    for segment in speaker_segments
                ],
            },
        )

    def _run_model(
        self,
        *,
        model_name: str,
        model_version: str,
        model_role: str,
        fn: Callable[[], list[TimelineSegmentRecord]],
    ) -> tuple[list[TimelineSegmentRecord], ModelRunRecord]:
        started_at = _utc_now()
        segments = fn()
        completed_at = _utc_now()
        return segments, _model_run(
            model_name=model_name,
            model_version=model_version,
            model_role=model_role,
            started_at=started_at,
            completed_at=completed_at,
            output_summary={"segmentCount": len(segments)},
        )

    def _validate_public_segments(self, segments: list[TimelineSegmentRecord]) -> None:
        for segment in segments:
            try:
                PostcallTimelineSegment.model_validate(_timeline_segment_payload(segment))
            except Exception as exc:
                raise ModelRuntimeError(
                    "RESULT_CONTRACT_INVALID",
                    f"worker generated invalid public segment: {segment.segment_id}",
                ) from exc


def _model_run(
    *,
    model_name: str,
    model_version: str | None,
    model_role: str,
    started_at: datetime,
    completed_at: datetime,
    output_summary: dict[str, object],
) -> ModelRunRecord:
    return ModelRunRecord(
        model_name=model_name,
        model_version=model_version,
        model_role=model_role,
        status="succeeded",
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=max(0, int((completed_at - started_at).total_seconds() * 1000)),
        output_summary=output_summary,
    )


def _timeline_segment_payload(segment: TimelineSegmentRecord) -> dict[str, object]:
    return {
        "segmentId": segment.segment_id,
        "startSec": segment.start_sec,
        "endSec": segment.end_sec,
        "speakerLabel": segment.speaker_label,
        "speakerRole": segment.speaker_role,
        "roleSource": segment.role_source,
        "audioEventScores": segment.audio_event_scores,
        "voiceEmotionScores": segment.voice_emotion_scores,
        "voiceEmotionDimensions": segment.voice_emotion_dimensions,
    }


def _configure_torch_threads(settings: PostcallWorkerSettings) -> None:
    global _TORCH_INTEROP_THREADS_CONFIGURED

    if settings.torch_num_threads is not None:
        torch.set_num_threads(settings.torch_num_threads)

    if settings.torch_interop_threads is None:
        return

    if _TORCH_INTEROP_THREADS_CONFIGURED == settings.torch_interop_threads:
        return
    if _TORCH_INTEROP_THREADS_CONFIGURED is not None:
        raise ModelRuntimeError(
            "TORCH_THREADS_ALREADY_CONFIGURED",
            "torch interop threads cannot be changed after worker initialization",
        )

    torch.set_num_interop_threads(settings.torch_interop_threads)
    _TORCH_INTEROP_THREADS_CONFIGURED = settings.torch_interop_threads


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_retryable_error(error_code: str) -> bool:
    return error_code in RETRYABLE_WORKER_ERROR_CODES


def _retry_delay_seconds(
    *,
    attempt_count: int,
    base_delay_seconds: int,
    max_delay_seconds: int,
) -> int:
    exponent = max(0, attempt_count - 1)
    return min(max_delay_seconds, base_delay_seconds * (2**exponent))


def _job_storage_dir(
    *,
    root_dir: Path,
    job: ClaimedPostcallJob,
    storage_timezone: ZoneInfo,
) -> Path:
    """Partition audio files by alarm time so storage matches the case timeline."""

    bjsj = job.bjsj
    if bjsj.tzinfo is None:
        bjsj = bjsj.replace(tzinfo=timezone.utc)

    partition_time = bjsj.astimezone(storage_timezone)
    return (
        root_dir
        / f"{partition_time.year:04d}"
        / f"{partition_time.month:02d}"
        / f"{partition_time.day:02d}"
        / f"{partition_time.hour:02d}"
        / job.job_id
    )
