# provider

Provider-agnostic LLM streaming protocol plus adapter shells for Gemini Vertex, Gemini AI Studio, and Anthropic.

Public API:

- `xframe_agent.provider.base:Provider`
- `xframe_agent.provider.base:ProviderFailoverRouter`
- `xframe_agent.provider.gemini_vertex:GeminiVertexProvider`
- `xframe_agent.provider.gemini_aistudio:GeminiAIStudioProvider`
- `xframe_agent.provider.anthropic:AnthropicProvider`

Extension point: wire SDK calls inside each provider while keeping `ChatMessage`, `ContentBlock`, and `StreamEvent` stable for the agent loop.
