# xFRAME Ai Agent

Python 3.12 + FastAPI service for the Sales Representative agent workflow. PriceFRAME remains the system of record; this service calls PriceFRAME's REST API with the end user's JWT so PriceFRAME RBAC is still authoritative.

## What Is In Phase D

- FastAPI app factory mounted under `/api/v1/agent`.
- PriceFRAME JWT verification with `/api/auth/profile` introspection and a 60 second session cache.
- Typed `PriceFrameClient` with JWT pass-through, retries, structured errors, and generic GET/POST helpers for tools.
- Health endpoint covering provider configuration, queue, database, Redis, and PriceFRAME upstream.
- Structlog setup with secret redaction, request ID middleware, optional Redis sliding-window rate limit middleware, and Prometheus wiring.
- Alembic, SQLAlchemy async session plumbing, Dockerfile, docker compose dev stack, CI, OpenAPI snapshot export, and tests.
- Agent persistence tables for conversations, messages, runs, run events, tool calls, idempotency keys, user cache, device tokens, and audit log.
- Conversation/run REST API, SSE replay with `Last-Event-ID`, cancellation, and decision endpoints.
- Pydantic-native tool registry with permission-filtered discovery; Phase D registers the read path plus `recalculate_quote_aggregates`.
- Provider protocol and failover router shells for Gemini Vertex, Gemini AI Studio, and Anthropic. AI Studio fails closed when `ALLOW_REAL_DATA=true`.
- arq worker entry point for durable run execution and a synthetic eval harness with five golden traces.

Phase D deliberately keeps write tools scaffolded but unregistered until Phase E confirmation/audit work. `preview_pricing_change` is also scaffolded but skipped until PriceFRAME delta-PR #2 exists.

## Stack

- Python 3.12
- FastAPI + Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x async + Alembic
- Redis + arq
- structlog, Langfuse, Prometheus
- httpx for PriceFRAME calls
- PyJWT for PriceFRAME HS256 token verification

## Local Setup

```bash
uv sync --extra dev
cp .env.example .env
docker compose up -d postgres redis langfuse-db langfuse minio
uv run uvicorn xframe_agent.main:app --reload --host 0.0.0.0 --port 8000
```

The OpenAPI contract is exposed at:

```text
http://localhost:8000/api/v1/agent/openapi.json
```

The health endpoint is:

```text
http://localhost:8000/api/v1/agent/health
```

Start the arq worker when `RUN_EXECUTION_MODE=arq`:

```bash
uv run arq xframe_agent.worker.WorkerSettings
```

Local tests use `RUN_EXECUTION_MODE=inline` so runs complete inside the request.

## Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_openapi.py
git diff --exit-code openapi.yaml
```

## Dev Services

`docker-compose.yml` starts:

- `postgres`: Postgres 16 with pgvector for the agent database.
- `redis`: Redis for arq, rate limits, and future SSE buffers.
- `langfuse-db` + `langfuse`: self-hosted Langfuse for local traces.
- `minio`: S3-compatible object storage for later attachment work.

## Module Map

- `src/xframe_agent/main.py`: FastAPI app factory.
- `src/xframe_agent/settings.py`: environment-driven configuration.
- `src/xframe_agent/logging.py`: structlog and redaction processors.
- `src/xframe_agent/auth/`: JWT verification and PriceFRAME profile introspection.
- `src/xframe_agent/priceframe/`: PriceFRAME REST client.
- `src/xframe_agent/agent/`: run loop, durable events, idempotency helpers.
- `src/xframe_agent/tools/`: Pydantic-native tool definitions and registry.
- `src/xframe_agent/provider/`: provider protocol and adapter shells.
- `src/xframe_agent/api/v1/`: versioned HTTP routers.
- `src/xframe_agent/db/`: SQLAlchemy async engine/session primitives.
- `src/xframe_agent/middleware/`: request ID and rate limiting.
- `src/xframe_agent/observability/`: Langfuse and Prometheus integration.
- `evals/`: synthetic golden trace harness for CI.
