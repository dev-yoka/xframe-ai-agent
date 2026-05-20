# Phase D — Agent MVP Handoff

> **Maintenance:** This file is a **point-in-time handoff**. For **current** tool registration and APIs, use [**09-xframe-ai-agent-complete-reference.md**](./09-xframe-ai-agent-complete-reference.md) and the **`xframe-ai-agent`** repo (`README.md`, `src/xframe_agent/tools/registry.py`). In particular, Phase E write tools are **registered** today — the “scaffolded / unregistered” bullets below are **obsolete**.

Date: 2026-05-19

Repo: `/Users/bhairava/WorkSpace/repos/xframe-ai-agent`

Branch: `phase-D/mvp`

## Built

- Added Phase D persistence: `agent_conversations`, `agent_messages`, `agent_runs`, `agent_run_steps`, `agent_tool_calls`, `agent_run_events`, `agent_idempotency_keys`, `agent_users_cache`, `agent_device_tokens`, and `agent_audit_log`.
- Added conversation/run REST API under `/api/v1/agent`: create/list/get/update/delete conversations, non-streaming message send, async run start, run snapshot, cancel, decisions, and SSE stream replay.
- Added durable SSE event protocol with monotonic `seq`, `Last-Event-ID`/`last_event_id` resume, heartbeat support, and durable `agent_run_events`.
- Added Pydantic-native tool registry and `/tools` discovery with per-user permission filtering. Registered Phase D tools: `list_my_quotations`, `get_quotation`, `list_corridors_available`, `get_currency_rate`, `lookup_salesforce_pr`, and `recalculate_quote_aggregates`.
- Scaffolded Phase E/write tools but left them unregistered: `preview_pricing_change`, `create_quotation`, `bulk_add_corridors`, `update_corridor_pricing`, `set_fx_spread`, and `submit_for_approval`.
- Added provider protocol and failover router shells for Gemini Vertex, Gemini AI Studio, and Anthropic. AI Studio refuses to initialize when `ALLOW_REAL_DATA=true`.
- Added arq worker entry point for queued run execution and inline mode for local/dev tests.
- Added synthetic eval harness with five golden traces, OpenAPI snapshot update, module READMEs, and tests for API, provider guardrail, auth, health, and eval structure.

## Deferred / Gaps

- The model loop is deterministic in this handoff; actual Gemini/Anthropic SDK streaming and tool-call orchestration still need to be wired before a real demo.
- Redis SSE buffering is represented by settings and durable DB replay, but Redis LIST buffering is not yet implemented.
- `preview_pricing_change` stays unregistered until PriceFRAME delta-PR #2 exists.
- Human-in-the-loop write execution, PriceFRAME audit callback, attachments, voice, memory, and web chat remain Phase E.

## How To Run

```bash
cd /Users/bhairava/WorkSpace/repos/xframe-ai-agent
uv sync --extra dev
docker compose up -d postgres redis langfuse-db langfuse minio
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --host 0.0.0.0 --port 8000
```

For queued execution:

```bash
RUN_EXECUTION_MODE=arq uv run arq xframe_agent.worker.WorkerSettings
```

## Verification

Fresh verification on 2026-05-19:

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy`
- `uv run pytest`
- `uv run python scripts/export_openapi.py`
- `DATABASE_URL=sqlite+aiosqlite:////tmp/<tmp>.db uv run alembic upgrade head`
