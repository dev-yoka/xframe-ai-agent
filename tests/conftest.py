"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from xframe_agent.main import create_app
from xframe_agent.settings import Settings


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        priceframe_jwt_secret="test-secret-with-32-bytes-minimum",  # noqa: S106
        health_check_externals=False,
        prometheus_enabled=False,
        rate_limit_enabled=False,
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
