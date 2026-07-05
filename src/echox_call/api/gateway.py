"""Gateway-compatible realtime ASR/audio stream endpoints."""

from __future__ import annotations

import logging
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
logger = logging.getLogger("uvicorn.error")


@router.post("/asr/push")
def push_realtime_audio(
    file: Annotated[UploadFile, File()],
    voice_id: Annotated[str, Form()],
    seq: Annotated[int, Form()],
    end: Annotated[int, Form()],
    voice_format: Annotated[int, Form()],
    final: Annotated[int, Form()] = 0,
    vendor_specific_param: Annotated[str | None, Form()] = None,
    tracking_id: Annotated[str | None, Header(alias="Tracking-Id")] = None,
) -> dict[str, object]:
    vendor = parse_vendor_specific_param(vendor_specific_param)
    logger.info(
        "realtime audio push received trackingId=%s callId=%s voiceId=%s seq=%s final=%s "
        "end=%s voiceFormat=%s filename=%s contentType=%s vendorSpecificParam=%s",
        tracking_id,
        vendor.call_id,
        voice_id,
        seq,
        final,
        end,
        voice_format,
        file.filename,
        file.content_type,
        vendor.raw,
    )
    if not vendor.call_id:
        logger.warning(
            "realtime audio push rejected reason=missing_callid trackingId=%s voiceId=%s seq=%s "
            "final=%s end=%s voiceFormat=%s vendorSpecificParam=%s",
            tracking_id,
            voice_id,
            seq,
            final,
            end,
            voice_format,
            vendor.raw,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="vendor_specific_param.callid is required",
        )
    if seq < 0:
        logger.warning(
            "realtime audio push rejected reason=negative_seq trackingId=%s callId=%s voiceId=%s "
            "seq=%s final=%s end=%s voiceFormat=%s",
            tracking_id,
            vendor.call_id,
            voice_id,
            seq,
            final,
            end,
            voice_format,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="seq must be greater than or equal to 0",
        )

    try:
        path, size_bytes, sha256, audio_info = save_upload_chunk(
            storage_dir=_repository.settings.storage_dir,
            call_id=vendor.call_id,
            seq=seq,
            voice_format=voice_format,
            source=file.file,
        )
        logger.info(
            "realtime audio chunk saved trackingId=%s callId=%s voiceId=%s seq=%s "
            "sizeBytes=%s sha256=%s durationSec=%s sampleRate=%s channels=%s path=%s",
            tracking_id,
            vendor.call_id,
            voice_id,
            seq,
            size_bytes,
            sha256,
            audio_info.duration_sec,
            audio_info.sample_rate,
            audio_info.channels,
            path,
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
        logger.warning(
            "realtime audio push failed trackingId=%s callId=%s voiceId=%s seq=%s "
            "final=%s end=%s voiceFormat=%s error=%s",
            tracking_id,
            vendor.call_id,
            voice_id,
            seq,
            final,
            end,
            voice_format,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception(
            "realtime audio push crashed trackingId=%s callId=%s voiceId=%s seq=%s "
            "final=%s end=%s voiceFormat=%s",
            tracking_id,
            vendor.call_id,
            voice_id,
            seq,
            final,
            end,
            voice_format,
        )
        raise

    logger.info(
        "realtime audio chunk recorded trackingId=%s callId=%s voiceId=%s seq=%s "
        "duplicate=%s receivedDurationSec=%s ended=%s streamId=%s",
        tracking_id,
        record.call_id,
        record.voice_id,
        record.seq,
        record.duplicate,
        record.received_duration_sec,
        record.ended,
        record.stream_id,
    )
    return {"code": 0, "message": "success"}
