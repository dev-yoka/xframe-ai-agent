"""PriceFRAME JWT verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from xframe_agent.settings import Settings


class AuthTokenError(Exception):
    """Raised when a PriceFRAME JWT is invalid for this service."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Verified claims extracted from a PriceFRAME JWT."""

    user_id: int
    email: str | None
    role_id: int | None
    profile_id: int | None
    session_id: int | None
    expires_at: int | None


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Authenticated PriceFRAME user context for agent requests."""

    user_id: int
    role_code: str
    profile_code: str
    permissions: tuple[str, ...]
    jwt_raw: str
    session_id: int | None

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


def verify_priceframe_jwt(token: str, settings: Settings) -> TokenClaims:
    """Verify a PriceFRAME JWT and normalize its known claims."""

    try:
        payload = jwt.decode(
            token,
            settings.priceframe_jwt_secret,
            algorithms=[settings.priceframe_jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise AuthTokenError("Invalid PriceFRAME JWT") from exc

    if not isinstance(payload, dict):
        raise AuthTokenError("PriceFRAME JWT payload must be an object")

    user_id = _required_int(payload, "userId", "user_id", "sub")
    return TokenClaims(
        user_id=user_id,
        email=_optional_str(payload.get("email")),
        role_id=_optional_int(payload.get("roleId") or payload.get("role_id")),
        profile_id=_optional_int(payload.get("profileId") or payload.get("profile_id")),
        session_id=_optional_int(payload.get("sessionId") or payload.get("session_id")),
        expires_at=_optional_int(payload.get("exp")),
    )


def _required_int(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    joined = ", ".join(keys)
    raise AuthTokenError(f"PriceFRAME JWT missing integer claim: {joined}")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
