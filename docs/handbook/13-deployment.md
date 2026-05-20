# 13 — Deployment & Infrastructure

> **Reading this section answers:** how do you get this running? Locally, in Docker, in production? How do you upgrade and roll back?

This section is a runbook companion to [`docs/deploy/v1-deployment.md`](../deploy/v1-deployment.md) and [`docs/deploy/provider-setup.md`](../deploy/provider-setup.md). It explains the *why* behind each step.

## 13.1 Three deployment shapes

| Shape | When | Stack |
|---|---|---|
| **Local dev** | Day-to-day engineering | `uv run uvicorn ...` + `docker compose up postgres redis` |
| **Local full stack** | Manual E2E testing | `docker compose up` (all services) |
| **Production** | Real users | `docker-compose.prod.yml` behind nginx + TLS |

## 13.2 Local dev

### 13.2.1 First-time setup

```bash
git clone <repo> && cd xframe-ai-agent
uv sync --extra dev
cp .env.example .env
docker compose up -d postgres redis
uv run alembic upgrade head
```

`.env` defaults work for local Postgres on port 5433 (note: docker-compose.yml maps host:5433 → container:5432 to avoid conflict with a local Postgres on 5432).

### 13.2.2 Run

```bash
uv run uvicorn xframe_agent.main:app --reload --port 8000
# Open http://localhost:8000/api/v1/agent/docs
```

The `--reload` flag watches `src/` and restarts on save.

### 13.2.3 Tests

```bash
uv run pytest -v                      # all tests
uv run pytest tests/test_runner.py    # one file
uv run ruff check . && uv run mypy   # static gate
```

## 13.3 Local full stack

`docker-compose.yml` brings up:

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 5433 → 5432 | Main DB + Langfuse DB |
| `redis` | `redis:7.4-alpine` | 6379 | Rate limit + arq + SSE buffer |
| `langfuse-db` | `postgres:16-alpine` | internal | Langfuse |
| `langfuse` | `langfuse/langfuse:2` | 3001 | Trace UI |
| `minio` | `minio/minio:RELEASE...` | 9000/9001 | S3-compatible attachment store |
| `clamav` | `clamav/clamav:stable` | 3310 | Optional virus scan |

```bash
docker compose up -d
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

Useful URLs:

- Agent: `http://localhost:8000/api/v1/agent/docs`
- Langfuse: `http://localhost:3001` (sign up for keys, set in `.env`)
- MinIO Console: `http://localhost:9001` (login `minioadmin`/`minioadmin`)

## 13.4 Production (Docker Compose)

`docker-compose.prod.yml` is intended for a single-host deployment. For Kubernetes, see §13.10.

### 13.4.1 Services

```yaml
postgres:       # pgvector:pg16, restart: unless-stopped
redis:          # redis:7.4-alpine
clamav:         # clamav:stable
xframe-agent:   # built image; env_file: .env.production
```

### 13.4.2 Build the image

```bash
docker build -t xframe-agent:v1 .
```

The `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --no-dev --no-install-project
RUN uv pip install --system .
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod +x ./scripts/entrypoint.sh
EXPOSE 8000
CMD ["bash", "scripts/entrypoint.sh"]
```

`scripts/entrypoint.sh`:

```bash
#!/bin/bash
set -e
echo "Running database migrations..."
uv run alembic upgrade head
echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

`--proxy-headers` lets uvicorn trust `X-Forwarded-*` from nginx so `request.client.host` becomes the real client IP (essential for rate limiting).

### 13.4.3 `.env.production`

Required minimum:

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://xframe:CHANGEME@postgres:5432/xframe_agent
REDIS_URL=redis://redis:6379/0

PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<from-priceframe-owner>
PRICEFRAME_SERVICE_SECRET=<from-priceframe-owner>

CORS_ORIGINS=https://priceframe-yg.buy-frame.com,https://app.example.com

# Provider: at least one of:
GEMINI_VERTEX_PROJECT=my-gcp-project
GEMINI_VERTEX_LOCATION=us-central1
ANTHROPIC_API_KEY=sk-ant-...

RUN_EXECUTION_MODE=arq
```

### 13.4.4 GCP service account secret mount

`docker-compose.prod.yml:`

```yaml
xframe-agent:
  environment:
    GOOGLE_APPLICATION_CREDENTIALS: /var/run/secrets/gcp.json
  secrets:
    - gcp_sa
  ...
secrets:
  gcp_sa:
    file: ./secrets/gcp-sa.json
```

The host file `./secrets/gcp-sa.json` is mounted into the container at `/var/run/secrets/gcp.json`. Vertex SDK reads this path via `GOOGLE_APPLICATION_CREDENTIALS`.

**Required GCP role:** `roles/aiplatform.user` on the SA.

### 13.4.5 Bring it up

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f xframe-agent
```

The entrypoint runs `alembic upgrade head` automatically on every container start — safe because Alembic migrations are idempotent.

## 13.5 nginx in front

A working nginx fragment (full version in `docs/deploy/v1-deployment.md`):

```nginx
location /api/v1/agent/ {
    proxy_pass http://xframe-agent:8000;
    proxy_http_version 1.1;

    # Required for SSE
    proxy_buffering off;
    proxy_read_timeout 3600s;
    chunked_transfer_encoding on;

    # Headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Why each setting?**

- `proxy_buffering off` — without this, nginx batches SSE responses, breaking real-time streaming.
- `proxy_read_timeout 3600s` — runs may legitimately stay open while awaiting decision. The heartbeat every 15s keeps the TCP connection healthy.
- `chunked_transfer_encoding on` — SSE relies on chunked encoding.
- `X-Forwarded-For` — combined with `--proxy-headers` on uvicorn, gives the real client IP to rate limiting.

## 13.6 Provider setup

See `docs/deploy/provider-setup.md` for the full step-by-step. Summary:

### 13.6.1 Gemini Vertex (primary)

1. **GCP project**: create or pick one with billing.
2. **Enable Vertex AI API**.
3. **Service account**: IAM → Service Accounts → Create → role `roles/aiplatform.user`.
4. **Key**: download as JSON, place at `./secrets/gcp-sa.json`.
5. **Env vars**:
   ```
   GEMINI_VERTEX_PROJECT=my-project
   GEMINI_VERTEX_LOCATION=us-central1
   GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json
   ```
6. **Smoke test**:
   ```bash
   docker exec xframe-agent uv run python -c "
   from xframe_agent.provider.gemini_vertex import GeminiVertexProvider
   from xframe_agent.settings import Settings
   p = GeminiVertexProvider(Settings())
   print(p.name, 'ok')
   "
   ```

### 13.6.2 Anthropic (fallback)

1. **API key** from Anthropic console.
2. **Env**: `ANTHROPIC_API_KEY=sk-ant-...`
3. **Smoke test**: similar pattern with `AnthropicProvider`.

### 13.6.3 Router order

`provider/__init__.py` (or wherever `ProviderFailoverRouter` is instantiated) decides order. Typical:

```python
providers = []
if settings.gemini_vertex_project: providers.append(GeminiVertexProvider(settings))
if settings.anthropic_api_key: providers.append(AnthropicProvider(settings))
router = ProviderFailoverRouter(providers=providers, unhealthy_seconds=300)
```

Order is by code, not env — verify the construction site after any change.

## 13.7 Worker process (arq)

When `RUN_EXECUTION_MODE=arq`, runs are dispatched to a background worker:

```bash
# In production:
docker run --rm --env-file .env.production xframe-agent:v1 \
  uv run arq xframe_agent.worker.WorkerSettings
```

Or add a worker service to `docker-compose.prod.yml`:

```yaml
xframe-worker:
  image: xframe-agent:v1
  command: ["uv", "run", "arq", "xframe_agent.worker.WorkerSettings"]
  env_file: .env.production
  depends_on: [postgres, redis]
  restart: unless-stopped
```

**Scaling:** add more containers. Each runs up to `max_jobs=4` concurrent jobs by default. Tune in `worker.WorkerSettings`.

## 13.8 Upgrade flow

```mermaid
sequenceDiagram
    participant CI as GitHub Actions
    participant Reg as Image registry
    participant Host as Production host
    CI->>CI: tests + lint + mypy + openapi
    CI->>Reg: push xframe-agent:v1.2
    Host->>Reg: pull xframe-agent:v1.2
    Host->>Host: docker compose -f prod.yml up -d xframe-agent
    Note over Host: entrypoint runs alembic upgrade head
    Host->>Host: health check passes
    Host->>Host: previous container shut down
```

**Key properties:**

- Migrations run inside the new container's entrypoint **before** uvicorn starts → if migrations fail, the new container never serves traffic.
- Old container keeps serving until the new one is healthy.
- Zero downtime if you have ≥2 instances behind a load balancer.

## 13.9 Rollback

```bash
# Find prior image tag
docker images xframe-agent

# Roll back container
docker compose -f docker-compose.prod.yml stop xframe-agent
# edit docker-compose.prod.yml: image: xframe-agent:v1.1
docker compose -f docker-compose.prod.yml up -d xframe-agent
```

**Migration rollback is RISKIER.** Forward migrations are tested; downgrade scripts may not have parity. Preferred approach:

1. Make schema changes additive (V1 migrations all are).
2. Deploy code that tolerates both old and new schema.
3. After bake-in, deploy code that requires the new schema.
4. Rollback is "redeploy the prior code"; the schema stays.

For destructive migrations (rare), test the `alembic downgrade` path before deploying.

## 13.10 Kubernetes (sketch)

Not provided in repo today. A reasonable shape:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: xframe-agent }
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: agent
          image: xframe-agent:v1
          envFrom:
            - secretRef: { name: xframe-agent-secrets }
          ports: [{ containerPort: 8000 }]
          readinessProbe:
            httpGet: { path: /api/v1/agent/health, port: 8000 }
            initialDelaySeconds: 30
            periodSeconds: 10
          resources:
            requests: { memory: 512Mi, cpu: 250m }
            limits:   { memory: 1Gi,   cpu: 1000m }
          volumeMounts:
            - { name: gcp-sa, mountPath: /var/run/secrets, readOnly: true }
      volumes:
        - name: gcp-sa
          secret: { secretName: gcp-sa }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: xframe-worker }
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: worker
          image: xframe-agent:v1
          command: ["uv","run","arq","xframe_agent.worker.WorkerSettings"]
          envFrom: [{ secretRef: { name: xframe-agent-secrets } }]
```

Plus an Ingress with the nginx annotations from §13.5 for SSE.

## 13.11 Scaling considerations

| Bottleneck | Scale strategy |
|---|---|
| HTTP throughput | More API container replicas |
| Run throughput | More arq worker replicas (each `max_jobs=4`) |
| LLM RPS | Provider quota; may need to request increase |
| PriceFRAME RPS | Cache profile longer; reduce parallel reads in `max_parallel_tool_calls` |
| Postgres | Use a managed Postgres with read replicas for analytics queries; main DB is small (just conversations/runs/events) |
| Redis | Single instance OK for moderate scale; cluster for very high scale |
| SSE FDs | Use uvicorn workers; tune `ulimit -n`; monitor `process_open_fds` |

## 13.12 Disaster recovery

| What | Procedure |
|---|---|
| Agent DB corruption | Restore from latest Postgres backup; replay runs is not possible (LLM outputs not reproducible) |
| All runs since backup are lost; conversations remain in users' clients | Communicate to users |
| Image registry unavailable | Use `docker tag` to keep a local copy of the last known-good image |
| GCP outage (Vertex down) | Router fails over to Anthropic; if both down, runs fail with `cause=provider_error`; users can retry later |
| PriceFRAME down | Runs that try to call PriceFRAME fail with `cause=tool_error`. Read tools return cached profile but writes fail. Communicate; resume on PriceFRAME recovery |

## 13.13 Deployment checklist

Use this before flipping production traffic:

- [ ] `uv run pytest` — all tests pass
- [ ] `uv run ruff check . && uv run mypy` — clean
- [ ] `uv run python scripts/export_openapi.py && git diff --exit-code openapi.yaml` — no drift
- [ ] Image built and pushed to registry with version tag
- [ ] `.env.production` has all required vars including secrets
- [ ] GCP SA key file present and mounted
- [ ] PriceFRAME `PRICEFRAME_JWT_SECRET` matches deployed PriceFRAME
- [ ] PriceFRAME `PRICEFRAME_SERVICE_SECRET` matches deployed PriceFRAME
- [ ] PriceFRAME has `agent.*` permissions seeded on the test profile
- [ ] nginx config uses `proxy_buffering off` for `/api/v1/agent/`
- [ ] `/api/v1/agent/health` returns 200 with all components ok
- [ ] Smoke test: `POST /auth/login` with test user → token
- [ ] Smoke test: end-to-end Create Pricing Request runs to completion
- [ ] Smoke test: SSE event stream via `curl -N` shows events
- [ ] Prometheus scraping `/metrics`
- [ ] Logs reaching aggregator with `request_id` present
- [ ] Backup schedule active on Postgres
- [ ] On-call runbook (this section + §10) shared with operations

---

**Next:** [§14 Walkthroughs](./14-walkthroughs.md) — 8 realistic end-to-end scenarios.
