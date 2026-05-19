"""Cached PriceFRAME profile introspection."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from xframe_agent.auth.jwt import AuthContext, AuthTokenError, TokenClaims


class ProfileClient(Protocol):
    """Client protocol for PriceFRAME profile introspection."""

    async def get_profile(self, jwt_raw: str) -> Mapping[str, Any]:
        """Return PriceFRAME's `/api/auth/profile` payload."""


@dataclass(slots=True)
class _CacheEntry:
    context: AuthContext
    expires_at: float


class ProfileIntrospectionCache:
    """Small TTL cache for PriceFRAME profile introspection results."""

    def __init__(self) -> None:
        self._by_token_hash: dict[str, _CacheEntry] = {}
        self._by_session: dict[tuple[int, int], _CacheEntry] = {}

    async def get_context(
        self,
        *,
        jwt_raw: str,
        claims: TokenClaims,
        client: ProfileClient,
        ttl_seconds: int,
    ) -> AuthContext:
        now = time.monotonic()
        token_hash = _hash_token(jwt_raw)

        token_entry = self._by_token_hash.get(token_hash)
        if token_entry and token_entry.expires_at > now:
            return token_entry.context

        if claims.session_id is not None:
            session_entry = self._by_session.get((claims.user_id, claims.session_id))
            if session_entry and session_entry.expires_at > now:
                self._by_token_hash[token_hash] = session_entry
                return session_entry.context

        payload = await client.get_profile(jwt_raw)
        context = auth_context_from_profile(payload, jwt_raw=jwt_raw)
        entry = _CacheEntry(context=context, expires_at=now + ttl_seconds)
        self._by_token_hash[token_hash] = entry
        if context.session_id is not None:
            self._by_session[(context.user_id, context.session_id)] = entry
        return context

    def clear(self) -> None:
        self._by_token_hash.clear()
        self._by_session.clear()


profile_cache = ProfileIntrospectionCache()


async def get_auth_context_from_profile(
    *,
    jwt_raw: str,
    claims: TokenClaims,
    client: ProfileClient,
    ttl_seconds: int,
) -> AuthContext:
    """Return an `AuthContext`, using a 60 second session cache by default."""

    return await profile_cache.get_context(
        jwt_raw=jwt_raw,
        claims=claims,
        client=client,
        ttl_seconds=ttl_seconds,
    )


def clear_profile_cache() -> None:
    """Clear profile cache; intended for tests."""

    profile_cache.clear()


def auth_context_from_profile(payload: Mapping[str, Any], *, jwt_raw: str) -> AuthContext:
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise AuthTokenError("PriceFRAME profile payload missing data object")

    user = _mapping(data.get("user"), "user")
    role = _mapping(data.get("role"), "role")
    profile = _mapping(data.get("profile"), "profile")
    session = _mapping(data.get("session"), "session")

    user_id = _int_field(user, "id")
    role_code = _str_field(role, "code")
    profile_code = _str_field(profile, "code")
    permissions = _permission_codes(data.get("permissions"))
    session_id = _optional_int(session.get("id"))

    return AuthContext(
        user_id=user_id,
        role_code=role_code,
        profile_code=profile_code,
        permissions=tuple(sorted(permissions)),
        jwt_raw=jwt_raw,
        session_id=session_id,
    )


def _hash_token(jwt_raw: str) -> str:
    return hashlib.sha256(jwt_raw.encode("utf-8")).hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuthTokenError(f"PriceFRAME profile payload missing {label} object")
    return value


def _int_field(payload: Mapping[str, Any], key: str) -> int:
    parsed = _optional_int(payload.get(key))
    if parsed is None:
        raise AuthTokenError(f"PriceFRAME profile field {key} must be an integer")
    return parsed


def _str_field(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AuthTokenError(f"PriceFRAME profile field {key} must be a string")
    return value


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _permission_codes(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return set()

    codes: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            code = item.get("code")
            if isinstance(code, str) and code:
                codes.add(code)
        elif isinstance(item, str) and item:
            codes.add(item)
    return codes
