"""Langfuse client factory."""

from __future__ import annotations

from typing import Any

from xframe_agent.settings import Settings


def get_langfuse_client(settings: Settings) -> Any | None:
    """Return a Langfuse client when credentials are configured."""

    if not settings.langfuse_configured:
        return None

    from langfuse import Langfuse

    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
