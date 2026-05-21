# Part 2 — Project Overview

> Three chapters that map every concept from Part 1 onto the actual xFRAME AI Agent codebase. After this part, the file/folder layout, the runtime, and the deployment should feel familiar enough that Part 3 (codebase deep dive) is a leisurely walk rather than a forced march.

---

## Chapter 11 — The Business Context

### 11.1 What PriceFRAME is and why the agent exists

**PriceFRAME** is a remittance pricing platform. Sales representatives at financial institutions use it to:

- Create **quotations** for corporate customers who need to send money cross-border.
- Configure **corridors** (country pairs: e.g., USD → INR).
- Set **FX spreads** (the markup the institution charges on currency conversion).
- Apply **fees**, **volume tiers**, and **term lengths**.
- Submit for **internal approval** before the quote becomes binding.

A typical quote involves 12+ form screens, dozens of dropdowns, and lots of context-switching. The xFRAME AI Agent **collapses the workflow to a conversation**:

> "Create a quote for Acme Corp, India corridor, USD, 0.02 spread, 12-month term."

The agent does the rest — looks up the Salesforce record, pulls market rates, drafts the quotation, asks for confirmation, submits for approval. The sales rep clicks "Approve" on a mobile card and moves on.

**Why this matters:** sales reps spend significant time clicking through PriceFRAME. Even a 30% reduction in mean-time-to-quote pays for the agent many times over. The conversational interface also enables **mobile use** — a rep can quote from the airport on a phone.

### 11.2 The user journey, end to end

```mermaid
sequenceDiagram
    actor Rep as Sales Rep
    participant App as Flutter mobile app
    participant Agent as xFRAME AI Agent
    participant PF as PriceFRAME (system of record)
    participant LLM as Gemini Vertex

    Rep->>App: Open, sign in with PriceFRAME credentials
    App->>Agent: POST /auth/login
    Agent->>PF: Proxy login
    PF-->>Agent: token + permissions
    Agent-->>App: JWT + role + permissions

    Rep->>App: "Create a quote for Acme, India, 0.02 spread"
    App->>Agent: POST /conversations (kind=create_pricing_request)
    App->>Agent: POST /conversations/{id}/runs
    App->>Agent: GET /runs/{id}/stream (SSE)

    loop Until completion or approval needed
        Agent->>LLM: stream(messages + tools)
        LLM-->>Agent: tool_use
        Agent->>PF: REST call with user's JWT
        PF-->>Agent: result
        Agent-->>App: SSE events
    end

    Note over Agent,App: write tool proposed → run pauses
    App->>Rep: Approval card (Approve / Reject / Edit)
    Rep->>App: Tap Approve
    App->>Agent: POST /runs/{id}/decisions
    Agent->>PF: Execute write + HMAC-signed audit callback
    Agent->>LLM: continue with result
    LLM-->>Agent: "Done. Quote 5042 created."
    Agent-->>App: Completion event
```

Every event the user sees is reconstructable from the durable `agent_run_events` log. A mobile that loses connection mid-run reconnects with `Last-Event-ID` and picks up where it left off.

### 11.3 The cardinal rule: agent ≠ system of record

**PriceFRAME owns the truth:**

- Quotes, corridors, customers, RBAC, audit logs, approval workflows.
- The agent **never** has a master PriceFRAME admin key.
- Every PriceFRAME call uses the end-user's own JWT, so PriceFRAME's permission middleware stays authoritative.

**The agent owns:**

- Conversations, messages, runs, events, tool-call records.
- Idempotency replays.
- Local audit mirror (`agent_audit_log`) for cross-referencing.
- Optional attachments and user memory.

Two databases. Two boundaries. Two audit logs. This separation is deliberate and prevents a bug in the agent from corrupting authoritative pricing data.

### 11.4 What v1 ships and what's deferred

| In v1 (shipped) | Deferred (roadmap) |
|---|---|
| Auth proxy (`POST /auth/login`) | Multi-conversation embeddings / RAG |
| Conversations + runs + SSE | Voice transcription pipeline (Groq Whisper exists; not wired to chat) |
| 12 tools (6 read, 6 write) | Approval tier-up (managers approve large quotes) |
| HITL approval on every write | Multi-agent (orchestrator + specialists) |
| HMAC-signed audit callbacks | Open-source MCP server |
| Gemini Vertex + Anthropic failover | Open-weight model fallback |
| `LoopBudget` ceilings | Per-user daily/monthly budgets |
| PII redaction + prompt-injection containment | Distributed tracing (OTel) |
| Postgres + Redis + (optional) MinIO + ClamAV | Production Kubernetes manifests |
| Docker Compose for prod | |

Full backlog in `docs/handbook/15-improvements.md`.

---

## Chapter 12 — High-Level Architecture

### 12.1 The 8-layer view

```mermaid
flowchart TB
  subgraph L1[1. Edge / Transport]
    NGINX[nginx<br/>TLS + SSE buffering off]
  end
  subgraph L2[2. HTTP / API]
    FastAPI[FastAPI<br/>middleware chain<br/>v1 routers]
  end
  subgraph L3[3. Orchestration]
    Dispatch[agent.dispatch.execute_run]
    Runner[ModelRunner LLM-driven]
    Loop[AgentLoop deterministic]
    Budget[LoopBudget]
    Events[Run event log]
  end
  subgraph L4[4. Reasoning]
    Router[ProviderFailoverRouter]
    Vertex[Gemini Vertex]
    Anthropic[Anthropic Claude]
  end
  subgraph L5[5. Tools]
    Registry[Tool registry<br/>permission-filtered]
    ReadTools[6 read tools]
    WriteTools[6 write tools]
  end
  subgraph L6[6. Integration]
    Client[PriceFrameClient<br/>httpx + retries + HMAC]
  end
  subgraph L7[7. Persistence]
    PG[(PostgreSQL<br/>11 tables)]
    Redis[(Redis<br/>rate limit + arq + SSE buffer)]
    S3[(S3 or MinIO<br/>attachments)]
  end
  subgraph L8[8. Observability]
    Prom[Prometheus /metrics]
    LF[Langfuse traces]
    Logs[structlog JSON to stdout]
  end

  NGINX --> FastAPI
  FastAPI --> Dispatch
  Dispatch --> Runner
  Dispatch --> Loop
  Runner --> Budget
  Runner --> Events
  Runner --> Router
  Router --> Vertex
  Router --> Anthropic
  Runner --> Registry
  Registry --> ReadTools
  Registry --> WriteTools
  ReadTools --> Client
  WriteTools --> Client
  Client -->|REST + JWT| External[(PriceFRAME)]
  FastAPI --> PG
  FastAPI --> Redis
  FastAPI --> S3
  FastAPI --> Prom
  Runner --> LF
  FastAPI --> Logs
```

Read top to bottom: requests enter at nginx, route through FastAPI, dispatch to a runner, reason via providers, act via tools, integrate with PriceFRAME, persist state, observe everything.

### 12.2 Component dependencies

| Component | Imports from |
|---|---|
| `main.py` | settings, routers, middleware, exception handlers |
| `api/v1/conversations.py` | `agent/dispatch`, idempotency helpers, models, schemas |
| `agent/dispatch.py` | `agent/runner`, `agent/loop`, `agent/history`, `provider/factory`, `priceframe/client` |
| `agent/runner.py` | provider router, `LoopBudget`, `tool_registry`, `wrap_tool_output`, `redact`, `append_run_event`, `get_system_prompt` |
| `tools/registry.py` | All 12 tool classes |
| `tools/priceframe_*.py` | `PriceFrameClient`, `tools/base` |
| `priceframe/client.py` | `httpx` (only) |
| `provider/factory.py` | `provider/gemini_vertex`, `provider/anthropic`, `provider/base` |
| `provider/gemini_vertex.py` | `google-genai` (lazy import) |
| `provider/anthropic.py` | `anthropic` (lazy import) |
| `auth/dependencies.py` | `auth/jwt`, `priceframe/client` |
| `models/agent.py` | SQLAlchemy |

The lazy imports for vendor SDKs let the service run without any LLM dependencies installed — useful for the deterministic `AgentLoop` test path.

### 12.3 Data flow for one tool call (sequence diagram)

```mermaid
sequenceDiagram
    participant Mobile
    participant API as FastAPI<br/>conversations.py
    participant Disp as execute_run
    participant Runner as ModelRunner
    participant LLM as Gemini Vertex
    participant Tool as get_quotation tool
    participant PF as PriceFrameClient
    participant PFE as PriceFRAME API
    participant DB as Postgres

    Mobile->>API: POST /messages {content}
    API->>DB: INSERT AgentMessage + AgentRun
    API->>Disp: execute_run(run_id, ctx)
    Disp->>Runner: ModelRunner(router, settings, model, priceframe).run(run, ctx, history)
    Runner->>DB: append v1.step.started
    Runner->>LLM: stream(system + history + user, tools)
    LLM-->>Runner: tool_use {get_quotation, id=42, c1}
    LLM-->>Runner: usage {tokens}
    Runner->>DB: append v1.tool.proposed
    Runner->>Tool: tool.execute(args, ctx, priceframe)
    Tool->>PF: get_json("/api/v1/quotes/42/pricing-context", jwt=ctx.jwt_raw)
    PF->>PFE: GET (httpx, retries on 5xx)
    PFE-->>PF: 200 {data: {...}}
    PF-->>Tool: response
    Tool-->>Runner: JsonOutput(data)
    Runner->>DB: append v1.tool.completed (projected output)
    Runner->>Runner: messages.append(tool_result wrapped)
    Runner->>LLM: stream(... + tool_result)
    LLM-->>Runner: text_delta "Quote 42 is..."
    Runner->>DB: append v1.message.delta + v1.run.completed
    Runner-->>API: AgentRun
    API-->>Mobile: 200 {run_id, status: completed}
```

### 12.4 Data flow with HITL approval (sequence diagram)

```mermaid
sequenceDiagram
    participant Mobile
    participant API
    participant Runner as ModelRunner
    participant DB as Postgres
    participant LLM
    participant PF as PriceFrameClient
    participant PFE as PriceFRAME

    Note over Mobile,DB: Run #1 — produces proposal
    Mobile->>API: POST /runs
    API->>Runner: run()
    Runner->>LLM: stream(...)
    LLM-->>Runner: tool_use {create_quotation, args}
    Runner->>DB: INSERT AgentToolCall (status=proposed, requires_approval=true)
    Runner->>DB: append v1.tool.proposed
    Runner->>DB: append v1.run.awaiting_decision
    Runner-->>API: return (run.status=awaiting_decision)

    Note over Mobile,DB: User reviews via SSE
    Mobile->>API: GET /runs/{id}/stream
    API-->>Mobile: SSE v1.tool.proposed + v1.run.awaiting_decision

    Note over Mobile,DB: Approval
    Mobile->>API: POST /runs/{id}/decisions {approve}
    API->>PF: POST /api/quotes (jwt + Idempotency-Key=tool_call_id)
    PF->>PFE: POST
    PFE-->>PF: {quote_id: 5042}
    PF->>PFE: POST /agent-audit-callbacks (HMAC-signed)
    PFE-->>PF: {audit_log_id: 8801}
    PF-->>API: result
    API->>DB: UPDATE AgentToolCall (status=succeeded, priceframe_audit_log_id)
    API->>DB: append v1.tool.completed
    API-->>Mobile: 200
```

Notice the **two-phase commit**: tool execution against PriceFRAME, then HMAC-signed audit callback so PriceFRAME records the agent's role. If either fails, the audit trail still captures the attempt.

### 12.5 The two-runner design (`AgentLoop` vs `ModelRunner`)

xFRAME has **two orchestration paths**, selected at runtime by `agent.dispatch.execute_run`:

| | `AgentLoop` (`agent/loop.py`) | `ModelRunner` (`agent/runner.py`) |
|---|---|---|
| Reasoning | **Deterministic** — regex `tool:{...}` directives | **LLM-driven** — provider streams `tool_use` blocks |
| Provider required? | No | Yes (Vertex or Anthropic) |
| Tool execution | Defers to `POST /runs/{id}/decisions` | Inline (reads parallel, writes serial) |
| System prompt injection | No | Yes — for `kind="create_pricing_request"` or empty history |
| Tested by | `test_agent_api.py`, `test_phase_e_api.py` | `test_runner.py`, `test_create_pricing_request_flow.py`, `test_dispatch.py` |

The dispatch logic (`agent/dispatch.py:execute_run`):

```python
async def execute_run(session, *, settings, run_id, context):
    router = build_router(settings)
    if router is None:
        # No LLM provider configured → deterministic fallback
        return await AgentLoop(settings).run(session, run_id=run_id, context=context)

    run = await session.get(AgentRun, run_id)
    history = await load_history(session, conversation_id=run.conversation_id)

    async with PriceFrameClient.from_settings(settings) as priceframe:
        runner = ModelRunner(
            router=router,
            settings=settings,
            model=settings.default_model,
            priceframe_factory=priceframe,
        )
        return await runner.run(session, run=run, context=context, history=history)
```

**Why both?** `AgentLoop` lets the system run end-to-end in tests and demos **without** provider credentials. `ModelRunner` is the production path. Production deployments set `GEMINI_VERTEX_PROJECT` (and/or `ANTHROPIC_API_KEY`) in env, which trips `settings.provider_configured` to `True` and selects `ModelRunner`.

### 12.6 What lives where (cheat sheet)

| You're looking for… | Open… |
|---|---|
| The HTTP endpoint definition | `src/xframe_agent/api/v1/<name>.py` |
| The reasoning loop | `src/xframe_agent/agent/runner.py` (LLM) or `agent/loop.py` (deterministic) |
| The runner selection logic | `src/xframe_agent/agent/dispatch.py` |
| A tool's PriceFRAME API call | `src/xframe_agent/tools/priceframe_*.py` `_execute` method |
| The PriceFRAME REST contract | `src/xframe_agent/priceframe/client.py` + PriceFRAME repo |
| The system prompt | `src/xframe_agent/agent/prompts/create_pricing_request.py` |
| A database table | `src/xframe_agent/models/agent.py` |
| An env var | `src/xframe_agent/settings.py` |
| A test case | `tests/test_<subsystem>.py` |
| The HMAC signing logic | `src/xframe_agent/priceframe/client.py:post_agent_audit_callback` |
| The provider factory | `src/xframe_agent/provider/factory.py` |
| The history loader | `src/xframe_agent/agent/history.py` |
| The error-feedback path | `src/xframe_agent/agent/runner.py` `_build_error_result`, `_record_tool_failure` |

Print this. It's the most useful page in the book.

---

## Chapter 13 — The Runtime Environment

### 13.1 Process model: API + worker + DB + Redis

In production, the xFRAME AI Agent runs as **multiple cooperating processes**:

```
┌─────────────────────────────────────────────────────────┐
│ nginx (TLS, reverse proxy)                              │
└─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ xframe-agent (uvicorn)        x N replicas              │
│   - serves HTTP API                                     │
│   - inline runs when RUN_EXECUTION_MODE=inline          │
│   - enqueues to Redis when =arq                         │
└─────────────────────────────────────────────────────────┘
            │
            ├──> PostgreSQL (state)
            │
            ├──> Redis (rate limit, arq queue, SSE buffer)
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ xframe-worker (arq)           x M replicas              │
│   - pulls run_agent_job from Redis                      │
│   - executes runs out-of-band                           │
└─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ External services                                       │
│   - PriceFRAME REST API     (over HTTPS)                │
│   - Gemini Vertex            (over HTTPS, gRPC)         │
│   - Anthropic                (over HTTPS)               │
│   - Groq Whisper             (optional, voice)          │
│   - MinIO / S3               (attachments)              │
│   - ClamAV                   (optional, virus scan)     │
│   - Langfuse                 (optional, traces)         │
└─────────────────────────────────────────────────────────┘
```

For local dev, all of this can run in one host via `docker compose up`.

### 13.2 Local vs production topology

**Local development:**

```bash
docker compose up -d postgres redis    # just the stateful pieces
uv run alembic upgrade head             # migrate schema
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

Open `http://localhost:8000/api/v1/agent/docs` for the OpenAPI UI.

**Local full stack** (everything containerized for E2E testing):

```bash
docker compose up -d                    # includes langfuse, minio, clamav
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

**Production:**

```bash
docker compose -f docker-compose.prod.yml up -d
```

`docker-compose.prod.yml` pins image tags, restarts unless stopped, mounts secrets, exposes only port 8000 to nginx.

See Part 12 (Deployment) for the full runbook.

### 13.3 nginx, SSE, and reverse-proxy contracts

The agent uses **Server-Sent Events** for the run streaming endpoint (`GET /runs/{id}/stream`). nginx needs specific settings:

```nginx
location /api/v1/agent/ {
    proxy_pass http://xframe-agent:8000;
    proxy_http_version 1.1;
    proxy_buffering off;            # essential — without this nginx buffers SSE
    proxy_read_timeout 3600s;       # runs can wait for human approval
    chunked_transfer_encoding on;   # SSE relies on chunked encoding
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Three settings matter most:

| Setting | Why |
|---|---|
| `proxy_buffering off` | Without this, nginx accumulates the entire response before forwarding. Breaks real-time streaming. |
| `proxy_read_timeout 3600s` | A run may sit in `awaiting_decision` for an hour while a user reviews. Heartbeats keep TCP alive. |
| `chunked_transfer_encoding on` | SSE responses are chunked. |

uvicorn is started with `--proxy-headers` so the X-Forwarded-* headers are trusted for rate-limit IP detection.

### 13.4 GCP service-account and provider credentials

To use Gemini Vertex (primary provider):

1. Create a GCP project (or reuse one).
2. Enable the Vertex AI API.
3. Create a service account with role `roles/aiplatform.user`.
4. Generate a JSON key, download as `secrets/gcp-sa.json`.
5. Mount as a Docker secret in `docker-compose.prod.yml`:

```yaml
xframe-agent:
  environment:
    GOOGLE_APPLICATION_CREDENTIALS: /var/run/secrets/gcp.json
    GEMINI_VERTEX_PROJECT: my-gcp-project
    GEMINI_VERTEX_LOCATION: us-central1
  secrets:
    - gcp_sa
secrets:
  gcp_sa:
    file: ./secrets/gcp-sa.json
```

For Anthropic fallback:

```yaml
ANTHROPIC_API_KEY: sk-ant-...
```

The `provider/factory.py:build_router` reads env, builds providers in priority order, returns a `ProviderFailoverRouter`.

### 13.5 Key environment variables you'll touch

The full list lives in `src/xframe_agent/settings.py`. The ones you'll touch most:

**Connection:**
```dotenv
DATABASE_URL=postgresql+asyncpg://xframe:xframe@localhost:5432/xframe_agent
REDIS_URL=redis://localhost:6379/0
```

**PriceFRAME:**
```dotenv
PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<from-priceframe-owner>
PRICEFRAME_SERVICE_SECRET=<from-priceframe-owner>
PRICEFRAME_PROFILE_CACHE_TTL_SECONDS=60
```

**LLM providers:**
```dotenv
GEMINI_VERTEX_PROJECT=my-gcp-project
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json
# OR
ANTHROPIC_API_KEY=sk-ant-...
# Optional:
DEFAULT_MODEL=gemini-2.5-flash
```

**Budgets:**
```dotenv
MAX_STEPS_PER_RUN=10
MAX_TOOL_CALLS_PER_RUN=15
MAX_INPUT_TOKENS_PER_RUN=50000
MAX_OUTPUT_TOKENS_PER_RUN=8000
COST_HARD_PER_RUN_USD=0.60
MAX_WALL_CLOCK_PER_RUN_S=60
```

**Execution mode:**
```dotenv
RUN_EXECUTION_MODE=arq    # or 'inline' for dev
ARQ_QUEUE_NAME=agent-runs
SSE_HEARTBEAT_SECONDS=15
SSE_REPLAY_EVENT_LIMIT=2000
IDEMPOTENCY_TTL_SECONDS=604800   # 7 days
```

**Observability:**
```dotenv
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=http://langfuse:3001
PROMETHEUS_ENABLED=true
```

**Security:**
```dotenv
CORS_ORIGINS=https://app.example.com,https://priceframe-yg.buy-frame.com
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60
```

Settings carrying secrets are marked `repr=False` in `Settings` so they never appear in logs or stack traces. See Chapter 63 for the full secret-handling story.

---

### 🔑 Part 2 takeaways

- xFRAME is a conversational front-end to PriceFRAME, **not** a system of record.
- 8 layers, one dispatch point (`execute_run`), two runners (`ModelRunner` for LLM-driven, `AgentLoop` for deterministic).
- The HTTP path: nginx → FastAPI middleware → router → dispatch → runner → provider/tool → DB events.
- Multi-process production: API replicas + worker replicas + Postgres + Redis + external services.
- nginx config is non-negotiable for SSE; learn the three magic settings.

### ✍️ Part 2 exercises

1. Draw the request lifecycle for `POST /messages` on paper, with **every** component the request touches before the response. Compare to §12.3.
2. Read `docker-compose.prod.yml`. Identify every service, every secret, every volume. What happens if `gcp-sa.json` is missing?
3. With the local stack up, hit `GET /api/v1/agent/health`. Then `GET /api/v1/agent/openapi.json`. Then `GET /api/v1/agent/docs`. What do each tell you?

### 📚 Part 2 further reading

- FastAPI docs — Middleware, Dependencies, OpenAPI.
- nginx — `proxy_buffering`, `proxy_read_timeout`.
- The `pgvector` README (for future RAG).

---

**End of Part 2.**

**Next:** [Part 3 — Codebase Deep Dive](./part-03-codebase-deep-dive.md).
