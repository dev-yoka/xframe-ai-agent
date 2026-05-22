# xFRAME AI Agent — Quickstart

> One file. Step-by-step. Copy-paste commands. Expected outputs. Covers local setup, every feature test, free deployment, and troubleshooting.

**Estimated time:**
- Local setup + verification: ~30 minutes
- Full feature test pass: ~60 minutes
- Free cloud deployment: ~45 minutes

---

## Table of contents

- [Part 1 — Prerequisites](#part-1--prerequisites)
- [Part 2 — Local Setup](#part-2--local-setup)
- [Part 3 — Verify the Build](#part-3--verify-the-build)
- [Part 4 — Start the API](#part-4--start-the-api)
- [Part 5 — Configure LLM Provider (Optional)](#part-5--configure-llm-provider-optional)
- [Part 6 — Test Every Feature](#part-6--test-every-feature)
- [Part 7 — PriceFRAME Integration Tests](#part-7--priceframe-integration-tests)
- [Part 8 — Free Cloud Deployment](#part-8--free-cloud-deployment)
- [Part 9 — Troubleshooting](#part-9--troubleshooting)
- [Part 10 — Cleanup](#part-10--cleanup)

---

## Part 1 — Prerequisites

### 1.1 Required tools

```bash
python --version          # need 3.12+
docker --version          # need 24+
git --version
```

Install `uv` (Python package manager) if missing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or on macOS: brew install uv
uv --version
```

### 1.2 Required secrets from PriceFRAME team

| Secret | Used for | Required for |
|---|---|---|
| `PRICEFRAME_JWT_SECRET` | Verify JWTs locally | Phase 7 onwards |
| `PRICEFRAME_SERVICE_SECRET` | HMAC audit callbacks | Phase 7 write tests |

**Don't have them yet?** Phases 2-6 work with placeholders. Phase 7 (real PriceFRAME integration) requires them.

### 1.3 Optional: LLM provider credentials

Pick one (or both for failover):

- **Gemini Vertex (recommended)** — needs a GCP project + service account JSON key
- **Anthropic** — needs an API key from console.anthropic.com

Without any LLM provider, the agent runs in **deterministic mode** using `AgentLoop`. Most features still testable.

### 1.4 Deployed PriceFRAME (for Phase 7)

```
URL:      https://priceframe-yg.buy-frame.com
User:     admin@priceframe.local
Password: Pricing2026
```

The test user needs `agent.*` permissions seeded on the PriceFRAME profile. If you see fewer tools than expected in `/tools`, those permissions are missing — ask the PriceFRAME team to add them.

---

## Part 2 — Local Setup

### 2.1 Clone and install

```bash
git clone https://github.com/dev-yoka/xframe-ai-agent.git
cd xframe-ai-agent
uv sync --extra dev
```

**Expected:** ~30-60s; creates `.venv/` and installs all deps + dev tools.

### 2.2 Create `.env` file

```bash
cp .env.example .env
```

Edit `.env`. Minimum required values:

```dotenv
# Connection
DATABASE_URL=postgresql+asyncpg://xframe:xframe@localhost:5433/xframe_agent
REDIS_URL=redis://localhost:6379/0

# PriceFRAME (use real secrets for Phase 7; placeholders work for Phases 2-6)
PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=test-secret-32-chars-minimum-aaaaaaaa
PRICEFRAME_SERVICE_SECRET=test-service-secret-32-chars-aaaaaaaaa

# Local app config
APP_ENV=development
LOG_LEVEL=INFO
CORS_ORIGINS=http://localhost:5173,http://localhost:3000,http://localhost:8000

# Execution mode (inline = HTTP request waits for run completion)
RUN_EXECUTION_MODE=inline
```

### 2.3 Start dependencies (Postgres + Redis)

```bash
docker compose up -d postgres redis
```

**Expected output:**
```
[+] Running 3/3
 ✔ Network xframe-ai-agent_default       Created
 ✔ Container xframe-ai-agent-postgres-1  Started
 ✔ Container xframe-ai-agent-redis-1     Started
```

Verify both are healthy:

```bash
docker compose ps
# Both should show "Up" and "(healthy)" after ~5 seconds
```

### 2.4 Run database migrations

```bash
uv run alembic upgrade head
```

**Expected:**
```
INFO  [alembic.runtime.migration] Running upgrade  -> 202605190001, phase d agent core
INFO  [alembic.runtime.migration] Running upgrade 202605190001 -> 202605200001, phase e beta
```

Verify schema:

```bash
docker compose exec postgres psql -U xframe -d xframe_agent -c "\dt" 2>/dev/null \
  || psql postgres://xframe:xframe@localhost:5433/xframe_agent -c "\dt"
```

You should see ~13 tables (11 agent_* + agent_attachments + alembic_version).

---

## Part 3 — Verify the Build

### 3.1 Static checks (the CI gate)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
```

**Expected:** all three exit clean. If any fails, fix before proceeding.

### 3.2 Run the full test suite

```bash
uv run pytest -v
```

**Expected:** 40 tests pass in ~5 seconds:

```
tests/test_agent_api.py ..                                                [  5%]
tests/test_auth_jwt.py ...                                                [ 12%]
tests/test_auth_login.py ..                                               [ 17%]
tests/test_budget.py .....                                                [ 30%]
tests/test_create_pricing_request_flow.py ..                              [ 35%]
tests/test_dispatch.py ..                                                 [ 40%]
tests/test_health.py .                                                    [ 42%]
tests/test_phase_e_api.py ......                                          [ 57%]
tests/test_provider.py .                                                  [ 60%]
tests/test_redaction_wrapping.py ......                                   [ 75%]
tests/test_runner.py .....                                                [ 87%]
tests/test_tool_base.py ....                                              [ 97%]
evals/test_eval_ci.py .                                                   [100%]

============================== 40 passed in 5.21s ==============================
```

### 3.3 Verify OpenAPI is current

```bash
uv run python scripts/export_openapi.py
git diff --exit-code openapi.yaml
```

**Expected:** no diff. Committed schema matches code.

---

## Part 4 — Start the API

### 4.1 Boot uvicorn

```bash
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

**Expected output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started server process
INFO:     Application startup complete.
```

Keep this terminal running. Open a new terminal for the rest.

### 4.2 Health check

```bash
curl -s http://localhost:8000/api/v1/agent/health | python -m json.tool
```

**Expected:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "components": {
    "database": "ok",
    "redis": "ok"
  }
}
```

### 4.3 Open interactive API docs

```bash
open http://localhost:8000/api/v1/agent/docs   # or paste into browser
```

Swagger UI loads. Browse every endpoint, see request/response shapes, click "Try it out."

---

## Part 5 — Configure LLM Provider (Optional)

Skip this section if testing in deterministic mode (`AgentLoop`).

### 5.1 Option A: Gemini Vertex (recommended)

```bash
# 1. Create GCP project (if you don't have one)
gcloud projects create xframe-test --name="xFRAME Test"
gcloud config set project xframe-test

# 2. Enable Vertex AI API
gcloud services enable aiplatform.googleapis.com

# 3. Create service account + key
gcloud iam service-accounts create xframe-agent \
    --display-name="xFRAME AI Agent"

gcloud projects add-iam-policy-binding xframe-test \
    --member="serviceAccount:xframe-agent@xframe-test.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"

mkdir -p secrets
gcloud iam service-accounts keys create ./secrets/gcp-sa.json \
    --iam-account=xframe-agent@xframe-test.iam.gserviceaccount.com

# 4. Install the SDK
uv add google-genai
```

Add to `.env`:

```dotenv
GEMINI_VERTEX_PROJECT=xframe-test
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/xframe-ai-agent/secrets/gcp-sa.json
DEFAULT_MODEL=gemini-2.5-flash
```

### 5.2 Option B: Anthropic

```bash
uv add anthropic
```

Add to `.env`:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
DEFAULT_MODEL=claude-haiku-4-5
```

⚠️ **If using Anthropic alone**, make sure `DEFAULT_MODEL` is a Claude model. Vertex models like `gemini-2.5-flash` won't work with Anthropic.

### 5.3 Restart the API after provider config

```bash
# Ctrl+C the running uvicorn, then:
uv run uvicorn xframe_agent.main:app --reload --port 8000
```

### 5.4 Verify provider configuration

In a new terminal:

```bash
uv run python -c "
from xframe_agent.settings import Settings
s = Settings()
print(f'Provider configured: {s.provider_configured}')
print(f'Vertex project: {s.gemini_vertex_project}')
print(f'Anthropic configured: {bool(s.anthropic_api_key)}')
print(f'Default model: {s.default_model}')
"
```

**Expected:** `Provider configured: True`. Now `dispatch.execute_run` routes to `ModelRunner` (LLM-driven).

---

## Part 6 — Test Every Feature

This section tests with **placeholder PriceFRAME secrets** — no real PriceFRAME calls. Phase 7 covers real integration.

### 6.1 Generate a test JWT

```bash
export TOKEN=$(uv run python -c "
import jwt, time
print(jwt.encode(
    {'user_id': 1, 'role_id': 1, 'profile_id': 1, 'session_id': 1,
     'email': 'admin@priceframe.local',
     'exp': int(time.time()) + 86400},
    'test-secret-32-chars-minimum-aaaaaaaa',  # matches PRICEFRAME_JWT_SECRET
    algorithm='HS256'
))
")
echo "Token (first 50 chars): $(echo $TOKEN | cut -c1-50)..."
```

⚠️ This token is **valid for JWT verification only**. Profile fetch (`/auth/me`, `/tools`) will fail because we're not authenticated against the real PriceFRAME. For full testing, use Phase 7.

### 6.2 Test 1: Health endpoint (no auth)

```bash
curl -s http://localhost:8000/api/v1/agent/health | python -m json.tool
```

✅ Should return `{"status": "ok", ...}`.

### 6.3 Test 2: 401 on missing auth

```bash
curl -i http://localhost:8000/api/v1/agent/conversations
```

✅ Should return `HTTP/1.1 401 Unauthorized` with `{"error": {"code": "http_401", "message": "..."}}`.

### 6.4 Test 3: Standardized error envelope

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{}' | python -m json.tool
```

✅ Validation error returns:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "detail": "..."
  }
}
```

### 6.5 Test 4: Idempotency replay (no PriceFRAME needed)

We can test the idempotency mechanism by hitting `POST /conversations` twice with the same key.

```bash
KEY=$(uuidgen)

# First call
RESPONSE1=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $KEY" \
    -d '{"title":"Test idempotency","kind":"general"}')

# Second call with same key
RESPONSE2=$(curl -i -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $KEY" \
    -d '{"title":"Different title","kind":"general"}')

echo "First response: $RESPONSE1"
echo "Second response:"
echo "$RESPONSE2"
```

✅ Second call should return the **same conversation** as the first, with header `Idempotency-Replayed: true`, even though the body differs. (Requires `/auth/me` to succeed though, which needs Phase 7. Skip if it fails.)

### 6.6 Test 5: Rate limiting (429)

The default rate limit is 120 requests per 60s per IP+path. Trigger it:

```bash
# Hit the same endpoint rapidly
for i in {1..150}; do
    curl -s -o /dev/null -w "%{http_code} " http://localhost:8000/api/v1/agent/health
done
echo
```

✅ You should see a mix of `200`s followed by `429`s after ~120 requests. The response includes `Retry-After`.

Verify in headers:

```bash
curl -i -s http://localhost:8000/api/v1/agent/health | head -20
```

### 6.7 Test 6: Request ID propagation

```bash
curl -i -s -H "X-Request-ID: my-custom-id-123" http://localhost:8000/api/v1/agent/health | grep -i request-id
```

✅ Response includes `X-Request-ID: my-custom-id-123`. Look at the uvicorn terminal — logs for this request include the ID.

### 6.8 Test 7: Database inspection

In a new terminal:

```bash
docker compose exec postgres psql -U xframe -d xframe_agent <<'EOF'
\dt
SELECT COUNT(*) FROM agent_conversations;
SELECT COUNT(*) FROM agent_runs;
SELECT COUNT(*) FROM agent_run_events;
EOF
```

✅ All 11 tables exist. Row counts reflect what you've done.

### 6.9 Test 8: Static check / coverage

```bash
uv run pytest --cov=src/xframe_agent --cov-report=term-missing
```

✅ See coverage by module. Useful for spotting under-tested code paths.

### 6.10 Test 9: Inspect a specific tool's contract

```bash
uv run python -c "
from xframe_agent.tools.priceframe_read import GetCurrencyRateTool
from xframe_agent.tools.priceframe_write import CreateQuotationTool
import json

print('GetCurrencyRate JSON Schema:')
print(json.dumps(GetCurrencyRateTool.to_provider_schema(), indent=2))
print()
print('CreateQuotation JSON Schema:')
print(json.dumps(CreateQuotationTool.to_provider_schema(), indent=2))
"
```

✅ Prints the JSON Schema sent to the LLM for each tool. This is what the model literally sees.

### 6.11 Test 10: Run the full test suite again (sanity)

```bash
uv run pytest
```

✅ 40 tests still pass after all the experiments.

---

## Part 7 — PriceFRAME Integration Tests

**Prerequisite:** real `PRICEFRAME_JWT_SECRET` and `PRICEFRAME_SERVICE_SECRET` in `.env`. Restart uvicorn after updating `.env`.

### 7.1 Test 11: Login proxy

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' | python -m json.tool
```

✅ Expected:
```json
{
  "token": "eyJ...",
  "user": {"id": ..., "email": "admin@priceframe.local"},
  "role_code": "...",
  "profile_code": "...",
  "permissions": ["agent.enabled", "agent.quotes.read", ...],
  "expires_at": ...
}
```

Save the real token:

```bash
export TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
echo "Token saved: $(echo $TOKEN | cut -c1-40)..."
```

### 7.2 Test 12: `/auth/me`

```bash
curl -s http://localhost:8000/api/v1/agent/auth/me \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Returns user info matching the login response.

### 7.3 Test 13: `/auth/refresh`

If the login response included a refresh token (depends on PriceFRAME config):

```bash
REFRESH_TOKEN="<from-login-response-if-present>"

curl -s -X POST http://localhost:8000/api/v1/agent/auth/refresh \
    -H "Content-Type: application/json" \
    -d "{\"refresh_token\":\"$REFRESH_TOKEN\"}" | python -m json.tool
```

✅ Returns a fresh token.

### 7.4 Test 14: List tools (permission-filtered)

```bash
curl -s http://localhost:8000/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Returns all 12 tools if the user has all `agent.*` permissions. If fewer appear, the missing permissions explain why.

Count the tools:

```bash
curl -s http://localhost:8000/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -c "import json,sys;d=json.load(sys.stdin);print(f'{len(d[\"tools\"])} tools available')"
```

### 7.5 Test 15: Create a conversation

```bash
export CONV=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"title":"Manual test","kind":"create_pricing_request"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "Conversation ID: $CONV"
```

### 7.6 Test 16: List conversations with pagination

```bash
curl -s "http://localhost:8000/api/v1/agent/conversations?limit=5" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Returns `conversations: [...]`, `next_cursor`, `has_more`. Verify pagination by creating 6+ conversations.

### 7.7 Test 17: Get conversation detail

```bash
curl -s "http://localhost:8000/api/v1/agent/conversations/$CONV" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Returns conversation + first 50 messages.

### 7.8 Test 18: Update conversation (PATCH)

```bash
curl -s -X PATCH "http://localhost:8000/api/v1/agent/conversations/$CONV" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"Updated title","pinned":true}' | python -m json.tool
```

✅ Returns the updated conversation.

### 7.9 Test 19: Send a message (with run)

**LLM-driven mode** (provider configured):

```bash
export RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"What are my permissions?","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")
echo "Run ID: $RUN"
```

✅ Returns `{"run_id": "...", "status": "completed"}`. The LLM responded based on the user context in the system prompt.

**Deterministic mode** (no provider): same call returns `status: "awaiting_decision"` because AgentLoop pauses every tool.

### 7.10 Test 20: Inspect the run

```bash
curl -s "http://localhost:8000/api/v1/agent/runs/$RUN" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Returns run details with status, timestamps.

### 7.11 Test 21: The event log (the most useful query)

```bash
docker compose exec -T postgres psql -U xframe -d xframe_agent -c "
SELECT seq, event_type,
       payload->>'tool_name' AS tool,
       payload->>'cause' AS cause,
       created_at
FROM agent_run_events
WHERE run_id = '$RUN'
ORDER BY seq;
"
```

✅ Shows the full timeline of what happened.

### 7.12 Test 22: SSE stream (in new terminal)

```bash
# Start a new run AND simultaneously stream it
NEW_RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"What tools can you use?","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

curl -N -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/api/v1/agent/runs/$NEW_RUN/stream"
```

✅ Events stream in real time:

```
id: 1
event: v1.step.started
data: {"run_id":"01HX...","seq":1,"ts":"...","step":1,"kind":"model_call"}

id: 2
event: v1.message.delta
data: ...

id: N
event: v1.run.completed
data: ...
```

### 7.13 Test 23: SSE reconnection (`Last-Event-ID`)

```bash
# Reconnect skipping the first event
curl -N -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/api/v1/agent/runs/$NEW_RUN/stream?last_event_id=1"
```

✅ Stream starts from event 2 onwards (events 1 is skipped).

### 7.14 Test 24: Trigger a write tool (HITL approve)

```bash
# Ask the agent to create a quotation
WRITE_RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create a draft quotation titled \"Smoke Test\" for customer 1 in USD","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

sleep 3

# Check status — should be awaiting_decision
curl -s "http://localhost:8000/api/v1/agent/runs/$WRITE_RUN" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Run status is `awaiting_decision`. The LLM proposed `create_quotation`; the harness paused for approval.

Find the proposed tool call:

```bash
export TC=$(docker compose exec -T postgres psql -U xframe -d xframe_agent -At -c "
SELECT id FROM agent_tool_calls
WHERE run_id = '$WRITE_RUN' AND status = 'proposed'
LIMIT 1;
")
echo "Tool call ID: $TC"
```

Approve it:

```bash
curl -s -X POST "http://localhost:8000/api/v1/agent/runs/$WRITE_RUN/decisions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool_call_id\":\"$TC\",\"decision\":\"approve\"}" | python -m json.tool
```

✅ Tool executes against PriceFRAME, HMAC audit callback fires, response includes the new quote ID.

⚠️ **This creates a real quotation in PriceFRAME.** Clean up in the PriceFRAME UI after testing.

### 7.15 Test 25: HITL reject

```bash
# Create another write proposal
RUN_REJECT=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create another draft quotation","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

sleep 3

TC_REJECT=$(docker compose exec -T postgres psql -U xframe -d xframe_agent -At -c "
SELECT id FROM agent_tool_calls
WHERE run_id = '$RUN_REJECT' AND status = 'proposed'
LIMIT 1;
")

curl -s -X POST "http://localhost:8000/api/v1/agent/runs/$RUN_REJECT/decisions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool_call_id\":\"$TC_REJECT\",\"decision\":\"reject\"}" | python -m json.tool
```

✅ Returns `{"success": true, "tool_call_status": "rejected"}`. No write happens.

### 7.16 Test 26: HITL edit-then-approve

```bash
RUN_EDIT=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create a quote for customer 1","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

sleep 3

TC_EDIT=$(docker compose exec -T postgres psql -U xframe -d xframe_agent -At -c "
SELECT id FROM agent_tool_calls
WHERE run_id = '$RUN_EDIT' AND status = 'proposed'
LIMIT 1;
")

curl -s -X POST "http://localhost:8000/api/v1/agent/runs/$RUN_EDIT/decisions" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
        \"tool_call_id\":\"$TC_EDIT\",
        \"decision\":\"edit\",
        \"edited_args\":{
            \"title\":\"Customized by approver\",
            \"customer_id\":1,
            \"currency\":\"USD\",
            \"notes\":\"Edited via decisions endpoint\"
        }
    }" | python -m json.tool
```

✅ Executes with the edited args. New quote in PriceFRAME has the edited title.

### 7.17 Test 27: Run cancellation

```bash
# Start a run
CANCEL_RUN=$(curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Do something complex","source":"text"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

# Cancel it
curl -s -X POST "http://localhost:8000/api/v1/agent/runs/$CANCEL_RUN/cancel" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ Run status becomes `cancelled`.

### 7.18 Test 28: Individual tool invocations (deterministic directive)

Even with LLM-driven mode, you can force-test specific tools using literal directives. **Note:** these work only via `AgentLoop`, so they require *no* provider configured. Otherwise the LLM might interpret the directive as text.

For deterministic-only testing of each tool:

```bash
# Temporarily unset providers:
# Comment out GEMINI_VERTEX_PROJECT and ANTHROPIC_API_KEY in .env, restart uvicorn

# Then test each tool:

# 1. list_my_quotations
curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"list_my_quotations\",\"args\":{\"limit\":5}}","source":"text"}'

# 2. get_currency_rate
curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"get_currency_rate\",\"args\":{\"currency\":\"USD\"}}","source":"text"}'

# 3. list_corridors_available
curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"list_corridors_available\",\"args\":{}}","source":"text"}'
```

After each, check `agent_tool_calls`:

```bash
docker compose exec -T postgres psql -U xframe -d xframe_agent -c "
SELECT tool_name, status, result->>'data'
FROM agent_tool_calls
WHERE run_id IN (SELECT id FROM agent_runs WHERE conversation_id = '$CONV')
ORDER BY created_at DESC LIMIT 5;
"
```

### 7.19 Test 29: Memory CRUD

```bash
# List memory (initially empty)
curl -s http://localhost:8000/api/v1/agent/memory \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Memory writes happen via the agent's summarizer (in `AgentLoop`). To populate, send a message like:

```bash
curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/messages" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"remember that I prefer USD corridors","source":"text"}'

sleep 2

# Now check memory
curl -s http://localhost:8000/api/v1/agent/memory \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

✅ A memory row appears. Delete it:

```bash
MEM_ID=$(curl -s http://localhost:8000/api/v1/agent/memory \
    -H "Authorization: Bearer $TOKEN" \
    | python -c "import json,sys;d=json.load(sys.stdin);print(d['memories'][0]['id'] if d.get('memories') else '')")

curl -i -X DELETE "http://localhost:8000/api/v1/agent/memory/$MEM_ID" \
    -H "Authorization: Bearer $TOKEN"
```

✅ Returns `204 No Content`. Memory row gone.

### 7.20 Test 30: Attachment upload

Create a test file:

```bash
echo "This is a test document for xFRAME." > /tmp/test.txt
```

Upload it:

```bash
curl -s -X POST http://localhost:8000/api/v1/agent/attachments \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@/tmp/test.txt" | python -m json.tool
```

✅ Returns:
```json
{
  "id": "01HX...",
  "filename": "test.txt",
  "size_bytes": ...,
  "status": "pending_scan" or "ready",
  ...
}
```

⚠️ **Requires MinIO running.** If you only started `postgres redis`, the upload will fail. Run `docker compose up -d minio` first.

For full local storage instead of S3:

```dotenv
# In .env
ATTACHMENT_STORAGE_BACKEND=local
ATTACHMENT_LOCAL_STORAGE_PATH=.data/attachments
```

Then restart uvicorn.

Get the attachment download URL:

```bash
ATT_ID=01HX...  # from upload response
curl -s "http://localhost:8000/api/v1/agent/attachments/$ATT_ID" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### 7.21 Test 31: Voice transcription (requires Groq API key)

Skip if no Groq API key. To enable:

```dotenv
# In .env
GROQ_API_KEY=gsk_...
```

Restart uvicorn, then test with an audio file:

```bash
# Need a test audio file
curl -s -X POST http://localhost:8000/api/v1/agent/voice/transcriptions \
    -H "Authorization: Bearer $TOKEN" \
    -F "audio=@/path/to/test.mp3"
```

Without a Groq key, this returns 503.

### 7.22 Test 32: Budget overflow (engineered failure)

Set very low budgets and force an overflow:

```dotenv
# Temporarily in .env
MAX_STEPS_PER_RUN=2
MAX_TOOL_CALLS_PER_RUN=2
```

Restart, then trigger a complex flow:

```bash
curl -s -X POST "http://localhost:8000/api/v1/agent/conversations/$CONV/runs" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Look up customer 1, then list corridors, then get USD rate, then EUR rate","source":"text"}'
```

The run should error with `cause: step_budget_exceeded`. Verify:

```bash
docker compose exec -T postgres psql -U xframe -d xframe_agent -c "
SELECT event_type, payload->>'cause' AS cause, payload->>'message' AS msg
FROM agent_run_events
WHERE event_type = 'v1.run.error'
ORDER BY created_at DESC LIMIT 3;
"
```

### 7.23 Feature test checklist

Tick off as you verify:

- [ ] Health endpoint
- [ ] 401 on missing auth
- [ ] Error envelope
- [ ] Idempotency replay
- [ ] Rate limit triggers 429
- [ ] Request ID propagation
- [ ] Database schema present
- [ ] Login proxy
- [ ] /auth/me
- [ ] /auth/refresh (if applicable)
- [ ] /tools permission-filtered
- [ ] Conversation CRUD (POST, GET, PATCH, DELETE)
- [ ] Pagination
- [ ] Send message → run completes
- [ ] Event log captures everything
- [ ] SSE stream events in real time
- [ ] SSE reconnection with Last-Event-ID
- [ ] HITL approve a write
- [ ] HITL reject
- [ ] HITL edit-then-approve
- [ ] Run cancellation
- [ ] Individual tool invocation (deterministic directive)
- [ ] Memory list, create (via summarizer), delete
- [ ] Attachment upload
- [ ] Voice transcription (if Groq key)
- [ ] Budget overflow handled gracefully

If everything ticks ✅, the v1 surface is verified.

---

## Part 8 — Free Cloud Deployment

Three options, ordered by ease of setup. **All free for light testing.**

### 8.1 Option A: Railway (easiest, ~$5 free credit)

[Railway](https://railway.app) gives you Postgres + Redis + your app, all on one platform, with a $5 free trial credit. Enough for weeks of testing.

#### 8.1.1 Prerequisites

- A Railway account (sign up at railway.app — no credit card needed for trial)
- The Railway CLI:
  ```bash
  npm install -g @railway/cli
  railway login   # opens browser
  ```

#### 8.1.2 Create the project

```bash
cd xframe-ai-agent
railway init   # creates a new Railway project
```

#### 8.1.3 Add Postgres and Redis

In the Railway dashboard:

1. Click "+ New" → Database → **Add PostgreSQL**.
2. Click "+ New" → Database → **Add Redis**.

Or via CLI:

```bash
railway add postgresql
railway add redis
```

Railway provisions both. Note the connection strings (Settings → Variables).

#### 8.1.4 Deploy the app

Railway can build from your local code or GitHub. Easiest: GitHub.

In the dashboard:

1. Project → "+ New" → "GitHub Repo" → select `dev-yoka/xframe-ai-agent`.
2. Railway auto-detects the Dockerfile and builds.

#### 8.1.5 Set environment variables

In Railway dashboard, the app service → Variables → add:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<from-priceframe-team>
PRICEFRAME_SERVICE_SECRET=<from-priceframe-team>
APP_ENV=production
LOG_LEVEL=INFO
RUN_EXECUTION_MODE=inline
SSE_HEARTBEAT_SECONDS=15
PORT=8000
```

⚠️ **The asyncpg DATABASE_URL needs the `+asyncpg` driver suffix.** Railway sets `DATABASE_URL=postgresql://...`. Override manually:

```
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db
```

Copy the values from `${{Postgres.DATABASE_URL}}` and substitute `postgresql://` with `postgresql+asyncpg://`.

For LLM provider (optional):

```
ANTHROPIC_API_KEY=sk-ant-...
DEFAULT_MODEL=claude-haiku-4-5
```

(Anthropic is simplest on Railway since you don't need to mount a GCP JSON key.)

#### 8.1.6 Expose the service

In the dashboard, the app service → Settings → Networking → **Generate Domain**.

Railway gives you a `*.up.railway.app` URL.

#### 8.1.7 Deploy

Push to your branch on GitHub. Railway auto-deploys.

Or trigger manually via CLI:

```bash
railway up
```

#### 8.1.8 Verify

```bash
curl -s https://<your-app>.up.railway.app/api/v1/agent/health | python -m json.tool
```

✅ Should return `{"status": "ok", ...}`.

Run a smoke test:

```bash
TOKEN=$(curl -s -X POST https://<your-app>.up.railway.app/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['token'])")

curl -s https://<your-app>.up.railway.app/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

#### 8.1.9 Cost

The $5 credit lasts ~30 days for a small workload. After that, ~$5-10/month for a low-traffic xFRAME deploy. Or pause the project to stop charges.

---

### 8.2 Option B: Fly.io + Neon + Upstash (truly free combo)

This combo is **fully free** (within generous limits). Three providers:

- **Fly.io** — hosts the agent container (free: 3 small VMs, 256 MB RAM each)
- **Neon** — Postgres (free: 3 GB storage, 1 shared compute)
- **Upstash** — Redis (free: 10,000 commands/day)

#### 8.2.1 Sign up for all three

- Fly.io: https://fly.io/app/sign-up
- Neon: https://neon.tech
- Upstash: https://upstash.com

#### 8.2.2 Set up Neon (Postgres)

1. Create a project in Neon dashboard.
2. Copy the connection string.
3. Change `postgresql://` to `postgresql+asyncpg://`.

Example:
```
postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/xframe_agent?sslmode=require
```

#### 8.2.3 Set up Upstash (Redis)

1. Create a Redis database.
2. Copy the connection URL (`rediss://...`).

Example:
```
rediss://default:pass@us1-xyz.upstash.io:6379
```

⚠️ Use `rediss://` (with double s) for TLS. arq supports this.

#### 8.2.4 Install Fly CLI

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
```

#### 8.2.5 Create `fly.toml`

In the project root:

```toml
app = "xframe-agent-test"
primary_region = "iad"

[build]
  dockerfile = "Dockerfile"

[env]
  APP_ENV = "production"
  LOG_LEVEL = "INFO"
  RUN_EXECUTION_MODE = "inline"
  SSE_HEARTBEAT_SECONDS = "15"
  PRICEFRAME_BASE_URL = "https://priceframe-yg.buy-frame.com"
  CORS_ORIGINS = "*"
  PORT = "8000"
  PROMETHEUS_ENABLED = "false"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 1

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"
```

`auto_stop_machines = true` lets the VM sleep when idle — saves free tier hours. Wakes on incoming request (adds ~3s latency on cold start).

#### 8.2.6 Set secrets

```bash
fly secrets set \
  DATABASE_URL="postgresql+asyncpg://user:pass@ep-xxx.aws.neon.tech/xframe_agent?sslmode=require" \
  REDIS_URL="rediss://default:pass@us1-xyz.upstash.io:6379" \
  PRICEFRAME_JWT_SECRET="<real-secret-from-priceframe>" \
  PRICEFRAME_SERVICE_SECRET="<real-secret-from-priceframe>" \
  ANTHROPIC_API_KEY="sk-ant-..."
```

#### 8.2.7 Launch

```bash
fly launch --no-deploy   # creates the app without deploying
fly deploy                # builds + deploys
```

The entrypoint runs `alembic upgrade head` automatically on container start, so migrations apply to Neon.

#### 8.2.8 Verify

```bash
fly status                                     # see app status
curl -s https://xframe-agent-test.fly.dev/api/v1/agent/health
fly logs                                       # tail logs
```

#### 8.2.9 Test from a client

```bash
APP_URL=https://xframe-agent-test.fly.dev

TOKEN=$(curl -s -X POST $APP_URL/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['token'])")

curl -s $APP_URL/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

#### 8.2.10 Free tier limits

| Service | Limit | What happens beyond |
|---|---|---|
| Fly.io | 3 small VMs, 160 GB outbound bandwidth/mo | Soft warnings, then $ charged |
| Neon | 3 GB Postgres, 100 hours compute/mo | DB read-only until next month |
| Upstash | 10K Redis commands/day | Throttled |

For testing/demo: comfortably free. For real users at low traffic: ~free or a few dollars/month.

---

### 8.3 Option C: Render (simplest UI, generous free tier — but with caveat)

[Render](https://render.com) offers free web services + free Postgres.

⚠️ **Caveat for xFRAME**: Render's **free instances sleep after 15 minutes of inactivity**. The wake-up takes ~30s. This breaks SSE for the wake-up duration and degrades UX. Acceptable for occasional testing; not for active demos.

Steps if you accept the caveat:

1. Connect GitHub repo via the Render dashboard.
2. New → Web Service → pick your fork.
3. Render detects the Dockerfile, builds.
4. New → PostgreSQL → free tier (90-day expiry, then $).
5. Set env vars in the web service settings (copy DATABASE_URL with `+asyncpg`, etc.).
6. Redis: use Upstash separately (Render doesn't offer free Redis).

Deploys on every git push. Comes with a `*.onrender.com` URL.

For active demos, **Railway** or **Fly.io** are better choices.

---

### 8.4 Configuring providers in cloud deployments

For **Anthropic**, just set the env var:

```bash
fly secrets set ANTHROPIC_API_KEY="sk-ant-..."
# or in Railway: dashboard → Variables → ANTHROPIC_API_KEY
```

For **Gemini Vertex**, you need to mount the GCP service account JSON.

**Fly.io**:

```bash
# Encode the JSON as base64
base64 -i secrets/gcp-sa.json | tr -d '\n' > sa.b64

# Set as a secret
fly secrets set GCP_SA_KEY_B64="$(cat sa.b64)"

# Update entrypoint to decode at startup:
# Add to scripts/entrypoint.sh BEFORE the alembic call:
#   if [ -n "$GCP_SA_KEY_B64" ]; then
#     mkdir -p /var/run/secrets
#     echo "$GCP_SA_KEY_B64" | base64 -d > /var/run/secrets/gcp.json
#   fi
```

Then set:

```bash
fly secrets set \
  GOOGLE_APPLICATION_CREDENTIALS=/var/run/secrets/gcp.json \
  GEMINI_VERTEX_PROJECT=your-project \
  GEMINI_VERTEX_LOCATION=us-central1
```

**Railway**: similar — set `GCP_SA_KEY_B64` as a variable and update the entrypoint.

For testing, **Anthropic is simpler** because it's a single env var.

### 8.5 Free deployment checklist

After cloud deploy:

- [ ] `GET /health` returns `ok`
- [ ] `POST /auth/login` works against deployed PriceFRAME
- [ ] `GET /tools` returns expected tool list
- [ ] Create conversation works
- [ ] Send message + run completes
- [ ] SSE stream works through the platform's proxy
- [ ] LLM provider responds (if configured)
- [ ] Database migrations applied on startup
- [ ] Logs accessible via platform UI/CLI

If all ticked, your deployment is functional.

---

## Part 9 — Troubleshooting

### 9.1 Build / install issues

| Symptom | Cause | Fix |
|---|---|---|
| `uv sync` slow first time | Downloading wheels | Normal; subsequent runs use cache |
| `python: command not found` | Python 3.12 not installed | `pyenv install 3.12` or `brew install python@3.12` |
| `Address already in use :8000` | Another process on port 8000 | `lsof -i :8000` to find; `--port 8001` to use another |
| `Cannot connect to Docker daemon` | Docker Desktop not running | Start Docker Desktop |

### 9.2 Database issues

| Symptom | Cause | Fix |
|---|---|---|
| `connection refused` to postgres | Container not up | `docker compose ps`; `docker compose up -d postgres` |
| `password authentication failed` | Wrong creds in `.env` | Match `DATABASE_URL` user/pass to compose file |
| `relation "agent_runs" does not exist` | Migrations not run | `uv run alembic upgrade head` |
| `port 5433 already in use` | Another Postgres on 5433 | `docker compose down`; pick another port in compose |

### 9.3 Auth issues

| Symptom | Cause | Fix |
|---|---|---|
| 401 with valid-looking JWT | JWT secret mismatch | Verify `PRICEFRAME_JWT_SECRET` matches what PriceFRAME signs with |
| 401 from `/auth/login` | Wrong PriceFRAME credentials | Verify with the PriceFRAME team |
| 401 from `/auth/me` after login | Token expired | Re-login |
| 403 on tool calls | User missing `agent.*` perms | Update PriceFRAME profile |

### 9.4 Runtime issues

| Symptom | Cause | Fix |
|---|---|---|
| Run stuck in `running` | Crashed mid-execution | Check `agent_run_events` for last event; `POST /runs/{id}/cancel` |
| `cause: provider_error` | All providers failed | Check API keys, quotas, network egress |
| `cause: step_budget_exceeded` | Too many iterations | Increase `MAX_STEPS_PER_RUN` or refine prompt |
| `cause: loop_detected` | Model repeating same tool | Look at the tool args; usually missing data |
| SSE events in burst at end | Server buffering | Verify response not gzipped; verify proxy_buffering off |
| 502 from cloud platform | Container failing health checks | `fly logs` / Railway logs |

### 9.5 LLM provider issues

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: google.genai` | SDK not installed | `uv add google-genai` |
| `ModuleNotFoundError: anthropic` | SDK not installed | `uv add anthropic` |
| `GEMINI_VERTEX_PROJECT is not configured` | Env var missing | Add to `.env` and restart |
| `GOOGLE_APPLICATION_CREDENTIALS not found` | File path wrong | Use absolute path; verify file exists |
| Vertex 403 / "permission denied" | SA missing role | Add `roles/aiplatform.user` to the service account |
| Anthropic 401 | API key invalid | Regenerate at console.anthropic.com |

### 9.6 Deployment issues

| Symptom | Cause | Fix |
|---|---|---|
| Railway/Fly: build fails | Dockerfile error | Check build logs; build locally first to verify |
| Fly: 502 after deploy | App crashing | `fly logs`; usually missing env var |
| Cold-start latency on free tier | Render free tier sleeps | Use Railway or Fly with `min_machines_running=1` |
| Database url asyncpg error | URL uses `postgresql://` not `postgresql+asyncpg://` | Edit env var |
| Migrations failing on first deploy | Database not reachable | Verify connection string |

### 9.7 The most useful debug commands

```bash
# Local
docker compose ps
docker compose logs xframe-agent
docker compose logs postgres
uv run pytest tests/test_dispatch.py -v   # quick smoke

# Database inspection
docker compose exec postgres psql -U xframe -d xframe_agent
# Inside psql:
SELECT id, status, error FROM agent_runs ORDER BY created_at DESC LIMIT 5;
SELECT seq, event_type, payload FROM agent_run_events WHERE run_id = '...' ORDER BY seq;

# Cloud
fly logs                               # Fly.io
railway logs                           # Railway
fly ssh console                        # SSH into the container
fly status                             # check VM state
```

---

## Part 10 — Cleanup

### 10.1 Local

```bash
# Stop uvicorn: Ctrl+C in its terminal

# Stop containers (preserves data)
docker compose stop

# Stop AND remove containers + volumes (DESTROYS DATA)
docker compose down -v
```

### 10.2 PriceFRAME

Delete any test quotations you created via the PriceFRAME admin UI.

### 10.3 Cloud

- **Railway**: project settings → Delete Project
- **Fly.io**: `fly apps destroy xframe-agent-test`
- **Neon**: dashboard → project settings → Delete
- **Upstash**: dashboard → database → Delete
- **Render**: dashboard → service → Settings → Delete

### 10.4 GCP

If you set up Vertex:

```bash
gcloud iam service-accounts keys list \
    --iam-account=xframe-agent@xframe-test.iam.gserviceaccount.com
gcloud iam service-accounts keys delete <KEY_ID> \
    --iam-account=xframe-agent@xframe-test.iam.gserviceaccount.com
gcloud projects delete xframe-test
```

### 10.5 Anthropic

Visit console.anthropic.com → API Keys → revoke the test key.

---

## What's Next

After completing this quickstart, recommended deeper reading:

| Want to… | Read… |
|---|---|
| Understand the architecture | `docs/handbook/03-architecture.md` |
| Learn AI agents from scratch | `docs/book/part-01-foundations.md` |
| Add a new tool | `docs/book/part-07-tools-integrations.md` Chapter 51 |
| Debug a stuck run | `docs/handbook/10-debugging-guide.md` |
| Production-grade deployment | `docs/deploy/v1-deployment.md` |
| The full engineering book | `docs/book/00-front-matter.md` |

---

## Appendix — One-page command cheat sheet

```bash
# Local setup
uv sync --extra dev
docker compose up -d postgres redis
uv run alembic upgrade head
uv run uvicorn xframe_agent.main:app --reload --port 8000

# Tests
uv run pytest                              # all
uv run ruff check . && uv run mypy         # static
uv run pytest --cov=src/xframe_agent       # coverage

# Login + token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' \
    | python -c "import json,sys;print(json.load(sys.stdin)['token'])")

# Common API calls
curl -s http://localhost:8000/api/v1/agent/health | python -m json.tool
curl -s http://localhost:8000/api/v1/agent/tools -H "Authorization: Bearer $TOKEN" | python -m json.tool
curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" -d '{"title":"...","kind":"create_pricing_request"}'

# Database
docker compose exec postgres psql -U xframe -d xframe_agent
# Quick checks:
SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 5;
SELECT seq, event_type, payload FROM agent_run_events WHERE run_id = '...' ORDER BY seq;
SELECT * FROM agent_tool_calls WHERE run_id = '...';

# Cloud deployment
fly deploy                                 # Fly.io
railway up                                 # Railway

# Cleanup
docker compose down -v                     # local
fly apps destroy <app>                     # Fly
```

---

**End of quickstart.** Time to ship.
