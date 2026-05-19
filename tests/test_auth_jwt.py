"""PriceFRAME JWT and profile introspection tests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from xframe_agent.auth.jwt import AuthTokenError, TokenClaims, verify_priceframe_jwt
from xframe_agent.auth.priceframe_session import clear_profile_cache, get_auth_context_from_profile
from xframe_agent.settings import Settings


def _make_token(settings: Settings, **overrides: object) -> str:
    payload: dict[str, object] = {
        "userId": 7,
        "email": "rep@example.com",
        "roleId": 9,
        "profileId": 7,
        "exp": datetime.now(UTC) + timedelta(minutes=10),
    }
    payload.update(overrides)
    return jwt.encode(
        payload,
        settings.priceframe_jwt_secret,
        algorithm=settings.priceframe_jwt_algorithm,
    )


def test_verify_priceframe_jwt_accepts_valid_priceframe_style_token(
    test_settings: Settings,
) -> None:
    token = _make_token(test_settings)

    claims = verify_priceframe_jwt(token, test_settings)

    assert claims == TokenClaims(
        user_id=7,
        email="rep@example.com",
        role_id=9,
        profile_id=7,
        session_id=None,
        expires_at=claims.expires_at,
    )
    assert claims.expires_at is not None


def test_verify_priceframe_jwt_rejects_invalid_token(test_settings: Settings) -> None:
    with pytest.raises(AuthTokenError):
        verify_priceframe_jwt("not-a-jwt", test_settings)


class FakeProfileClient:
    """Minimal fake for cached profile introspection tests."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_profile(self, jwt_raw: str) -> Mapping[str, Any]:
        self.calls += 1
        assert jwt_raw
        return {
            "success": True,
            "data": {
                "user": {"id": 7},
                "role": {"code": "ROLE_AM_SALES"},
                "profile": {"code": "PROFILE_SALES"},
                "permissions": [
                    {"code": "agent.enabled"},
                    {"code": "agent.quotes.read"},
                ],
                "session": {"id": 42},
            },
        }


async def test_profile_introspection_cache_reuses_context_for_same_token(
    test_settings: Settings,
) -> None:
    clear_profile_cache()
    token = _make_token(test_settings)
    claims = verify_priceframe_jwt(token, test_settings)
    client = FakeProfileClient()

    first = await get_auth_context_from_profile(
        jwt_raw=token,
        claims=claims,
        client=client,
        ttl_seconds=60,
    )
    second = await get_auth_context_from_profile(
        jwt_raw=token,
        claims=claims,
        client=client,
        ttl_seconds=60,
    )

    assert client.calls == 1
    assert first == second
    assert first.user_id == 7
    assert first.role_code == "ROLE_AM_SALES"
    assert first.profile_code == "PROFILE_SALES"
    assert first.session_id == 42
    assert first.permissions == ("agent.enabled", "agent.quotes.read")
