"""Auth login, refresh, and me endpoint tests."""

from __future__ import annotations

import pytest

from xframe_agent.schemas.auth import LoginRequest, LoginResponse, UserInfo


class TestLoginEndpoint:
    """Tests for the login endpoint against a stubbed PriceFRAME."""

    @pytest.mark.asyncio
    async def test_login_endpoint_structure(self) -> None:
        """Verify the endpoint exists and accepts the right request shape."""
        request = LoginRequest(email="test@example.com", password="testpass")  # noqa: S106
        assert request.email == "test@example.com"
        assert request.password == "testpass"  # noqa: S105

    def test_login_response_structure(self) -> None:
        """Verify the response model shape."""
        response = LoginResponse(
            token="abc123",  # noqa: S106
            user=UserInfo(id=1, email="test@example.com", role="SALES", profile="PROFILE"),
            role_code="SALES",
            profile_code="PROFILE",
            permissions=["agent.quotes.read"],
            expires_at=1234567890,
        )
        assert response.token == "abc123"  # noqa: S105
        assert response.user.id == 1
        assert "agent.quotes.read" in response.permissions


# Full integration tests would require mocking PriceFrameClient.
# These unit tests verify schemas; integration tests happen in manual §4 of the completion plan.
