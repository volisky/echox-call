"""API v1 router composition."""

from __future__ import annotations

from fastapi import APIRouter

from echox_call.api.v1.postcall import router as postcall_router


router = APIRouter()
router.include_router(postcall_router, prefix="/postcall", tags=["postcall"])

