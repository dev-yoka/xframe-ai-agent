# Part 12 — Deployment

> Nine chapters on getting xFRAME running, from local laptop to multi-region production. Local setup, Docker image, docker-compose for prod, nginx + TLS + SSE, GCP credentials, Alembic on startup, rolling updates + rollback, Kubernetes sketch, CI/CD.

---

## Chapter 76 — Local Development Setup

### 76.1 Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.12 | Runtime |
| `uv` | latest | Python package manager (replaces pip/venv/pip-tools) |
| Docker | 24+ | Local stack (Postgres, Redis, etc.) |
| `git` | any | source control |
| (optional) `psql` | any | direct DB queries |
| (optional) `redis-cli` | any | Redis inspection |

### 76.2 First-time setup

```bash
git clone https://github.com/dev-yoka/xframe-ai-agent.git
cd xframe-ai-agent
uv sync --extra dev
cp .env.example .env
```

`uv sync` reads `pyproject.toml` + `uv.lock`, creates a `.venv/`, installs everything. `--extra dev` adds testing/lint tools.

### 76.3 Start dependencies

```bash
docker compose up -d postgres redis
```

This brings up:
- `postgres` on host port 5433 (mapped to container 5432 — avoids conflict with any local Postgres).
- `redis` on host port 6379.

Wait ~5 seconds for them to be ready. `docker compose logs postgres` confirms.

### 76.4 Run migrations

```bash
uv run alembic upgrade head
```

Creates all 11 tables in the `xframe_agent` database. Idempotent — safe to re-run.

### 76.5 Start the API

```bash
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

`--reload` watches `src/` and restarts on save. Open `http://localhost:8000/api/v1/agent/docs` for the interactive Swagger UI.

### 76.6 Run tests

```bash
uv run pytest                          # all tests
uv run pytest tests/test_runner.py -v  # one file
uv run ruff format --check . && uv run ruff check . && uv run mypy   # static gate
```

### 76.7 Optional: full local stack

```bash
docker compose up -d   # brings up everything: postgres, redis, langfuse, minio, clamav
```

Useful URLs:

- `http://localhost:8000/api/v1/agent/docs` — agent API
- `http://localhost:3001` — Langfuse (sign up for keys, set in `.env`)
- `http://localhost:9001` — MinIO console (`minioadmin`/`minioadmin`)

### 76.8 Connecting to the dev DB

```bash
psql postgres://xframe:xframe@localhost:5433/xframe_agent
```

Useful queries:

```sql
-- Recent runs
SELECT id, status, created_at FROM agent_runs ORDER BY created_at DESC LIMIT 10;

-- Latest events for a run
SELECT seq, event_type, payload->>'cause' AS cause
FROM agent_run_events WHERE run_id = '...' ORDER BY seq;
```

### 76.9 Common dev gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused` | docker compose not up | `docker compose up -d postgres redis` |
| `relation "agent_runs" does not exist` | migrations not run | `uv run alembic upgrade head` |
| `ModuleNotFoundError: xframe_agent` | not in venv | `uv sync && uv run ...` |
| Tests hang | port 5433 collision with local postgres | stop local postgres or change `DATABASE_URL` |

### 🔑 Chapter 76 takeaways

- `uv sync` installs everything, including dev deps.
- `docker compose up -d postgres redis` for minimal local stack.
- `uv run alembic upgrade head` before first run.
- `uv run uvicorn xframe_agent.main:app --reload --port 8000` to start.

---

## Chapter 77 — Docker Image Anatomy

### 77.1 The Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

RUN uv sync --no-dev --no-install-project
RUN uv pip install --system .

EXPOSE 8000
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod +x ./scripts/entrypoint.sh
CMD ["bash", "scripts/entrypoint.sh"]
```

### 77.2 Why `python:3.12-slim`

- **Smaller than full Python image** (~150 MB vs ~900 MB).
- **Debian-based** — `apt-get` available for adding what you need.
- **Active security updates** — Python maintainers update regularly.

Alternatives:
- `python:3.12-alpine` — even smaller, but musl libc occasionally breaks Python C extensions (psycopg2, numpy).
- Distroless — smallest, but no shell for debugging.
- Stick with slim unless you have specific size constraints.

### 77.3 `uv` in the image

```dockerfile
RUN pip install --no-cache-dir uv
RUN uv sync --no-dev --no-install-project
RUN uv pip install --system .
```

Three steps:

1. Install `uv` itself (using the bootstrap `pip`).
2. `uv sync --no-dev` — installs runtime deps only (skips pytest, mypy, etc.).
3. `uv pip install --system .` — installs the `xframe_agent` package into the system Python.

`--system` means: don't create a venv inside the container; install into the system site-packages. Simpler for containers (no activation step needed).

`--no-install-project` on step 2 skips the agent itself; step 3 does it. Two-phase install means deps cache layer.

### 77.4 Layer caching

Docker caches layers. The order in the Dockerfile is optimized for cache hits:

1. **OS packages** (rarely change).
2. **uv install** (rarely changes).
3. **`pyproject.toml` + `uv.lock`** (changes when deps change).
4. **`uv sync`** (rebuilds when 3 changes).
5. **Source code** (`COPY src ./src`, changes per commit).
6. **Package install** (`uv pip install --system .`, rebuilds when source changes).
7. **Entrypoint** (rarely changes).

If you change only Python source, layers 1-4 are cached → ~10-second rebuild. Change deps and you rebuild from layer 3. Change OS → full rebuild.

### 77.5 Multi-stage builds (optional optimization)

A multi-stage build separates build deps from runtime:

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install uv && uv sync --no-dev --no-install-project && uv pip install --target=/install .

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /install /usr/local/lib/python3.12/site-packages
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod +x ./scripts/entrypoint.sh
EXPOSE 8000
CMD ["bash", "scripts/entrypoint.sh"]
```

Smaller runtime image (no pip/uv tools). xFRAME's current Dockerfile is single-stage for simplicity; multi-stage if you need to shrink further.

### 77.6 The entrypoint

`scripts/entrypoint.sh`:

```bash
#!/bin/bash
set -e
echo "Running database migrations..."
uv run alembic upgrade head
echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Three steps:

1. **Migrations first.** If migrations fail, the container never starts uvicorn → unhealthy → orchestrator (Compose/k8s) restarts or rolls back.
2. **`exec uvicorn`** — replaces the shell process with uvicorn. Signals (SIGTERM on shutdown) propagate correctly.
3. **`--proxy-headers`** — trusts `X-Forwarded-For` from nginx for rate limiting.

### 77.7 Container security

| Concern | Mitigation |
|---|---|
| Running as root | Add a `USER` directive to run as non-root |
| Read-only root FS | Add `--read-only` at runtime; mount specific writable volumes |
| Image bloat | Multi-stage; remove apt caches; `--no-cache-dir` on pip |
| Vulnerable base image | Rebuild regularly (Renovate, dependabot) |
| Secrets in layers | Never `COPY .env` or similar; use runtime secrets |

xFRAME's current Dockerfile runs as root. Production hardening: add `RUN useradd -m -u 1000 app && USER app` and verify nothing requires root.

### 🔑 Chapter 77 takeaways

- `python:3.12-slim` + `uv` for fast, small images.
- Layer order matters for cache hits.
- Entrypoint runs migrations then `exec uvicorn`.
- Production hardening: non-root user, read-only FS, regular rebuilds.

---

## Chapter 78 — docker-compose for Production

### 78.1 The file

`docker-compose.prod.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: xframe
      POSTGRES_PASSWORD: ${PG_PASSWORD}
      POSTGRES_DB: xframe_agent
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U xframe -d xframe_agent"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7.4-alpine
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  clamav:
    image: clamav/clamav:stable
    restart: unless-stopped

  xframe-agent:
    image: xframe-agent:v1
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /var/run/secrets/gcp.json
    env_file: .env.production
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -fs http://localhost:8000/api/v1/agent/health || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    secrets:
      - gcp_sa
    restart: unless-stopped

volumes:
  postgres-data:
  redis-data:

secrets:
  gcp_sa:
    file: ./secrets/gcp-sa.json
```

### 78.2 What's different from dev

| Aspect | dev (`docker-compose.yml`) | prod (`docker-compose.prod.yml`) |
|---|---|---|
| Postgres password | `xframe` (hardcoded) | `${PG_PASSWORD}` (from env) |
| Postgres exposed port | 5433 (host) | not exposed (internal only) |
| Restart policy | none | `unless-stopped` |
| Health checks | none / minimal | required |
| MinIO included | yes | no (use real S3) |
| Langfuse included | yes | optional (often self-hosted separately) |
| Secrets | env file | Docker secrets (GCP key) |

### 78.3 Health checks are not optional

The `healthcheck` blocks let Docker:

- Mark containers unhealthy after N failures.
- `restart: unless-stopped` then restarts unhealthy containers automatically.
- `depends_on: condition: service_healthy` makes downstream services wait.

For the agent:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -fs http://localhost:8000/api/v1/agent/health || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s
```

- `start_period: 60s` — grace period for the first health check (waiting for migrations + uvicorn).
- `interval: 30s` — checks every 30 seconds after that.
- `timeout: 10s` — single check must respond in 10s.
- `retries: 3` — three consecutive failures = unhealthy.

So a degraded agent (e.g., DB connection dropped) gets restarted ~2 minutes after first failure. Tunable.

### 78.4 Volumes

```yaml
volumes:
  postgres-data:
  redis-data:
```

Named volumes managed by Docker. Persist across container restarts. **Back them up** — Postgres data loss = full system loss.

Backup approach for postgres-data:

```bash
docker compose exec postgres pg_dump -U xframe xframe_agent | gzip > backup-$(date +%F).sql.gz
```

Or use a managed Postgres (RDS, Cloud SQL) and skip Docker entirely.

### 78.5 Networking

By default, Docker Compose creates one network per project. All services in the file resolve each other by service name:

- Agent reaches Postgres at `postgres:5432`.
- Agent reaches Redis at `redis:6379`.
- Agent reaches ClamAV at `clamav:3310`.

External access only through `ports:` exposed services (`8000:8000` for the agent).

### 78.6 Secrets mount

```yaml
xframe-agent:
  secrets:
    - gcp_sa
secrets:
  gcp_sa:
    file: ./secrets/gcp-sa.json
```

The host file `./secrets/gcp-sa.json` is mounted into the container at `/run/secrets/gcp_sa` (default) — but xFRAME uses `/var/run/secrets/gcp.json` (via `GOOGLE_APPLICATION_CREDENTIALS`). The mount path is `/run/secrets/<secret_name>` by default; verify your setup.

For Swarm or k8s, Docker Compose secrets translate to proper secret stores. For single-host Compose, the file is the secret.

### 78.7 Bringing it up

```bash
# First time
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f xframe-agent

# Updates
docker pull xframe-agent:v1.1
docker compose -f docker-compose.prod.yml up -d xframe-agent  # rolling update

# Logs
docker compose -f docker-compose.prod.yml logs --tail 100 xframe-agent

# Status
docker compose -f docker-compose.prod.yml ps
```

### 🔑 Chapter 78 takeaways

- Production compose pins images, uses health checks, mounts secrets properly.
- Volumes need separate backup strategy.
- Service-name DNS resolves between containers automatically.
- Restart policies + health checks = poor-man's self-healing.

---

## Chapter 79 — nginx, TLS, and SSE Buffering

### 79.1 Why nginx in front

The agent container can serve HTTP directly, but production wants:

- **TLS termination** — HTTPS in, HTTP inside.
- **Header sanitization** — control what reaches the app.
- **Rate limiting (optional)** — first defense before app-level limits.
- **Logging** — access log for auditing.
- **Multiple backends** — if you scale to multiple agent replicas.

### 79.2 The minimal nginx config for xFRAME

```nginx
http {
    # ... usual config ...

    upstream xframe_agent {
        server xframe-agent:8000;
        # add more for HA:
        # server xframe-agent-2:8000;
        # server xframe-agent-3:8000;
    }

    server {
        listen 443 ssl http2;
        server_name agent.example.com;

        ssl_certificate     /etc/nginx/certs/agent.crt;
        ssl_certificate_key /etc/nginx/certs/agent.key;

        # Strict-Transport-Security (HSTS)
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

        # CSP, X-Content-Type-Options, etc.
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-Frame-Options "DENY" always;

        location /api/v1/agent/ {
            proxy_pass http://xframe_agent;
            proxy_http_version 1.1;

            # ESSENTIAL for SSE
            proxy_buffering off;
            proxy_read_timeout 3600s;
            chunked_transfer_encoding on;

            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

    # Optional: HTTP → HTTPS redirect
    server {
        listen 80;
        server_name agent.example.com;
        return 301 https://$host$request_uri;
    }
}
```

### 79.3 The three SSE-critical settings

```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
chunked_transfer_encoding on;
```

| Setting | Why |
|---|---|
| `proxy_buffering off` | Without this, nginx buffers the entire response before sending → SSE breaks |
| `proxy_read_timeout 3600s` | Agents pause for human approval; default 60s would kill long sessions |
| `chunked_transfer_encoding on` | SSE relies on HTTP chunked encoding |

The `proxy_read_timeout` only kicks in if no data flows. xFRAME emits heartbeats every 15s (`SSE_HEARTBEAT_SECONDS=15`), so the timer resets. The 3600s is just slack — you could go shorter if heartbeats are reliable.

### 79.4 TLS certificates

For production, **Let's Encrypt** via certbot is the simplest path:

```bash
# Install certbot (Ubuntu)
sudo apt install certbot python3-certbot-nginx

# Get a cert
sudo certbot --nginx -d agent.example.com

# Auto-renewal runs via systemd timer (already configured)
```

Or use a managed proxy (Caddy, Traefik) that handles TLS automatically.

For internal-only deployments, self-signed certs work but require client trust configuration.

### 79.5 nginx as a docker-compose service

Add to `docker-compose.prod.yml`:

```yaml
nginx:
  image: nginx:1.27-alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/certs:/etc/nginx/certs:ro
  depends_on:
    - xframe-agent
  restart: unless-stopped
```

The agent container's port mapping `"8000:8000"` becomes internal-only — drop the external port:

```yaml
xframe-agent:
  # ports: removed
  expose:
    - "8000"
```

### 79.6 HTTP/2 considerations

nginx `listen 443 ssl http2;` enables HTTP/2. Benefits:

- Multiplexing — many requests over one TCP connection.
- Header compression.

⚠️ **HTTP/2 has poor SSE support in some clients.** The `EventSource` browser API works fine over HTTP/2, but some intermediate proxies may have issues. Test before relying.

### 79.7 Real client IP

uvicorn with `--proxy-headers` trusts `X-Forwarded-For` from nginx, so `request.client.host` becomes the real client IP. Essential for rate limiting.

⚠️ **Don't trust forwarded headers from arbitrary sources.** Configure nginx to only set them for trusted upstream traffic. Otherwise an attacker can spoof:

```http
X-Forwarded-For: 1.2.3.4
```

To bypass IP-based rate limits.

### 🔑 Chapter 79 takeaways

- nginx for TLS termination + SSE-friendly proxying.
- Three magic settings: `proxy_buffering off`, `proxy_read_timeout 3600s`, `chunked_transfer_encoding on`.
- HTTP/2 fine for general traffic; verify SSE works in your stack.
- `X-Forwarded-For` only from trusted sources.

---

## Chapter 80 — GCP Service Account and Provider Credentials

### 80.1 Gemini Vertex setup (step by step)

```bash
# 1. Set up GCP project
gcloud projects create xframe-prod --name="xFRAME Production"
gcloud config set project xframe-prod
gcloud services enable aiplatform.googleapis.com

# 2. Create service account
gcloud iam service-accounts create xframe-agent \
    --display-name="xFRAME AI Agent"

# 3. Grant Vertex AI User role
gcloud projects add-iam-policy-binding xframe-prod \
    --member="serviceAccount:xframe-agent@xframe-prod.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

# 4. Generate key
gcloud iam service-accounts keys create ./secrets/gcp-sa.json \
    --iam-account=xframe-agent@xframe-prod.iam.gserviceaccount.com
```

`./secrets/gcp-sa.json` now exists. Add to `.gitignore` (already there).

### 80.2 Env vars

```dotenv
GEMINI_VERTEX_PROJECT=xframe-prod
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json
```

The Google SDK reads `GOOGLE_APPLICATION_CREDENTIALS` automatically. No explicit credential loading in the code.

### 80.3 Pick a location

Vertex Gemini is available in many regions. Pick the one closest to your users (and compliant with data residency):

- `us-central1` — Iowa, USA. Default. Cheap and fast for US users.
- `us-east1`, `us-east5` — Eastern USA.
- `europe-west1` — Belgium. EU residency.
- `asia-southeast1` — Singapore.
- `asia-northeast1` — Tokyo.

Pricing varies slightly by region. Latency varies a lot. For EU users, use a European region — adds maybe 200ms to call setup but keeps data in-region.

### 80.4 Anthropic setup

```bash
# 1. Sign up at console.anthropic.com
# 2. Generate API key
# 3. Set env
```

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

That's it. No regional config (Anthropic handles globally). Note: Anthropic's traffic goes through their servers regardless; check their data processing terms for residency.

### 80.5 Smoke testing providers

After deploy:

```bash
# Test Vertex
docker exec xframe-agent uv run python -c "
import asyncio
from xframe_agent.provider.gemini_vertex import GeminiVertexProvider
from xframe_agent.settings import Settings
async def main():
    p = GeminiVertexProvider(Settings())
    print(f'Provider name: {p.name}')
    print(f'Project: {p._project}, Location: {p._location}')
asyncio.run(main())
"
```

Should print without error. For end-to-end:

```bash
curl -X POST https://agent.example.com/api/v1/agent/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"..."}'

# Use returned token to create conversation + send message
```

### 80.6 Quota management

GCP and Anthropic have rate limits:

- Vertex: typically thousands of requests/min per project. Adjustable via quota requests.
- Anthropic: tier-based; higher tiers = higher limits.

Monitor:

- GCP Console → IAM & Admin → Quotas — see current usage.
- Anthropic Console → Settings → Usage.

Set up alerts before you hit limits. Surprise quota exhaustion = production outage.

### 80.7 Cost monitoring

Both providers bill per token. Daily budgets:

- **GCP Billing alerts** — email when project spend crosses thresholds.
- **Anthropic spending limits** — set max monthly spend in console.

For per-user attribution, use `agent_run_events.payload->'budget'->>'cost_usd'`. Aggregate to find expensive users (Chapter 67 §67.6).

### 🔑 Chapter 80 takeaways

- Vertex: GCP project + SA + JSON key + `GEMINI_VERTEX_PROJECT` env.
- Anthropic: API key only.
- Pick region for latency + data residency.
- Set quota alerts and spending limits before going live.

---

## Chapter 81 — Alembic Migrations on Container Start

### 81.1 The current pattern

`scripts/entrypoint.sh`:

```bash
#!/bin/bash
set -e
echo "Running database migrations..."
uv run alembic upgrade head
echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

Every container start runs `alembic upgrade head`. Idempotent if already current.

### 81.2 Why this works for v1

- Single-replica deploy: no race.
- Multi-replica deploy: first container to acquire `alembic_version` row lock wins; others wait or fail+retry.
- Failed migration = container failed = orchestrator restarts.

Acceptable for moderate scale. Not bulletproof.

### 81.3 The race-condition risk

When K replicas start simultaneously:

- All K try `alembic upgrade head`.
- One acquires the lock; K-1 wait or fail.
- If a non-winner sees the migration applied (after the winner commits), it's a no-op success.
- If the winner fails mid-migration, the others may inherit a half-applied state.

In practice: rare. xFRAME's migrations are short. Risk is low.

### 81.4 The dedicated migration job pattern (recommended)

For higher reliability, separate migration from runtime:

```yaml
# docker-compose.prod.yml
services:
  xframe-migrate:
    image: xframe-agent:v1
    command: ["uv", "run", "alembic", "upgrade", "head"]
    env_file: .env.production
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"   # one-shot

  xframe-agent:
    image: xframe-agent:v1
    command: ["uv", "run", "uvicorn", "xframe_agent.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
    # ... rest unchanged ...
```

Run migration first, then start replicas:

```bash
docker compose -f docker-compose.prod.yml run --rm xframe-migrate
docker compose -f docker-compose.prod.yml up -d xframe-agent
```

In Kubernetes:

```yaml
apiVersion: batch/v1
kind: Job
metadata: { name: xframe-migrate-v1.1 }
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: xframe-agent:v1.1
          command: ["uv", "run", "alembic", "upgrade", "head"]
          envFrom:
            - secretRef: { name: xframe-agent-secrets }
```

Deploy strategy:

1. Apply Job manifest.
2. Wait for Job completion.
3. Apply Deployment manifest with new image.

### 81.5 Forward-only migrations

xFRAME's discipline: **all migrations are additive**. New columns get defaults; never drop, never rename.

This makes deployments safe:

- Old code can run against new schema (it ignores new columns).
- New code can run against old schema (briefly, during rolling deploy).
- Rollback = redeploy old code; schema is forward-compatible.

If you ever **must** drop a column, do it in two steps:

1. Deploy code that no longer uses the column.
2. After bake-in, drop the column in a second migration.

Don't combine the two — a rollback would leave you stuck.

### 81.6 Handling failed migrations

If `alembic upgrade head` fails:

```
ERROR [alembic.util.messaging] target database is not up to date.
```

Diagnose:

```bash
docker compose exec postgres psql -U xframe -d xframe_agent -c "SELECT version_num FROM alembic_version;"
```

Compare with `migrations/versions/` — what's the latest available?

If you've manually applied schema changes outside Alembic, `alembic stamp <revision>` tells Alembic "you're already here." Use carefully.

### 81.7 Data migrations

Schema migrations are stateless. **Data migrations** (e.g., backfilling a new column) need care:

- Small data: do it in the migration.
- Large data: write a separate script; run after schema migration; checkpoint progress.
- Production data: test on a copy first.

xFRAME doesn't have data migrations yet.

### 🔑 Chapter 81 takeaways

- Entrypoint runs `alembic upgrade head` on start; idempotent.
- For high reliability, use a dedicated migration job/step.
- Forward-only migrations keep rollback safe.
- Test data migrations on prod-shaped data before deploying.

---

## Chapter 82 — Rolling Updates and Rollback

### 82.1 Goal: zero-downtime deploys

You ship a new image. Old containers continue serving while new ones come up. When new are healthy, old are stopped. Users see no interruption.

### 82.2 Docker Compose rolling update

Compose doesn't natively support rolling updates as elegantly as Swarm or k8s, but a basic pattern:

```bash
# 1. Build / pull new image
docker pull xframe-agent:v1.1

# 2. Update compose file to reference new tag (or use env var)
sed -i 's/xframe-agent:v1/xframe-agent:v1.1/' docker-compose.prod.yml

# 3. Restart agent service (with --no-deps to avoid restarting Postgres)
docker compose -f docker-compose.prod.yml up -d --no-deps xframe-agent
```

If you have multiple replicas (a real production setup), you'd want a load balancer in front and scale up new ones before scaling down old ones.

### 82.3 Docker Swarm pattern (one notch up)

```bash
docker stack deploy -c docker-compose.prod.yml xframe
docker service update --image xframe-agent:v1.1 --update-parallelism 1 --update-delay 30s xframe_xframe-agent
```

Swarm handles rolling: takes down one replica at a time, waits 30s for the new one to be healthy, moves on.

### 82.4 Kubernetes rolling update

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: xframe-agent }
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    spec:
      containers:
        - name: agent
          image: xframe-agent:v1.1
          readinessProbe:
            httpGet: { path: /api/v1/agent/health, port: 8000 }
```

`maxSurge: 1, maxUnavailable: 0` = "always have all 3 healthy; bring up 1 new before taking 1 old down." Zero-downtime by construction.

### 82.5 The deployment runbook

For each release:

```
1. Code merged to main, CI green.
2. Build new image:
   docker build -t xframe-agent:v1.1 .
   docker tag xframe-agent:v1.1 registry.example.com/xframe-agent:v1.1
   docker push registry.example.com/xframe-agent:v1.1

3. Run migration (separately if using dedicated job).

4. Update production:
   docker compose pull xframe-agent
   docker compose up -d xframe-agent

5. Monitor health:
   - Watch logs: docker logs -f xframe-agent
   - Check /health endpoint
   - Monitor /metrics for error rate spike

6. If green for 10+ minutes → success.
   If unhealthy → rollback (see §82.7).
```

### 82.6 Smoke tests post-deploy

After deploy:

```bash
# 1. Health endpoint
curl -fs https://agent.example.com/api/v1/agent/health || echo "FAIL"

# 2. Login (with test creds)
TOKEN=$(curl -s -X POST https://agent.example.com/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"..."}' | jq -r .token)

# 3. Tool list
curl -fs -H "Authorization: Bearer $TOKEN" https://agent.example.com/api/v1/agent/tools

# 4. End-to-end conversation
curl -fs -X POST https://agent.example.com/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"title":"smoke test"}'
```

Automate this as a CI smoke-test job that runs post-deploy.

### 82.7 Rollback procedures

If the new version is broken:

**Code rollback (easy)**:

```bash
docker compose -f docker-compose.prod.yml stop xframe-agent
# Edit compose file: image: xframe-agent:v1.0
docker compose -f docker-compose.prod.yml up -d xframe-agent
```

xFRAME's forward-only migrations mean the new schema works with old code. No schema rollback needed.

**Image deletion (don't!)**: keep old images around for at least one major version. Don't `docker rmi` aggressively.

**Schema rollback (rare and risky)**: only if you ran a destructive migration. Test downgrade scripts on a clone first. Better: avoid the situation by not running destructive migrations.

### 82.8 Canary deployments (advanced)

Instead of all-or-nothing, route a small percentage of traffic to the new version:

- 10% of users → v1.1
- 90% of users → v1.0

Use header-based routing in nginx, or a service mesh (Istio, Linkerd).

If error rate on v1.1 stays low for 30 minutes, ramp to 100%. If it spikes, route back to v1.0.

xFRAME doesn't have canary infrastructure today. For v1, "deploy and watch closely" is the strategy.

### 🔑 Chapter 82 takeaways

- Zero-downtime requires multiple replicas + load balancer or service orchestrator.
- Forward-only migrations make rollback a simple image swap.
- Automate smoke tests; run them post-deploy.
- Canary deploys are a nice-to-have, not essential for v1.

---

## Chapter 83 — Kubernetes Sketch (Future)

### 83.1 When to move to k8s

xFRAME's Docker Compose setup works for moderate scale. Move to Kubernetes when:

- You need >5 agent replicas with auto-scaling.
- You're already on k8s for other services.
- You need multi-region deployments.
- You want sophisticated routing (canary, blue/green).
- Your ops team is comfortable with it.

Don't move just because k8s is popular. The complexity tax is real.

### 83.2 Minimal Deployment manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xframe-agent
  namespace: xframe
spec:
  replicas: 3
  selector:
    matchLabels: { app: xframe-agent }
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  template:
    metadata:
      labels: { app: xframe-agent }
    spec:
      serviceAccountName: xframe-agent
      containers:
        - name: agent
          image: registry.example.com/xframe-agent:v1
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef: { name: xframe-agent-secrets }
            - configMapRef: { name: xframe-agent-config }
          volumeMounts:
            - { name: gcp-sa, mountPath: /var/run/secrets, readOnly: true }
          readinessProbe:
            httpGet: { path: /api/v1/agent/health, port: 8000 }
            initialDelaySeconds: 30
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /api/v1/agent/health, port: 8000 }
            initialDelaySeconds: 60
            periodSeconds: 30
          resources:
            requests: { memory: 512Mi, cpu: 250m }
            limits:   { memory: 1Gi,   cpu: 1000m }
      volumes:
        - name: gcp-sa
          secret: { secretName: gcp-sa }
```

### 83.3 Service + Ingress

```yaml
apiVersion: v1
kind: Service
metadata: { name: xframe-agent }
spec:
  selector: { app: xframe-agent }
  ports:
    - { port: 80, targetPort: 8000 }

---

apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: xframe-agent
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts: [agent.example.com]
      secretName: agent-tls
  rules:
    - host: agent.example.com
      http:
        paths:
          - path: /api/v1/agent
            pathType: Prefix
            backend:
              service:
                name: xframe-agent
                port: { number: 80 }
```

nginx-ingress-controller annotations replicate the SSE settings from raw nginx config. cert-manager provisions TLS.

### 83.4 Worker Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: xframe-worker }
spec:
  replicas: 2
  selector:
    matchLabels: { app: xframe-worker }
  template:
    metadata:
      labels: { app: xframe-worker }
    spec:
      containers:
        - name: worker
          image: registry.example.com/xframe-agent:v1
          command: ["uv", "run", "arq", "xframe_agent.worker.WorkerSettings"]
          envFrom: [{ secretRef: { name: xframe-agent-secrets } }]
          # no probes for worker — arq health is internal
```

Two replicas of the worker, each with `max_jobs=4` → 8 concurrent runs. Scale by changing `replicas:`.

### 83.5 Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: xframe-agent }
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: xframe-agent
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
    - type: Resource
      resource:
        name: memory
        target: { type: Utilization, averageUtilization: 80 }
```

Auto-scales between 3 and 20 replicas based on CPU/memory. Tweak thresholds for your traffic.

### 83.6 Migration as a Job

```yaml
apiVersion: batch/v1
kind: Job
metadata: { name: xframe-migrate-v1.1 }
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: registry.example.com/xframe-agent:v1.1
          command: ["uv", "run", "alembic", "upgrade", "head"]
          envFrom: [{ secretRef: { name: xframe-agent-secrets } }]
```

Deploy order in CI:

1. Apply Job manifest.
2. `kubectl wait --for=condition=complete --timeout=10m job/xframe-migrate-v1.1`.
3. Apply Deployment with new image.

### 83.7 Cost considerations

| k8s flavor | Monthly baseline (small) |
|---|---|
| Single-node k3s/Minikube | $0 (your hardware) |
| Managed (GKE, EKS, AKS) | $70-200 base + nodes |
| Hosted (DigitalOcean, Linode) | $40-100 |

For xFRAME v1 scale (a few replicas + small DB), managed k8s is overkill cost-wise. Docker Compose on a $40/month VPS works. Migrate when scale justifies it.

### 🔑 Chapter 83 takeaways

- k8s is for ≥5 replicas or multi-region needs. Not "just because."
- Deployment + Service + Ingress + (optional) HPA + (one-shot) Job for migrations.
- SSE settings translate to ingress annotations.
- Don't migrate until the complexity tax is justified.

---

## Chapter 84 — CI/CD with GitHub Actions

### 84.1 The current workflow

`.github/workflows/ci.yml`:

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main, phase-B/**, phase-D/**]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - run: uv sync --extra dev
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run mypy
      - run: uv run pytest
      - run: uv run python scripts/export_openapi.py
      - run: git diff --exit-code openapi.yaml
```

Single job: install deps, run all checks. Total time: ~1-2 minutes.

### 84.2 What each step does

| Step | Catches |
|---|---|
| `ruff format --check` | Unformatted code |
| `ruff check` | Lint errors |
| `mypy` | Type errors |
| `pytest` | Test failures |
| `export_openapi.py` | Regenerate OpenAPI from FastAPI |
| `git diff --exit-code openapi.yaml` | Drift between code and committed schema |

The OpenAPI drift check is subtle but valuable: if you change a schema and forget to regenerate, CI fails with the diff visible.

### 84.3 Adding deploy on merge

Extend with a deploy job:

```yaml
deploy:
  needs: test
  if: github.ref == 'refs/heads/main' && github.event_name == 'push'
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: docker/setup-buildx-action@v3
    - uses: docker/login-action@v3
      with:
        registry: registry.example.com
        username: ${{ secrets.REGISTRY_USER }}
        password: ${{ secrets.REGISTRY_TOKEN }}
    - uses: docker/build-push-action@v5
      with:
        context: .
        push: true
        tags: |
          registry.example.com/xframe-agent:${{ github.sha }}
          registry.example.com/xframe-agent:latest
    - name: Deploy
      uses: appleboy/ssh-action@v1
      with:
        host: ${{ secrets.DEPLOY_HOST }}
        username: ${{ secrets.DEPLOY_USER }}
        key: ${{ secrets.DEPLOY_KEY }}
        script: |
          cd /opt/xframe
          docker pull registry.example.com/xframe-agent:${{ github.sha }}
          IMAGE_TAG=${{ github.sha }} docker compose -f docker-compose.prod.yml up -d xframe-agent
```

Triggers only on `main` push. Builds + pushes image, then SSHes to the prod host and triggers a compose update.

For k8s: replace the SSH step with `kubectl apply` or a `helm upgrade`.

### 84.4 Branch protection

In GitHub Settings → Branches → main, enable:

- ☑ Require pull request before merging
- ☑ Require status checks (the CI job)
- ☑ Require branches to be up to date
- ☑ Require signed commits (optional but recommended)

Prevents direct pushes to main; everything goes through PR + CI.

### 84.5 Secrets in GitHub Actions

Secrets stored in repo settings:

- `REGISTRY_USER`, `REGISTRY_TOKEN` — for docker push.
- `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY` — for SSH.

Or use `GOOGLE_APPLICATION_CREDENTIALS_BASE64` if deploying to GCP from Actions:

```yaml
- name: Auth GCP
  uses: google-github-actions/auth@v2
  with:
    credentials_json: ${{ secrets.GCP_SA_KEY }}
```

### 84.6 Caching to speed up CI

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/uv
    key: uv-${{ runner.os }}-${{ hashFiles('uv.lock') }}
```

`uv` caches downloaded wheels. First run downloads everything (~30s). Subsequent runs with same `uv.lock` reuse the cache (~3s).

For Docker layer caching:

```yaml
- uses: docker/build-push-action@v5
  with:
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

Uses GitHub Actions' built-in cache for Docker layers.

### 84.7 Notifications

Failed CI on main:

```yaml
- name: Notify Slack on failure
  if: failure()
  uses: slackapi/slack-github-action@v1
  with:
    payload: |
      { "text": "CI failed on main: ${{ github.event.head_commit.message }}" }
```

Notify the team channel. Pair with an on-call rotation if it's actually critical.

### 84.8 What CI doesn't catch

| Gap | Caught by |
|---|---|
| Real LLM regressions | Provider-mode evals (scheduled, not per-PR) |
| Production-only env issues | Smoke tests post-deploy |
| Performance regressions | Load tests (manual, not CI) |
| Security vulnerabilities in deps | Dependabot / Renovate (separate) |

Layer CI with these complementary tools.

### 🔑 Chapter 84 takeaways

- One CI job covers static checks + tests + OpenAPI drift.
- Extend to build + push + deploy on merge to main.
- Branch protection enforces "always through PR + CI."
- CI is one layer; supplement with smoke tests, evals, load tests.

---

### Part 12 wrap-up

You can now deploy xFRAME from blank Linux box to multi-replica Kubernetes cluster, with TLS, auto-scaling, and rolling updates.

### ✍️ Part 12 exercises

1. Stand up the local stack from scratch on a clean machine. Time yourself.
2. Write the GitHub Actions workflow that deploys to your hosting provider on merge to main.
3. Sketch the k8s migration job pattern. What happens if the migration takes longer than the deploy timeout?

### 📚 Part 12 further reading

- Docker docs: BuildKit, multi-stage builds, secrets.
- nginx Admin Guide on `proxy_buffering` and SSE.
- Kubernetes "Best Practices" guides from cloud providers.

---

**End of Part 12.**

**Next:** [Part 13 — Scaling and Production](./part-13-scaling-production.md).
