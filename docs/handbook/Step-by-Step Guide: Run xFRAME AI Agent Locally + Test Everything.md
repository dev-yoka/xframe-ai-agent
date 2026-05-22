Step-by-Step Guide: Run xFRAME AI Agent Locally + Test Everything
Practical hands-on guide. Copy-paste commands. Expected outputs included. ~45-60 minutes for the full walkthrough.

Phase 0 — Prerequisites
# Verify versions
python --version          # need 3.12+
docker --version          # need 24+
git --version
Install uv if you don't have it:

curl -LsSf https://astral.sh/uv/install.sh | sh
# or: brew install uv
uv --version              # need any modern version
What you'll need from the PriceFRAME team (for full integration tests):

Secret	Used for
PRICEFRAME_JWT_SECRET	Verify JWTs issued by PriceFRAME
PRICEFRAME_SERVICE_SECRET	Sign audit callbacks (HMAC)
Don't have these yet? You can still complete Phases 1-3 and most of Phase 4 (deterministic mode). Skip Phase 5's integration tests until you have the secrets.

Phase 1 — Initial Setup (~10 min)
1.1 Clone and install
git clone https://github.com/dev-yoka/xframe-ai-agent.git
cd xframe-ai-agent
uv sync --extra dev
Expected: ~30-60s install. Creates .venv/ and installs all deps + dev tools.

1.2 Configure environment
cp .env.example .env
Open .env and edit:

# Required even for deterministic mode
PRICEFRAME_BASE_URL=https://priceframe-yg.buy-frame.com
PRICEFRAME_JWT_SECRET=<paste-from-priceframe-team>
PRICEFRAME_SERVICE_SECRET=<paste-from-priceframe-team>

# Standard local config (already in .env.example)
DATABASE_URL=postgresql+asyncpg://xframe:xframe@localhost:5433/xframe_agent
REDIS_URL=redis://localhost:6379/0

# Optional: if you want LLM-driven mode (Phase 4b)
# GEMINI_VERTEX_PROJECT=your-gcp-project
# GEMINI_VERTEX_LOCATION=us-central1
# GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/gcp-sa.json
# ANTHROPIC_API_KEY=sk-ant-...
Don't have PriceFRAME secrets yet? Use placeholders to start:

PRICEFRAME_JWT_SECRET=test-secret-32-chars-minimum-aaaaaaaa
PRICEFRAME_SERVICE_SECRET=test-service-secret-32-chars-aaaaaaaaa
These let the agent boot. Real PriceFRAME calls will fail until you have the real secrets.

1.3 Start dependencies
docker compose up -d postgres redis
Expected:

[+] Running 3/3
 ✔ Network xframe-ai-agent_default       Created
 ✔ Container xframe-ai-agent-postgres-1  Started
 ✔ Container xframe-ai-agent-redis-1     Started
Verify:

docker compose ps
# Both should show "Up" and "(healthy)" after ~5 seconds
1.4 Run database migrations
uv run alembic upgrade head
Expected:

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 202605190001, phase d agent core
INFO  [alembic.runtime.migration] Running upgrade 202605190001 -> 202605200001, phase e beta
Verify the schema landed:

psql postgres://xframe:xframe@localhost:5433/xframe_agent -c "\dt"
You should see 11 tables: agent_conversations, agent_messages, agent_runs, agent_run_steps, agent_run_events, agent_tool_calls, agent_idempotency_keys, agent_users_cache, agent_device_tokens, agent_audit_log, agent_attachments, agent_attachment_pages, agent_user_memory, plus alembic_version.

Phase 2 — Verify the Build (~5 min)
2.1 Static checks (the CI gate)
uv run ruff format --check .
uv run ruff check .
uv run mypy
Expected: all three exit clean. No formatting needed, no lint errors, no type errors.

2.2 Run the test suite
uv run pytest -v
Expected: 40 tests pass in ~5 seconds.

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
If anything fails here, don't proceed. Fix the basics first.

2.3 Verify OpenAPI is current
uv run python scripts/export_openapi.py
git diff --exit-code openapi.yaml
Expected: no diff. The committed schema matches what the code produces.

Phase 3 — Start the API Server (~2 min)
3.1 Boot the server
uv run uvicorn xframe_agent.main:app --reload --port 8000
Expected output:

INFO:     Will watch for changes in these directories: ['/path/to/xframe-ai-agent']
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
3.2 Verify the API is up (new terminal)
curl http://localhost:8000/api/v1/agent/health | python -m json.tool
Expected:

{
  "status": "ok",
  "version": "1.0.0",
  "components": {
    "database": "ok",
    "redis": "ok"
  }
}
If status is degraded, look at the response — it'll tell you which component failed.

3.3 Open the interactive API docs
open http://localhost:8000/api/v1/agent/docs
Swagger UI loads. You can see every endpoint and click "Try it out" to exercise them interactively.

Phase 4a — Test in Deterministic Mode (no LLM needed)
This path works without any LLM provider configured. Useful for verifying the pipeline end-to-end before adding LLM cost.

4a.1 Generate a test JWT
uv run python -c "
import jwt, time
token = jwt.encode(
    {
        'user_id': 1,
        'role_id': 1,
        'profile_id': 1,
        'session_id': 1,
        'email': 'admin@priceframe.local',
        'exp': int(time.time()) + 86400
    },
    'test-secret-32-chars-minimum-aaaaaaaa',  # ← match PRICEFRAME_JWT_SECRET in .env
    algorithm='HS256'
)
print(token)
"
Save the output:

export TOKEN="eyJ..."  # paste the output here
⚠️ This works because we set a known JWT secret. It validates the JWT but the /auth/profile call will fail because we're not actually authenticated to PriceFRAME. For tools that don't require profile, this is fine.

4a.2 List available tools
curl -s http://localhost:8000/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
⚠️ This will fail with 401 because it tries to fetch the profile from PriceFRAME. For deterministic-mode-only testing, you'd need to mock or skip the profile fetch.

For full end-to-end testing, go to Phase 5 (PriceFRAME integration).

Phase 4b — Test in LLM-Driven Mode
4b.1 Add provider credentials
Pick one provider and add to .env:

Option A: Gemini Vertex (primary)

Create a GCP project; enable Vertex AI API.
Create a service account with roles/aiplatform.user.
Download the JSON key to ./secrets/gcp-sa.json.
Update .env:
GEMINI_VERTEX_PROJECT=your-gcp-project-id
GEMINI_VERTEX_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/xframe-ai-agent/secrets/gcp-sa.json
Install the SDK:
uv add google-genai
Option B: Anthropic

Get an API key from https://console.anthropic.com.
Update .env:
ANTHROPIC_API_KEY=sk-ant-...
Install the SDK:
uv add anthropic
4b.2 Restart the server
The uvicorn --reload may not catch new env vars. Kill and restart:

# Ctrl+C the running uvicorn, then:
uv run uvicorn xframe_agent.main:app --reload --port 8000
4b.3 Verify provider configuration
uv run python -c "
from xframe_agent.settings import Settings
s = Settings()
print(f'Provider configured: {s.provider_configured}')
print(f'Vertex project: {s.gemini_vertex_project}')
print(f'Anthropic configured: {bool(s.anthropic_api_key)}')
print(f'Default model: {s.default_model}')
"
Expected:

Provider configured: True
Vertex project: your-gcp-project-id
Anthropic configured: False
Default model: gemini-2.5-flash
Now dispatch.execute_run will route to ModelRunner (LLM-driven) instead of AgentLoop (deterministic).

Phase 5 — PriceFRAME Integration Testing (~20 min)
Prerequisite: real PRICEFRAME_JWT_SECRET and PRICEFRAME_SERVICE_SECRET from the PriceFRAME team, AND the user admin@priceframe.local has agent.* permissions seeded on their profile.

5.1 Test login (proxy to PriceFRAME)
curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' | python -m json.tool
Expected:

{
  "token": "eyJhbGciOiJIUzI1NiI...",
  "user": {
    "id": 1,
    "email": "admin@priceframe.local"
  },
  "role_code": "ROLE_ADMIN",
  "profile_code": "PROFILE_ADMIN",
  "permissions": [
    "agent.enabled",
    "agent.quotes.read",
    "agent.quotes.create",
    "agent.quotes.edit",
    "agent.quotes.recalc",
    "agent.approvals.submit",
    "agent.salesforce.read"
  ],
  "expires_at": 1716290000
}
Save the token:

export TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/agent/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@priceframe.local","password":"Pricing2026"}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
echo $TOKEN | cut -c1-50
5.2 Verify /auth/me
curl -s http://localhost:8000/api/v1/agent/auth/me \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
Expected: returns the same user/role/permissions as login.

5.3 List tools (permission-filtered)
curl -s http://localhost:8000/api/v1/agent/tools \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
Expected: a JSON list of all tools the admin user can call — should be all 12 if agent.* permissions are fully seeded.

Troubleshooting: if you see fewer tools, the missing permissions explain why. Add them on the PriceFRAME profile.

5.4 Create a conversation
curl -s -X POST http://localhost:8000/api/v1/agent/conversations \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"title":"Manual test","kind":"create_pricing_request"}' | python -m json.tool
Expected:

{
  "id": "01HX...",
  "title": "Manual test",
  "kind": "create_pricing_request",
  "pinned": false,
  "archived": false,
  "created_at": "2026-05-21T...",
  "updated_at": "2026-05-21T..."
}
Save it:

export CONV=01HX...   # paste the id from above
5.5 List conversations (test pagination)
curl -s "http://localhost:8000/api/v1/agent/conversations?limit=10" \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
Expected: the conversation you just created appears. next_cursor: null, has_more: false if it's your only one.

5.6 Send a message (triggers a run)
curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Show me my open quotations","source":"text"}' | python -m json.tool
Expected:

LLM-driven mode (Phase 4b complete):
{"run_id": "01HX...", "status": "completed"}
The model called list_my_quotations, summarized the result. Total time ~3-5 seconds.
Deterministic mode:
{"run_id": "01HX...", "status": "awaiting_decision"}
No LLM, so AgentLoop produces a canned response. No tool actually called unless your message starts with tool: {...}.
Save it:

export RUN=01HX...   # from the response
5.7 Inspect the run
curl -s http://localhost:8000/api/v1/agent/runs/$RUN \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
Expected: the full run record with status, timestamps, error (if any).

5.8 View the durable event log (the journal)
psql postgres://xframe:xframe@localhost:5433/xframe_agent <<EOF
SELECT seq, event_type, jsonb_pretty(payload), created_at
FROM agent_run_events
WHERE run_id = '$RUN'
ORDER BY seq;
EOF
Expected (LLM-driven): multiple events including:

v1.step.started
v1.tool.proposed (list_my_quotations)
v1.tool.started
v1.tool.completed
v1.message.delta
v1.run.completed
This is the single most useful query in the system. Memorize it.

5.9 Stream events via SSE
In a new terminal:

# Start the SSE stream BEFORE you send the next message
RUN2=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/runs \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"What can you do?","source":"text"}' | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

curl -N -H "Authorization: Bearer $TOKEN" \
     "http://localhost:8000/api/v1/agent/runs/$RUN2/stream"
Expected: SSE events stream in real time:

id: 1
event: v1.step.started
data: {"run_id":"01HX...","seq":1,"ts":"...","step":1,"kind":"model_call"}
id: 2
event: v1.tool.proposed
data: ...
...
id: N
event: v1.run.completed
data: ...
Heartbeats every 15s if the run pauses. Stream closes on v1.run.completed or v1.run.awaiting_decision.

5.10 Test HITL approval (the big one)
Send a message that should trigger a write tool:

RUN3=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/runs \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create a draft quotation titled Test Quote for customer 1 in USD","source":"text"}' | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

# Wait a few seconds for the model to propose
sleep 3

# Check the run status — should be awaiting_decision
curl -s http://localhost:8000/api/v1/agent/runs/$RUN3 \
    -H "Authorization: Bearer $TOKEN" | python -m json.tool
Expected: status: "awaiting_decision" (LLM-driven mode).

Find the pending tool call:

psql postgres://xframe:xframe@localhost:5433/xframe_agent -c "
SELECT id, tool_name, status, requires_approval, args
FROM agent_tool_calls WHERE run_id = '$RUN3' AND status = 'proposed';
"
Approve it:

export TOOL_CALL_ID=01HX...   # from the SQL output

curl -s -X POST http://localhost:8000/api/v1/agent/runs/$RUN3/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool_call_id\":\"$TOOL_CALL_ID\",\"decision\":\"approve\"}" | python -m json.tool
Expected: the decision endpoint executes the create_quotation against PriceFRAME and returns the quote ID.

⚠️ This is a real write to PriceFRAME. A new quotation appears in the PriceFRAME UI. Use a throwaway customer ID; clean up after.

5.11 Verify the audit trail
psql postgres://xframe:xframe@localhost:5433/xframe_agent -c "
SELECT id, tool_name, status, priceframe_audit_log_id, approved_at
FROM agent_tool_calls WHERE id = '$TOOL_CALL_ID';
"
Expected: status='succeeded', priceframe_audit_log_id populated (cross-reference key to PriceFRAME's audit_logs table), approved_at set.

5.12 Reject path
Create another run, but this time reject:

RUN4=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/runs \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create another draft quotation","source":"text"}' | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

sleep 3

TC=$(psql -At postgres://xframe:xframe@localhost:5433/xframe_agent -c "
SELECT id FROM agent_tool_calls WHERE run_id = '$RUN4' AND status = 'proposed' LIMIT 1;
")

curl -s -X POST http://localhost:8000/api/v1/agent/runs/$RUN4/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"tool_call_id\":\"$TC\",\"decision\":\"reject\"}" | python -m json.tool
Expected: tool call status becomes rejected. No PriceFRAME write happens.

5.13 Edit-then-approve path
RUN5=$(curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/runs \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"Create a draft for customer 1","source":"text"}' | python -c "import json,sys;print(json.load(sys.stdin)['run_id'])")

sleep 3

TC=$(psql -At postgres://xframe:xframe@localhost:5433/xframe_agent -c "
SELECT id FROM agent_tool_calls WHERE run_id = '$RUN5' AND status = 'proposed' LIMIT 1;
")

# Edit args before approving
curl -s -X POST http://localhost:8000/api/v1/agent/runs/$RUN5/decisions \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
        \"tool_call_id\":\"$TC\",
        \"decision\":\"edit\",
        \"edited_args\":{
            \"title\":\"My Edited Title\",
            \"customer_id\":1,
            \"currency\":\"USD\",
            \"notes\":\"Edited by approver\"
        }
    }" | python -m json.tool
Expected: the agent executes create_quotation with the edited args. New quote appears in PriceFRAME with the edited title.

Phase 6 — Trigger Specific Tools
To test specific tools without relying on the LLM choosing them, use deterministic-mode tool directives. Send a message starting with tool::

6.1 List quotations
curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"list_my_quotations\",\"args\":{\"limit\":5}}","source":"text"}'
6.2 Get a specific quote
curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"get_quotation\",\"args\":{\"id\":1}}","source":"text"}'
6.3 Get a currency rate
curl -s -X POST http://localhost:8000/api/v1/agent/conversations/$CONV/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(uuidgen)" \
    -d '{"content":"tool:{\"name\":\"get_currency_rate\",\"args\":{\"currency\":\"USD\"}}","source":"text"}'
After each, check the run + tool call status:

psql postgres://xframe:xframe@localhost:5433/xframe_agent -c "
SELECT tc.tool_name, tc.status, tc.result
FROM agent_tool_calls tc
JOIN agent_runs r ON r.id = tc.run_id
WHERE r.conversation_id = '$CONV'
ORDER BY tc.created_at DESC LIMIT 5;
"
This is how you test individual tools surgically.

Phase 7 — Optional: Full Stack with Observability
For Langfuse LLM tracing, MinIO for attachments, and ClamAV:

docker compose up -d   # brings up everything
Open:

Langfuse: http://localhost:3001 → sign up, generate API keys, paste into .env as LANGFUSE_*
MinIO: http://localhost:9001 → login minioadmin/minioadmin
Restart the agent. Every LLM call now exports a trace to Langfuse. Useful for prompt debugging.

Phase 8 — Run the Worker (for async runs)
By default, runs execute inline (in the HTTP request handler). For async (via arq queue):

In .env:

RUN_EXECUTION_MODE=arq
Restart the API, then in a new terminal:

uv run arq xframe_agent.worker.WorkerSettings
Expected:

INFO     starting worker for 2 functions
INFO     redis_settings=RedisSettings(host='localhost', port=6379, ...)
Now POST /runs returns 202 Accepted immediately; the worker processes the run asynchronously. Use SSE to follow progress.

Troubleshooting Matrix
Symptom	Likely cause	Fix
connection refused	Postgres/Redis not running	docker compose up -d postgres redis
relation "agent_runs" does not exist	Migrations not run	uv run alembic upgrade head
401 on every endpoint	JWT secret mismatch	Verify PRICEFRAME_JWT_SECRET matches the deployed PriceFRAME
401 from PriceFRAME on tool calls	User JWT expired	Re-login
403 from PriceFRAME on tool calls	User missing agent.* permissions	Update PriceFRAME profile
Run stuck in running	Crashed or hanging	Check agent_run_events for last event; manually cancel via POST /runs/{id}/cancel
provider_error cause	All providers failed	Check API keys, quotas, network egress
422 validation error	Bad request body	Check the response error.detail field for which field failed
SSE events arrive in burst at end	Server buffering	Verify uvicorn output isn't piped through gzip
tool.error cause=unknown_tool	Model invented a tool name	LLM hallucination; refine system prompt; or, in deterministic mode, fix the tool:{...} JSON
For deeper issues see docs/handbook/10-debugging-guide.md or docs/book/part-11-testing-debugging.md.

Cleanup
# Stop the API: Ctrl+C in its terminal

# Stop the worker (if running): Ctrl+C in its terminal

# Stop services (keeps data)
docker compose stop

# Stop and remove containers + volumes (DESTROYS DATA)
docker compose down -v
If you created quotations during testing, clean them up in PriceFRAME.