# tools

Pydantic-native tool definitions that wrap PriceFRAME REST APIs using the end user's JWT.

Public API:

- `xframe_agent.tools.base:ToolDefinition`
- `xframe_agent.tools.registry:tool_registry`
- `xframe_agent.tools.registry:REGISTERED_TOOLS`
- `xframe_agent.tools.registry:SCAFFOLDED_TOOLS`

Extension point: Phase D registers the read path plus `recalculate_quote_aggregates`; Phase E promotes scaffolded write tools once confirmation and audit callbacks are implemented.
