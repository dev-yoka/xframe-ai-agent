"""Voice transcription endpoint backed by Groq Whisper."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.schemas import VoiceTranscriptionResponse
from xframe_agent.settings import Settings, get_settings

router = APIRouter(tags=["voice"])

AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/voice/transcriptions", response_model=VoiceTranscriptionResponse)
async def transcribe_voice(
    auth: AuthDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> VoiceTranscriptionResponse:
    if not auth.has_permission("agent.enabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent access required")
    if not settings.groq_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice transcription is not configured",
        )

    data = await file.read()
    if len(data) > settings.attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file is too large",
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            data={"model": settings.groq_whisper_model},
            files={
                "file": (
                    file.filename or "voice.webm",
                    data,
                    file.content_type or "application/octet-stream",
                )
            },
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Groq transcription request failed",
        )
    payload: Any = response.json()
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Groq transcription response was malformed",
        )
    return VoiceTranscriptionResponse(text=text, model=settings.groq_whisper_model)
