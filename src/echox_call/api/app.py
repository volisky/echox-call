"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from echox_call.api.responses import api_success, register_exception_handlers
from echox_call.api.v1.router import router as api_v1_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="EchoX Call API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, object]:
        return api_success({"status": "ok"})

    return app


app = create_app()
