"""FastAPI dependencies shared by API routes."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from echox_call.core.auth import (
    ApiAuthenticationError,
    ApiClient,
    ApiClientDisabledError,
    ClientConfigError,
    authenticate_api_key,
)


def get_api_client(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> ApiClient:
    try:
        return authenticate_api_key(x_api_key)
    except ApiClientDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ApiAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except ClientConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"API client config error: {exc}",
        ) from exc

