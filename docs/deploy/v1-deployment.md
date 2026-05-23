# xFRAME AI Agent — v1 Deployment Guide

## Prerequisites

- Docker Engine 24+ and Docker Compose v2+
- GCP service account key with **Vertex AI User** role (JSON file)
- Secrets from the PriceFRAME deploy owner:
  - `PRICEFRAME_JWT_SECRET` (HS256 symmetric key, must match PriceFRAME)
  - `PRICEFRAME_SERVICE_SECRET`
- PostgreSQL password chosen for production (`PG_PASSWORD`)

## 1. Prepare secrets

```bash
mkdir -p secrets
cp /path/to/gcp-sa-key.json secrets/gcp-sa.json
chmod 600 secrets/gcp-sa.json
```

## 2. Create `.env.production`

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

ANTHROPIC_API_KEY=<optional fallback>

S3_ENDPOINT_URL=https://s3.<region>.amazonaws.com
S3_ACCESS_KEY_ID=<...>
S3_SECRET_ACCESS_KEY=<...>
S3_BUCKET=xframe-agent-prod

RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60

RUN_EXECUTION_MODE=inline
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

## 3. Required environment variables

| Variable | Description |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | asyncpg connection string (points to `postgres` service) |
| `REDIS_URL` | Redis connection string (points to `redis` service) |
| `PRICEFRAME_BASE_URL` | PriceFRAME API base URL |
| `PRICEFRAME_JWT_SECRET` | HS256 key shared with PriceFRAME |
| `PRICEFRAME_SERVICE_SECRET` | Service-to-service secret for audit callbacks |
| `PRICEFRAME_JWT_ALGORITHM` | Default `HS256` |
| `GEMINI_VERTEX_PROJECT` | GCP project ID for Vertex AI |
| `GEMINI_VERTEX_LOCATION` | Vertex AI region, e.g. `us-central1` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Set automatically to `/var/run/secrets/gcp.json` |
| `S3_ENDPOINT_URL` | S3 or MinIO endpoint for attachment storage |
| `S3_ACCESS_KEY_ID` | S3 access key |
| `S3_SECRET_ACCESS_KEY` | S3 secret key |
| `S3_BUCKET` | S3 bucket name |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `RATE_LIMIT_ENABLED` | Enable Redis-backed rate limiting |
| `RUN_EXECUTION_MODE` | `inline` (sync) or `arq` (background worker) |

## 4. Build the image

```bash
docker build -t xframe-agent:v1 .
```

## 5. Deploy

```bash
PG_PASSWORD=<your-pw> docker compose -f docker-compose.prod.yml up -d
```

The container entrypoint automatically runs `alembic upgrade head` before starting uvicorn.
It uses `${PORT:-8000}` so managed platforms such as Render can inject a port.

## 6. Post-deploy smoke test

```bash
curl https://<host>/api/v1/agent/health
# Expected: {"status":"ok","version":"..."}
```

## 7. Nginx SSE configuration

SSE streams require the following Nginx directives on the location that proxies to the agent:

```nginx
location /api/v1/agent/ {
    proxy_pass http://xframe-agent:8000;
    proxy_http_version 1.1;

    proxy_buffering off;
    proxy_read_timeout 3600s;
    chunked_transfer_encoding on;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## 8. Rollback

To revert to a previous image tag:

```bash
# Update docker-compose.prod.yml: image: xframe-agent:<previous-tag>
docker compose -f docker-compose.prod.yml up -d xframe-agent
```

Or if using explicit tags:

```bash
docker tag xframe-agent:<previous-tag> xframe-agent:v1
docker compose -f docker-compose.prod.yml up -d xframe-agent
```
