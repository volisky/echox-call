"""Standalone realtime audio receiver for gateway integration tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, BinaryIO

import uvicorn
from fastapi import APIRouter, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse


DEFAULT_STORAGE_DIR = "/app/data/realtime-audio-test"
STORAGE_ENV = "REALTIME_AUDIO_TEST_STORAGE_DIR"
_lock = threading.RLock()


app = FastAPI(title="Realtime Audio Test Server", version="0.1.0")
router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_dir() -> Path:
    return Path(os.environ.get(STORAGE_ENV, DEFAULT_STORAGE_DIR))


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)


def _stream_dir(call_id: str) -> Path:
    return _storage_dir() / _safe_id(call_id)


def _metadata_path(call_id: str) -> Path:
    return _stream_dir(call_id) / "metadata.json"


def _parse_vendor_specific_param(value: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in (value or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, item_value = item.split("=", 1)
        key = key.strip().lower()
        item_value = item_value.strip()
        if key and item_value:
            parsed[key] = item_value
    return parsed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_upload(source: BinaryIO, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        shutil.copyfileobj(source, output)
    if target.stat().st_size <= 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded audio chunk is empty",
        )


def _inspect_wav(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
    except Exception:
        return {"durationSec": None, "sampleRate": None, "channels": None}
    duration = frames / sample_rate if sample_rate else None
    return {
        "durationSec": round(duration, 3) if duration is not None else None,
        "sampleRate": sample_rate,
        "channels": channels,
    }


def _load_metadata(call_id: str) -> dict[str, Any]:
    path = _metadata_path(call_id)
    if not path.exists():
        return {
            "callId": call_id,
            "state": "receiving",
            "createdAt": _now(),
            "updatedAt": _now(),
            "receivedDurationSec": 0.0,
            "chunkCount": 0,
            "ended": False,
            "chunks": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _save_metadata(call_id: str, metadata: dict[str, Any]) -> None:
    stream_dir = _stream_dir(call_id)
    stream_dir.mkdir(parents=True, exist_ok=True)
    metadata["updatedAt"] = _now()
    metadata["chunks"] = sorted(metadata.get("chunks", []), key=lambda item: item["seq"])
    metadata["chunkCount"] = len(metadata["chunks"])
    duration = sum(
        item.get("durationSec") or 0.0
        for item in metadata["chunks"]
        if isinstance(item.get("durationSec"), int | float)
    )
    metadata["receivedDurationSec"] = round(duration, 3)
    tmp_path = stream_dir / "metadata.json.tmp"
    tmp_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(_metadata_path(call_id))


def _list_metadata() -> list[dict[str, Any]]:
    storage_dir = _storage_dir()
    if not storage_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(storage_dir.glob("*/metadata.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


@app.exception_handler(HTTPException)
async def _http_exception_handler(_request: object, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": {}},
    )


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "storageDir": str(_storage_dir()),
        "time": _now(),
    }


@router.post("/gateway/asr/push")
def push_realtime_audio(
    file: Annotated[UploadFile, File()],
    voice_id: Annotated[str, Form()],
    seq: Annotated[int, Form()],
    final: Annotated[int, Form()],
    end: Annotated[int, Form()],
    voice_format: Annotated[int, Form()],
    vendor_specific_param: Annotated[str | None, Form()] = None,
    tracking_id: Annotated[str | None, Header(alias="Tracking-Id")] = None,
) -> dict[str, object]:
    vendor = _parse_vendor_specific_param(vendor_specific_param)
    call_id = vendor.get("callid")
    if not call_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="vendor_specific_param.callid is required",
        )
    if seq < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="seq must be greater than or equal to 0",
        )
    if final not in {0, 1} or end not in {0, 1}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="final and end must be 0 or 1",
        )

    stream_dir = _stream_dir(call_id)
    chunk_path = stream_dir / "chunks" / f"{seq:08d}.wav"
    tmp_path = stream_dir / "chunks" / f"{seq:08d}.uploading"

    with _lock:
        metadata = _load_metadata(call_id)
        _copy_upload(file.file, tmp_path)
        sha256 = _sha256_file(tmp_path)
        existing = next(
            (item for item in metadata["chunks"] if item["seq"] == seq),
            None,
        )
        if existing is not None:
            tmp_path.unlink(missing_ok=True)
            if existing["sha256"] != sha256:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"seq {seq} already exists with different content",
                )
            metadata["lastDuplicateSeq"] = seq
            metadata["lastReceivedAt"] = _now()
            _save_metadata(call_id, metadata)
            return {"code": 0, "message": "success"}

        tmp_path.replace(chunk_path)
        audio_info = _inspect_wav(chunk_path)
        metadata.update(
            {
                "callId": call_id,
                "voiceId": voice_id,
                "agentId": vendor.get("agentid"),
                "usrdn": vendor.get("usrdn"),
                "vendorSpecificParam": vendor_specific_param or "",
                "state": "ended" if end == 1 else "receiving",
                "ended": end == 1,
                "endedAt": _now() if end == 1 else metadata.get("endedAt"),
                "lastReceivedAt": _now(),
            }
        )
        metadata["chunks"].append(
            {
                "seq": seq,
                "fileName": chunk_path.name,
                "filePath": str(chunk_path),
                "sha256": sha256,
                "sizeBytes": chunk_path.stat().st_size,
                "durationSec": audio_info["durationSec"],
                "sampleRate": audio_info["sampleRate"],
                "channels": audio_info["channels"],
                "voiceId": voice_id,
                "voiceFormat": voice_format,
                "final": final,
                "end": end,
                "trackingId": tracking_id,
                "receivedAt": _now(),
            }
        )
        _save_metadata(call_id, metadata)
    return {"code": 0, "message": "success"}


@router.get("/streams")
def list_streams() -> dict[str, object]:
    streams = _list_metadata()
    return {"code": 0, "message": "success", "data": {"streams": streams}}


@router.get("/streams/{call_id}")
def get_stream(call_id: str) -> dict[str, object]:
    with _lock:
        path = _metadata_path(call_id)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"stream not found: {call_id}",
            )
        metadata = json.loads(path.read_text(encoding="utf-8"))
    return {"code": 0, "message": "success", "data": metadata}


@router.get("/streams/{call_id}/chunks/{seq}")
def get_stream_chunk(call_id: str, seq: int) -> FileResponse:
    chunk_path = _stream_dir(call_id) / "chunks" / f"{seq:08d}.wav"
    if not chunk_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"chunk not found: {call_id} seq={seq}",
        )
    return FileResponse(chunk_path, media_type="audio/wav", filename=chunk_path.name)


app.include_router(router)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run realtime audio test server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8022, type=int)
    parser.add_argument("--storage-dir", default=os.environ.get(STORAGE_ENV, DEFAULT_STORAGE_DIR))
    parser.add_argument("--reload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    os.environ[STORAGE_ENV] = args.storage_dir
    uvicorn.run(
        "server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
