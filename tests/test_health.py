"""Health endpoint tests."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_endpoint_returns_200_with_external_checks_disabled(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/agent/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["database"]["status"] == "skipped"
    assert payload["checks"]["queue"]["status"] == "skipped"
    assert payload["checks"]["priceframe"]["status"] == "skipped"
    assert "provider" in payload["checks"]
