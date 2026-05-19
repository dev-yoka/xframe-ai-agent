"""Provider-agnostic streaming protocol and failover router."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field

from xframe_agent.models.agent import utc_now
from xframe_agent.tools.base import ToolDefinition


class ContentBlock(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: str
    content: list[ContentBlock]


class StreamEvent(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Provider(Protocol):
    name: str

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
    ) -> AsyncIterator[StreamEvent]: ...


class ProviderError(Exception):
    """Raised when a provider cannot serve a model call."""

    def __init__(self, message: str, *, failover: bool = True) -> None:
        super().__init__(message)
        self.failover = failover


@dataclass(slots=True)
class ProviderHealth:
    unhealthy_until_epoch: float = 0.0


@dataclass(slots=True)
class ProviderFailoverRouter:
    """Try providers in priority order and quarantine hard failures briefly."""

    providers: Sequence[Provider]
    unhealthy_seconds: int = 300
    _health: dict[str, ProviderHealth] = field(default_factory=dict)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition[Any, Any]],
        *,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[StreamEvent]:
        last_error: ProviderError | None = None
        for provider in self._healthy_providers():
            try:
                async with asyncio.timeout(timeout_seconds):
                    async for event in provider.stream(
                        messages,
                        tools,
                        model=model,
                        max_output_tokens=max_output_tokens,
                    ):
                        yield event
                return
            except ProviderError as exc:
                last_error = exc
                if not exc.failover:
                    raise
                self.mark_unhealthy(provider.name)
            except TimeoutError as exc:
                last_error = ProviderError(f"{provider.name} timed out: {exc}")
                self.mark_unhealthy(provider.name)

        raise last_error or ProviderError("No healthy provider is available")

    def mark_unhealthy(self, provider_name: str) -> None:
        now = utc_now()
        self._health[provider_name] = ProviderHealth(
            unhealthy_until_epoch=(now + timedelta(seconds=self.unhealthy_seconds)).timestamp()
        )

    def _healthy_providers(self) -> list[Provider]:
        now_epoch = utc_now().timestamp()
        return [
            provider
            for provider in self.providers
            if self._health.get(provider.name, ProviderHealth()).unhealthy_until_epoch <= now_epoch
        ]
