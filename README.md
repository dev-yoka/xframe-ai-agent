# xFRAME Ai Agent

Python 3.12 + FastAPI service for the Sales Representative agent workflow. PriceFRAME remains the system of record; this service calls PriceFRAME's REST API with the end user's JWT so PriceFRAME RBAC is still authoritative.

## What Is In Phase B

- FastAPI app factory mounted under `/api/v1/agent`.
- PriceFRAME JWT verification with `/api/auth/profile` introspection and a 60 second session cache.
- Typed `PriceFrameClient.get_profile()` with JWT pass-through, retries, and structured errors.
- Health endpoint covering provider configuration, queue, database, Redis, and PriceFRAME upstream.
- Structlog setup with secret redaction, request ID middleware, optional Redis sliding-window rate limit middleware, and Prometheus wiring.
- Alembic, SQLAlchemy async session plumbing, Dockerfile, docker compose dev stack, CI, OpenAPI snapshot export, and tests.

No tools, agent loop, SSE run streaming, attachments, or provider calls are implemented in Phase B.

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
- `src/xframe_agent/api/v1/`: versioned HTTP routers.
- `src/xframe_agent/db/`: SQLAlchemy async engine/session primitives.
- `src/xframe_agent/middleware/`: request ID and rate limiting.
- `src/xframe_agent/observability/`: Langfuse and Prometheus integration.
