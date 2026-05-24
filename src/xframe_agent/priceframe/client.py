"""HTTP client for PriceFRAME REST APIs."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections.abc import Mapping
from hashlib import sha256
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
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Accept": "application/json"},
        )
        self._max_retries = max_retries
        self._default_headers = dict(default_headers or {})

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        default_headers: Mapping[str, str] | None = None,
    ) -> PriceFrameClient:
        return cls(
            base_url=settings.priceframe_base_url,
            timeout_seconds=settings.priceframe_timeout_seconds,
            max_retries=settings.priceframe_max_retries,
            default_headers=default_headers,
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

        payload = await self.get_json("/api/auth/profile", jwt_raw=jwt_raw)
        if not isinstance(payload, Mapping):
            raise PriceFrameResponseError("PriceFRAME profile response was not a JSON object")
        return cast(Mapping[str, Any], payload)

    async def get_json(
        self,
        path: str,
        *,
        jwt_raw: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """GET a JSON payload from PriceFRAME with JWT pass-through."""

        response = await self._request(
            "GET",
            path,
            headers={"Authorization": f"Bearer {jwt_raw}"},
            params=params,
        )
        return response.json()

    async def post_json(
        self,
        path: str,
        *,
        jwt_raw: str,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """POST a JSON payload to PriceFRAME with JWT pass-through."""

        request_headers = {**self._default_headers, "Authorization": f"Bearer {jwt_raw}"}
        if headers:
            request_headers.update(headers)
        response = await self._request(
            "POST",
            path,
            headers=request_headers,
            json=json,
        )
        payload = response.json()
        return payload

    async def post_public_json(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """POST a public JSON payload to PriceFRAME without bearer auth."""

        request_headers = {**self._default_headers}
        if headers:
            request_headers.update(headers)
        response = await self._request(
            "POST",
            path,
            headers=request_headers,
            json=json,
        )
        return response.json()

    async def put_json(
        self,
        path: str,
        *,
        jwt_raw: str,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """PUT a JSON payload to PriceFRAME with JWT pass-through."""

        request_headers = {**self._default_headers, "Authorization": f"Bearer {jwt_raw}"}
        if headers:
            request_headers.update(headers)
        response = await self._request(
            "PUT",
            path,
            headers=request_headers,
            json=json,
        )
        return response.json()

    async def post_agent_audit_callback(
        self,
        *,
        jwt_raw: str,
        service_secret: str,
        payload: Mapping[str, Any],
    ) -> int:
        """Post an HMAC-signed write audit callback to PriceFRAME."""

        timestamp = str(int(time.time() * 1000))
        signature_payload = json.dumps(dict(payload), separators=(",", ":"))
        signature = hmac.new(
            service_secret.encode("utf-8"),
            f"{timestamp}.{signature_payload}".encode(),
            sha256,
        ).hexdigest()
        response = await self.post_json(
            "/api/v1/agent-audit-callbacks",
            jwt_raw=jwt_raw,
            json=payload,
            headers={
                "X-Agent-Timestamp": timestamp,
                "X-Agent-Service-Signature": signature,
            },
        )
        if isinstance(response, Mapping):
            audit_log_id = response.get("audit_log_id")
            if isinstance(audit_log_id, int):
                return audit_log_id
        raise PriceFrameResponseError("PriceFRAME audit callback response missing audit_log_id")

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
                    _response_message(response),
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
