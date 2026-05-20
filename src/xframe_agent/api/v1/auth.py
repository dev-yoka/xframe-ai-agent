"""Authentication endpoints: login, refresh, and profile."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from xframe_agent.auth.dependencies import get_auth_context
from xframe_agent.auth.jwt import AuthContext, AuthTokenError, verify_priceframe_jwt
from xframe_agent.auth.priceframe_session import get_auth_context_from_profile
from xframe_agent.priceframe import PriceFrameClient, PriceFrameError
from xframe_agent.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    UserInfo,
)
from xframe_agent.settings import Settings, get_settings

router = APIRouter(tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    settings: SettingsDep,
) -> LoginResponse:
    """Log in with email and password, returning a PriceFRAME JWT."""

    try:
        async with PriceFrameClient.from_settings(settings) as client:
            # Call PriceFRAME's login endpoint with basic auth-style payload
            login_response = await client.post_json(
                "/api/auth/login",
                jwt_raw="",  # No JWT needed for login
                json={"email": request.email, "password": request.password},
            )
    except PriceFrameError as exc:
        # Surface PriceFRAME errors as-is
        status_code = getattr(exc, "status_code", status.HTTP_503_SERVICE_UNAVAILABLE)
        if status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            ) from exc
        if status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="PriceFRAME unavailable",
            ) from exc
        raise HTTPException(
            status_code=status_code,
            detail=str(exc),
        ) from exc

    # Extract token from response
    if not isinstance(login_response, dict) or "token" not in login_response:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid login response from PriceFRAME",
        )

    token = str(login_response["token"])

    # Verify the token
    try:
        claims = verify_priceframe_jwt(token, settings)
    except AuthTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to verify PriceFRAME token",
        ) from exc

    # Fetch profile + permissions
    try:
        async with PriceFrameClient.from_settings(settings) as client:
            context = await get_auth_context_from_profile(
                jwt_raw=token,
                claims=claims,
                client=client,
                ttl_seconds=settings.priceframe_profile_cache_ttl_seconds,
            )
    except PriceFrameError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch PriceFRAME profile",
        ) from exc

    return LoginResponse(
        token=token,
        user=UserInfo(
            id=context.user_id,
            email=claims.email,
            role=context.role_code,
            profile=context.profile_code,
        ),
        role_code=context.role_code,
        profile_code=context.profile_code,
        permissions=list(context.permissions),
        expires_at=claims.expires_at,
    )


@router.post("/auth/refresh", response_model=RefreshResponse)
async def refresh(
    request: RefreshRequest,
    settings: SettingsDep,
) -> RefreshResponse:
    """Refresh a PriceFRAME session, returning a new access token."""

    try:
        async with PriceFrameClient.from_settings(settings) as client:
            refresh_response = await client.post_json(
                "/api/auth/refresh",
                jwt_raw=request.refresh_token,
                json={},
            )
    except PriceFrameError as exc:
        status_code = getattr(exc, "status_code", status.HTTP_503_SERVICE_UNAVAILABLE)
        if status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            ) from exc
        if status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="PriceFRAME unavailable",
            ) from exc
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    if not isinstance(refresh_response, dict) or "token" not in refresh_response:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid refresh response from PriceFRAME",
        )

    token = str(refresh_response["token"])
    expires_at = refresh_response.get("expires_at")
    if isinstance(expires_at, int):
        expires_at = expires_at
    else:
        expires_at = None

    return RefreshResponse(token=token, expires_at=expires_at)


@router.get("/auth/me", response_model=MeResponse)
async def me(auth: AuthDep) -> MeResponse:
    """Return the authenticated user's profile and permissions."""

    return MeResponse(
        user_id=auth.user_id,
        email=None,  # Not cached in AuthContext today; would need to add if needed
        role_code=auth.role_code,
        profile_code=auth.profile_code,
        permissions=list(auth.permissions),
        session_id=auth.session_id,
    )
