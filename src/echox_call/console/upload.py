"""Console helpers for uploading local audio and creating postcall jobs."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from uuid import uuid4

from echox_call.core.auth import ApiClient
from echox_call.features.audio_analysis.postcall.schemas import CreatePostcallJobRequest
from echox_call.features.audio_analysis.postcall.service import postcall_job_service


UPLOAD_ROOT = Path("data/console_uploads")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}

CONSOLE_UPLOAD_CLIENT = ApiClient(
    client_id="console_upload",
    name="控制台上传",
    api_key="",
    source_system="console_upload",
    enabled=True,
    allow_debug=True,
)


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class StoredUpload:
    filename: str
    path: Path
    content_type: str


class ConsoleUploadError(RuntimeError):
    """Raised when console upload form data is invalid."""


def default_upload_form() -> dict[str, str]:
    now = datetime.now()
    stamp = now.strftime("%Y%m%d%H%M%S")
    return {
        "jjdh": f"CONSOLE_UPLOAD_{stamp}",
        "bjsj": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "JCJXTJSDWMC": "控制台测试单位",
        "JJDWMC": "控制台接警单位",
        "GXDWMC": "控制台管辖单位",
        "bjdh": "13800000000",
        "bjrmc": "测试报警人",
        "bjrxbdm": "0",
        "lxdh": "13800000000",
        "jqdz": "控制台上传测试地址",
        "bjnr": "控制台上传音频分析测试",
        "jqlbdm": "test_category",
        "jqlxdm": "test_type",
        "jqxldm": "",
        "jqzldm": "",
        "jqdj": "测试",
        "callbackUrl": "",
    }


def parse_multipart_form(body: bytes, content_type: str | None) -> tuple[dict[str, str], UploadedFile]:
    if not content_type or "multipart/form-data" not in content_type:
        raise ConsoleUploadError("上传表单必须使用 multipart/form-data。")
    if len(body) > MAX_UPLOAD_BYTES + 1024 * 1024:
        raise ConsoleUploadError(f"上传内容不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB。")

    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\n"
        b"MIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise ConsoleUploadError("上传表单格式不正确。")

    fields: dict[str, str] = {}
    uploaded_file: UploadedFile | None = None
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            uploaded_file = UploadedFile(
                filename=filename,
                content_type=part.get_content_type() or "application/octet-stream",
                content=payload,
            )
            continue
        charset = part.get_content_charset() or "utf-8"
        fields[name] = payload.decode(charset, errors="replace").strip()

    if uploaded_file is None:
        raise ConsoleUploadError("请选择要上传的音频文件。")
    if not uploaded_file.content:
        raise ConsoleUploadError("上传的音频文件为空。")
    if len(uploaded_file.content) > MAX_UPLOAD_BYTES:
        raise ConsoleUploadError(f"音频文件不能超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB。")
    return fields, uploaded_file


def save_uploaded_audio(uploaded_file: UploadedFile) -> StoredUpload:
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ConsoleUploadError("仅支持 wav、mp3、m4a、flac、ogg、aac 音频文件。")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex}{suffix}"
    path = UPLOAD_ROOT / stored_name
    path.write_bytes(uploaded_file.content)
    content_type = uploaded_file.content_type
    if content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(stored_name)[0] or "application/octet-stream"
    return StoredUpload(filename=stored_name, path=path, content_type=content_type)


def uploaded_audio_path(filename: str) -> Path | None:
    if not re.fullmatch(r"[0-9A-Za-z._-]+", filename):
        return None
    path = (UPLOAD_ROOT / filename).resolve()
    root = UPLOAD_ROOT.resolve()
    if root not in path.parents:
        return None
    return path if path.is_file() else None


def create_postcall_job_from_upload(
    *,
    fields: dict[str, str],
    audio_url: str,
):
    request = _build_request(fields, audio_url)
    return postcall_job_service.create_job(request, CONSOLE_UPLOAD_CLIENT)


def _build_request(fields: dict[str, str], audio_url: str) -> CreatePostcallJobRequest:
    payload: dict[str, Any] = {
        "jjdh": fields.get("jjdh", ""),
        "audioUrl": "https://example.com/internal-upload.wav",
        "bjsj": fields.get("bjsj", ""),
        "JCJXTJSDWMC": fields.get("JCJXTJSDWMC", ""),
        "JJDWMC": fields.get("JJDWMC", ""),
        "GXDWMC": fields.get("GXDWMC", ""),
        "bjdh": fields.get("bjdh", ""),
        "bjrmc": fields.get("bjrmc", ""),
        "bjrxbdm": int(fields.get("bjrxbdm") or "0"),
        "lxdh": fields.get("lxdh", ""),
        "jqdz": fields.get("jqdz", ""),
        "bjnr": fields.get("bjnr", ""),
        "jqlbdm": fields.get("jqlbdm", ""),
        "jqlxdm": fields.get("jqlxdm", ""),
        "jqxldm": fields.get("jqxldm") or None,
        "jqzldm": fields.get("jqzldm") or None,
        "jqdj": fields.get("jqdj", ""),
        "callbackUrl": fields.get("callbackUrl") or None,
        "asrResult": None,
    }
    validated = CreatePostcallJobRequest.model_validate(payload)
    return validated.model_copy(update={"audioUrl": audio_url})
