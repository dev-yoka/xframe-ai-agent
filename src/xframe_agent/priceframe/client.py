"""HTTP client for PriceFRAME REST APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Self, cast

import httpx

from xframe_agent.priceframe.errors import (
    PriceFrameAuthError,
    PriceFrameError,
    PriceFrameForbiddenError,
    PriceFrameNotFoundError,
    PriceFrameResponseError,
    PriceFrameUnavailableError,
)
from xframe_agent.settings import Settings


class PriceFrameClient:
    """Small typed wrapper around PriceFRAME's REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Accept": "application/json"},
        )
        self._max_retries = max_retries

    @classmethod
    def from_settings(cls, settings: Settings) -> PriceFrameClient:
        return cls(
            base_url=settings.priceframe_base_url,
            timeout_seconds=settings.priceframe_timeout_seconds,
            max_retries=settings.priceframe_max_retries,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_profile(self, jwt_raw: str) -> Mapping[str, Any]:
        """Return the current PriceFRAME auth profile for a user JWT."""

        response = await self._request(
            "GET",
            "/api/auth/profile",
            headers={"Authorization": f"Bearer {jwt_raw}"},
        )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise PriceFrameResponseError("PriceFRAME profile response was not a JSON object")
        return cast(Mapping[str, Any], payload)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: PriceFrameError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = PriceFrameUnavailableError(str(exc))
            else:
                if response.status_code < 500:
                    self._raise_for_status(response)
                    return response
                last_error = PriceFrameUnavailableError(
                    "PriceFRAME returned a transient server error",
                    status_code=response.status_code,
                )

            if attempt < self._max_retries:
                await asyncio.sleep(0.1 * (2**attempt))

        raise last_error or PriceFrameUnavailableError("PriceFRAME request failed")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        message = _response_message(response)
        if response.status_code == 401:
            raise PriceFrameAuthError(message, status_code=response.status_code)
        if response.status_code == 403:
            raise PriceFrameForbiddenError(message, status_code=response.status_code)
        if response.status_code == 404:
            raise PriceFrameNotFoundError(message, status_code=response.status_code)
        raise PriceFrameResponseError(message, status_code=response.status_code)


def _response_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"PriceFRAME returned HTTP {response.status_code}"
    if isinstance(payload, Mapping):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str):
            return message
    return f"PriceFRAME returned HTTP {response.status_code}"
