"""FastAPI auth dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from xframe_agent.auth.jwt import AuthContext, AuthTokenError, verify_priceframe_jwt
from xframe_agent.auth.priceframe_session import get_auth_context_from_profile
from xframe_agent.priceframe import PriceFrameAuthError, PriceFrameClient
from xframe_agent.settings import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_auth_context(
    request: Request,
    settings: SettingsDep,
) -> AuthContext:
    """Verify PriceFRAME JWT and introspect the backing PriceFRAME session."""

    token = _extract_bearer_token(request)
    try:
        claims = verify_priceframe_jwt(token, settings)
        async with PriceFrameClient.from_settings(settings) as client:
            return await get_auth_context_from_profile(
                jwt_raw=token,
                claims=claims,
                client=client,
                ttl_seconds=settings.priceframe_profile_cache_ttl_seconds,
            )
    except (AuthTokenError, PriceFrameAuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired PriceFRAME session",
        ) from exc


def require_permission(permission: str) -> Callable[[AuthContext], AuthContext]:
    """Create a dependency that requires a specific PriceFRAME permission code."""

    def dependency(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        if not context.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
        return context

    return dependency


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    token_from_query = request.query_params.get("token")

    raw = auth_header or (f"Bearer {token_from_query}" if token_from_query else None)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing PriceFRAME bearer token",
        )

    if " " in raw:
        scheme, token = raw.split(" ", 1)
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unsupported authorization scheme",
            )
        return token.strip()
    return raw.strip()
