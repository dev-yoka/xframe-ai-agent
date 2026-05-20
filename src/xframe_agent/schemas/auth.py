"""Authentication request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserInfo(BaseModel):
    id: int
    email: str | None = None
    role: str | None = None
    profile: str | None = None


class LoginResponse(BaseModel):
    token: str
    user: UserInfo
    role_code: str
    profile_code: str
    permissions: list[str]
    expires_at: int | None = None


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RefreshResponse(BaseModel):
    token: str
    expires_at: int | None = None


class MeResponse(BaseModel):
    user_id: int
    email: str | None = None
    role_code: str
    profile_code: str
    permissions: list[str]
    session_id: int | None = None
