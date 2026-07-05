"""Unified API response helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


DataT = TypeVar("DataT")
logger = logging.getLogger("uvicorn.error")


class ApiResponse(BaseModel, Generic[DataT]):
    code: int
    message: str
    data: DataT | dict[str, Any]
    timestamp: str


def response_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_api_response(
    *,
    code: int = 0,
    message: str = "success",
    data: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "data": jsonable_encoder(data) if data is not None else {},
        "timestamp": response_timestamp(),
    }
    return payload


def api_success(data: Any | None = None) -> dict[str, Any]:
    return build_api_response(data=data)


def api_error_response(
    *,
    status_code: int,
    message: str,
    data: Any | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=build_api_response(
            code=status_code,
            message=message,
            data=data,
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        logger.warning(
            "api request failed method=%s path=%s statusCode=%s detail=%s client=%s",
            request.method,
            request.url.path,
            exc.status_code,
            exc.detail,
            request.client.host if request.client else None,
        )
        return api_error_response(
            status_code=exc.status_code,
            message=str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        logger.warning(
            "api request validation failed method=%s path=%s statusCode=%s errors=%s client=%s",
            request.method,
            request.url.path,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            errors,
            request.client.host if request.client else None,
        )
        return api_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="request validation failed",
            data={"errors": errors},
        )
