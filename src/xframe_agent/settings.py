"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1/agent"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")

    database_url: str = "postgresql+asyncpg://xframe:xframe@localhost:5432/xframe_agent"
    redis_url: str = "redis://localhost:6379/0"

    priceframe_base_url: str = "http://localhost:3333"
    priceframe_jwt_secret: str = Field(default="replace-me", repr=False)
    priceframe_service_secret: str = Field(default="replace-me", repr=False)
    priceframe_jwt_algorithm: str = "HS256"
    priceframe_profile_cache_ttl_seconds: int = 60
    priceframe_timeout_seconds: float = 10.0
    priceframe_max_retries: int = 2

    health_check_externals: bool = True
    prometheus_enabled: bool = True

    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["redis", "memory"] = "redis"
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    run_execution_mode: Literal["inline", "arq"] = "inline"
    arq_queue_name: str = "agent-runs"
    sse_redis_buffer_enabled: bool = True
    sse_heartbeat_seconds: int = 15
    sse_replay_event_limit: int = 2000
    idempotency_ttl_seconds: int = 7 * 24 * 60 * 60

    max_steps_per_run: int = 10
    max_wall_clock_per_run_s: int = 60
    max_input_tokens_per_run: int = 50_000
    max_output_tokens_per_run: int = 8_000
    max_tool_calls_per_run: int = 15
    max_parallel_tool_calls: int = 3
    cost_soft_per_run_usd: float = 0.15
    cost_hard_per_run_usd: float = 0.60

    langfuse_public_key: str | None = Field(default=None, repr=False)
    langfuse_secret_key: str | None = Field(default=None, repr=False)
    langfuse_host: str = "http://localhost:3001"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = Field(default="minioadmin", repr=False)
    s3_secret_access_key: str = Field(default="minioadmin", repr=False)
    s3_bucket: str = "xframe-agent-dev"
    attachment_storage_backend: Literal["s3", "local"] = "s3"
    attachment_local_storage_path: Path = Path(".data/attachments")
    attachment_max_bytes: int = 20 * 1024 * 1024
    attachment_scan_mode: Literal["inline", "arq"] = "inline"
    clamav_enabled: bool = False
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    groq_api_key: str | None = Field(default=None, repr=False)
    groq_whisper_model: str = "whisper-large-v3-turbo"

    allow_real_data: bool = False
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str = "us-central1"
    gemini_aistudio_api_key: str | None = Field(default=None, repr=False)
    anthropic_api_key: str | None = Field(default=None, repr=False)
    default_model: str = "gemini-2.5-flash"

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> tuple[str, ...] | object:
        if isinstance(value, str):
            return tuple(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @property
    def provider_configured(self) -> bool:
        return bool(
            self.gemini_developer_api_key or self.gemini_vertex_project or self.anthropic_api_key
        )

    @property
    def gemini_developer_api_key(self) -> str | None:
        return self.gemini_api_key or self.gemini_aistudio_api_key

    @property
    def langfuse_configured(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""

    return Settings()
