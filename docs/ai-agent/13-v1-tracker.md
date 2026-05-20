# xFRAME AI Agent v1 — Completion Tracker

**Last updated:** 2026-05-20
**Target:** ship v1 demo against deployed PriceFRAME
**Total effort:** ~4 engineer-days

---

## V1.1 — Wire the deployed PriceFRAME backend

**Status:** `TODO` | **Effort:** 0.5 day | **Blocker for:** V1.2, V1.3

- [ ] V1.1.a Update `.env.example` defaults to deployed PriceFRAME URL
- [ ] V1.1.b Confirm `priceframe_jwt_secret` matches deployed signing key
- [ ] V1.1.c Confirm `priceframe_service_secret` matches deployed HMAC secret
- [ ] V1.1.d Confirm CORS origins include mobile origins or allow `*`
- [ ] Verify: `GET /api/auth/profile` returns 200 with real test JWT

**Files:** `.env.example`

**Notes:** Configuration-only; no code changes. Requires coordination with PriceFRAME owner for secrets.

---

## V1.2 — POST /api/v1/agent/auth/login proxy endpoint

**Status:** `TODO` | **Effort:** 0.5 day | **Depends on:** V1.1

- [ ] V1.2.a Create `api/v1/auth.py` with login endpoint
  - [ ] `POST /api/v1/agent/auth/login` accepting `{email, password}`
  - [ ] Proxy to PriceFRAME's `/api/auth/login`
  - [ ] Return `{token, user, role, profile, permissions, expires_at}`
- [ ] V1.2.b Add refresh endpoint passthrough
  - [ ] `POST /api/v1/agent/auth/refresh` → PriceFRAME PR #3 endpoint
- [ ] V1.2.c Add `/auth/me` endpoint
  - [ ] Returns cached `AuthContext` (id, role_code, profile_code, permissions)
- [ ] V1.2.d Create `schemas/auth.py`
  - [ ] `LoginRequest`, `LoginResponse`, `RefreshRequest`, `MeResponse`
- [ ] V1.2.e Add router to `api/v1/router.py`
- [ ] V1.2.f Write tests `tests/test_auth_login.py`
  - [ ] Happy path with real PriceFRAME login
  - [ ] 401 handling
  - [ ] 5xx → 502 mapping

**Files:** `api/v1/auth.py`, `schemas/auth.py`, `tests/test_auth_login.py`, `api/v1/router.py`

**Notes:** Thin wrapper; most logic is in `PriceFrameClient.post_json()`.

---

## V1.3 — Provider credentials in production

**Status:** `TODO` | **Effort:** 0.5 day | **Depends on:** V1.1 | **Blocker for:** V1.4

- [ ] V1.3.a Choose primary provider
  - [ ] Decision: Gemini 2.5 Flash on Vertex (default) or Anthropic (fallback)
  - [ ] Document in deploy README
- [ ] V1.3.b Provision GCP service account (if Vertex)
  - [ ] Service-account key mounted at `/var/run/secrets/gcp.json`
  - [ ] `GOOGLE_APPLICATION_CREDENTIALS` set in env
- [ ] V1.3.c Configure Anthropic fallback (optional but recommended)
  - [ ] `ANTHROPIC_API_KEY` in env
- [ ] V1.3.d Smoke test with live provider
  - [ ] Single run with non-tool message returns `v1.run.completed` with text

**Files:** `Dockerfile`, `.env.example`, `.github/workflows/*.yml` (if env needs update)

**Notes:** Mostly ops work; no code changes to agent itself.

---

## V1.4 — Create Pricing Request guided plan

**Status:** `TODO` | **Effort:** 1 day | **Depends on:** V1.1 | **Blocker for:** v1 demo

- [ ] V1.4.a Create system prompt assembly function
  - [ ] New file: `agent/prompts/create_pricing_request.py`
  - [ ] Function: `get_system_prompt(context: AuthContext) -> str`
  - [ ] Include role, profile, permission list, canonical step order
- [ ] V1.4.b Add conversation kind schema
  - [ ] Add `kind: str` field to `AgentConversation` model
  - [ ] Migrate existing conversations (set kind = null or default)
  - [ ] Update schemas to accept `kind` on create
- [ ] V1.4.c Inject system prompt into runner
  - [ ] `agent/runner.py`: prepend system message before first model call
  - [ ] Match prompt to conversation kind
- [ ] V1.4.d Add few-shot examples
  - [ ] Two examples in the prompt (simple + FX-spread adjustment)
- [ ] V1.4.e Optionally narrow tool list (discussion point)
  - [ ] When `conversation.kind == "create_pricing_request"`, filter tool list to ~9 tools
- [ ] V1.4.f Write integration test
  - [ ] `tests/test_create_pricing_request_flow.py`
  - [ ] Scripted `FakeProvider` walks through: list corridors → create quotation (paused) → bulk add corridors (paused) → preview → submit → complete
  - [ ] Verify audit rows written

**Files:** `agent/prompts/create_pricing_request.py`, `models/agent.py`, `schemas/conversations.py`, `agent/runner.py`, `tests/test_create_pricing_request_flow.py`

**Notes:** Prompting + system design; the most creative work. Requires iterating on the prompt locally first.

---

## V1.5 — Typed write-tool inputs

**Status:** `TODO` | **Effort:** 1 day | **Depends on:** none (can parallel)

- [ ] V1.5.a Typed input for `create_quotation`
  - [ ] Replace `JsonPayloadInput` with `CreateQuotationInput`
  - [ ] Fields: `title: str`, `customer_id: int`, `currency: str`, `corridors: list[CorridorDraft]`, `notes: str | None`
- [ ] V1.5.b Typed input for `bulk_add_corridors`
  - [ ] New `BulkAddCorridorsInput` with `quote_id: int`, `corridors: list[CorridorDraft]`
- [ ] V1.5.c Typed input for `update_corridor_pricing`
  - [ ] New `UpdateCorridorPricingInput` with corridor-specific rate/spread/... fields
- [ ] V1.5.d Define shared `CorridorDraft` model
  - [ ] Fields: `corridor_id: int`, `volume: Decimal | None`, `term_months: int | None`, `applied_rate: Decimal | None`, `fx_spread: Decimal | None`
- [ ] V1.5.e Update `tools/priceframe_write.py`
  - [ ] Replace input models in affected tools
- [ ] V1.5.f Regenerate OpenAPI
  - [ ] `uv run python scripts/export_openapi.py`
  - [ ] Commit changes to `openapi.yaml`
- [ ] V1.5.g Update tests
  - [ ] `tests/test_phase_e_api.py`: update write-tool test cases to use new typed inputs

**Files:** `tools/priceframe_write.py`, `openapi.yaml`, `tests/test_phase_e_api.py`

**Notes:** Straightforward Pydantic work. Can ship before V1.4 for early validation by the model.

---

## V1.6 — Deploy targets

**Status:** `TODO` | **Effort:** 0.5 day | **Depends on:** V1.1, V1.3

- [ ] V1.6.a Verify production Dockerfile
  - [ ] Builds cleanly: `docker build -t xframe-agent:v1 .`
- [ ] V1.6.b Create `docker-compose.prod.yml`
  - [ ] Pin all images to stable tags
  - [ ] Mount secrets (GCP SA key, env file)
  - [ ] Include: postgres + redis + minio + clamav + xframe-agent
  - [ ] Health probes for postgres + redis
- [ ] V1.6.c Alembic on startup
  - [ ] Update `Dockerfile` entrypoint to run `alembic upgrade head` before uvicorn
  - [ ] Or create `scripts/entrypoint.sh`
- [ ] V1.6.d Health endpoint validation
  - [ ] `GET /api/v1/agent/health` returns 200
  - [ ] Externals check includes PriceFRAME + provider (if configured)
- [ ] V1.6.e TLS/reverse-proxy config
  - [ ] nginx fragment in README or separate doc
  - [ ] SSE buffering disabled (`proxy_buffering off`)
  - [ ] Timeouts ≥ `sse_heartbeat_seconds`
- [ ] V1.6.f Deploy README
  - [ ] Document env vars, docker-compose bring-up, alembic, nginx config

**Files:** `Dockerfile`, `docker-compose.prod.yml`, `scripts/entrypoint.sh`, `docs/deploy/v1-deployment.md`

**Notes:** Infrastructure; parallelize with other tasks.

---

## V1.7 — Mobile-facing polish

**Status:** `TODO` | **Effort:** 0.5 day | **Depends on:** none (can parallel)

- [ ] V1.7.a SSE behind reverse proxy
  - [ ] Test `curl -N` with SSE endpoint through nginx
  - [ ] Verify no buffering, heartbeats arrive
  - [ ] Document in nginx config
- [ ] V1.7.b Token-based SSE auth
  - [ ] Confirm `?token=...` query param works (already implemented)
  - [ ] Document for mobile (can't set custom headers on `EventSource`)
- [ ] V1.7.c Error envelope standardization
  - [ ] Create `schemas/errors.py`
  - [ ] New schema: `ErrorResponse` with `{error: {code, message, detail}}`
  - [ ] Update FastAPI exception handlers to use it
- [ ] V1.7.d Pagination on `/conversations`
  - [ ] Response includes `next_cursor` + `has_more`
  - [ ] Mobile can scroll infinitely without loading all conversations

**Files:** `schemas/errors.py`, `api/v1/conversations.py`, `docs/deploy/v1-deployment.md` (nginx)

**Notes:** User-experience improvements; mostly schema work.

---

## Post-completion checklist

Once all items above are `DONE`:

- [ ] All tests pass: `uv run pytest` ≥ 40 tests
- [ ] Static checks pass: `ruff format --check .`, `ruff check .`, `mypy`
- [ ] OpenAPI clean: `git diff --exit-code openapi.yaml`
- [ ] Manual e2e against deployed PriceFRAME (§4 of 12-v1-completion-plan.md)
  - [ ] Login works
  - [ ] Permissions populated
  - [ ] Create Pricing Request conversation + run
  - [ ] SSE streams correctly
  - [ ] Approve proposals
  - [ ] Quotation appears in PriceFRAME
- [ ] Deployment dry-run: `docker-compose -f docker-compose.prod.yml up` boots cleanly

---

## Summary by workstream

| WS | Item | Status | Files |
|---|---|---|---|
| V1.1 | Deploy config | TODO | `.env.example` |
| V1.2 | Auth proxy | TODO | `api/v1/auth.py`, `schemas/auth.py`, tests |
| V1.3 | Provider creds | TODO | env + ops |
| V1.4 | Guided prompt | TODO | `agent/prompts/`, models, tests |
| V1.5 | Typed inputs | TODO | `tools/priceframe_write.py`, tests |
| V1.6 | Deployment | TODO | `Dockerfile`, `docker-compose.prod.yml`, docs |
| V1.7 | Mobile polish | TODO | `schemas/errors.py`, API handlers, docs |

**Parallel paths:**
- V1.1 → V1.2, V1.3 (blocking)
- V1.5 can land independently (tests update)
- V1.6, V1.7 can land while others are in flight
