# Phase B — xframe-ai-agent Bootstrap

**Date:** 2026-05-19  
**Scope:** runnable Python/FastAPI service skeleton only. No tools, agent loop, run streaming, attachments, or LLM calls yet.

## Built

- Created the Phase B service under `/Users/bhairava/WorkSpace/repos/xframe-ai-agent`.
- Replaced the pre-existing placeholder scaffold because it referenced MCP, LangGraph, WorkOS, and OpenAI, which conflict with the committed v1 architecture.
- Added Python 3.12 project config with Hatch build metadata, `uv.lock`, ruff, strict mypy, pytest, and pre-commit hooks.
- Added FastAPI app factory mounted under `/api/v1/agent`, with OpenAPI at `/api/v1/agent/openapi.json` and committed snapshot `openapi.yaml`.
- Added settings via `pydantic-settings`, structlog JSON logging with secret redaction, CORS, request ID middleware, optional Redis sliding-window rate limiting, Prometheus wiring, and Langfuse client factory.
- Added SQLAlchemy 2.x async engine/session primitives, Alembic environment, and empty `models/` / `schemas/` packages for later phases.
- Added PriceFRAME JWT verification using `PyJWT`, plus cached `/api/auth/profile` introspection with 60s TTL. The cache records the resolved `(user_id, session_id)` from PriceFRAME's profile payload and also avoids repeated introspection for the same JWT.
- Added typed `PriceFrameClient.get_profile()` with JWT pass-through, retries, and structured upstream errors.
- Added `GET /api/v1/agent/health` covering provider configuration, DB, Redis/arq queue substrate, and PriceFRAME upstream.
- Added Dockerfile and `docker-compose.yml` for Postgres 16 + pgvector, Redis, self-hosted Langfuse, and MinIO.
- Added GitHub Actions CI for ruff format, ruff lint, mypy, pytest, and OpenAPI snapshot drift.

## Deferred

- Agent persistence migrations and ORM models land in Phase D.
- Provider adapters, tool registry, 7 MVP tools, arq worker, SSE run streaming, eval harness, and Langfuse traces for actual model/tool calls land in Phase D.
- Attachments, voice, memory, write tools, and audit callbacks land in Phase E.
- No PriceFRAME code was changed in Phase B.

## How To Run

```bash
cd /Users/bhairava/WorkSpace/repos/xframe-ai-agent
uv sync --extra dev
cp .env.example .env
docker compose up -d postgres redis langfuse-db langfuse minio
uv run uvicorn xframe_agent.main:app --reload --host 0.0.0.0 --port 8000
```

Health:

```bash
curl http://localhost:8000/api/v1/agent/health
```

Verification:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_openapi.py
git diff --exit-code openapi.yaml
```

## Notes

- `PyJWT` is the only runtime dependency added beyond the Phase B stack because PriceFRAME uses JWTs and the agent needs safe HS256 verification without hand-rolled crypto.
- The local `xframe-ai-agent` directory did not start as a git repository; Phase B initialized it on branch `phase-B/bootstrap`.
