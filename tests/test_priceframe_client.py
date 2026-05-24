"""PriceFRAME HTTP client error handling tests."""

from __future__ import annotations

import httpx
import pytest

from xframe_agent.priceframe.client import PriceFrameClient
from xframe_agent.priceframe.errors import PriceFrameUnavailableError


@pytest.mark.asyncio
async def test_priceframe_client_preserves_5xx_error_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "database is unavailable"})

    http_client = httpx.AsyncClient(
        base_url="https://priceframe.test",
        transport=httpx.MockTransport(handler),
    )
    client = PriceFrameClient(
        base_url="https://priceframe.test",
        timeout_seconds=1,
        max_retries=0,
        client=http_client,
    )

    with pytest.raises(PriceFrameUnavailableError, match="database is unavailable"):
        await client.post_json("/api/quotes", jwt_raw="jwt", json={"name": "Quote"})
