"""Versioned API router."""

from __future__ import annotations

from fastapi import APIRouter

from xframe_agent.api.v1 import conversations, health, runs, tools

router = APIRouter()
router.include_router(health.router)
router.include_router(conversations.router)
router.include_router(runs.router)
router.include_router(tools.router)
