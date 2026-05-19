# agent

Durable run-loop primitives for conversations, run events, idempotency, and future model/tool orchestration.

Public API:

- `xframe_agent.agent.loop:AgentLoop`
- `xframe_agent.agent.events:append_run_event`
- `xframe_agent.agent.events:list_run_events`
- `xframe_agent.agent.idempotency:get_replay`
- `xframe_agent.agent.idempotency:store_replay`

Extension point: the deterministic Phase D loop is intentionally small; provider-backed planning and read-tool execution plug into `AgentLoop.run()` without changing the HTTP API.
