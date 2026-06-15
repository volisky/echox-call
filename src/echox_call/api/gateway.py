"""Gateway-compatible realtime ASR/audio stream endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status

from echox_call.features.audio_analysis.postcall.realtime import (
    RealtimeAudioError,
    RealtimeAudioRepository,
    parse_vendor_specific_param,
    save_upload_chunk,
)


router = APIRouter()
_repository = RealtimeAudioRepository()


@router.post("/asr/push")
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
    _ = final
    _ = voice_format
    vendor = parse_vendor_specific_param(vendor_specific_param)
    if not vendor.call_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="vendor_specific_param.callid is required",
        )
    if seq < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="seq must be greater than or equal to 0",
        )

    try:
        path, size_bytes, sha256, audio_info = save_upload_chunk(
            storage_dir=_repository.settings.storage_dir,
            call_id=vendor.call_id,
            seq=seq,
            source=file.file,
        )
        record = _repository.record_chunk(
            call_id=vendor.call_id,
            voice_id=voice_id,
            seq=seq,
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            audio_info=audio_info,
            vendor=vendor,
            ended=end == 1,
        )
    except RealtimeAudioError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    _ = record
    _ = tracking_id
    return {"code": 0, "message": "success"}
