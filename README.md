# xFRAME Ai Agent

Python 3.12 + FastAPI service for the Sales Representative agent workflow. PriceFRAME remains the system of record; this service calls PriceFRAME's REST API with the end user's JWT so PriceFRAME RBAC is still authoritative.

## What Is In Phase E

- FastAPI app factory mounted under `/api/v1/agent`.
- PriceFRAME JWT verification with `/api/auth/profile` introspection and a 60 second session cache.
- Typed `PriceFrameClient` with JWT pass-through, retries, structured errors, generic GET/POST/PUT helpers, and HMAC-signed audit callbacks to PriceFRAME.
- Health endpoint covering provider configuration, queue, database, Redis, and PriceFRAME upstream.
- Structlog setup with secret redaction, request ID middleware, optional Redis sliding-window rate limit middleware, and Prometheus wiring.
- Alembic, SQLAlchemy async session plumbing, Dockerfile, docker compose dev stack, CI, OpenAPI snapshot export, and tests.
- Agent persistence tables for conversations, messages, runs, run events, tool calls, idempotency keys, user cache, device tokens, and audit log.
- Conversation/run REST API, SSE replay with `Last-Event-ID`, cancellation, and decision endpoints.
- Pydantic-native tool registry with permission-filtered discovery. Registered tools cover the Phase D read path, pricing previews, quote creation, corridor writes, FX spread updates, approval submission, and quote aggregate recalculation.
- Backend-owned guided setup for generic `Create a pricing request` prompts. The runner emits `v1.input.requested`; web/mobile clients submit `workflow:create_pricing_request` JSON; the runner converts that payload into a normal `create_quotation` proposal.
- Human approval decisions execute pending write tool calls, send idempotency keys to PriceFRAME, create local `agent_audit_log` rows, and call `POST /api/v1/agent-audit-callbacks`.
- Attachments API stores blobs in S3-compatible storage, tracks ClamAV scan status, and can scan inline or through arq.
- Memory API exposes user-visible memory rows, with the deterministic loop writing a small summarizer memory when the user says "remember that ...".
- Voice transcription endpoint uses Groq Whisper Large v3 Turbo when `GROQ_API_KEY` is configured.
- Provider protocol and failover router for Gemini API-key calls, Gemini Vertex, and Anthropic. The API-key provider uses `GEMINI_API_KEY` against `generativelanguage.googleapis.com` and fails closed when `ALLOW_REAL_DATA=true`.
- arq worker entry point for durable run execution and a synthetic eval harness with five golden traces.

Provider order is `GEMINI_API_KEY` first, then Gemini Vertex, then Anthropic fallback.

## Stack

- Python 3.12
- FastAPI + Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x async + Alembic
- Redis + arq
- structlog, Langfuse, Prometheus
- httpx for PriceFRAME calls
- PyJWT for PriceFRAME HS256 token verification
- boto3 for MinIO/S3-compatible attachment storage
- python-multipart for upload endpoints

## Local Setup

```bash
uv sync --extra dev
cp .env.example .env
docker compose up -d postgres redis langfuse-db langfuse minio
uv run alembic upgrade head
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

For asynchronous attachment scanning set:

```bash
ATTACHMENT_SCAN_MODE=arq
CLAMAV_ENABLED=true
docker compose up -d clamav redis
uv run arq xframe_agent.worker.WorkerSettings
```

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
- `clamav`: optional malware scanner for attachment scan jobs.

## Module Map

- `src/xframe_agent/main.py`: FastAPI app factory.
- `src/xframe_agent/settings.py`: environment-driven configuration.
- `src/xframe_agent/logging.py`: structlog and redaction processors.
- `src/xframe_agent/auth/`: JWT verification and PriceFRAME profile introspection.
- `src/xframe_agent/priceframe/`: PriceFRAME REST client.
- `src/xframe_agent/agent/`: run loop, durable events, idempotency helpers.
- `src/xframe_agent/agent/guided_workflows.py`: chat UI workflow contracts and submission parsing.
- `src/xframe_agent/attachments/`: blob storage and ClamAV scan helpers.
- `src/xframe_agent/tools/`: Pydantic-native tool definitions and registry.
- `src/xframe_agent/provider/`: provider protocol and adapter shells.
- `src/xframe_agent/api/v1/`: versioned HTTP routers.
- `src/xframe_agent/db/`: SQLAlchemy async engine/session primitives.
- `src/xframe_agent/middleware/`: request ID and rate limiting.
- `src/xframe_agent/observability/`: Langfuse and Prometheus integration.
- `evals/`: synthetic golden trace harness for CI.
