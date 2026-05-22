"""Standalone FastAPI application for the management console."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from echox_call.api.responses import api_success, register_exception_handlers
from echox_call.console.auth import (
    CONSOLE_SESSION_COOKIE,
    ConsoleAuthConfigError,
    get_console_session_user,
)
from echox_call.console.router import (
    get_console_static_directory,
    router as console_router,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Management Console",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    register_exception_handlers(app)

    @app.middleware("http")
    async def console_auth_middleware(request: Request, call_next):
        path = request.url.path
        if not _is_protected_console_path(path):
            return await call_next(request)

        try:
            user = get_console_session_user(request.cookies.get(CONSOLE_SESSION_COOKIE))
        except ConsoleAuthConfigError:
            user = None

        if user is not None:
            request.state.console_user = user
            return await call_next(request)

        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return RedirectResponse(
            url=f"/console/login?next={quote(next_path, safe='')}",
            status_code=303,
        )

    app.mount(
        "/console/static",
        StaticFiles(directory=str(get_console_static_directory())),
        name="console_static",
    )
    app.include_router(console_router, prefix="/console")

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/console/", status_code=307)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, object]:
        return api_success({"status": "ok", "service": "console"})

    return app


def _is_protected_console_path(path: str) -> bool:
    if not path.startswith("/console"):
        return False
    if path.startswith("/console/static"):
        return False
    if path.startswith("/console/uploads/"):
        return False
    if path in {"/console/login", "/console/logout"}:
        return False
    return True


app = create_app()
