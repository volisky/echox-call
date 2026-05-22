"""Offline pyannote speaker diarization smoke test."""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any


DEFAULT_MODEL_DIR = Path("models/speaker-diarization-community-1")
DEFAULT_AUDIO_PATH = Path("data/62050220250630025635052017.wav")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run offline pyannote speaker diarization smoke test",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help="Local pyannote diarization pipeline directory",
    )
    parser.add_argument(
        "--audio-path",
        type=Path,
        default=DEFAULT_AUDIO_PATH,
        help="Local WAV file used for smoke test",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Pipeline device, for example cpu or cuda",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        help="Optional known speaker count passed to pyannote",
    )
    parser.add_argument(
        "--min-speakers",
        type=int,
        help="Optional lower bound passed to pyannote",
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        help="Optional upper bound passed to pyannote",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    model_dir = args.model_dir
    audio_path = args.audio_path
    if not model_dir.exists():
        print(f"model directory not found: {model_dir}", file=sys.stderr)
        return 2
    if not (model_dir / "config.yaml").exists():
        print(f"pyannote config.yaml not found in: {model_dir}", file=sys.stderr)
        return 2
    if not audio_path.exists():
        print(f"audio file not found: {audio_path}", file=sys.stderr)
        return 2

    # The smoke test must prove local loading works without contacting Hugging Face.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        speaker_segments = _run_diarization(
            model_dir=model_dir,
            audio_path=audio_path,
            device=args.device,
            num_speakers=args.num_speakers,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
        )
    except ImportError as exc:
        print(f"pyannote dependency missing: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"diarization smoke test failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    speaker_count = len({segment["speakerLabel"] for segment in speaker_segments})
    result = {
        "modelDir": str(model_dir),
        "audioPath": str(audio_path),
        "speakerSegments": speaker_segments,
        "speakerCount": speaker_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_diarization(
    *,
    model_dir: Path,
    audio_path: Path,
    device: str,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> list[dict[str, Any]]:
    import librosa
    import soundfile as sf
    import torch
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"\s*torchcodec is not installed correctly.*",
        )
        from pyannote.audio import Pipeline
        from pyannote.audio.core.task import Problem, Resolution, Specifications

        torch.serialization.add_safe_globals([Specifications, Problem, Resolution])
        pipeline = Pipeline.from_pretrained(str(model_dir))
    pipeline.to(torch.device(device))

    kwargs: dict[str, int] = {}
    if num_speakers is not None:
        kwargs["num_speakers"] = num_speakers
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers

    output = pipeline(_load_audio_for_pyannote(audio_path, sf=sf, librosa=librosa, torch=torch), **kwargs)
    annotation = (
        getattr(output, "exclusive_speaker_diarization", None)
        or getattr(output, "speaker_diarization", output)
    )

    segments: list[dict[str, Any]] = []
    if hasattr(annotation, "itertracks"):
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            segments.append(_segment_to_json(turn, speaker))
    else:
        for turn, speaker in annotation:
            segments.append(_segment_to_json(turn, speaker))

    segments.sort(key=lambda item: (item["startSec"], item["endSec"], item["speakerLabel"]))
    return segments


def _segment_to_json(turn: Any, speaker: object) -> dict[str, Any]:
    return {
        "startSec": round(float(turn.start), 3),
        "endSec": round(float(turn.end), 3),
        "speakerLabel": str(speaker),
    }


def _load_audio_for_pyannote(audio_path: Path, *, sf: Any, librosa: Any, torch: Any) -> dict[str, Any]:
    waveform, sample_rate = sf.read(audio_path, always_2d=True, dtype="float32")
    mono = waveform.mean(axis=1)
    if sample_rate != 16_000:
        mono = librosa.resample(mono, orig_sr=sample_rate, target_sr=16_000)
        sample_rate = 16_000

    tensor = torch.from_numpy(mono).float().unsqueeze(0)
    return {"waveform": tensor, "sample_rate": sample_rate}


if __name__ == "__main__":
    raise SystemExit(main())
