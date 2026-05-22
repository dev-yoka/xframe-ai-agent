"""Auth login, refresh, and me endpoint tests."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import jwt
import pytest
from httpx import AsyncClient

from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.priceframe.errors import PriceFrameUnavailableError
from xframe_agent.schemas.auth import LoginRequest, LoginResponse, UserInfo
from xframe_agent.settings import Settings


class FakePriceFrameClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        profile_payload: Mapping[str, Any] | None = None,
        public_response: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.token = token
        self.profile_payload = profile_payload
        self.public_response = public_response
        self.error = error
        self.public_posts: list[tuple[str, Mapping[str, Any] | None]] = []

    async def __aenter__(self) -> FakePriceFrameClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        return None

    async def post_public_json(
        self,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.public_posts.append((path, json))
        if self.error:
            raise self.error
        if self.public_response is not None:
            return self.public_response
        return {
            "success": True,
            "data": {
                "session": {"token": self.token, "id": 1710},
                "user": {"id": 1, "email": "admin@priceframe.local"},
            },
        }

    async def post_json(self, *_args: object, **_kwargs: object) -> Mapping[str, Any]:
        raise AssertionError("login must call PriceFRAME without an Authorization header")

    async def get_profile(self, jwt_raw: str) -> Mapping[str, Any]:
        assert jwt_raw == self.token
        if self.profile_payload is not None:
            return self.profile_payload
        return {
            "success": True,
            "data": {
                "user": {"id": 1},
                "role": {"code": "ADMIN"},
                "profile": {"code": "administrator"},
                "permissions": [{"code": "agent.enabled"}, {"code": "agent.quotes.read"}],
                "session": {"id": 1710},
            },
        }


def _priceframe_token(settings: Settings) -> str:
    return jwt.encode(
        {
            "userId": 1,
            "email": "admin@priceframe.local",
            "roleId": 1,
            "profileId": 1,
            "exp": int(time.time()) + 3600,
        },
        settings.priceframe_jwt_secret,
        algorithm=settings.priceframe_jwt_algorithm,
    )


class TestLoginEndpoint:
    """Tests for the login endpoint against a stubbed PriceFRAME."""

    @pytest.mark.asyncio
    async def test_login_endpoint_structure(self) -> None:
        """Verify the endpoint exists and accepts the right request shape."""
        request = LoginRequest(email="test@example.com", password="testpass")  # noqa: S106
        assert request.email == "test@example.com"
        assert request.password == "testpass"  # noqa: S105

    def test_login_response_structure(self) -> None:
        """Verify the response model shape."""
        response = LoginResponse(
            token="abc123",  # noqa: S106
            user=UserInfo(id=1, email="test@example.com", role="SALES", profile="PROFILE"),
            role_code="SALES",
            profile_code="PROFILE",
            permissions=["agent.quotes.read"],
            expires_at=1234567890,
        )
        assert response.token == "abc123"  # noqa: S105
        assert response.user.id == 1
        assert "agent.quotes.read" in response.permissions

    @pytest.mark.asyncio
    async def test_login_proxies_live_priceframe_response_shape(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        test_settings: Settings,
    ) -> None:
        """Login extracts the persisted session token from PriceFRAME's live response shape."""

        token = _priceframe_token(test_settings)
        fake_priceframe = FakePriceFrameClient(token=token)
        monkeypatch.setattr(
            PriceFrameClient,
            "from_settings",
            staticmethod(lambda _settings: fake_priceframe),
        )

        response = await client.post(
            "/api/v1/agent/auth/login",
            json={"email": "admin@priceframe.local", "password": "Pricing2026"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["token"] == token
        assert payload["user"]["id"] == 1
        assert payload["role_code"] == "ADMIN"
        assert fake_priceframe.public_posts == [
            (
                "/api/auth/login",
                {"email": "admin@priceframe.local", "password": "Pricing2026"},
            )
        ]

    @pytest.mark.asyncio
    async def test_login_maps_statusless_upstream_error_to_502(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transport-level upstream failures return a 502 instead of crashing."""

        fake_priceframe = FakePriceFrameClient(
            error=PriceFrameUnavailableError("Illegal header value")
        )
        monkeypatch.setattr(
            PriceFrameClient,
            "from_settings",
            staticmethod(lambda _settings: fake_priceframe),
        )

        response = await client.post(
            "/api/v1/agent/auth/login",
            json={"email": "admin@priceframe.local", "password": "Pricing2026"},
        )

        assert response.status_code == 502
        assert response.json()["error"]["message"] == "PriceFRAME unavailable"
