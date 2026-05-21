# Part 17 — Appendices

> Reference material: setup commands, scripts, CLI cheat sheets, API examples, dependency rationale, exercise solutions, and an index.

---

## Appendix A — Setup Commands Cheat Sheet

### A.1 First-time setup

```bash
git clone https://github.com/dev-yoka/xframe-ai-agent.git
cd xframe-ai-agent
uv sync --extra dev
cp .env.example .env
docker compose up -d postgres redis
uv run alembic upgrade head
```

### A.2 Run the API (dev)

```bash
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

Open `http://localhost:8000/api/v1/agent/docs`.

### A.3 Run tests

```bash
uv run pytest                                       # all tests
uv run pytest tests/test_runner.py -v               # one file
uv run pytest tests/test_runner.py::test_X -v       # one test
uv run pytest --cov=src/xframe_agent                # with coverage
uv run pytest -n auto                               # parallel
```

### A.4 Static checks (CI gate)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run python scripts/export_openapi.py
git diff --exit-code openapi.yaml
```

### A.5 Local full stack

```bash
docker compose up -d   # adds langfuse, minio, clamav
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

Useful URLs:

- Agent: `http://localhost:8000/api/v1/agent/docs`
- Langfuse: `http://localhost:3001`
- MinIO console: `http://localhost:9001`

### A.6 Worker (arq)

```bash
uv run arq xframe_agent.worker.WorkerSettings
```

### A.7 Database admin

```bash
# Connect
psql postgres://xframe:xframe@localhost:5433/xframe_agent

# Migrate
uv run alembic upgrade head
uv run alembic current
uv run alembic history

# New migration
uv run alembic revision -m "add foo to bar"
```

### A.8 Production deploy

```bash
docker build -t xframe-agent:v1 .
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f xframe-agent
```

### A.9 Generate test JWT (dev only)

```python
import jwt
token = jwt.encode(
    {"user_id": 1, "role_id": 1, "profile_id": 1, "session_id": 1, "exp": 9999999999},
    "x" * 32,  # match PRICEFRAME_JWT_SECRET in .env
    algorithm="HS256",
)
print(token)
```

⚠️ **For dev only**. Never use static test JWTs in production.

---

## Appendix B — Useful Scripts and Shell Aliases

### B.1 `scripts/entrypoint.sh`

```bash
#!/bin/bash
set -e
echo "Running database migrations..."
uv run alembic upgrade head
echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

### B.2 `scripts/export_openapi.py`

```python
"""Export the FastAPI OpenAPI schema to openapi.yaml."""
import yaml
from xframe_agent.main import create_app

app = create_app()
openapi = app.openapi()
with open("openapi.yaml", "w") as f:
    yaml.dump(openapi, f, sort_keys=False)
```

Run via `uv run python scripts/export_openapi.py`.

### B.3 Useful aliases (add to your shell rc)

```bash
alias xpytest="uv run pytest"
alias xlint="uv run ruff format --check . && uv run ruff check . && uv run mypy"
alias xup="docker compose up -d postgres redis && uv run alembic upgrade head && uv run uvicorn xframe_agent.main:app --reload --port 8000"
alias xpsql="psql postgres://xframe:xframe@localhost:5433/xframe_agent"
alias xredis="redis-cli"
```

### B.4 Backup script

```bash
#!/bin/bash
# scripts/backup.sh
set -e
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR=/var/backups/xframe
mkdir -p "$BACKUP_DIR"

docker compose -f docker-compose.prod.yml exec -T postgres \
    pg_dump -U xframe xframe_agent | gzip > "$BACKUP_DIR/db-$TIMESTAMP.sql.gz"

# Upload to S3 (optional)
# aws s3 cp "$BACKUP_DIR/db-$TIMESTAMP.sql.gz" s3://my-backups/xframe/

# Prune old backups (keep 30 days)
find "$BACKUP_DIR" -name "db-*.sql.gz" -mtime +30 -delete

echo "Backup completed: db-$TIMESTAMP.sql.gz"
```

### B.5 SQL inspection helpers

```sql
-- Recent runs by status
SELECT status, COUNT(*) FROM agent_runs
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;

-- Top tools called this week
SELECT payload->>'tool_name' AS tool, COUNT(*)
FROM agent_run_events
WHERE event_type = 'v1.tool.completed'
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY tool ORDER BY COUNT(*) DESC;

-- Recent errors
SELECT created_at, payload->>'cause' AS cause, payload->>'message' AS msg
FROM agent_run_events
WHERE event_type = 'v1.run.error'
  AND created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
```

---

## Appendix C — CLI References

### C.1 `uv`

```bash
uv sync                    # install deps from uv.lock
uv sync --extra dev        # include dev deps
uv add fastapi             # add a runtime dep
uv add --dev pytest        # add a dev dep
uv remove old_pkg          # remove a dep
uv run <cmd>               # run cmd in the project venv
uv pip list                # list installed packages
uv lock                    # update uv.lock from pyproject.toml
```

### C.2 `alembic`

```bash
alembic current              # show current revision
alembic history              # show all revisions
alembic upgrade head         # apply all pending
alembic upgrade +1           # apply next one
alembic downgrade -1         # revert one
alembic revision -m "msg"    # create new (manual)
alembic revision --autogenerate -m "msg"   # auto-generate (review carefully)
alembic stamp <rev>          # mark as if applied (no actual changes)
```

### C.3 `arq`

```bash
arq xframe_agent.worker.WorkerSettings   # run worker
# inspect queue
redis-cli LLEN agent-runs
redis-cli KEYS 'arq:*'
```

### C.4 `docker compose`

```bash
docker compose up -d                          # bring up in background
docker compose up -d <service>                # one service
docker compose down                           # stop + remove
docker compose down -v                        # also remove volumes (data loss!)
docker compose logs -f <service>              # follow logs
docker compose ps                             # list services
docker compose exec <service> <cmd>           # run command in container
docker compose -f docker-compose.prod.yml up -d   # use alternate file
```

### C.5 `kubectl` (for k8s deploys)

```bash
kubectl get pods -n xframe                    # list pods
kubectl logs -f -n xframe deploy/xframe-agent # follow logs
kubectl describe pod -n xframe <pod>          # debug pod
kubectl rollout status deploy/xframe-agent    # check rollout
kubectl rollout undo deploy/xframe-agent      # rollback
kubectl exec -it -n xframe <pod> -- bash      # shell in pod
```

### C.6 `psql`

```bash
psql postgres://xframe:xframe@localhost:5433/xframe_agent

# Inside psql:
\dt                     # list tables
\d agent_runs           # describe table
\timing on              # show query times
\watch 5                # repeat last query every 5s
\q                      # quit
```

---

## Appendix D — API Reference (curl examples)

### D.1 Login

```bash
curl -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}'
```

Response:

```json
{
  "token": "eyJ...",
  "user": {"id": 1, "email": "admin@priceframe.local"},
  "role_code": "ROLE_ADMIN",
  "profile_code": "PROFILE_ADMIN",
  "permissions": ["agent.enabled", "agent.quotes.read", ...],
  "expires_at": 1716290000
}
```

Save the token:

```bash
TOKEN=$(curl -s ... | jq -r .token)
```

### D.2 List tools

```bash
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/v1/agent/tools | jq
```

### D.3 Create conversation

```bash
curl -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"title":"My quote","kind":"create_pricing_request"}'
```

### D.4 Send a message (with inline run)

```bash
CONV=01HX...
curl -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Show me my open quotes","source":"text"}'
```

Response (after the run completes):

```json
{"run_id": "01HX...", "status": "completed"}
```

### D.5 Stream a run

```bash
RUN=01HX...
curl -N -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/v1/agent/runs/$RUN/stream
```

You'll see SSE events stream in real time:

```
id: 1
event: v1.step.started
data: {"run_id":"01HX...","seq":1,"ts":"...","step":1,"kind":"model_call"}

id: 2
event: v1.message.delta
data: {...}

...
```

### D.6 Approve a proposed tool call

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs/$RUN/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"tool_call_id":"01HX...","decision":"approve"}'
```

### D.7 Reject

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs/$RUN/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"tool_call_id":"01HX...","decision":"reject"}'
```

### D.8 Edit and approve

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs/$RUN/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "tool_call_id":"01HX...",
        "decision":"edit",
        "edited_args":{"title":"Revised","customer_id":42,"currency":"USD"}
    }'
```

### D.9 Cancel a run

```bash
curl -X POST http://localhost:8000/api/v1/agent/runs/$RUN/cancel \
    -H "Authorization: Bearer $TOKEN"
```

### D.10 Health check

```bash
curl http://localhost:8000/api/v1/agent/health
```

### D.11 OpenAPI spec

```bash
curl http://localhost:8000/api/v1/agent/openapi.json | jq
```

Or open `http://localhost:8000/api/v1/agent/docs` in a browser for interactive Swagger.

---

## Appendix E — Dependency Rationale

### E.1 Core runtime

| Package | Purpose | Why this one |
|---|---|---|
| `fastapi` | HTTP framework | Async-native, OpenAPI auto-gen, dep injection |
| `uvicorn` | ASGI server | Production-grade async server |
| `pydantic` v2 | Data validation | Generates JSON Schema from models; type-checked |
| `pydantic-settings` | Env var loading | Companion to Pydantic |
| `sqlalchemy[asyncio]` | ORM | Best async Python ORM as of 2026 |
| `asyncpg` | Postgres driver | Fast async Postgres |
| `aiosqlite` | SQLite async (tests only) | Lightweight test DB |
| `alembic` | Migrations | Standard for SQLAlchemy |
| `httpx` | HTTP client | Async, modern; replaces `requests` |
| `pyjwt` | JWT verification | Standard JWT library |
| `redis[asyncio]` | Redis client | Async Redis access |
| `arq` | Job queue | Best async Python job queue |
| `structlog` | Logging | JSON output; binds context vars |
| `sse-starlette` | SSE responses | Wraps async generators as SSE |
| `prometheus-fastapi-instrumentator` | Metrics | Standard middleware for FastAPI metrics |

### E.2 Optional LLM providers (lazy-imported)

| Package | Why optional | Activated by |
|---|---|---|
| `google-genai` | Big SDK; not all deployments use Vertex | `GEMINI_VERTEX_PROJECT` set |
| `anthropic` | Big SDK; not all deployments use Anthropic | `ANTHROPIC_API_KEY` set |

Both are imported only when the corresponding provider class is instantiated, keeping the base install lean.

### E.3 Optional infra

| Package | When you need it |
|---|---|
| `boto3` / `aiobotocore` | S3 attachment storage |
| `pgvector` | RAG (future) |
| `langfuse` | LLM tracing (optional observability) |

### E.4 Dev deps

| Package | Purpose |
|---|---|
| `pytest`, `pytest-asyncio` | Test runner |
| `ruff` | Linter + formatter (replaces flake8 + black + isort) |
| `mypy` | Static type checker |
| `pytest-cov` | Coverage reports |
| `hypothesis` (roadmap) | Property-based tests |
| `locust` (recommended for load) | Load testing |

### E.5 Why `uv` not pip

- 10-100× faster than pip for resolve + install.
- One tool: replaces pip, pip-tools, virtualenv.
- Reproducible: `uv.lock` pins everything.
- Same API as pip (most commands map directly).

### E.6 Why no LangChain/LangGraph

xFRAME implements the agent loop directly. LangChain is widely used but:

- Adds a heavy dependency.
- The abstractions can obscure what's actually happening.
- xFRAME's loop is ~600 lines; LangChain would be more.

Direct implementation keeps the code transparent and maintainable.

---

## Appendix F — Exercise Solutions

Selected exercises from each part. Full set in the per-part files.

### F.1 Part 1 — Classify chatbot/workflow/agent

(a) Weather app calling one API: **workflow** (engineer-orchestrated; LLM might be inside but doesn't decide).

(b) Cursor's agent mode: **agent** (LLM decides which files to edit, in what order).

(c) GitHub's PR template auto-fill: **chatbot** (one-shot prompt → reply).

(d) ChatGPT with web search: **agent** (LLM decides when to search; multi-step).

### F.2 Part 2 — Health endpoint exploration

```bash
curl http://localhost:8000/api/v1/agent/health | jq
```

Returns `{status, version, components: {database, redis, ...}}`. Health "ok" or "degraded." Used by Docker `HEALTHCHECK` and k8s probes.

### F.3 Part 3 — Add `cancel_quotation`

Full code in Chapter 51. ~50 lines total: input model + tool class + registry entry + 3 tests.

### F.4 Part 6 — System prompt cost at 10 turns

Setup tokens (system + tools): ~2,500 per turn.
History grows ~200 tokens per turn → 200 + 400 + ... ≈ 10,000 over 10 turns.

Without caching: 10 × 2,500 + history_aggregate = ~35,000 input tokens.
At $0.10/M Gemini Flash: ~$0.0035.

With 90% caching on the structural part: ~$0.0006. **5-6× savings**.

### F.5 Part 9 — Recent terminal events query

```sql
SELECT e.event_type, e.created_at, r.user_id, e.payload->>'cause' AS cause
FROM agent_run_events e
JOIN agent_runs r ON r.id = e.run_id
WHERE e.event_type LIKE 'v1.run.%'
  AND e.created_at > NOW() - INTERVAL '1 hour'
ORDER BY e.created_at DESC
LIMIT 5;
```

### F.6 Part 11 — Hypothesis test for `redact`

```python
from hypothesis import given, strategies as st
from xframe_agent.agent.redaction import redact

@given(st.text())
def test_redact_no_email_survives(text):
    out = redact(text).text
    assert "@" not in out or "<PII:email>" in out
```

May find edge cases like Unicode `@` variants or escape sequences. Investigate and refine the regex if so.

---

## Appendix G — Index

The book is too long for a per-term index, but here are pointers to where major topics live.

| Topic | Primary location |
|---|---|
| AI agents (fundamentals) | Part 1, Chapter 1 |
| LLMs (fundamentals) | Part 1, Chapter 2 |
| Tokens and context | Part 1, Chapter 3 |
| Embeddings | Part 1, Chapter 4 |
| Prompt engineering | Part 1, Chapter 5; Part 6 (deep) |
| Tool calling | Part 1, Chapter 6; Part 7 (deep) |
| RAG | Part 1, Chapter 7; Part 5 (deep) |
| Multi-agent + MCP | Part 1, Chapter 8; Part 14 §92, §97 |
| Memory / reasoning | Part 1, Chapter 9; Part 14 §94 |
| Safety | Part 1, Chapter 10; Part 10 (deep) |
| Project overview | Part 2 |
| Codebase walkthrough | Part 3 |
| Agent architecture | Part 4 |
| Frontend / UX | Part 8 |
| Databases / storage | Part 9 |
| Security threats | Part 10 |
| Testing patterns | Part 11 §69-70 |
| Eval / golden traces | Part 11 §71 |
| Debugging stuck runs | Part 11 §72 |
| Langfuse | Part 11 §73 |
| Deployment | Part 12 |
| Scaling | Part 13 |
| Reflection | Part 14 §91 |
| Fine-tuning | Part 14 §96 |
| Improvements (shipped + planned) | Part 15 |
| Glossary | Part 16 |

---

## Closing words

You've read 100+ chapters across 17 parts. You now understand:

- **What AI agents are** — and why this specific architecture made each trade-off.
- **The xFRAME codebase** — every file, every pattern, every decision.
- **How to operate it** — debug, deploy, scale, secure.
- **How to extend it** — new tools, new prompts, new flows.
- **Where it's going** — the roadmap and long-term vision.

That's a lot. But it's also finite. The system is built to be understood, and now you do.

Build something with it.

---

**End of Part 17.**

**End of book.**

---

> *"The best documentation is the code itself. The next best is documentation that points you back to the code, accurately and lovingly."*

> *— Inspired by half a century of software engineering wisdom.*
