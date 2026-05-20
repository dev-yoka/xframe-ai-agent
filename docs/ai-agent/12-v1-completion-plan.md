# xFRAME AI Agent — v1 Completion Plan

**Date:** 2026-05-20
**Goal:** ship a first usable version of the AI agent that lets a Sales Representative authenticate against the deployed PriceFRAME backend, walk through a guided **Create Pricing Request** flow end-to-end, and consume the service from a Flutter mobile client.
**Branch:** `claude/deep-scan-workspace-ELH2Q`
**Deployed PriceFRAME:** `https://priceframe-yg.buy-frame.com/api/`
**Test account:** `admin@priceframe.local` / `Pricing2026`

> This document is the single source of truth for v1. It supersedes the long-range plan in `10-completion-plan.md` for everything required to demo v1, and complements the test procedures in `11-testing-guide.md`.

---

## 1. v1 scope

A Sales Representative opens the Flutter app, signs in with PriceFRAME credentials, asks the agent in natural language to *"create a pricing request for corridor USD→INR, volume 500k, term 12 months"*, and the agent:

1. Authenticates the user against PriceFRAME and obtains a JWT.
2. Loads the conversation thread (or creates one).
3. Calls the LLM provider (Gemini Vertex primary, Anthropic fallback) with the tool catalog filtered by the user's PriceFRAME permissions.
4. Walks through the deterministic *Create Pricing Request* sub-plan:
   - Look up reference data (corridors, currency rates, optional Salesforce PR lookup).
   - Propose a draft quotation (`create_quotation`).
   - Add corridors in bulk (`bulk_add_corridors`).
   - Preview the pricing change (`preview_pricing_change`).
   - Optionally tune FX spread (`set_fx_spread`).
   - Recalculate aggregates (`recalculate_quote_aggregates`).
   - Pause on the write proposals so the user can approve them from the mobile UI.
   - Submit for approval (`submit_for_approval`) once approved.
5. Streams the entire run over SSE so the mobile client renders deltas live.
6. Writes a full audit trail back to PriceFRAME via the HMAC-signed callback.

Anything else (RAG v1.5, fee-annex retrieval, web chat surface in the React client, cost dashboards) is **out of scope for v1** and tracked in `10-completion-plan.md` for later.

---

## 2. What is already completed

This is the verified inventory of capabilities currently on `claude/deep-scan-workspace-ELH2Q` (commit `1ea313e`).

### 2.1 Service surface (FastAPI)

| Endpoint group | File | Status |
|---|---|---|
| `GET /api/v1/agent/health` | `api/v1/health.py` | Live, with optional externals probe |
| `POST /api/v1/agent/conversations` | `api/v1/conversations.py` | Live, idempotency-key supported |
| `GET /api/v1/agent/conversations` | `api/v1/conversations.py` | Live, paginated |
| `GET/PATCH/DELETE /api/v1/agent/conversations/{id}` | `api/v1/conversations.py` | Live |
| `POST /api/v1/agent/conversations/{id}/messages` | `api/v1/conversations.py` | Live |
| `POST /api/v1/agent/conversations/{id}/runs` | `api/v1/conversations.py` | Live, idempotency-key supported |
| `GET /api/v1/agent/runs/{id}` | `api/v1/runs.py` | Live |
| `POST /api/v1/agent/runs/{id}/cancel` | `api/v1/runs.py` | Live |
| `POST /api/v1/agent/runs/{id}/decisions` | `api/v1/runs.py` | Live (approve / reject) |
| `GET /api/v1/agent/runs/{id}/stream` (SSE) | `api/v1/runs.py` | Live, DB replay + heartbeat |
| `GET /api/v1/agent/tools` | `api/v1/tools.py` | Live, permission-filtered |
| `POST /api/v1/agent/attachments` | `api/v1/attachments.py` | Live, ClamAV + S3/MinIO |
| `GET /api/v1/agent/memory` | `api/v1/memory.py` | Live |
| `POST /api/v1/agent/voice/transcriptions` | `api/v1/voice.py` | Live, Groq Whisper |

### 2.2 Authentication

- **PriceFRAME JWT verification** in `auth/jwt.py` (HS256, configurable secret).
- **Profile introspection** in `auth/priceframe_session.py` — calls `GET /api/auth/profile` on PriceFRAME, caches the resolved `AuthContext` for 60s by token hash and by `(user_id, session_id)`.
- `AuthContext` carries `user_id`, `role_code`, `profile_code`, `permissions` tuple, raw JWT, session id.
- `require_permission()` dependency factory in `auth/dependencies.py` for per-route gating.
- The agent **does not** mint its own tokens — it consumes PriceFRAME's. This is intentional and matches PR #5 in `03-priceframe-delta-prs.md`.

### 2.3 Run loop

Two loops coexist:

- **Deterministic loop** (`agent/loop.py`): parses `tool:{...}` directives in user input. Used by tests and for fallback when no provider is configured. Now writes `agent_run_steps` rows and respects the HITL `requires_approval` policy (the bug fixed in this sprint).
- **Model-orchestrated loop** (`agent/runner.py`): drives a provider failover router (`provider/base.py:ProviderFailoverRouter`). Enforces budgets, parallel reads with `Semaphore(max_parallel_tool_calls)`, serial writes, loop detection (3× identical tool+args), redaction, tool-output wrapping, model-field projection.

### 2.4 Tools registered (12)

Permission-gated via PriceFRAME `agent.*` codes, all returning `JsonOutput`.

| Tool | Risk | Permission |
|---|---|---|
| `list_my_quotations` | READ | `agent.quotes.read` |
| `get_quotation` | READ | `agent.quotes.read` |
| `list_corridors_available` | READ | `agent.quotes.read` |
| `get_currency_rate` | READ | `agent.quotes.read` |
| `lookup_salesforce_pr` | READ | `agent.salesforce.read` |
| `recalculate_quote_aggregates` | LOW_RISK_WRITE (no approval) | `agent.quotes.recalc` |
| `preview_pricing_change` | READ | `agent.quotes.recalc` |
| `create_quotation` | LOW_RISK_WRITE | `agent.quotes.create` |
| `bulk_add_corridors` | LOW_RISK_WRITE | `agent.quotes.edit` |
| `update_corridor_pricing` | LOW_RISK_WRITE | `agent.quotes.edit` |
| `set_fx_spread` | LOW_RISK_WRITE (FX min guard) | `agent.quotes.edit` |
| `submit_for_approval` | HIGH_RISK_WRITE | `agent.approvals.submit` |

### 2.5 Provider adapters

- `provider/gemini_vertex.py` — real `google-genai` SDK calls behind a lazy import. Constructor validates config; SDK is only loaded on first `stream()`.
- `provider/anthropic.py` — real `anthropic` SDK calls behind a lazy import.
- `provider/gemini_aistudio.py` — keeps the `ALLOW_REAL_DATA=false` guard for accidental dev keys.
- All three speak the same `StreamEvent` taxonomy (`text_delta`, `tool_use`, `usage`).

### 2.6 Safety and policy

- `agent/budget.py` — step / wall-clock / token / tool-call / cost ceilings, all emitting `v1.run.error{cause=*_budget_exceeded}` on breach.
- `agent/redaction.py` — strips emails / phones / cards / control chars from anything that reaches a provider or persisted message content.
- `agent/wrapping.py` — wraps tool outputs in `<tool_output>` blocks with `[Untrusted: ...]` prefix and HTML-escapes nested close tags.
- `ToolDefinition.project_for_model()` — strips non-visible fields before the model sees a tool result (full result still stored in DB).
- `agent_audit_log` rows + PriceFRAME HMAC callback (`/api/v1/agent-audit-callbacks`).

### 2.7 Persistence

13 tables under `xframe_agent.models` with two Alembic revisions:

- `202605190001_phase_d_agent_core.py` — conversations, runs, steps, messages, tool calls, events, audit log, memory, attachments.
- `202605200001_phase_e_beta.py` — beta surfaces (HMAC fields, attachment scan state, memory pinning, voice transcripts).

### 2.8 Testing

33 automated tests on `pytest`, covering: budget enforcement, redaction, wrapping, tool projection / approval policy, the model runner (read auto-execute, write pause, loop detection, provider error), conversation + run + SSE, Phase E surfaces, JWT verification, AI Studio guard, eval harness.

---

## 3. What is pending for v1

Each item below is what blocks the v1 demo on the deployed PriceFRAME at `https://priceframe-yg.buy-frame.com/api/`. Every item names files, acceptance criteria, and estimated size.

### V1.1 — Wire the deployed PriceFRAME backend

**Why:** today `.env.example` points at `http://localhost:3333`. Without pointing the service at the deployed backend, no real auth or tool call can succeed.

| Sub-task | File | Acceptance |
|---|---|---|
| V1.1.a Update `.env.example` defaults | `.env.example` | `PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com` documented as the v1 default; keep localhost as a comment for dev. |
| V1.1.b Verify `priceframe_jwt_secret` matches deployed signing key | env / deploy config | `verify_priceframe_jwt()` accepts a real token minted by the deployed PriceFRAME (`GET /api/auth/profile` returns 200). |
| V1.1.c Confirm `priceframe_service_secret` matches deployed HMAC shared secret | env / deploy config | `POST /api/v1/agent-audit-callbacks` accepts our HMAC and returns `audit_log_id`. |
| V1.1.d Confirm CORS origins include the mobile origin (or `*` for native) | `cors_origins` | A mobile `httpx`/`dart-http` call with `Origin: https://app.local` does not get blocked. Native mobile clients usually do not send `Origin`, but document the values to use during local browser testing. |

**Effort:** 0.5 day (mostly configuration + a single round-trip verification).

### V1.2 — `POST /api/v1/agent/auth/login` proxy endpoint *(new)*

**Why:** today the agent service only **verifies** PriceFRAME JWTs. The Flutter app needs a single endpoint to log in with email + password and receive a JWT. Two options were considered:

1. **Mobile calls PriceFRAME's `/api/auth/login` directly.** Simpler, but couples the mobile to PriceFRAME's exact response shape and requires PriceFRAME-specific error handling.
2. **Agent proxies the login** with a thin pass-through endpoint that returns the JWT + a normalized profile payload.

**Decision:** ship option 2 for v1. It gives the mobile client a single base URL, lets us add agent-side telemetry on login, and shields the mobile from any future PriceFRAME auth changes.

| Sub-task | File | Acceptance |
|---|---|---|
| V1.2.a `POST /api/v1/agent/auth/login` | new `api/v1/auth.py` | Accepts `{email, password}`. Calls `https://priceframe-yg.buy-frame.com/api/auth/login` via `PriceFrameClient`. Returns `{token, user, role, profile, permissions, expires_at}` to the client. |
| V1.2.b Refresh endpoint passthrough | `api/v1/auth.py` | `POST /api/v1/agent/auth/refresh` proxies PriceFRAME PR #3 (`/api/auth/refresh`). Returns the new short-lived access token. |
| V1.2.c `GET /api/v1/agent/auth/me` | `api/v1/auth.py` | Returns the cached `AuthContext` (id, role_code, profile_code, permissions). Used by mobile on app start to validate a stored JWT. |
| V1.2.d Login schema in `schemas/` | `schemas/auth.py` | Pydantic `LoginRequest`, `LoginResponse`, `RefreshRequest`, `MeResponse` with explicit field types. |
| V1.2.e Add to router | `api/v1/router.py` | `router.include_router(auth.router)`. |
| V1.2.f Tests | `tests/test_auth_login.py` | Happy path against a stubbed PriceFRAME; 401 surfaced unchanged; 5xx mapped to `502`. |

**Effort:** 0.5 day.

### V1.3 — Provider credentials in production

**Why:** the LLM provider scaffolds are wired but the deployed instance has neither Vertex nor Anthropic credentials configured.

| Sub-task | Acceptance |
|---|---|
| V1.3.a Choose a primary provider for v1 (recommendation: **Gemini 2.5 Flash on Vertex**) | Documented in deploy README and reflected in `XFRAME_AGENT_MODEL` env. |
| V1.3.b Provision a GCP service account with Vertex AI User role | Service-account key file mounted into the container at `/var/run/secrets/gcp.json`; `GOOGLE_APPLICATION_CREDENTIALS` set. |
| V1.3.c Optional Anthropic fallback | `ANTHROPIC_API_KEY` set; `ProviderFailoverRouter` initialized with `[Vertex, Anthropic]`. |
| V1.3.d Smoke test live | A run with a non-tool message returns `v1.run.completed` with a real text response. |

**Effort:** 0.5 day (mostly ops).

### V1.4 — *Create Pricing Request* guided plan

**Why:** today the model gets the full tool catalog and is left to its own devices. v1 needs an opinionated sub-plan that walks a Sales Rep through the canonical *Create Pricing Request* flow with predictable steps. This is **prompting work**, not new tools.

| Sub-task | File | Acceptance |
|---|---|---|
| V1.4.a System prompt assembly | new `agent/prompts/create_pricing_request.py` | Returns the system prompt string given an `AuthContext`. Includes role, profile, permission list, and the canonical step order. |
| V1.4.b Default conversation kind | `models/agent.py` + `schemas/conversations.py` | `AgentConversation.kind` defaults to `"create_pricing_request"` if no other intent is set; mobile sets this explicitly when launching the flow. |
| V1.4.c Inject the system prompt | `agent/runner.py` | Before the first model call of a run, prepend `ChatMessage(role="system", ...)` with the prompt that matches the conversation kind. |
| V1.4.d Few-shot examples in the prompt | `agent/prompts/create_pricing_request.py` | Two examples: one happy path (USD→INR, simple corridor), one with FX-spread adjustment. |
| V1.4.e Tool catalog narrowing | `agent/runner.py` | Optionally restrict the tool list to the *Create Pricing Request* subset (the 9 tools the flow actually uses) when `conversation.kind == "create_pricing_request"`. Reduces prompt size and stops the model from offering unrelated tools. |
| V1.4.f Test the guided plan | `tests/test_create_pricing_request_flow.py` | With a scripted `FakeProvider`, the flow proposes (a) `list_corridors_available`, (b) `create_quotation` (paused), (c) `bulk_add_corridors` (paused), (d) `preview_pricing_change`, (e) `submit_for_approval` (paused), and writes the expected audit rows. |

**Effort:** 1 day.

### V1.5 — Make `create_quotation` accept structured input

**Why:** today `CreateQuotationTool` and friends accept a free-form `payload: dict[str, Any]`. That works for the deterministic loop, but a real LLM benefits from a stricter schema so it produces consistent JSON and we catch missing fields before they reach PriceFRAME.

| Sub-task | File | Acceptance |
|---|---|---|
| V1.5.a Typed input for `create_quotation` | `tools/priceframe_write.py` | Replace `JsonPayloadInput` with `CreateQuotationInput` containing `title: str`, `customer_id: int`, `currency: str`, `corridors: list[CorridorDraft]`, `notes: str \| None`. |
| V1.5.b Typed input for `bulk_add_corridors` | `tools/priceframe_write.py` | Add `BulkAddCorridorsInput` with `quote_id: int`, `corridors: list[CorridorDraft]`. |
| V1.5.c Typed input for `update_corridor_pricing` | `tools/priceframe_write.py` | Add `UpdateCorridorPricingInput` with `corridor_id: int`, `applied_rate: Decimal`, `applied_fx_spread: Decimal \| None`, ... |
| V1.5.d JSON-schema regeneration | `scripts/export_openapi.py` | `openapi.yaml` reflects the new tool input schemas. |
| V1.5.e Regression test | `tests/test_phase_e_api.py` | Existing write-tool tests still pass against the typed inputs (use real shapes, no more raw payload). |

**Effort:** 1 day. **Tradeoff:** if we don't ship this, the model will frequently emit malformed JSON and the tool will 400 from PriceFRAME — not catastrophic, but visible.

### V1.6 — Deploy targets

| Sub-task | File | Acceptance |
|---|---|---|
| V1.6.a Production `Dockerfile` already exists | `Dockerfile` | Builds clean. |
| V1.6.b `docker-compose.prod.yml` for the agent stack | new `docker-compose.prod.yml` | Pins images, mounts secrets, includes `postgres + redis + minio + clamav + xframe-agent` (and optionally `langfuse` if used). |
| V1.6.c Alembic on container start | `entrypoint.sh` or compose `command:` | `uv run alembic upgrade head` runs before `uvicorn`. |
| V1.6.d Health probe wired | k8s/compose probe | `GET /api/v1/agent/health` returns 200 with externals OK. |
| V1.6.e Reverse proxy / TLS | nginx / Caddy config | Public URL (e.g. `https://agent-yg.buy-frame.com`) terminates TLS and forwards to the container on `:8000`. |
| V1.6.f Deploy README | `docs/ai-agent/README.md` cross-link or new `docs/deploy/v1.md` | Step-by-step server bring-up; mirrors §6 below. |

**Effort:** 0.5 day.

### V1.7 — Mobile-facing polish

| Sub-task | File | Acceptance |
|---|---|---|
| V1.7.a Confirm SSE works behind reverse proxy | reverse proxy config | `nginx`/`Caddy` does not buffer the `text/event-stream` response (`proxy_buffering off;` and matching timeouts ≥ `sse_heartbeat_seconds`). |
| V1.7.b Token-based SSE auth | `auth/dependencies.py` | Already supports `?token=...` query param for SSE (verified). Document for mobile, which can't easily set custom headers on `EventSource`. |
| V1.7.c Stable error envelope | `schemas/errors.py` (new) | Every 4xx/5xx returns `{error: {code, message, detail}}` so the mobile renderer doesn't have to special-case FastAPI's default `{detail: ...}` shape. |
| V1.7.d Pagination + sort on `/conversations` | `api/v1/conversations.py` | Response includes `next_cursor` and `has_more` so the mobile list view can scroll. |

**Effort:** 0.5 day.

### Total remaining v1 effort

~**4 engineer-days** spread across the 7 workstreams above, with no hard dependency ordering except V1.1 → V1.3 → V1.4.

---

## 4. Manual verification — step by step

The full test matrix lives in `11-testing-guide.md`. This section is the **shortest path** to verify v1 against the deployed PriceFRAME.

### 4.1 Prerequisites

```bash
cd /path/to/xframe-ai-agent
uv sync --extra dev
cp .env.example .env
```

Edit `.env`:

```dotenv
PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<value from PriceFRAME deploy>
PRICEFRAME_SERVICE_SECRET=<value from PriceFRAME deploy>
DATABASE_URL=postgresql+asyncpg://xframe:xframe@localhost:5433/xframe_agent
REDIS_URL=redis://localhost:6379/0
GEMINI_VERTEX_PROJECT=<your gcp project>
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/sa.json
```

Bring up the local stack:

```bash
docker compose up -d postgres redis minio clamav
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

### 4.2 Step 1 — login (V1.2)

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@priceframe.local","password":"Pricing2026"}'
```

**Expected:** `200` with `{ "token": "...", "user": {...}, "permissions": ["agent.quotes.read", ...] }`.

Save the token:

```bash
JWT=$(curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' | jq -r .token)
```

### 4.3 Step 2 — confirm permissions are populated

```bash
curl -s http://localhost:8000/api/v1/agent/auth/me \
  -H "Authorization: Bearer $JWT" | jq
```

**Expected:** `permissions` contains at least `agent.enabled`, `agent.quotes.read`, `agent.quotes.create`, `agent.quotes.edit`, `agent.quotes.recalc`, `agent.approvals.submit`. If any are missing, the PriceFRAME profile assignment is wrong — escalate to the PriceFRAME owner.

### 4.4 Step 3 — list tools

```bash
curl -s http://localhost:8000/api/v1/agent/tools \
  -H "Authorization: Bearer $JWT" | jq '.tools[].name'
```

**Expected:** the 12 tools listed in §2.4, filtered to the ones the user has permissions for.

### 4.5 Step 4 — start a *Create Pricing Request* conversation

```bash
CONV=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: v1-conv-$RANDOM" \
  -d '{"title":"Pricing request – USD→INR","kind":"create_pricing_request"}')

CONV_ID=$(echo "$CONV" | jq -r .id)
echo "conversation $CONV_ID"
```

### 4.6 Step 5 — run the guided flow

```bash
RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV_ID/runs" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: v1-run-$RANDOM" \
  -d '{"content":"Create a pricing request for corridor USD→INR, volume 500k, term 12 months."}')

RUN_ID=$(echo "$RUN" | jq -r .id)
echo "run $RUN_ID"
```

### 4.7 Step 6 — stream the events

```bash
curl -N "http://localhost:8000/api/v1/agent/runs/$RUN_ID/stream?token=$JWT"
```

**Expected event sequence:**

```
v1.run.started
v1.step.started        (model_call)
v1.message.delta       ...assistant thinks...
v1.step.completed
v1.tool.proposed       list_corridors_available
v1.tool.started
v1.tool.completed
v1.step.started        (model_call)
v1.message.delta       ...
v1.tool.proposed       create_quotation
v1.run.awaiting_decision
```

The run pauses at `awaiting_decision` because `create_quotation` is `LOW_RISK_WRITE` and requires approval.

### 4.8 Step 7 — approve the proposal

Grab `tool_call_id` from the `v1.tool.proposed` event payload, then:

```bash
curl -s -X POST "http://localhost:8000/api/v1/agent/runs/$RUN_ID/decisions" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"tool_call_id\":\"$TOOL_CALL_ID\",\"decision\":\"approve\"}"
```

**Expected:** stream resumes, tool executes, audit row appears in `agent_audit_log` and a callback is posted to PriceFRAME (verifiable from PriceFRAME's audit log).

### 4.9 Step 8 — repeat for the remaining write proposals

`bulk_add_corridors → preview_pricing_change → set_fx_spread (optional) → recalculate_quote_aggregates → submit_for_approval`. Each write pauses, each approval continues the run. The final state is `v1.run.completed` with the new quotation visible in PriceFRAME UI.

### 4.10 Step 9 — verify in PriceFRAME

Log in at `https://priceframe-yg.buy-frame.com` as `admin@priceframe.local`. Open *Quotations*. The newly created quote should be present with the corridors and FX spread from the run. The PriceFRAME audit log should show entries with `source = "agent"`.

---

## 5. Regression bar before declaring v1 ready

All must hold simultaneously:

- [ ] `uv run ruff format --check .` clean.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run mypy` clean.
- [ ] `uv run pytest` 33+ passing (more after V1.4 / V1.5 land).
- [ ] `git diff --exit-code openapi.yaml` clean.
- [ ] `POST /api/v1/agent/auth/login` with the deployed PriceFRAME returns a JWT.
- [ ] `GET /api/v1/agent/auth/me` returns the expected permissions for `admin@priceframe.local`.
- [ ] A single end-to-end run that creates a quotation, adds corridors, previews pricing, recalculates, and submits for approval completes with status `completed` and a clean audit trail.
- [ ] SSE works behind the production reverse proxy with no buffering.
- [ ] Mobile client (Flutter) can authenticate, list tools, start a run, render SSE, and POST decisions.

---

## 6. Deployment guide (v1)

### 6.1 Target topology

```
mobile (Flutter)
    │  HTTPS
    ▼
nginx / Caddy  ──► xframe-agent (uvicorn :8000)
                       │
                       ├──► postgres (xframe_agent db)
                       ├──► redis (rate limit + SSE buffer)
                       ├──► minio / s3 (attachments)
                       ├──► clamav (attachment scan)
                       ├──► priceframe API (https://priceframe-yg.buy-frame.com)
                       └──► gemini vertex (and anthropic fallback)
```

### 6.2 Build the image

```bash
docker build -t xframe-agent:v1 .
```

### 6.3 Required environment

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
API_PREFIX=/api/v1/agent
CORS_ORIGINS=https://app-yg.buy-frame.com,https://priceframe-yg.buy-frame.com

DATABASE_URL=postgresql+asyncpg://xframe:<pw>@postgres:5432/xframe_agent
REDIS_URL=redis://redis:6379/0

PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<must match deployed PriceFRAME>
PRICEFRAME_SERVICE_SECRET=<must match deployed PriceFRAME>
PRICEFRAME_JWT_ALGORITHM=HS256
PRICEFRAME_PROFILE_CACHE_TTL_SECONDS=60

GEMINI_VERTEX_PROJECT=<gcp project>
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json

ANTHROPIC_API_KEY=<optional fallback>

S3_ENDPOINT_URL=https://s3.<region>.amazonaws.com   # or minio
S3_ACCESS_KEY_ID=<...>
S3_SECRET_ACCESS_KEY=<...>
S3_BUCKET=xframe-agent-prod

RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60

RUN_EXECUTION_MODE=inline             # set to arq once arq worker is enabled
SSE_REDIS_BUFFER_ENABLED=true
SSE_HEARTBEAT_SECONDS=15

MAX_STEPS_PER_RUN=10
MAX_WALL_CLOCK_PER_RUN_S=60
MAX_INPUT_TOKENS_PER_RUN=50000
MAX_OUTPUT_TOKENS_PER_RUN=8000
MAX_TOOL_CALLS_PER_RUN=15
MAX_PARALLEL_TOOL_CALLS=3
COST_SOFT_PER_RUN_USD=0.15
COST_HARD_PER_RUN_USD=0.60

LANGFUSE_PUBLIC_KEY=<optional>
LANGFUSE_SECRET_KEY=<optional>
LANGFUSE_HOST=https://langfuse-yg.buy-frame.com
```

### 6.4 Database migration

The container's entrypoint should run migrations before starting uvicorn:

```bash
uv run alembic upgrade head
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

### 6.5 docker-compose (single-host bring-up)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_DB: xframe_agent, POSTGRES_USER: xframe, POSTGRES_PASSWORD: ${PG_PW} }
    volumes: [ "postgres-data:/var/lib/postgresql/data" ]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U xframe -d xframe_agent"], interval: 10s }

  redis:
    image: redis:7.4-alpine
    healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 10s }

  clamav:
    image: clamav/clamav:stable

  xframe-agent:
    image: xframe-agent:v1
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    env_file: .env.production
    secrets: [ gcp_sa ]
    ports: [ "8000:8000" ]
    healthcheck:
      test: ["CMD", "curl", "-fs", "http://localhost:8000/api/v1/agent/health"]
      interval: 30s

secrets:
  gcp_sa:
    file: ./secrets/gcp-sa.json

volumes:
  postgres-data:
```

### 6.6 Reverse proxy (nginx fragment)

```nginx
location /api/v1/agent/ {
    proxy_pass http://xframe-agent:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # SSE-friendly tuning
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    chunked_transfer_encoding on;
}
```

### 6.7 Post-deploy smoke

Run §4.2 → §4.9 against the production URL with the test account.

### 6.8 Rollback

The deployment is a single container plus a postgres schema migration. To roll back:

1. Re-deploy the previous image tag.
2. `uv run alembic downgrade -1` only if the new migration is incompatible — otherwise leave the schema in place (it is additive).

---

## 7. Next steps after v1

These are deliberately **out of scope for v1** but tracked here so the post-v1 roadmap is explicit:

1. **Flutter mobile app** (separate repo) — implement the screens that consume the endpoints above. The agent service is the only backend the mobile app talks to.
2. **arq worker mode** — switch `RUN_EXECUTION_MODE` from `inline` to `arq` once load justifies it. Plumbing already exists.
3. **Redis SSE buffer** — already configured; flip on once we have more than a single agent replica.
4. **RAG v1.5** — fee-annex retrieval (PriceFRAME PR #6/#7) and the `search_fee_annexes` tool. Improves quality of automated FX/fee suggestions.
5. **Cost dashboard** — `/admin/spend` endpoint + nightly roll-up job. Required before broadening user base beyond pilot.
6. **Live evals in Langfuse** — flip `XFRAME_EVAL_MODE=provider` and seed a small set of golden traces. Enables regression gating on prompt changes.
7. **Approval guidelines client→DB** — move the `approval-guidelines.ts` shape from the PriceFRAME client into a DB table so the agent and the React UI share one source of truth.
8. **Web chat surface** — integrate the same backend into the existing PriceFRAME React client (separate workstream tracked in the PriceFRAME repo).

---

## 8. Open questions

- **JWT signing key parity.** Confirm with the PriceFRAME deploy owner that `PRICEFRAME_JWT_SECRET` is the **exact** symmetric secret used to sign tokens at `https://priceframe-yg.buy-frame.com/api/auth/login`. If PriceFRAME has moved to RS256/EdDSA, `auth/jwt.py` needs a small change.
- **Service-secret rotation.** The HMAC callback shares a single secret today. v1 ships with this; v2 should support `kid` + key rotation.
- **Model choice for cost.** Default of Gemini 2.5 Flash on Vertex is the cheapest credible option. If Vertex isn't available in the target region, fall back to Anthropic Claude Haiku 4.5; both are wired and tested.
- **PriceFRAME `agent.*` permission seeding.** PR #5 in `03-priceframe-delta-prs.md` is required for the deployed PriceFRAME to return those permission codes in `/api/auth/profile`. If it has not landed, V1.4/V1.6 will appear to "work" but every tool call will 403. Verify with the §4.3 step before any further work.
