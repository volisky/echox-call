"""Server-rendered management console routes."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.templating import Jinja2Templates

from echox_call.console.auth import (
    CONSOLE_SESSION_COOKIE,
    ConsoleAuthenticationError,
    ConsoleAuthConfigError,
    authenticate_console_user,
    create_console_session_cookie,
    get_console_session_user,
    load_console_auth_config,
)
from echox_call.console.annotations import (
    EMOTION_LABELS,
    EmotionAnnotationError,
    EmotionAnnotationRepository,
    get_server_audio_root,
    parse_annotation_submission,
    save_annotation_upload,
    scan_server_audio_files,
    server_audio_file_from_relative_path,
)
from echox_call.console.jobs import ConsoleJobRepository, JobListFilters, LEVEL_FILTER_OPTIONS
from echox_call.console.upload import (
    ConsoleUploadError,
    create_postcall_job_from_upload,
    default_upload_form,
    parse_multipart_form,
    save_uploaded_audio,
    uploaded_audio_path,
)
from echox_call.core.db import DatabaseConnectionError
from echox_call.core.settings import DatabaseConfigError
from echox_call.features.audio_analysis.postcall.repository import PostcallJobRepositoryError


CONSOLE_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(CONSOLE_ROOT / "templates"))
router = APIRouter(include_in_schema=False)
job_repository = ConsoleJobRepository()
annotation_repository = EmotionAnnotationRepository()


@dataclass(frozen=True)
class ConsoleNavItem:
    key: str
    label: str
    href: str
    description: str


NAV_ITEMS = (
    ConsoleNavItem("overview", "首页", "/console/", "运行概览"),
    ConsoleNavItem("jobs", "分析任务", "/console/jobs", "任务管理"),
    ConsoleNavItem("upload", "音频测试", "/console/upload", "上传并提交任务"),
    ConsoleNavItem("annotations", "情绪标注", "/console/annotations", "微调数据集"),
)


def get_console_static_directory() -> Path:
    return CONSOLE_ROOT / "static" / "console"


def render_console_page(
    request: Request,
    template_name: str,
    *,
    active_nav: str,
    page_title: str,
    page_description: str,
    status_code: int = 200,
    context: dict[str, Any] | None = None,
) -> HTMLResponse:
    console_user = getattr(getattr(request, "state", None), "console_user", None)
    page_context: dict[str, Any] = {
        "request": request,
        "active_nav": active_nav,
        "nav_items": NAV_ITEMS,
        "page_title": page_title,
        "page_description": page_description,
        "console_user_name": console_user.name if console_user else "管理员",
    }
    if context:
        page_context.update(context)
    return templates.TemplateResponse(template_name, page_context, status_code=status_code)


@router.get("/login", response_class=HTMLResponse, response_model=None)
def console_login(request: Request) -> Response:
    next_path = _safe_next_path(request.query_params.get("next"))
    try:
        auth_config = load_console_auth_config()
        user = get_console_session_user(request.cookies.get(CONSOLE_SESSION_COOKIE), auth_config)
    except ConsoleAuthConfigError as exc:
        return templates.TemplateResponse(
            "console/login.html",
            {
                "request": request,
                "page_title": "控制台登录",
                "next_path": next_path,
                "username": "",
                "error_message": str(exc),
            },
            status_code=500,
        )

    if user is not None:
        return RedirectResponse(url=next_path, status_code=303)

    return templates.TemplateResponse(
        "console/login.html",
        {
            "request": request,
            "page_title": "控制台登录",
            "next_path": next_path,
            "username": "",
            "error_message": "",
        },
    )


@router.post("/login", response_class=HTMLResponse, response_model=None)
async def console_login_post(request: Request) -> Response:
    form = _parse_urlencoded_form(await request.body())
    username = _form_value(form, "username") or ""
    password = _form_value(form, "password") or ""
    next_path = _safe_next_path(_form_value(form, "next"))

    try:
        auth_config = load_console_auth_config()
        user = authenticate_console_user(username, password, auth_config)
        cookie_value = create_console_session_cookie(user, auth_config)
    except (ConsoleAuthConfigError, ConsoleAuthenticationError) as exc:
        return templates.TemplateResponse(
            "console/login.html",
            {
                "request": request,
                "page_title": "控制台登录",
                "next_path": next_path,
                "username": username,
                "error_message": str(exc),
            },
            status_code=400,
        )

    response = RedirectResponse(url=next_path, status_code=303)
    response.set_cookie(
        CONSOLE_SESSION_COOKIE,
        cookie_value,
        max_age=auth_config.max_age_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/logout", include_in_schema=False)
def console_logout() -> RedirectResponse:
    response = RedirectResponse(url="/console/login", status_code=303)
    response.delete_cookie(CONSOLE_SESSION_COOKIE)
    return response


@router.get("", include_in_schema=False)
def console_redirect() -> RedirectResponse:
    return RedirectResponse(url="/console/", status_code=307)


@router.get("/", response_class=HTMLResponse)
def console_home(request: Request) -> HTMLResponse:
    error_message = ""
    try:
        summary = job_repository.get_summary()
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        summary = _empty_job_summary()
        error_message = str(exc)

    return render_console_page(
        request,
        "console/index.html",
        active_nav="overview",
        page_title="运行概览",
        page_description="任务运行实时统计。",
        context={
            "summary": summary,
            "error_message": error_message,
        },
    )


@router.get("/upload", response_class=HTMLResponse)
def console_upload(request: Request) -> HTMLResponse:
    return render_console_page(
        request,
        "console/upload.html",
        active_nav="upload",
        page_title="音频测试",
        page_description="上传本地音频并提交一条真实分析任务。",
        context={
            "form": default_upload_form(),
            "error_message": "",
            "result": None,
        },
    )


@router.post("/upload", response_class=HTMLResponse)
async def console_upload_post(request: Request) -> HTMLResponse:
    form = default_upload_form()
    result = None
    error_message = ""

    try:
        fields, uploaded_file = parse_multipart_form(
            await request.body(),
            request.headers.get("content-type"),
        )
        form.update(fields)
        stored_upload = save_uploaded_audio(uploaded_file)
        audio_url = str(request.url_for("console_uploaded_audio", filename=stored_upload.filename))
        create_result = create_postcall_job_from_upload(fields=form, audio_url=audio_url)
        result = {
            "job_id": create_result.job_id,
            "jjdh": create_result.jjdh,
            "state": create_result.state,
            "duplicate": create_result.duplicate,
            "duplicate_count": create_result.duplicate_count,
            "audio_url": audio_url,
        }
        form = default_upload_form()
    except (
        ConsoleUploadError,
        PostcallJobRepositoryError,
        DatabaseConfigError,
        DatabaseConnectionError,
        ValidationError,
        ValueError,
    ) as exc:
        error_message = str(exc)

    return render_console_page(
        request,
        "console/upload.html",
        active_nav="upload",
        page_title="音频测试",
        page_description="上传本地音频并提交一条真实分析任务。",
        status_code=400 if error_message else 200,
        context={
            "form": form,
            "error_message": error_message,
            "result": result,
        },
    )


@router.api_route("/uploads/{filename}", methods=["GET", "HEAD"], name="console_uploaded_audio", response_model=None)
def console_uploaded_audio(filename: str):
    path = uploaded_audio_path(filename)
    if path is None:
        return PlainTextResponse("上传音频不存在。", status_code=404)
    return FileResponse(
        path=path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/annotations", response_class=HTMLResponse)
def console_annotations(request: Request) -> HTMLResponse:
    error_message = ""
    try:
        summary = annotation_repository.get_summary()
        audio_files = annotation_repository.list_audio_files()
        server_root = get_server_audio_root()
        server_audio_files = scan_server_audio_files(server_root)
        _add_server_annotation_statuses(server_audio_files)
    except (DatabaseConfigError, DatabaseConnectionError, EmotionAnnotationError) as exc:
        summary = {
            "audio_count": 0,
            "annotation_count": 0,
            "unannotated_count": 0,
            "annotator_count": 0,
        }
        audio_files = []
        server_root = get_server_audio_root()
        server_audio_files = []
        error_message = str(exc)

    return render_console_page(
        request,
        "console/annotations.html",
        active_nav="annotations",
        page_title="情绪微调标注台",
        page_description="把人声按稳定情绪切片标注；保留每位标注者的每一次提交，用于后续复核和微调。",
        context={
            "summary": summary,
            "audio_files": audio_files,
            "server_audio_files": server_audio_files,
            "server_root": str(server_root),
            "server_audio_truncated": len(server_audio_files) >= 2000,
            "error_message": error_message,
            "import_message": request.query_params.get("message", ""),
        },
    )


@router.post("/annotations/upload", response_class=HTMLResponse)
async def console_annotation_upload(request: Request) -> Response:
    try:
        _, uploaded_file = parse_multipart_form(
            await request.body(),
            request.headers.get("content-type"),
        )
        uploaded_audio = save_annotation_upload(uploaded_file)
        user = request.state.console_user
        audio, created = annotation_repository.add_audio_file(uploaded_audio, imported_by=user.username)
        if created:
            return RedirectResponse(url=f"/console/annotations/{audio.id}?uploaded=1", status_code=303)
        return RedirectResponse(
            url=f"/console/annotations/{audio.id}?message=该文件已在标注素材库中，已直接打开。",
            status_code=303,
        )
    except (
        ConsoleUploadError,
        DatabaseConfigError,
        DatabaseConnectionError,
        EmotionAnnotationError,
    ) as exc:
        return _render_annotations_error(request, str(exc), status_code=400)


@router.post("/annotations/import-server", response_class=HTMLResponse)
async def console_annotation_import_server(request: Request) -> Response:
    form = _parse_urlencoded_form(await request.body())
    selected_paths = form.get("server_path", [])
    if not selected_paths:
        return _render_annotations_error(request, "请先勾选至少一个服务器音频文件。", status_code=400)

    try:
        user = request.state.console_user
        source_audios = [
            server_audio_file_from_relative_path(relative_path)
            for relative_path in selected_paths[:2000]
        ]
        imported_count = 0
        existing_count = 0
        for source_audio in source_audios:
            _, created = annotation_repository.add_audio_file(source_audio, imported_by=user.username)
            if created:
                imported_count += 1
            else:
                existing_count += 1
    except (DatabaseConfigError, DatabaseConnectionError, EmotionAnnotationError) as exc:
        return _render_annotations_error(request, str(exc), status_code=400)

    message = f"已加入 {imported_count} 个服务器音频"
    if existing_count:
        message += f"；{existing_count} 个已存在，未重复创建"
    return RedirectResponse(url=f"/console/annotations?message={quote(message, safe='')}", status_code=303)


@router.get("/annotations/export.jsonl", response_model=None)
def console_annotation_export() -> Response:
    try:
        content = annotation_repository.export_jsonl()
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        return PlainTextResponse(str(exc), status_code=500)
    return PlainTextResponse(
        content,
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="wavlm_emotion_annotations.jsonl"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/annotations/export-soft-labels.jsonl", response_model=None)
def console_annotation_soft_label_export() -> Response:
    try:
        content = annotation_repository.export_soft_label_jsonl()
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        return PlainTextResponse(str(exc), status_code=500)
    return PlainTextResponse(
        content,
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="wavlm_emotion_soft_labels.jsonl"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/annotations/{audio_id}", response_class=HTMLResponse)
def console_annotation_editor(request: Request, audio_id: str) -> HTMLResponse:
    return _render_annotation_editor(request, audio_id)


@router.post("/annotations/{audio_id}", response_class=HTMLResponse)
async def console_annotation_editor_post(request: Request, audio_id: str) -> Response:
    form = _parse_urlencoded_form(await request.body())
    try:
        submission = parse_annotation_submission(form)
        duration_raw = _form_value(form, "duration_sec")
        duration_sec = float(duration_raw) if duration_raw else None
        if duration_sec is not None and (
            not isfinite(duration_sec) or duration_sec < 0 or duration_sec > 24 * 60 * 60
        ):
            raise EmotionAnnotationError("音频时长不在允许范围内。")
        user = request.state.console_user
        annotation_repository.save_annotation(
            audio_file_id=audio_id,
            annotator_username=user.username,
            annotator_name=user.name,
            submission=submission,
            duration_sec=duration_sec,
        )
    except ValueError:
        return _render_annotation_editor(
            request,
            audio_id,
            error_message="浏览器未能读取有效的音频时长，请重新加载音频后再保存。",
            status_code=400,
        )
    except (DatabaseConfigError, DatabaseConnectionError, EmotionAnnotationError) as exc:
        return _render_annotation_editor(request, audio_id, error_message=str(exc), status_code=400)
    return RedirectResponse(url=f"/console/annotations/{audio_id}?saved=1", status_code=303)


@router.api_route("/annotations/{audio_id}/audio", methods=["GET", "HEAD"], response_model=None)
def console_annotation_audio(audio_id: str) -> Response:
    try:
        audio = annotation_repository.get_audio_file(audio_id)
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        return PlainTextResponse(str(exc), status_code=500)
    if audio is None or not audio.stored_path.is_file():
        return PlainTextResponse("标注音频不存在或已从服务器移除。", status_code=404)
    return FileResponse(
        path=audio.stored_path,
        media_type=audio.content_type or "audio/*",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/jobs", response_class=HTMLResponse)
def console_jobs(request: Request) -> HTMLResponse:
    return _render_jobs_page(
        request,
        JobListFilters(
            keyword=None,
            state=None,
            source_system=None,
            summary_level=None,
            voice_level=None,
            page=1,
            page_size=20,
        ),
    )


@router.post("/jobs", response_class=HTMLResponse)
async def console_jobs_post(request: Request) -> HTMLResponse:
    form = _parse_urlencoded_form(await request.body())
    if _form_value(form, "clear"):
        filters = JobListFilters(
            keyword=None,
            state=None,
            source_system=None,
            summary_level=None,
            voice_level=None,
            page=1,
            page_size=_parse_int(_form_value(form, "page_size"), default=20, minimum=10, maximum=100),
        )
    else:
        filters = JobListFilters(
            keyword=_clean_query_value(_form_value(form, "q"), max_length=120),
            state=_clean_query_value(_form_value(form, "state"), max_length=64),
            source_system=_clean_query_value(_form_value(form, "source_system"), max_length=128),
            summary_level=_parse_level_filter(_form_value(form, "summary_level")),
            voice_level=_parse_level_filter(_form_value(form, "voice_level")),
            page=_parse_int(_form_value(form, "page"), default=1, minimum=1, maximum=100000),
            page_size=_parse_int(_form_value(form, "page_size"), default=20, minimum=10, maximum=100),
        )

    return _render_jobs_page(request, filters)


def _render_jobs_page(request: Request, filters: JobListFilters) -> HTMLResponse:
    error_message = ""
    result = None
    try:
        result = job_repository.list_jobs(filters)
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        error_message = str(exc)

    page_number = result.page if result else filters.page
    total_pages = result.total_pages if result else 1
    page_start = result.rows[0]["sequence"] if result and result.rows else 0
    page_end = result.rows[-1]["sequence"] if result and result.rows else 0
    page_size = result.page_size if result else filters.page_size
    return render_console_page(
        request,
        "console/jobs.html",
        active_nav="jobs",
        page_title="分析任务",
        page_description="查看真实 postcall_jobs 任务、状态、来源系统和错误摘要。",
        context={
            "rows": result.rows if result else [],
            "summary": result.summary if result else _empty_job_summary(),
            "total": result.total if result else 0,
            "page": page_number,
            "page_size": page_size,
            "total_pages": total_pages,
            "page_start": page_start,
            "page_end": page_end,
            "prev_page": page_number - 1 if page_number > 1 else 0,
            "next_page": page_number + 1 if result and page_number < total_pages else 0,
            "pagination_items": _pagination_items(page_number, total_pages),
            "state_options": result.state_options if result else [],
            "source_system_options": result.source_system_options if result else [],
            "level_options": LEVEL_FILTER_OPTIONS,
            "filters": filters,
            "error_message": error_message,
        },
    )


@router.get("/jobs/{job_id}/drawer", response_class=HTMLResponse)
def console_job_detail_drawer(request: Request, job_id: str) -> HTMLResponse:
    detail, not_found, error_message, status_code = _load_job_detail(job_id)
    return templates.TemplateResponse(
        "console/job_detail_drawer.html",
        {
            "request": request,
            "detail": detail,
            "job_id": job_id,
            "not_found": not_found,
            "error_message": error_message,
        },
        status_code=status_code,
    )


@router.api_route("/jobs/{job_id}/audio", methods=["GET", "HEAD"], response_model=None)
def console_job_audio(job_id: str):
    try:
        audio_asset = job_repository.get_job_audio_asset(job_id)
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        return PlainTextResponse(str(exc), status_code=500)

    if audio_asset is None:
        return PlainTextResponse(
            "未找到该任务已下载到本地的音频文件。",
            status_code=404,
        )

    return FileResponse(
        path=audio_asset.path,
        media_type=audio_asset.content_type or "audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def console_job_detail(request: Request, job_id: str) -> HTMLResponse:
    detail, not_found, error_message, status_code = _load_job_detail(job_id)
    return render_console_page(
        request,
        "console/job_detail.html",
        active_nav="jobs",
        page_title="任务详情",
        page_description="查看单条任务的接警信息和模型输出时间线。",
        status_code=status_code,
        context={
            "detail": detail,
            "job_id": job_id,
            "not_found": not_found,
            "error_message": error_message,
        },
    )


def _load_job_detail(job_id: str) -> tuple[Any, bool, str, int]:
    error_message = ""
    not_found = False
    status_code = 200
    detail = None

    try:
        detail = job_repository.get_job_detail(job_id)
        if detail is None:
            not_found = True
            status_code = 404
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        error_message = str(exc)
        status_code = 500

    return detail, not_found, error_message, status_code


def _render_annotations_error(request: Request, error_message: str, *, status_code: int) -> HTMLResponse:
    """Render the library again so an import/upload error does not strand the user."""
    try:
        summary = annotation_repository.get_summary()
        audio_files = annotation_repository.list_audio_files()
        server_root = get_server_audio_root()
        server_audio_files = scan_server_audio_files(server_root)
        _add_server_annotation_statuses(server_audio_files)
    except (DatabaseConfigError, DatabaseConnectionError, EmotionAnnotationError):
        summary = {
            "audio_count": 0,
            "annotation_count": 0,
            "unannotated_count": 0,
            "annotator_count": 0,
        }
        audio_files = []
        server_root = get_server_audio_root()
        server_audio_files = []
    return render_console_page(
        request,
        "console/annotations.html",
        active_nav="annotations",
        page_title="情绪微调标注台",
        page_description="把人声按稳定情绪切片标注；保留每位标注者的每一次提交，用于后续复核和微调。",
        status_code=status_code,
        context={
            "summary": summary,
            "audio_files": audio_files,
            "server_audio_files": server_audio_files,
            "server_root": str(server_root),
            "server_audio_truncated": len(server_audio_files) >= 2000,
            "error_message": error_message,
            "import_message": "",
        },
    )


def _render_annotation_editor(
    request: Request,
    audio_id: str,
    *,
    error_message: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    try:
        audio = annotation_repository.get_audio_file(audio_id)
        annotation_count = annotation_repository.get_annotation_count(audio_id) if audio else 0
    except (DatabaseConfigError, DatabaseConnectionError) as exc:
        audio = None
        annotation_count = 0
        error_message = error_message or str(exc)
        status_code = 500
    if audio is None:
        return render_console_page(
            request,
            "console/annotation_editor.html",
            active_nav="annotations",
            page_title="标注音频不存在",
            page_description="该音频可能尚未导入，或已被移除。",
            status_code=404,
            context={
                "audio": None,
                "annotation_count": 0,
                "emotion_labels": EMOTION_LABELS,
                "error_message": error_message or "未找到指定的标注音频。",
                "result_message": "",
            },
        )
    result_message = ""
    if request.query_params.get("saved") == "1":
        result_message = "本次标注已保存为一条记录；页面已开始一份新的空白标注。"
    elif request.query_params.get("uploaded") == "1":
        result_message = "音频已加入素材库。请先播放并切分人声片段，再提交第一份标注。"
    elif request.query_params.get("message"):
        result_message = request.query_params["message"]
    return render_console_page(
        request,
        "console/annotation_editor.html",
        active_nav="annotations",
        page_title="音频情绪标注",
        page_description="人工标注使用单一主情绪硬标签；训练时按重叠区间和把握程度聚合为软标签。",
        status_code=status_code,
        context={
            "audio": audio,
            "audio_url": str(request.url_for("console_annotation_audio", audio_id=audio.id)),
            "annotation_count": annotation_count,
            "emotion_labels": EMOTION_LABELS,
            "error_message": error_message,
            "result_message": result_message,
        },
    )


def _add_server_annotation_statuses(server_audio_files: list[dict[str, Any]]) -> None:
    statuses = annotation_repository.get_server_audio_statuses()
    for audio in server_audio_files:
        status = statuses.get(audio["relative_path"])
        if status is None:
            audio.update(
                {
                    "is_imported": False,
                    "annotation_count": 0,
                    "annotator_count": 0,
                }
            )
            continue
        audio.update(status)


def _clean_query_value(value: str | None, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if max_length is not None:
        stripped = stripped[:max_length]
    return stripped or None


def _parse_level_filter(value: str | None) -> int | None:
    level = _parse_int(value, default=0, minimum=0, maximum=3)
    return level if level in {1, 2, 3} else None


def _parse_urlencoded_form(body: bytes) -> dict[str, list[str]]:
    if not body:
        return {}
    return parse_qs(body.decode("utf-8"), keep_blank_values=True)


def _form_value(form: dict[str, list[str]], key: str) -> str | None:
    values = form.get(key)
    if not values:
        return None
    return values[-1]


def _safe_next_path(value: str | None) -> str:
    if not value:
        return "/console/"
    if not value.startswith("/console"):
        return "/console/"
    if value.startswith("/console/login"):
        return "/console/"
    if value.startswith("/console/static"):
        return "/console/"
    return value


def _parse_int(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


def _pagination_items(page: int, total_pages: int) -> list[dict[str, Any]]:
    if total_pages <= 1:
        return [
            {
                "label": "1",
                "page": 1,
                "current": True,
                "ellipsis": False,
            }
        ]

    pages = {1, total_pages}
    for number in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        pages.add(number)

    items: list[dict[str, Any]] = []
    previous_number = 0
    for number in sorted(pages):
        if previous_number and number - previous_number > 1:
            items.append(
                {
                    "label": "...",
                    "page": 0,
                    "current": False,
                    "ellipsis": True,
                }
            )
        items.append(
            {
                "label": str(number),
                "page": number,
                "current": number == page,
                "ellipsis": False,
            }
        )
        previous_number = number

    return items


def _empty_job_summary() -> dict[str, int]:
    return {
        "total": 0,
        "pending": 0,
        "active": 0,
        "completed": 0,
        "failed": 0,
        "today_completed": 0,
    }
