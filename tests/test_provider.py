"""Provider adapter guardrail tests."""

from __future__ import annotations

import pytest

from xframe_agent.provider import ProviderError
from xframe_agent.provider.gemini_aistudio import GeminiAIStudioProvider
from xframe_agent.settings import Settings


def test_ai_studio_provider_refuses_real_data(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={"allow_real_data": True, "gemini_aistudio_api_key": "dev-key"}
    )

    with pytest.raises(ProviderError, match="ALLOW_REAL_DATA"):
        GeminiAIStudioProvider(settings)
