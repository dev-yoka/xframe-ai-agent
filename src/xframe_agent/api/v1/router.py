"""Versioned API router."""

from __future__ import annotations

from fastapi import APIRouter

from xframe_agent.api.v1 import attachments, auth, conversations, health, memory, runs, tools, voice

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(conversations.router)
router.include_router(runs.router)
router.include_router(tools.router)
router.include_router(attachments.router)
router.include_router(memory.router)
router.include_router(voice.router)
