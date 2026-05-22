"""Postcall audio analysis job API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

from echox_call.api.dependencies import get_api_client
from echox_call.api.responses import ApiResponse, api_success
from echox_call.core.auth import ApiClient
from echox_call.core.db import DatabaseConnectionError
from echox_call.features.audio_analysis.postcall.repository import (
    PostcallJobNotFoundError,
    PostcallJobRepositoryError,
    PostcallResultContractError,
)
from echox_call.features.audio_analysis.postcall.schemas import (
    CreatePostcallJobData,
    CreatePostcallJobRequest,
    PostcallJobResultData,
)
from echox_call.features.audio_analysis.postcall.service import postcall_job_service


router = APIRouter()


@router.post(
    "/jobs",
    response_model=ApiResponse[CreatePostcallJobData],
    status_code=status.HTTP_201_CREATED,
)
def create_postcall_job(
    request: CreatePostcallJobRequest,
    response: Response,
    client: ApiClient = Depends(get_api_client),
) -> dict[str, object]:
    try:
        result = postcall_job_service.create_job(request, client)
    except PostcallJobRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except DatabaseConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    data = CreatePostcallJobData(
        jobId=result.job_id,
        jjdh=result.jjdh,
        state=result.state,
        duplicate=result.duplicate,
    )

    return api_success(data)


@router.get(
    "/jobs/{job_id}",
    response_model=ApiResponse[PostcallJobResultData],
    response_model_exclude_none=True,
)
def get_postcall_job_result(
    job_id: str,
    client: ApiClient = Depends(get_api_client),
) -> dict[str, object]:
    try:
        data = postcall_job_service.get_job_result(job_id, client)
    except PostcallJobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except PostcallResultContractError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except PostcallJobRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    except DatabaseConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return api_success(data)
