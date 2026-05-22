# provider

Provider-agnostic LLM streaming protocol plus adapters for Gemini API-key calls, Gemini Vertex, and Anthropic.

Public API:

- `xframe_agent.provider.base:Provider`
- `xframe_agent.provider.base:ProviderFailoverRouter`
- `xframe_agent.provider.gemini_vertex:GeminiVertexProvider`
- `xframe_agent.provider.gemini_aistudio:GeminiAIStudioProvider` (`GEMINI_API_KEY` / `GEMINI_AISTUDIO_API_KEY`)
- `xframe_agent.provider.anthropic:AnthropicProvider`

Extension point: keep `ChatMessage`, `ContentBlock`, and `StreamEvent` stable for the agent loop when adding providers.
