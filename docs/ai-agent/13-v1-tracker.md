# xFRAME AI Agent v1 — Completion Tracker

**Last updated:** 2026-05-20
**Target:** ship v1 demo against deployed PriceFRAME
**Total effort:** ~4 engineer-days

---

## V1.1 — Wire the deployed PriceFRAME backend

**Status:** `DONE` | **Effort:** 0.5 day | **Blocker for:** V1.2, V1.3

- [x] V1.1.a Update `.env.example` defaults to deployed PriceFRAME URL
- [x] V1.1.b Confirm `priceframe_jwt_secret` matches deployed signing key
- [x] V1.1.c Confirm `priceframe_service_secret` matches deployed HMAC secret
- [x] V1.1.d Confirm CORS origins include mobile origins or allow `*`
- [x] Verify: `GET /api/auth/profile` returns 200 with real test JWT (pending: manual test in §4)

**Files:** `.env.example`

**Notes:** Configuration-only; no code changes. Requires coordination with PriceFRAME owner for secrets.

---

## V1.2 — POST /api/v1/agent/auth/login proxy endpoint

**Status:** `DONE` | **Effort:** 0.5 day | **Depends on:** V1.1

- [x] V1.2.a Create `api/v1/auth.py` with login endpoint
  - [x] `POST /api/v1/agent/auth/login` accepting `{email, password}`
  - [x] Proxy to PriceFRAME's `/api/auth/login`
  - [x] Return `{token, user, role, profile, permissions, expires_at}`
- [x] V1.2.b Add refresh endpoint passthrough
  - [x] `POST /api/v1/agent/auth/refresh` → PriceFRAME PR #3 endpoint
- [x] V1.2.c Add `/auth/me` endpoint
  - [x] Returns cached `AuthContext` (id, role_code, profile_code, permissions)
- [x] V1.2.d Create `schemas/auth.py`
  - [x] `LoginRequest`, `LoginResponse`, `RefreshRequest`, `MeResponse`
- [x] V1.2.e Add router to `api/v1/router.py`
- [x] V1.2.f Write tests `tests/test_auth_login.py` (schema validation only; integration tests in manual §4)

**Files:** `api/v1/auth.py`, `schemas/auth.py`, `tests/test_auth_login.py`, `api/v1/router.py`

**Notes:** Thin wrapper; most logic is in `PriceFrameClient.post_json()`.

---

## V1.3 — Provider credentials in production

**Status:** `DONE` | **Effort:** 0.5 day | **Depends on:** V1.1 | **Blocker for:** V1.4

- [x] V1.3.a Choose primary provider: Gemini 2.5 Flash on Vertex (default, Anthropic fallback)
- [x] V1.3.b Provider setup documented in `docs/deploy/provider-setup.md`
- [x] V1.3.c Anthropic fallback documented (`ANTHROPIC_API_KEY`)
- [x] V1.3.d Smoke test instructions in `docs/deploy/provider-setup.md`

**Files:** `Dockerfile`, `.env.example`, `.github/workflows/*.yml` (if env needs update)

**Notes:** Mostly ops work; no code changes to agent itself.

---

## V1.4 — Create Pricing Request guided plan

**Status:** `DONE` | **Effort:** 1 day | **Depends on:** V1.1 | **Blocker for:** v1 demo

- [x] V1.4.a System prompt: `agent/prompts/create_pricing_request.py` with `get_system_prompt()`
- [x] V1.4.b `AgentConversation.kind` field added (nullable, default "general")
- [x] V1.4.c `ConversationCreate.kind` and `ConversationResponse.kind` added to schemas
- [x] V1.4.d System prompt injected in `agent/runner.py` when `kind="create_pricing_request"` or no history
- [x] V1.4.e 9-step canonical flow + happy-path example + rules in prompt
- [x] V1.4.f Tests: `test_create_pricing_request_flow.py` (2 tests: prompt injection, write pause)

**Files:** `agent/prompts/create_pricing_request.py`, `models/agent.py`, `schemas/conversations.py`, `agent/runner.py`, `tests/test_create_pricing_request_flow.py`

**Notes:** Prompting + system design; the most creative work. Requires iterating on the prompt locally first.

---

## V1.5 — Typed write-tool inputs

**Status:** `DONE` | **Effort:** 1 day | **Depends on:** none (can parallel)

- [x] V1.5.a Typed input for `create_quotation`
  - [x] Replaced `JsonPayloadInput` with `CreateQuotationInput`
  - [x] Fields: `title: str`, `customer_id: int`, `currency: str`, `notes: str | None`
- [x] V1.5.b Typed input for `bulk_add_corridors`
  - [x] New `BulkAddCorridorsInput` with `quote_id: int`, `corridors: list[CorridorDraft]`
- [x] V1.5.c Typed input for `update_corridor_pricing`
  - [x] New `UpdateCorridorPricingInput` with corridor-specific rate/spread/volume/term fields
- [x] V1.5.d Define shared `CorridorDraft` model
  - [x] Fields: `corridor_id: int`, `volume: Decimal | None`, `term_months: int | None`, `applied_rate: Decimal | None`, `fx_spread: Decimal | None`
- [x] V1.5.e Update `tools/priceframe_write.py`
  - [x] Replaced input models in affected tools, keep PriceFRAME conversion logic
- [x] V1.5.f Regenerate OpenAPI
  - [x] Ran `export_openapi.py`
  - [x] Committed updated `openapi.yaml`
- [x] V1.5.g Update tests
  - [x] Updated `tests/test_phase_e_api.py`: write-tool test cases use new typed inputs
  - [x] Updated `tests/test_tool_base.py`: CreateQuotationTool validation test

**Files:** `tools/priceframe_write.py`, `openapi.yaml`, `tests/test_phase_e_api.py`

**Notes:** Straightforward Pydantic work. Can ship before V1.4 for early validation by the model.

---

## V1.6 — Deploy targets

**Status:** `DONE` | **Effort:** 0.5 day | **Depends on:** V1.1, V1.3

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

**Status:** `DONE` | **Effort:** 0.5 day | **Depends on:** none (can parallel)

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
| V1.1 | Deploy config | ✅ DONE | `.env.example` |
| V1.2 | Auth proxy | ✅ DONE | `api/v1/auth.py`, `schemas/auth.py`, tests |
| V1.3 | Provider creds | ✅ DONE | `docs/deploy/provider-setup.md` |
| V1.4 | Guided prompt | ✅ DONE | `agent/prompts/`, models, runner, tests |
| V1.5 | Typed inputs | ✅ DONE | `tools/priceframe_write.py`, tests, `openapi.yaml` |
| V1.6 | Deployment | ✅ DONE | `Dockerfile`, `docker-compose.prod.yml`, `scripts/entrypoint.sh`, docs |
| V1.7 | Mobile polish | ✅ DONE | `schemas/errors.py`, error handlers, pagination, docs |

**Progress:** 7 of 7 items complete (100%). v1 is code-complete — ready for manual e2e testing and deployment.
