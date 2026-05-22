"""Server-rendered management console routes."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

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
from echox_call.console.jobs import ConsoleJobRepository, JobListFilters
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


@router.get("/jobs", response_class=HTMLResponse)
def console_jobs(request: Request) -> HTMLResponse:
    return _render_jobs_page(
        request,
        JobListFilters(
            keyword=None,
            state=None,
            source_system=None,
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
            page=1,
            page_size=_parse_int(_form_value(form, "page_size"), default=20, minimum=10, maximum=100),
        )
    else:
        filters = JobListFilters(
            keyword=_clean_query_value(_form_value(form, "q"), max_length=120),
            state=_clean_query_value(_form_value(form, "state"), max_length=64),
            source_system=_clean_query_value(_form_value(form, "source_system"), max_length=128),
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


def _clean_query_value(value: str | None, *, max_length: int | None = None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if max_length is not None:
        stripped = stripped[:max_length]
    return stripped or None


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
