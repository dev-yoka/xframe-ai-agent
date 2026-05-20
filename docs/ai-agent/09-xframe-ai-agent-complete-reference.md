# xFRAME Ai Agent — Complete Technical Reference

**Scope:** the **`xframe-ai-agent`** Python service and how it integrates with **PriceFRAME**.  
**Audience:** engineers implementing the sales-rep pricing flow, DevOps, and security reviewers.  
**Last reviewed:** 2026-05-20 (align with `xframe-ai-agent` `main` / active dev branch).

---

## 1. System role

- **PriceFRAME** remains the **system of record** for quotes, corridors, pricing, approvals, and RBAC.
- The agent service **never** holds elevated PriceFRAME credentials for end-user data. It calls PriceFRAME’s REST API with the **same JWT** the client used to call the agent, so **PriceFRAME permission middleware** stays authoritative.
- The agent service **does** persist its own data: conversations, messages, runs, streamed events, tool calls, idempotency replays, optional attachments, optional user memory rows, and a local **`agent_audit_log`** mirror for agent-side forensics.

---

## 2. Repository layout (`xframe-ai-agent`)

| Path | Responsibility |
|------|----------------|
| `src/xframe_agent/main.py` | FastAPI app factory |
| `src/xframe_agent/settings.py` | Pydantic settings / environment variables |
| `src/xframe_agent/auth/` | JWT verification, `GET /api/auth/profile` introspection, `AuthContext` |
| `src/xframe_agent/priceframe/` | `httpx`-based `PriceFrameClient` (GET/POST/PUT, retries, audit callback signing) |
| `src/xframe_agent/tools/` | `ToolDefinition` subclasses + `registry.py` |
| `src/xframe_agent/agent/` | `AgentLoop`, durable run events, idempotency helpers |
| `src/xframe_agent/api/v1/` | Versioned HTTP routers |
| `src/xframe_agent/db/` | Async SQLAlchemy engine/session |
| `src/xframe_agent/worker.py` | arq worker (`RUN_EXECUTION_MODE=arq`) |
| `evals/` | Synthetic golden-trace harness (structural / CI) |

**API base path:** configurable; default **`/api/v1/agent`** (`Settings.api_prefix`).

---

## 3. HTTP API surface

All routes below are relative to **`{api_prefix}`** (default `/api/v1/agent`). Clients must send **`Authorization: Bearer <PriceFRAME JWT>`**.

### 3.1 Health

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/health` | DB, Redis, queue mode, optional PriceFRAME probe, provider flags |

### 3.2 Tool discovery

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/tools` | Lists tools **filtered by JWT permissions** from PriceFRAME profile |

### 3.3 Conversations and runs

| Method | Path | Notes |
|--------|------|--------|
| `POST` | `/conversations` | Create; optional `Idempotency-Key` |
| `GET` | `/conversations` | List for current user |
| `GET` | `/conversations/{id}` | Detail + recent messages (capped) |
| `PATCH` | `/conversations/{id}` | Title / pinned / archived |
| `DELETE` | `/conversations/{id}` | Soft delete |
| `POST` | `/conversations/{id}/messages` | Create user message + **inline** `AgentLoop` (returns when run completes) |
| `POST` | `/conversations/{id}/runs` | Start run; **`202`** if `RUN_EXECUTION_MODE=arq`, else may complete inline |
| `GET` | `/runs/{run_id}` | Run snapshot |
| `POST` | `/runs/{run_id}/cancel` | Cancel |
| `POST` | `/runs/{run_id}/decisions` | **approve** / **reject** / **edit** pending tool call |
| `GET` | `/runs/{run_id}/stream` | **SSE** replay + heartbeats; supports `Last-Event-ID` header or `?last_event_id=` |

### 3.4 Attachments

| Method | Path | Notes |
|--------|------|--------|
| `POST` | `/attachments` | Multipart upload → S3/MinIO or local; ClamAV optional |
| `GET` | `/attachments/{id}` | Metadata / download path per implementation |

### 3.5 Memory

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/memory` | User-visible memory rows |
| `DELETE` | `/memory/{id}` | Remove row |

### 3.6 Voice

| Method | Path | Notes |
|--------|------|--------|
| `POST` | `/voice/transcriptions` | Groq Whisper when `GROQ_API_KEY` set |

**OpenAPI:** `GET {api_prefix}/openapi.json` (also export via `scripts/export_openapi.py` in the agent repo).

---

## 4. Run lifecycle and SSE

1. Client creates a **conversation**, then sends a **message** or starts a **run**.
2. `AgentLoop` appends durable rows to **`agent_run_events`** (e.g. assistant text, `v1.tool.proposed`, terminal states).
3. If a **write** (or any non-read tool the product treats as gated) is proposed, the run may enter **`awaiting_decision`** until the user **approves**, **rejects**, or **edits** args via `POST /runs/{id}/decisions`.
4. On **approve**, the server executes the tool via **`PriceFrameClient`**, passing **`Idempotency-Key: {tool_call_id}`** to PriceFRAME, persists result, appends `v1.tool.completed`, and for non-**`READ`** risk calls **`POST /api/v1/agent-audit-callbacks`** on PriceFRAME (HMAC-signed where configured).
5. **`GET /runs/{id}/stream`** reads **`agent_run_events`** after `seq` cursor, emits SSE `id` = seq, until terminal event or run status.

**Replay source of truth:** Postgres **`agent_run_events`**. Redis LIST buffering for live fan-out may be partial or future-facing (`sse_redis_buffer_enabled` in settings).

---

## 5. Tool registry (current)

Source of truth: `src/xframe_agent/tools/registry.py` → **`REGISTERED_TOOLS`**.

### 5.1 Read tools

| Tool name | Permission | Risk | PriceFRAME (summary) |
|-----------|------------|------|----------------------|
| `list_my_quotations` | `agent.quotes.read` | READ | `GET /api/quotes` (owner-scoped query params) |
| `get_quotation` | `agent.quotes.read` | READ | **`GET /api/v1/quotes/{id}/pricing-context`** |
| `list_corridors_available` | `agent.quotes.read` | READ | `GET /api/corridors/active` (**no filter args in v1 tool**) |
| `get_currency_rate` | `agent.quotes.read` | READ | `GET /api/app-config/currency-rates` |
| `lookup_salesforce_pr` | `agent.salesforce.read` | READ | `GET /api/quotes/salesforce/search` |
| `recalculate_quote_aggregates` | `agent.quotes.recalc` | READ* | `POST /api/quotes/{id}/recalculate-aggregates` — class overrides **`requires_approval` → False** (see §7) |

\*Declared **READ** in registry sense for audit callback behavior; still mutates aggregates on PriceFRAME — treat as **low-risk write** operationally.

### 5.2 Write / preview tools

| Tool name | Permission | Risk | PriceFRAME (summary) |
|-----------|------------|------|----------------------|
| `preview_pricing_change` | `agent.quotes.recalc` | LOW_RISK_WRITE | **`POST /api/v1/quotes/{id}/pricing/preview`** |
| `create_quotation` | `agent.quotes.create` | HIGH_RISK_WRITE | `POST /api/quotes` |
| `bulk_add_corridors` | `agent.quotes.edit` | HIGH_RISK_WRITE | `POST /api/quotes/{id}/corridors/bulk` |
| `update_corridor_pricing` | `agent.quotes.edit` | HIGH_RISK_WRITE | `PUT /api/quote-corridors/{id}` |
| `set_fx_spread` | `agent.quotes.edit` | HIGH_RISK_WRITE | `PUT /api/quote-corridors/{id}` (spread fields; client-side min check) |
| `submit_for_approval` | `agent.approvals.submit` | HIGH_RISK_WRITE | `POST /api/quotes/{id}/approvals` |

Each tool implements **`execute(args, ctx, priceframe)`** and exposes **`to_provider_schema()`** (JSON Schema from Pydantic models) for future LLM function calling.

**`SCAFFOLDED_TOOLS`** in `registry.py` duplicates write tool **classes** for historical naming; all production tools live in **`REGISTERED_TOOLS`**.

---

## 6. Deterministic loop vs LLM (important)

Until provider-backed orchestration ships:

- **`AgentLoop`** is **deterministic**: generic assistant text + permission list.
- **Tool proposals** are emitted only when the user message matches the literal prefix **`tool:`** followed by JSON:  
  `tool:{"name":"<tool_name>","args":{...}}`  
  This is for **demo / integration tests**, not end-user natural language.

**Provider layer:** Gemini Vertex, Gemini AI Studio, Anthropic adapters exist as **shells / protocols**; wiring streaming + native tool calls is the main **product gap** for a real “chat wizard” sales flow.

---

## 7. Human-in-the-loop (HITL) caveats

- Proposed **`AgentToolCall`** rows are created with **`requires_approval=True` in code** (`agent/loop.py`) for the demo path — the base class’s **`requires_approval()`** async method is **not** consulted when enqueueing proposals.
- Therefore **`recalculate_quote_aggregates`** may still require a user **Approve** step even though the tool class returns **`False`** for `requires_approval`.
- **Remediation (engineering backlog):** set `requires_approval` from `await tool.requires_approval(args, ctx)` when recording the tool call.

---

## 8. Environment variables (representative)

Defined in `src/xframe_agent/settings.py` (see `.env.example` in agent repo for full list).

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Async Postgres URL for agent DB |
| `REDIS_URL` | arq + rate limit + optional SSE buffer |
| `PRICEFRAME_BASE_URL` | PriceFRAME origin (e.g. `http://localhost:3333`) |
| `PRICEFRAME_JWT_SECRET` | HS256 verification for user JWTs |
| `PRICEFRAME_SERVICE_SECRET` | HMAC / service-style callbacks where used |
| `RUN_EXECUTION_MODE` | `inline` (default tests) or `arq` |
| `SSE_HEARTBEAT_SECONDS`, `SSE_REPLAY_EVENT_LIMIT` | SSE tuning |
| `S3_*`, `ATTACHMENT_*`, `CLAMAV_*` | Attachments pipeline |
| `GROQ_API_KEY` | Voice transcription |
| `GEMINI_*`, `ANTHROPIC_API_KEY`, `ALLOW_REAL_DATA` | Provider guards |

---

## 9. PriceFRAME prerequisites

The agent expects PriceFRAME to expose (non-exhaustive; align with `03-priceframe-delta-prs.md`):

- Standard quote / corridor / approval routes used by the tools above.
- **`GET /api/v1/quotes/:id/pricing-context`** — composite read for editor/agent.
- **`POST /api/v1/quotes/:id/pricing/preview`** — non-persistent pricing preview.
- **`POST /api/v1/agent-audit-callbacks`** — signed callback after agent-executed writes (metadata for `audit_logs`).
- **`GET /api/auth/profile`** — JWT introspection for permissions used in **`/tools`** filtering.

RBAC **permission codes** on the user’s PriceFRAME profile must include the **`agent.*`** strings listed in §5 where those tools should appear.

---

## 10. Verification commands (agent repo)

```bash
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_openapi.py
# optional: git diff --exit-code openapi.yaml
```

Local stack: Postgres + Redis (+ Langfuse, MinIO, optional ClamAV) per agent `README.md` / `docker-compose.yml`.

---

## 11. Product gaps (pricing-request flow)

For a **full** sales-rep “pricing request” chat flow (bootstrap → quoting → technical → corridor filters → pricing → fees → approval):

| Gap | Description |
|-----|-------------|
| **NL orchestration** | Map natural language → structured tool args; multi-turn state; streaming UX |
| **Corridor listing** | `list_corridors_available` hits unfiltered **active** list — may need **query dimensions** or server-side filtered endpoint for scale |
| **HITL policy** | Honor per-tool `requires_approval`; optional auto-run for safe reads |
| **Audit entity mapping** | `create_quotation` audit helper may not resolve entity id from nested `payload` — verify on change |
| **Web/mobile clients** | PriceFRAME web panel under `client/src/features/ai_agent/` (see **08**); Flutter app separate repo |
| **Evals** | `evals/README.md`: replace structural placeholders with provider-backed golden traces when loop is deterministic |

---

## 12. Related PriceFRAME docs

- [03-priceframe-delta-prs.md](./03-priceframe-delta-prs.md) — API deltas.
- [08-phase-E-beta.md](./08-phase-E-beta.md) — beta handoff including web chat and audit callback.
- [07-phase-D-mvp.md](./07-phase-D-mvp.md) — historical Phase D (**tool registration state is outdated** in that file; trust **09** + `registry.py`).

---

## 13. Document history

| Date | Change |
|------|--------|
| 2026-05-20 | Initial **09** complete reference authored in PriceFRAME `docs/ai-agent/`. |
