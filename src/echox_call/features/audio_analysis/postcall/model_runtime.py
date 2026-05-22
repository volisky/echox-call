"""Model loading and inference for postcall raw audio outputs."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from safetensors.torch import load_file

from echox_call.features.audio_analysis.postcall.audio_processing import (
    TARGET_SAMPLE_RATE,
    beats_windows,
    speech_windows_from_energy,
)
from echox_call.features.audio_analysis.postcall.worker_models import (
    SpeakerSegmentRecord,
    TimelineSegmentRecord,
)


DIMENSION_LABELS = {
    "arousal": ("Arousal", "唤醒度"),
    "valence": ("Valence", "情绪效价"),
    "dominance": ("Dominance", "控制感"),
}


@dataclass(frozen=True)
class LabelMeta:
    index: int | None
    mid: str | None
    name_en: str
    name_zh: str
    native_label: str | None


class ModelRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BeatsAudioEventModel:
    def __init__(
        self,
        *,
        checkpoint_path: Path,
        labels_path: Path,
        device: str,
        top_k: int,
    ) -> None:
        self.checkpoint_path = checkpoint_path
        self.labels_by_mid = _load_beats_labels(labels_path)
        self.device = torch.device(device)
        self.top_k = top_k
        self.model, self.label_dict = self._load_model()

    @property
    def model_name(self) -> str:
        return "BEATs_iter3_plus_AS2M_finetuned_on_AS2M_cpt2"

    @property
    def model_version(self) -> str:
        return self.checkpoint_path.name

    def predict(self, waveform: np.ndarray) -> list[TimelineSegmentRecord]:
        segments: list[TimelineSegmentRecord] = []
        for start_sec, end_sec, chunk, window_meta in beats_windows(waveform):
            source = torch.from_numpy(chunk).float().unsqueeze(0).to(self.device)
            with torch.no_grad():
                probabilities, _ = self.model.extract_features(source)
            scores = probabilities[0].detach().cpu().numpy()
            full_scores = self._scores_to_full_records(scores)
            top_scores = sorted(full_scores, key=lambda item: item["score"], reverse=True)[
                : self.top_k
            ]
            public_scores = [
                {
                    "eventNameEn": item["eventNameEn"],
                    "eventNameZh": item["eventNameZh"],
                    "score": item["score"],
                }
                for item in top_scores
            ]
            segments.append(
                TimelineSegmentRecord(
                    segment_id="",
                    start_sec=start_sec,
                    end_sec=end_sec,
                    speaker_label=None,
                    speaker_role=None,
                    role_source="global_audio",
                    audio_event_scores=public_scores,
                    voice_emotion_scores=[],
                    voice_detailed_scores=[],
                    voice_emotion_dimensions={},
                    internal_payload={
                        "sourceRole": "audio_event",
                        "roleSource": "global_audio",
                        "modelName": self.model_name,
                        "modelVersion": self.model_version,
                        "window": window_meta,
                        "audioEventScoresFull": full_scores,
                    },
                )
            )
        return segments

    def _load_model(self) -> tuple[Any, dict[int, str]]:
        beats_dir = Path("third_party/beats").resolve()
        if str(beats_dir) not in sys.path:
            sys.path.insert(0, str(beats_dir))

        from BEATs import BEATs, BEATsConfig  # type: ignore

        checkpoint = torch.load(self.checkpoint_path, map_location="cpu")
        model = BEATs(BEATsConfig(checkpoint["cfg"]))
        model.load_state_dict(checkpoint["model"])
        model.to(self.device)
        model.eval()
        label_dict = {int(index): mid for index, mid in checkpoint["label_dict"].items()}
        return model, label_dict

    def _scores_to_full_records(self, scores: np.ndarray) -> list[dict[str, Any]]:
        records = []
        for index, score in enumerate(scores.tolist()):
            mid = self.label_dict.get(index, f"unknown_index_{index}")
            label = self.labels_by_mid.get(
                mid,
                LabelMeta(
                    index=index,
                    mid=mid,
                    name_en=mid,
                    name_zh=mid,
                    native_label=f"audioset_index_{index}",
                ),
            )
            records.append(
                {
                    "index": index,
                    "mid": mid,
                    "eventNameEn": label.name_en,
                    "eventNameZh": label.name_zh,
                    "score": float(score),
                }
            )
        return records


class WavLMEmotionModel:
    def __init__(
        self,
        *,
        model_dir: Path,
        backbone_dir: Path,
        labels_path: Path,
        device: str,
    ) -> None:
        self.model_dir = model_dir
        self.backbone_dir = backbone_dir
        self.labels = _load_wavlm_labels(labels_path)
        self.device = torch.device(device)
        self.model = self._load_model()

    @property
    def model_name(self) -> str:
        return "wavlm-large-categorical-emotion"

    @property
    def model_version(self) -> str:
        return self.model_dir.name

    def predict(self, waveform: np.ndarray) -> list[TimelineSegmentRecord]:
        windows = [
            {
                "start_sec": window.start_sec,
                "end_sec": window.end_sec,
                "start_sample": window.start_sample,
                "end_sample": window.end_sample,
                "speaker_label": None,
                "speaker_role": None,
                "role_source": "energy_vad",
                "method": "librosa.effects.split",
            }
            for window in speech_windows_from_energy(waveform)
        ]
        return self._predict_windows(waveform, windows)

    def predict_for_speakers(
        self,
        waveform: np.ndarray,
        speaker_segments: list[SpeakerSegmentRecord],
    ) -> list[TimelineSegmentRecord]:
        windows = _speaker_windows(waveform, speaker_segments)
        return self._predict_windows(waveform, windows)

    def _predict_windows(
        self,
        waveform: np.ndarray,
        windows: list[dict[str, Any]],
    ) -> list[TimelineSegmentRecord]:
        segments: list[TimelineSegmentRecord] = []
        for window in windows:
            chunk = waveform[window["start_sample"] : window["end_sample"]].astype(np.float32)
            if len(chunk) == 0:
                continue
            model_input_padded = False
            min_samples = 3 * TARGET_SAMPLE_RATE
            if len(chunk) < min_samples:
                model_input_padded = True
                chunk = np.pad(chunk, (0, min_samples - len(chunk)))
            tensor = torch.from_numpy(chunk).float().unsqueeze(0).to(self.device)
            with torch.no_grad():
                logits, detailed_logits, arousal, valence, dominance = self.model(tensor)
            emotion_scores = F.softmax(logits, dim=1)[0].detach().cpu().numpy()
            detailed_scores = F.softmax(detailed_logits, dim=1)[0].detach().cpu().numpy()

            public_emotions = self._public_emotion_scores(emotion_scores)
            detailed = [
                {"index": index, "score": float(score)}
                for index, score in enumerate(detailed_scores.tolist())
            ]
            dimensions = {
                "arousal": _dimension_value("arousal", arousal),
                "valence": _dimension_value("valence", valence),
                "dominance": _dimension_value("dominance", dominance),
            }

            segments.append(
                TimelineSegmentRecord(
                    segment_id="",
                    start_sec=window["start_sec"],
                    end_sec=window["end_sec"],
                    speaker_label=window["speaker_label"],
                    speaker_role=window["speaker_role"],
                    role_source=window["role_source"],
                    audio_event_scores=[],
                    voice_emotion_scores=public_emotions,
                    voice_detailed_scores=detailed,
                    voice_emotion_dimensions=dimensions,
                    internal_payload={
                        "sourceRole": "voice_emotion",
                        "speakerLabel": window["speaker_label"],
                        "speakerRole": window["speaker_role"],
                        "roleSource": window["role_source"],
                        "modelName": self.model_name,
                        "modelVersion": self.model_version,
                        "window": {
                            "method": window["method"],
                            "sampleRate": TARGET_SAMPLE_RATE,
                            "modelInputPadded": model_input_padded,
                        },
                    },
                )
            )
        return segments

    def _load_model(self) -> torch.nn.Module:
        module = _load_module_from_path(
            "echox_call_third_party_wavlm_emotion",
            Path("third_party/WavLM/emotion/wavlm_emotion.py"),
        )
        config = json.loads((self.model_dir / "config.json").read_text(encoding="utf-8"))
        model = module.WavLMWrapper(
            **config,
            backbone_model_path=str(self.backbone_dir),
            processor_path=str(self.backbone_dir),
            local_files_only=True,
        )
        state_dict = load_file(str(self.model_dir / "model.safetensors"), device="cpu")
        model.load_state_dict(state_dict, strict=True)
        model.to(self.device)
        model.eval()
        return model

    def _public_emotion_scores(self, scores: np.ndarray) -> list[dict[str, Any]]:
        results = []
        for index, score in enumerate(scores.tolist()):
            label = self.labels[index]
            results.append(
                {
                    "emotionNameEn": label.name_en,
                    "emotionNameZh": label.name_zh,
                    "score": float(score),
                }
            )
        return results

class SpeakerDiarizationModel:
    def __init__(
        self,
        *,
        model_dir: Path,
        device: str,
        num_speakers: int | None,
    ) -> None:
        self.model_dir = model_dir
        self.device = torch.device(device)
        self.num_speakers = num_speakers
        self.pipeline = self._load_pipeline()

    @property
    def model_name(self) -> str:
        return "pyannote-speaker-diarization-community-1"

    @property
    def model_version(self) -> str:
        return self.model_dir.name

    def predict(self, waveform: np.ndarray) -> list[SpeakerSegmentRecord]:
        tensor = torch.from_numpy(waveform.astype(np.float32)).float().unsqueeze(0)
        kwargs = {}
        if self.num_speakers is not None:
            kwargs["num_speakers"] = self.num_speakers

        output = self.pipeline(
            {"waveform": tensor, "sample_rate": TARGET_SAMPLE_RATE},
            **kwargs,
        )
        annotation = (
            getattr(output, "exclusive_speaker_diarization", None)
            or getattr(output, "speaker_diarization", output)
        )
        return _merge_speaker_segments(_annotation_to_speaker_segments(annotation))

    def _load_pipeline(self) -> Any:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"\s*torchcodec is not installed correctly.*",
            )
            from pyannote.audio import Pipeline
            from pyannote.audio.core.task import Problem, Resolution, Specifications

            torch.serialization.add_safe_globals([Specifications, Problem, Resolution])
            pipeline = Pipeline.from_pretrained(str(self.model_dir))
        pipeline.to(self.device)
        return pipeline


def assign_segment_ids(segments: list[TimelineSegmentRecord]) -> list[TimelineSegmentRecord]:
    ordered = sorted(
        segments,
        key=lambda item: (
            item.start_sec,
            item.end_sec,
            item.internal_payload.get("sourceRole", ""),
            item.speaker_label or "",
        ),
    )
    assigned = []
    for index, segment in enumerate(ordered, start=1):
        assigned.append(
            TimelineSegmentRecord(
                segment_id=f"seg_{index:06d}",
                start_sec=segment.start_sec,
                end_sec=segment.end_sec,
                speaker_label=segment.speaker_label,
                speaker_role=segment.speaker_role,
                role_source=segment.role_source,
                audio_event_scores=segment.audio_event_scores,
                voice_emotion_scores=segment.voice_emotion_scores,
                voice_detailed_scores=segment.voice_detailed_scores,
                voice_emotion_dimensions=segment.voice_emotion_dimensions,
                internal_payload=segment.internal_payload,
            )
        )
    return assigned


def _load_beats_labels(path: Path) -> dict[str, LabelMeta]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = csv.DictReader(file)
        return {
            row["mid"]: LabelMeta(
                index=int(row["index"]),
                mid=row["mid"],
                name_en=row["label_en"],
                name_zh=row["label_zh"] or row["label_en"],
                native_label=row.get("db_native_label"),
            )
            for row in rows
        }


def _load_wavlm_labels(path: Path) -> dict[int, LabelMeta]:
    labels: dict[int, LabelMeta] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if row["output_group"] != "emotion":
                continue
            index = int(row["index"])
            labels[index] = LabelMeta(
                index=index,
                mid=None,
                name_en=row["name_en"],
                name_zh=row["name_zh"] or row["name_en"],
                native_label=row.get("db_native_label"),
            )
    if len(labels) != 9:
        raise ModelRuntimeError("WAVLM_LABELS_INVALID", "expected 9 WavLM emotion labels")
    return labels


def _dimension_value(key: str, tensor: torch.Tensor) -> dict[str, Any]:
    name_en, name_zh = DIMENSION_LABELS[key]
    return {
        "dimensionNameEn": name_en,
        "dimensionNameZh": name_zh,
        "value": float(tensor.detach().cpu().reshape(-1)[0].item()),
    }


def _annotation_to_speaker_segments(annotation: Any) -> list[SpeakerSegmentRecord]:
    segments: list[SpeakerSegmentRecord] = []
    if hasattr(annotation, "itertracks"):
        iterator = annotation.itertracks(yield_label=True)
        for turn, _, speaker in iterator:
            segments.append(
                SpeakerSegmentRecord(
                    start_sec=float(turn.start),
                    end_sec=float(turn.end),
                    speaker_label=str(speaker),
                )
            )
    else:
        for turn, speaker in annotation:
            segments.append(
                SpeakerSegmentRecord(
                    start_sec=float(turn.start),
                    end_sec=float(turn.end),
                    speaker_label=str(speaker),
                )
            )
    return segments


def _merge_speaker_segments(
    segments: list[SpeakerSegmentRecord],
    *,
    max_gap_sec: float = 0.5,
) -> list[SpeakerSegmentRecord]:
    merged: list[SpeakerSegmentRecord] = []
    for segment in sorted(segments, key=lambda item: (item.start_sec, item.end_sec)):
        if segment.end_sec <= segment.start_sec:
            continue
        if not merged:
            merged.append(segment)
            continue

        previous = merged[-1]
        if (
            previous.speaker_label == segment.speaker_label
            and segment.start_sec - previous.end_sec <= max_gap_sec
        ):
            merged[-1] = SpeakerSegmentRecord(
                start_sec=previous.start_sec,
                end_sec=max(previous.end_sec, segment.end_sec),
                speaker_label=previous.speaker_label,
                speaker_role=previous.speaker_role,
                role_source=previous.role_source,
            )
        else:
            merged.append(segment)
    return merged


def _speaker_windows(
    waveform: np.ndarray,
    speaker_segments: list[SpeakerSegmentRecord],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    total_samples = len(waveform)
    for segment in speaker_segments:
        start_sample = max(0, int(segment.start_sec * TARGET_SAMPLE_RATE))
        end_sample = min(total_samples, int(segment.end_sec * TARGET_SAMPLE_RATE))
        if end_sample <= start_sample:
            continue
        if (end_sample - start_sample) / TARGET_SAMPLE_RATE < 1.0:
            continue

        for window_start, window_end in _split_sample_window(start_sample, end_sample):
            windows.append(
                {
                    "start_sec": window_start / TARGET_SAMPLE_RATE,
                    "end_sec": window_end / TARGET_SAMPLE_RATE,
                    "start_sample": window_start,
                    "end_sample": window_end,
                    "speaker_label": segment.speaker_label,
                    "speaker_role": segment.speaker_role,
                    "role_source": segment.role_source,
                    "method": "pyannote.speaker_diarization",
                }
            )
    return windows


def _split_sample_window(start_sample: int, end_sample: int) -> list[tuple[int, int]]:
    max_samples = 15 * TARGET_SAMPLE_RATE
    window_samples = 10 * TARGET_SAMPLE_RATE
    hop_samples = 5 * TARGET_SAMPLE_RATE
    if end_sample - start_sample <= max_samples:
        return [(start_sample, end_sample)]

    windows = []
    cursor = start_sample
    while cursor < end_sample:
        window_end = min(cursor + window_samples, end_sample)
        windows.append((cursor, window_end))
        if window_end >= end_sample:
            break
        cursor += hop_samples
    return windows


def _load_module_from_path(module_name: str, path: Path) -> Any:
    resolved = path.resolve()
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise ModelRuntimeError("MODEL_IMPORT_FAILED", f"cannot import module: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
