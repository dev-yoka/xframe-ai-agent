# Glossary

All terms used across this handbook, alphabetical.

---

**`AgentConversation`** — ORM row for a long-running chat thread. Has `kind` (V1.4) that determines which system prompt is injected. Cascades to messages, runs.

**`AgentLoop`** — The deterministic, regex-driven legacy runner in `agent/loop.py`. Currently invoked from the HTTP entry points (`/messages`, `/runs`) and the arq worker. Demos a flow without a real LLM. Compare `ModelRunner`.

**`AgentMessage`** — ORM row for one chat message. Roles: `user`, `assistant`, `system`, `tool`. Linked to its originating `AgentRun` via `run_id`.

**`AgentRun`** — ORM row for one unit of agent execution. States: `queued`, `running`, `awaiting_decision`, `completed`, `error`, `cancelled`.

**`AgentRunEvent`** — Append-only journal row in `agent_run_events`. Carries `(run_id, seq)` unique + `event_type` + `payload`. The SSE replay source of truth.

**`AgentRunStep`** — One iteration within a run, kind `model_call` or `tool_call`.

**`AgentToolCall`** — ORM row for one tool invocation. Tracks `status` (proposed/pending/succeeded/failed/rejected), `requires_approval`, args, result, error, `priceframe_audit_log_id`.

**Anthropic** — One of the LLM providers (Claude). Fallback after Gemini Vertex in the failover order. SDK: `anthropic`.

**API prefix** — The path under which all agent endpoints live; default `/api/v1/agent` (env `API_PREFIX`).

**Approval gate** — The pause that happens when a tool's `requires_approval()` returns True. Sets the run to `awaiting_decision`, exits the runner, awaits a `POST /runs/{id}/decisions` call.

**`arq`** — Python async job queue backed by Redis. Used when `RUN_EXECUTION_MODE=arq` to defer runs to a background worker.

**Audit callback** — A POST from the agent to PriceFRAME's `/api/v1/agent-audit-callbacks` after every executed write. HMAC-signed using `PRICEFRAME_SERVICE_SECRET`.

**`AuthContext`** — Frozen dataclass carrying `user_id`, `role_code`, `profile_code`, `permissions`, `jwt_raw`, `session_id`. Created by `get_auth_context_from_profile`. Used everywhere as the "who is this request from?" object.

**Bearer token** — A JWT carried in the `Authorization: Bearer <token>` header (or `?token=<token>` for SSE).

**Budget** — See `LoopBudget`. Limits on steps, tool calls, tokens, cost, wall-clock per run.

**`call_id`** — A unique identifier the LLM provides for each `tool_use` block. The tool result must echo it so the model can match them.

**`cause`** — A short error code in `v1.run.error` payloads, e.g., `provider_error`, `cost_budget_exceeded`, `loop_detected`, `tool_error`.

**`ChatMessage`** — Pydantic model in `provider/base.py`. The provider-agnostic message format with `role` and `content: list[ContentBlock]`.

**`ContentBlock`** — Pydantic model. `type` is `"text"` or `"tool_result"` etc.; `payload` is the data.

**Context window** — The maximum input tokens a model accepts in one call. Gemini 2.5 Flash: 1M. Claude Sonnet: 200K.

**Cost soft/hard ceiling** — `COST_SOFT_PER_RUN_USD` triggers a warning (currently unused in code); `COST_HARD_PER_RUN_USD` raises `BudgetExceededError(cause=cost_budget_exceeded)`.

**Cursor pagination** — V1.7 addition. `GET /conversations?limit=20&cursor=<id>` returns `next_cursor` + `has_more`.

**`Decisions endpoint`** — `POST /api/v1/agent/runs/{id}/decisions` — where the user approves, rejects, or edits a pending tool call.

**Embedding** — A vector representation of text used for similarity search. Not yet implemented in this codebase (see §15.10).

**`EventSource`** — Browser API for consuming SSE. Cannot set custom headers, so JWT must be passed as `?token=...`.

**Failover router** — `ProviderFailoverRouter`. Tries providers in order; on `ProviderError(failover=True)` or 30s timeout, marks unhealthy for 300s and tries the next.

**Gemini Vertex** — Google's hosted Gemini API via Vertex AI on GCP. The primary LLM provider in this codebase.

**Gemini AI Studio** — Google's developer-tier Gemini API. Gated behind `ALLOW_REAL_DATA=true` to prevent dev keys from seeing production data. The `stream()` method is a stub in v1.

**HITL** — Human-in-the-loop. The pattern of pausing for explicit human approval before risky tool calls.

**HMAC** — Hash-based Message Authentication Code. Used by `post_agent_audit_callback` to prove the audit callback came from the agent service.

**`httpx`** — Async HTTP client library used by `PriceFrameClient`.

**Idempotency-Key** — HTTP header. The agent stores responses keyed by `(user_id, key)` with a 7-day TTL; replays the same response if the same key is reused. Also passed to PriceFRAME on write tools so PriceFRAME can dedupe.

**Inline run mode** — `RUN_EXECUTION_MODE=inline` — runs execute in the HTTP request handler thread. Useful for sync API responses; not for production at scale.

**JWT** — JSON Web Token. The PriceFRAME-issued token that authenticates the user. HS256 + `PRICEFRAME_JWT_SECRET`.

**Langfuse** — Open-source LLM tracing platform. Optional; when configured (`LANGFUSE_*` env), every LLM call is exported as a trace.

**`LoopBudget`** — Dataclass in `agent/budget.py` tracking per-run consumption against hard ceilings; raises `BudgetExceededError`.

**`LoopDetectedError`** — Raised when the same `(tool_name, sorted_args)` is proposed 3 times in a row.

**Memory tiers** — Working (in-process list), conversation (`agent_messages`), run (`agent_runs`, `agent_run_events`), user (`agent_user_memory` — scaffolded), tool catalog (`tool_registry`).

**`ModelRunner`** — The LLM-driven runner in `agent/runner.py`. Uses a provider, supports parallel reads, serial writes, HITL pause, system prompt injection, loop detection.

**Pagination** — Cursor-based on `GET /conversations` (V1.7).

**`PriceFrameClient`** — The `httpx`-based HTTP client for PriceFRAME. Handles retries, error mapping, HMAC callback signing.

**`PriceFrameError`** — Exception hierarchy: `PriceFrameAuthError` (401), `PriceFrameForbiddenError` (403), `PriceFrameNotFoundError` (404), `PriceFrameResponseError` (5xx + others), `PriceFrameTimeoutError`.

**`project_for_model`** — Class method on `ToolDefinition` that filters tool result fields down to `model_visible_fields` before they're sent back to the LLM. Reduces context bloat and limits exposure.

**Prompt injection** — Attack pattern where an attacker embeds instructions in data the LLM will process. Defended via `wrap_tool_output` + system-prompt rules + HITL approval.

**`ProposedCall`** — Dataclass in `runner.py`: `(name, args, call_id)` extracted from a `tool_use` event.

**Provider** — Protocol in `provider/base.py`. Any LLM API implementing `async def stream(messages, tools, *, model, max_output_tokens) -> AsyncIterator[StreamEvent]`.

**`ProviderError`** — Exception raised by providers. `failover=True` (default) tells the router to try the next; `failover=False` aborts.

**Pydantic** — Library for declarative data validation. All schemas, models, settings use Pydantic v2.

**`redact()`** — Function in `agent/redaction.py` that substitutes PII patterns with placeholders like `<PII:email>`.

**`RequestIdMiddleware`** — FastAPI middleware that adds `X-Request-ID` to every request and binds it to structlog context.

**Risk classification** — `READ`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE`. Determines approval requirements and audit behavior.

**Run kind** — `AgentConversation.kind` field. Selects the system prompt (V1.4 has only `create_pricing_request` and the implicit `general`).

**`Settings`** — Pydantic-settings model in `settings.py`. All configuration from env vars.

**SSE** — Server-Sent Events. The streaming protocol for `GET /runs/{id}/stream`. One-way (server → client), text-based, supports `Last-Event-ID` for replay.

**Step** — One iteration of the runner loop. Either a `model_call` (LLM round) or a `tool_call` (tool round).

**`StreamEvent`** — Pydantic model emitted by providers. Kinds: `text_delta`, `tool_use`, `usage`.

**System prompt** — The persistent instructions sent to the LLM with `role="system"`. Injected by `ModelRunner` for `create_pricing_request` conversations or empty histories.

**Tool** — A function the agent can invoke. Defined by a `ToolDefinition` subclass. Twelve concrete tools in v1.

**Tool catalog / `tool_registry`** — Singleton in `tools/registry.py`. `available_for(ctx)` filters by `tool.permission in ctx.permissions`.

**`ToolDefinition`** — Generic base class in `tools/base.py`. Subclasses declare `name`, `description`, `permission`, `risk`, `input_model`, `output_model`, etc.

**Tool output wrapping** — `wrap_tool_output()` in `agent/wrapping.py`. Wraps tool results in `<tool_output>` with `[Untrusted: ...]` marker; escapes embedded close tags.

**`tool_use`** — A structured block the LLM emits to call a function. Carries `name`, `args`, `call_id`.

**`uv`** — Modern Python package manager used in this project. Replaces pip + virtualenv + pip-tools.

**Vertex AI** — Google's managed AI platform. Hosts Gemini in this project.

**Worker** — The arq process that pulls jobs from Redis and executes them. Configured by `worker.WorkerSettings`.

**Wrapping** — See `wrap_tool_output`.

---

**See also:** [§02 Fundamentals](./02-fundamentals.md) for in-context introduction to many of these terms.
