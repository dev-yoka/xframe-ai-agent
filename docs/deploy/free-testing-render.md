# Free Testing Deployment Guide

This guide deploys `xframe-ai-agent` on free or low-friction hosted services for
manual testing. It is not a production runbook. Free services may sleep, expire,
or reset data.

## Recommended Path

Use Render for the first test deployment:

- Web service: Render Docker web service
- Database: Render Postgres free plan
- Redis-compatible cache: Render Key Value free plan

Important caveats:

- Free Render web services sleep after idle time.
- Render free Postgres is temporary and expires after 30 days.
- Render free Key Value is in-memory only.
- Use placeholders for secrets. Do not commit real API keys or shared secrets.

Official references:

- [Render free tier](https://render.com/docs/free)
- [Render Postgres](https://render.com/docs/databases)
- [Render Key Value](https://render.com/docs/key-value)
- [Render environment variables](https://render.com/docs/environment-variables)

## 1. Push The Repo

Render deploys from GitHub, so push the branch you want to test.

```bash
cd /Users/bhairava/WorkSpace/repos/xframe-ai-agent
git status
git add .
git commit -m "docs: add free testing deployment guide"
git push
```

If you already have uncommitted implementation work, commit that separately with
the right message for the change.

## 2. Create Render Postgres

1. Open the Render dashboard.
2. Click `New` -> `Postgres`.
3. Name it `xframe-agent-db`.
4. Choose the same region you will use for the web service.
5. Choose the free plan.
6. Create the database.
7. Open the database `Connect` panel.
8. Copy the internal database URL.

Render gives a URL shaped like this:

```text
postgresql://USER:PASSWORD@HOST:PORT/DB
```

The agent uses SQLAlchemy asyncpg, so set `DATABASE_URL` with the async driver:

```text
postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DB
```

## 3. Create Render Key Value

1. Click `New` -> `Key Value`.
2. Name it `xframe-agent-redis`.
3. Use the same region as the web service and database.
4. Choose the free plan.
5. Create it.
6. Open its `Connect` panel.
7. Copy the internal Redis URL.

Use that value as `REDIS_URL`.

Example shape:

```text
redis://red-xxxxx:6379
```

## 4. Create The Web Service

1. Click `New` -> `Web Service`.
2. Connect the GitHub repository for `xframe-ai-agent`.
3. Select the branch you want to test.
4. Choose Docker as the runtime.
5. Choose the free instance type.
6. Add the environment variables from the next section.
7. Deploy.

The existing `Dockerfile` copies the app, installs it with `uv`, exposes port
`8000`, and starts `scripts/entrypoint.sh`. The entrypoint runs:

```bash
alembic upgrade head
uvicorn xframe_agent.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
```

Set `PORT=8000` in Render for this deployment.

## 5. Environment Variables

Set these on the Render web service.

`PRICEFRAME_BASE_URL` must be the root host, without `/api`, because the agent
adds paths such as `/api/auth/login` itself.

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
API_PREFIX=/api/v1/agent
PORT=8000

DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/DB
REDIS_URL=redis://YOUR_RENDER_KEY_VALUE_INTERNAL_URL

PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<same JWT secret used by deployed PriceFRAME>
PRICEFRAME_SERVICE_SECRET=<same HMAC service secret used by PriceFRAME>
PRICEFRAME_JWT_ALGORITHM=HS256
PRICEFRAME_PROFILE_CACHE_TTL_SECONDS=60
PRICEFRAME_TIMEOUT_SECONDS=10
PRICEFRAME_MAX_RETRIES=2

CORS_ORIGINS=https://priceframe-yg.buy-frame.com

RUN_EXECUTION_MODE=inline
RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=redis
RATE_LIMIT_REQUESTS=120
RATE_LIMIT_WINDOW_SECONDS=60
SSE_REDIS_BUFFER_ENABLED=true
SSE_HEARTBEAT_SECONDS=15
HEALTH_CHECK_EXTERNALS=true
PROMETHEUS_ENABLED=true

ALLOW_REAL_DATA=false
GEMINI_API_KEY=<your Gemini Developer API key>
GEMINI_API_BASE_URL=https://generativelanguage.googleapis.com/v1beta

GEMINI_VERTEX_PROJECT=
GEMINI_VERTEX_LOCATION=us-central1
ANTHROPIC_API_KEY=

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=

ATTACHMENT_STORAGE_BACKEND=local
ATTACHMENT_LOCAL_STORAGE_PATH=/tmp/xframe-agent-attachments
ATTACHMENT_SCAN_MODE=inline
CLAMAV_ENABLED=false
```

For free testing, `RUN_EXECUTION_MODE=inline` avoids running a separate worker.
If you later switch to `arq`, deploy a separate worker service that uses the
same image and Redis URL.

## 6. Deploy And Watch Logs

After you click deploy, watch the Render logs.

Expected startup sequence:

```text
Running database migrations...
Starting xframe-agent...
```

If migrations fail, check:

- `DATABASE_URL` uses `postgresql+asyncpg://`.
- The database host is reachable from Render.
- The Postgres service is in the same region.

If the app starts but health is degraded, check:

- `REDIS_URL`
- `PRICEFRAME_BASE_URL`
- `PRICEFRAME_JWT_SECRET`
- `GEMINI_API_KEY`

## 7. Health Check

Replace the host with your Render service URL.

```bash
export AGENT_URL="https://xframe-ai-agent.onrender.com"

curl -s "$AGENT_URL/api/v1/agent/health" | python3 -m json.tool
```

Expected shape:

```json
{
  "status": "ok",
  "checks": {
    "provider": {
      "status": "ok"
    },
    "database": {
      "status": "ok"
    },
    "queue": {
      "status": "ok"
    },
    "priceframe": {
      "status": "ok"
    }
  }
}
```

If the free Redis service is unavailable but you still need a quick web smoke
test, temporarily set:

```dotenv
RATE_LIMIT_BACKEND=memory
SSE_REDIS_BUFFER_ENABLED=false
HEALTH_CHECK_EXTERNALS=false
```

That mode is only for manual smoke testing.

## 8. Login Smoke Test

```bash
curl -s -X POST "$AGENT_URL/api/v1/agent/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"<priceframe-test-email>","password":"<priceframe-test-password>"}' \
  | python3 -m json.tool
```

Save the token:

```bash
export TOKEN="<token from login response>"
```

## 9. Tool Discovery Smoke Test

```bash
curl -s "$AGENT_URL/api/v1/agent/tools" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

You should see the tools allowed by the user's `agent.*` permissions.

## 10. Conversation Smoke Test

Create a conversation:

```bash
curl -s -X POST "$AGENT_URL/api/v1/agent/conversations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"title":"Render test","kind":"create_pricing_request"}' \
  | python3 -m json.tool
```

Save the conversation ID:

```bash
export CONV="<conversation id>"
```

Send a message:

```bash
curl -s -X POST "$AGENT_URL/api/v1/agent/conversations/$CONV/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"content":"Show me my open quotations","source":"text"}' \
  | python3 -m json.tool
```

Expected response shape:

```json
{
  "run_id": "01...",
  "status": "completed"
}
```

## 11. SSE Smoke Test

If you have a run ID:

```bash
export RUN_ID="<run id>"

curl -N "$AGENT_URL/api/v1/agent/runs/$RUN_ID/stream" \
  -H "Authorization: Bearer $TOKEN"
```

You should see `event:` lines such as:

```text
event: v1.step.started
event: v1.message.delta
event: v1.run.completed
```

## 12. Decision Approval Smoke Test

For write flows, first inspect the run:

```bash
curl -s "$AGENT_URL/api/v1/agent/runs/$RUN_ID" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

If the run is waiting for approval, use the ID from:

```text
pending_tool_calls[0].id
```

Then approve:

```bash
export TOOL_CALL_ID="<pending tool call id>"

curl -s -X POST "$AGENT_URL/api/v1/agent/runs/$RUN_ID/decisions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"tool_call_id\":\"$TOOL_CALL_ID\",\"decision\":\"approve\"}" \
  | python3 -m json.tool
```

If `pending_tool_calls` is empty, there is nothing to approve for that run.

## 13. Troubleshooting

### Login returns 401 or profile introspection fails

Check:

- `PRICEFRAME_BASE_URL` is the root host, not `/api`.
- `PRICEFRAME_JWT_SECRET` matches the deployed PriceFRAME secret.
- The token returned by PriceFRAME includes claims this service expects.

### Health returns degraded for PriceFRAME

Check that the deployed PriceFRAME host is reachable from Render:

```bash
curl -s "$PRICEFRAME_BASE_URL/api/google-drive/health"
```

### Gemini fails with temporary high demand

The Gemini Developer API can return temporary demand errors. Retry later, or
configure Vertex/Anthropic fallback credentials for longer testing.

### Decisions return `Tool call not found`

Fetch the run first:

```bash
curl -s "$AGENT_URL/api/v1/agent/runs/$RUN_ID" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

Use the `pending_tool_calls[].id` value, not a model/provider function-call ID.

### Attachments disappear

This guide uses local `/tmp` storage for free testing. Files can disappear when
the service restarts. Use S3-compatible storage for durable attachment tests.

## 14. More Durable Free-ish Alternative

If you need a free setup that lasts longer than Render free Postgres, split the
services:

- Web: Koyeb free web service
- Postgres: Supabase or Neon free Postgres
- Redis: Upstash Redis free tier

That path is more durable, but it has more moving parts. Render is the fastest
first deployment for this project.

References:

- [Koyeb docs](https://www.koyeb.com/docs/)
- [Supabase pricing](https://supabase.com/pricing)
- [Neon pricing](https://neon.tech/pricing)
- [Upstash pricing](https://upstash.com/pricing)
