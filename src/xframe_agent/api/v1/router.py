"""Versioned API router."""

from __future__ import annotations

from fastapi import APIRouter

from xframe_agent.api.v1 import health

router = APIRouter()
router.include_router(health.router)
