"""Application service for postcall audio analysis jobs."""

from __future__ import annotations

from echox_call.core.auth import ApiClient
from echox_call.features.audio_analysis.postcall.repository import PostcallJobRepository
from echox_call.features.audio_analysis.postcall.schemas import (
    CreatePostcallJobRequest,
    PostcallJobCreateResult,
    PostcallJobResultData,
)


class PostcallJobService:
    def __init__(self, repository: PostcallJobRepository | None = None) -> None:
        self._repository = repository or PostcallJobRepository()

    def create_job(
        self,
        request: CreatePostcallJobRequest,
        client: ApiClient,
    ) -> PostcallJobCreateResult:
        return self._repository.create_or_requeue(request, client)

    def get_job_result(
        self,
        job_id: str,
        client: ApiClient,
    ) -> PostcallJobResultData:
        return self._repository.get_result(job_id, client)


postcall_job_service = PostcallJobService()
