"""Audio download, normalization, and segmentation helpers for postcall worker."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import librosa
import numpy as np
import soundfile as sf


TARGET_SAMPLE_RATE = 16_000


class AudioProcessingError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    content_type: str | None
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class NormalizedAudio:
    path: Path
    waveform: np.ndarray
    sample_rate: int
    channels: int
    duration_sec: float
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SpeechWindow:
    start_sec: float
    end_sec: float
    start_sample: int
    end_sample: int


def download_audio(
    *,
    audio_url: str,
    output_dir: Path,
    max_bytes: int,
    timeout_sec: int,
) -> DownloadedAudio:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / f"source{_suffix_from_url(audio_url)}"
    digest = hashlib.sha256()
    size_bytes = 0

    try:
        with httpx.stream(
            "GET",
            audio_url,
            follow_redirects=True,
            timeout=timeout_sec,
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > max_bytes:
                raise AudioProcessingError(
                    "AUDIO_TOO_LARGE",
                    f"audio content-length exceeds limit: {content_length}",
                )

            content_type = response.headers.get("content-type")
            if _is_known_non_audio_content_type(content_type):
                raise AudioProcessingError(
                    "UNSUPPORTED_AUDIO_CONTENT_TYPE",
                    f"audio URL returned non-audio content-type: {content_type}",
                )

            with source_path.open("wb") as file:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise AudioProcessingError(
                            "AUDIO_TOO_LARGE",
                            f"audio download exceeds limit: {max_bytes} bytes",
                        )
                    digest.update(chunk)
                    file.write(chunk)
    except AudioProcessingError:
        raise
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code == 408 or status_code == 429 or status_code >= 500:
            raise AudioProcessingError(
                "AUDIO_DOWNLOAD_FAILED",
                f"audio download failed with retryable HTTP {status_code}",
                retryable=True,
            ) from exc
        raise AudioProcessingError(
            "AUDIO_DOWNLOAD_HTTP_CLIENT_ERROR",
            f"audio download failed with non-retryable HTTP {status_code}",
        ) from exc
    except httpx.RequestError as exc:
        raise AudioProcessingError("AUDIO_DOWNLOAD_FAILED", str(exc), retryable=True) from exc

    if size_bytes == 0:
        raise AudioProcessingError("EMPTY_AUDIO", "downloaded audio is empty")

    return DownloadedAudio(
        path=source_path,
        content_type=content_type,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
    )


def normalize_audio(
    *,
    source_path: Path,
    output_dir: Path,
    max_duration_sec: int,
) -> NormalizedAudio:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = output_dir / "normalized.wav"

    try:
        audio, source_sample_rate = sf.read(
            source_path,
            always_2d=True,
            dtype="float32",
        )
    except Exception as exc:
        raise AudioProcessingError(
            "UNSUPPORTED_AUDIO_FORMAT",
            f"audio format is not supported by soundfile: {source_path.name}",
        ) from exc

    if audio.size == 0:
        raise AudioProcessingError("EMPTY_AUDIO", "audio contains no samples")

    channels = int(audio.shape[1])
    mono = audio.mean(axis=1).astype(np.float32)
    duration_sec = float(len(mono) / source_sample_rate)
    if duration_sec > max_duration_sec:
        raise AudioProcessingError(
            "AUDIO_TOO_LONG",
            f"audio duration exceeds limit: {duration_sec:.3f}s",
        )

    if source_sample_rate != TARGET_SAMPLE_RATE:
        mono = librosa.resample(
            mono,
            orig_sr=source_sample_rate,
            target_sr=TARGET_SAMPLE_RATE,
        ).astype(np.float32)

    sf.write(normalized_path, mono, TARGET_SAMPLE_RATE, subtype="PCM_16")
    digest = _sha256_file(normalized_path)
    size_bytes = normalized_path.stat().st_size

    return NormalizedAudio(
        path=normalized_path,
        waveform=mono,
        sample_rate=TARGET_SAMPLE_RATE,
        channels=1 if channels > 0 else channels,
        duration_sec=float(len(mono) / TARGET_SAMPLE_RATE),
        sha256=digest,
        size_bytes=size_bytes,
    )


def beats_windows(
    waveform: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
    *,
    window_sec: float = 10.0,
    hop_sec: float = 5.0,
) -> list[tuple[float, float, np.ndarray, dict[str, float | bool]]]:
    window_samples = int(window_sec * sample_rate)
    hop_samples = int(hop_sec * sample_rate)
    total_samples = len(waveform)
    if total_samples == 0:
        return []

    starts = [0]
    if total_samples > window_samples:
        starts = list(range(0, total_samples - window_samples + 1, hop_samples))
        if starts[-1] + window_samples < total_samples:
            starts.append(starts[-1] + hop_samples)

    windows = []
    for start in starts:
        end = min(start + window_samples, total_samples)
        chunk = waveform[start:end]
        padded = False
        if len(chunk) < window_samples:
            padded = True
            chunk = np.pad(chunk, (0, window_samples - len(chunk)))
        windows.append(
            (
                start / sample_rate,
                end / sample_rate,
                chunk.astype(np.float32),
                {
                    "padded": padded,
                    "modelWindowSec": window_sec,
                    "hopSec": hop_sec,
                },
            )
        )
    return windows


def speech_windows_from_energy(
    waveform: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> list[SpeechWindow]:
    intervals = librosa.effects.split(
        waveform,
        top_db=30,
        frame_length=1024,
        hop_length=256,
    )
    if len(intervals) == 0:
        return []

    merged = _merge_intervals(intervals.tolist(), max_gap_samples=int(0.5 * sample_rate))
    expanded: list[SpeechWindow] = []
    for start, end in merged:
        duration = (end - start) / sample_rate
        if duration < 1.0:
            continue
        if duration < 3.0:
            center = (start + end) // 2
            half = int(1.5 * sample_rate)
            start = max(0, center - half)
            end = min(len(waveform), center + half)
        expanded.extend(_split_speech_interval(start, end, len(waveform), sample_rate))

    return expanded


def _split_speech_interval(
    start: int,
    end: int,
    total_samples: int,
    sample_rate: int,
) -> list[SpeechWindow]:
    max_samples = 15 * sample_rate
    window_samples = 10 * sample_rate
    hop_samples = 5 * sample_rate
    if end - start <= max_samples:
        return [SpeechWindow(start / sample_rate, end / sample_rate, start, end)]

    windows: list[SpeechWindow] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + window_samples, end)
        windows.append(
            SpeechWindow(cursor / sample_rate, window_end / sample_rate, cursor, window_end)
        )
        if window_end >= end:
            break
        cursor += hop_samples
    return windows


def _merge_intervals(
    intervals: list[list[int]],
    *,
    max_gap_samples: int,
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if start - previous_end <= max_gap_samples:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _suffix_from_url(audio_url: str) -> str:
    suffix = Path(urlparse(audio_url).path).suffix.lower()
    if suffix and len(suffix) <= 10:
        return suffix
    return ".bin"


def _is_known_non_audio_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    if not normalized:
        return False
    if normalized.startswith("audio/"):
        return False
    if normalized in {"application/octet-stream", "binary/octet-stream"}:
        return False
    return (
        normalized.startswith("text/")
        or normalized
        in {
            "application/json",
            "application/problem+json",
            "application/xml",
            "application/xhtml+xml",
        }
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
