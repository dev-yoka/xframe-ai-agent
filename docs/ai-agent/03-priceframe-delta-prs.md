# PriceFRAME Delta-PR Catalog

> Small, well-scoped changes to **PriceFRAME** required to support the **xFRAME Ai Agent**. Each PR is independently reviewable, independently shippable, and sized so any one of them can be reverted without breaking the others.
>
> **Companion doc:** `docs/ai-agent/02-architecture-proposal.md` (Phase 3).
> **Status:** proposal, awaiting approval.
>
> **Naming convention** for branches: `agent-delta/<NN>-<short-slug>` (e.g., `agent-delta/01-pricing-context`).

---

## At a glance

| # | Title | Why the agent needs it | Risk | Sizing | Order |
|---|---|---|---|---|---|
| 1 | `GET /api/v1/quotes/:id/pricing-context` (composite) | Saves N+1 round-trips per `get_quotation` tool call | Low (read-only) | 1.5 days | 1 |
| 2 | `POST /api/v1/quotes/:id/pricing/preview` + server-side `PricingEngineService` | The agent's pre-write proposal step; also the precursor refactor from Phase 1 | Medium (touches existing client logic) | 3 days | 2 |
| 3 | `POST /api/auth/refresh` (short-lived access + 30-day refresh) | Flutter session model | Low | 1 day | 3 |
| 4 | `POST /api/v1/agent-audit-callbacks` + HMAC verification | Unified audit history across UI and agent | Low | 0.5 day | 4 |
| 5 | Seed `agent.*` permission codes + assign to Sales Representative profile | RBAC gating of agent tools | Trivial | 0.25 day | 5 |
| 6 | `GET /api/v1/fee-annex-versions/:id/chunks` (chunk-friendly read) | RAG v1.5 — fee annex retrieval | Low | 1 day | v1.5 |
| 7 | `GET /api/v1/notes/for-rag?since=cursor` | RAG v1.5 — partner-context retrieval | Low | 0.5 day | v1.5 |

**v1 critical path:** PRs **#1 → #2 → #3 → #4 → #5**. **~5.5 days** of PriceFRAME work, parallelizable across two devs if needed.
**v1.5:** PRs **#6, #7**. Defer until RAG is justified.

Each PR ships with:
- A migration if schema changes (Lucid, not Alembic).
- VineJS validators + shared Zod schemas in `shared/src/validators/`.
- Unit + Japa integration tests.
- An OpenAPI block in the README of the endpoint group.
- An entry in `CHANGELOG.md` under `Unreleased / Agent Support`.

---

## PR #1 — `GET /api/v1/quotes/:id/pricing-context` (composite read)

### Why

Today, to render a quote with everything the agent needs to reason about pricing, the agent would need:
- `GET /api/quotes/:id` (the quote)
- `GET /api/quotes/:id/corridors` (its corridors)
- `GET /api/corridors/active?...` (reference-pricing snapshot for matching corridors)
- `GET /api/currency-rates?currency=X` × N (one per currency)

That's 4–8 calls per agent turn. A composite endpoint collapses it to one, with internal joins. The endpoint is **explicitly designed for the agent** (and equally useful for future React features that need the full picture).

### Spec

**Path:** `GET /api/v1/quotes/:id/pricing-context`

**Auth:** existing `auth_middleware` + `permission_middleware(['quotes.read'])` (whatever code already gates `show`).

**Query params:**
- `include_currency_rates` (default `true`)
- `include_reference_pricing` (default `true`)
- `include_approval_state` (default `true`)

**Response (200):**

```jsonc
{
  "quote": {
    "id": 123,
    "name": "PR-2026-042",
    "status": "draft",
    "opportunityType": "...",
    "ownerId": 7,
    "filters": { "regions": [...], "countries": [...], "services": [...], /* ... */ },
    "snapshots": { /* opaque, passed through */ },
    "aggregates": {
      "totalYearlyRevenue": "12345.678901",  // strings, Decimal-safe
      "totalYearlyMargin": "...",
      "totalYearlyVolumeUsd": "...",
      "totalYearlyTransactions": 0,
      "averageTakeRate": "...",
      "weightedGrossMarginPercentage": "...",
      "totalCorridors": 5,
      "standardPricingCorridors": 4,
      "tieredPricingCorridors": 1,
      "corridorsNeedingApproval": 0
    },
    "updatedAt": "2026-05-19T10:30:00Z"        // for If-Match
  },
  "corridors": [
    {
      "id": 4567,
      "identity": {
        "region": "...", "country": "...", "transactionType": "...",
        "service": "...", "payer": "...", "receivingPartner": "...",
        "payoutCurrency": "...", "fundingCurrency": "..."
      },
      "pricing": {
        "pricingModel": "Standard",
        "stdFixedFeeUSD": "...",
        "variableFeePercentage": "...",
        "feeDiscountPercentage": "...",
        "appliedFxSpread": "...",
        "minimumSpread": "...",
        "treasuryFxCost": "..."
        // + tieredData if pricingModel == 'Trx Fee Tiered Pricing'
      },
      "volume":   { "atvUSD": "...", "yearlyVolumeUSD": "...", "yearlyTransactions": 0 },
      "financial":{ "revenueFee": "...", "fxMargin": "...", "totalRevenue": "...", "totalMargin": "...", "grossMarginPercentage": "...", "takeRate": "..." },
      "approval": { "needsApproval": false, "approvalStatus": "not_required" },
      "updatedAt": "..."                         // for If-Match
    }
  ],
  "referencePricing": [
    // Only included if include_reference_pricing=true. One entry per corridor identity,
    // sourced from the active CorridorVersion / CorridorList.
    {
      "identity": { /* same shape as corridors[].identity */ },
      "defaults": { /* default pricing from active corridors_list */ }
    }
  ],
  "currencyRates": {
    // Only included if include_currency_rates=true. Maps every currency referenced
    // in this quote (source, funding, payout) to its current xe_mid_mkt_avg.
    "USD": "1.000000",
    "PHP": "56.123456",
    "EUR": "0.918734"
  },
  "approvalState": {
    // Only if include_approval_state=true.
    "current": null | { "id": ..., "status": "...", "policy": "...", "currentStepIndex": 0, "approvers": [...], "dueDate": "..." },
    "needsApprovalCorridorIds": [...]
  }
}
```

### Implementation notes

- Live in `server/app/features/quotes/controllers/pricing_context_controller.ts` (new) wrapping a new `PricingContextService.assemble(quoteId, options, authContext)` in the same feature.
- Uses Lucid's `preload()` to keep query count low (1 for quote, 1 for corridors, 1 for reference rows joined on identity tuple, 1 for currency rates `whereIn`).
- **All money fields serialized as strings** (Decimal.js precision rule from Phase 1 §1.3). If any existing endpoint serializes them as floats today, that's a separate bug to file; for this endpoint we mandate strings.
- Reuses `QuoteVisibilityService` so 403 is returned correctly if the user can't see this quote.
- ETag header on the response = `sha256(quote.updated_at + corridors[i].updated_at)`. Agent uses this for cache validation.

### Tests

- Japa: 200 happy path; 404 unknown quote; 403 visibility deny; query-param toggles affect payload shape; ETag stable across identical reads.

---

## PR #2 — `POST /api/v1/quotes/:id/pricing/preview` + `PricingEngineService`

### Why

Two birds: (1) the agent must show users **what a proposed pricing change will compute to** before committing — this is the human-in-the-loop diff card. (2) Phase 1 §1.5 flagged that pricing math is duplicated in `client/src/features/quotes/lib/pricing-calculations.ts`. The refactor needs to happen anyway. We bundle them.

### Spec

**Path:** `POST /api/v1/quotes/:id/pricing/preview`

**Auth:** `auth_middleware` + `permission_middleware(['quotes.read'])`. (Read-only; no DB writes.)

**Request body:**

```jsonc
{
  "changes": [
    {
      "corridorId": 4567,                        // existing corridor
      "set": {
        "stdFixedFeeUSD": "1.50",
        "variableFeePercentage": "0.0035",
        "appliedFxSpread": "0.0050"
      }
    },
    {
      "corridorId": null,                         // hypothetical new corridor
      "identity": { "region": "...", "country": "...", /* ... */ },
      "set": { /* pricing fields */ },
      "volume": { "atvUSD": "...", "yearlyTransactions": 1200 }
    }
  ]
}
```

**Response (200):**

```jsonc
{
  "before": {
    "aggregates": { /* same shape as PR #1's quote.aggregates */ }
  },
  "after": {
    "aggregates": { /* recomputed with proposed changes applied in-memory */ },
    "corridors": [
      {
        "corridorId": 4567 | null,
        "financial": { "revenueFee": "...", "fxMargin": "...", "totalRevenue": "...", "totalMargin": "...", "grossMarginPercentage": "...", "takeRate": "..." },
        "approvalImpact": {
          "wouldNeedApproval": true,
          "reasons": ["applied_fx_spread_below_minimum_for_hard_currency"]
        }
      }
    ]
  },
  "warnings": [
    { "code": "fx_spread_below_minimum", "corridorId": 4567, "field": "appliedFxSpread", "value": "0.0050", "minimum": "0.0075" }
  ]
}
```

### Implementation notes

- New service: `server/app/features/quotes/services/pricing_engine_service.ts`.
- Port the math from `client/src/features/quotes/lib/pricing-calculations.ts` 1:1, using **Decimal.js** for all arithmetic.
- Pure functions: `computeCorridorFinancials(input)`, `computeQuoteAggregates(corridors[])`, `evaluateApprovalGuidelines(corridor, guidelines)`.
- Approval guidelines moved from client `approval-guidelines.ts` to a new `approval_rules` table (lookup by currency hardness, transaction type, etc.) — Vine-validated CRUD endpoints exposed under `/api/admin/approval-rules` (admin-only, separate from this PR — call it **#2b** if you want to split).
- Existing `QuotesService.recalcAggregates` is **refactored** to call `PricingEngineService.computeQuoteAggregates` so the same math runs in both the recalc path and the preview path. **This is the cut-over** for the duplicated math.
- Client-side `pricing-calculations.ts` is **kept temporarily** as a UI-only formatter that calls the preview endpoint for any computed value. Removed in a follow-up cleanup PR.

### Tests

- Pure-function unit tests for every formula (revenue fee, FX margin, gross margin %, take rate, tier outputs).
- Japa integration tests: preview returns deltas matching post-write recalc; warnings fire for sub-minimum FX spread; new-corridor previews work without an `id`.

### Risk

This is the only **medium-risk** PR in the batch — it touches the existing quote recalc path. Mitigation:

- Land behind a feature flag (`ENABLE_PRICING_ENGINE_REFACTOR=true`) for the recalc path.
- Run shadow comparison in staging for one week: existing `recalcAggregates` vs. new path on every real recalc; alert on divergence > 1e-6.
- Flip the flag on after a clean week.

---

## PR #3 — `POST /api/auth/refresh` (refresh-token flow)

### Why

Today: a single JWT with 7-day logical expiry + 60-min physical session, refreshed on every request. That works on the web (every navigation refreshes), but mobile sessions go idle in the background and lose the rolling refresh. Standard fix: short-lived access token + long-lived refresh token.

### Spec

**Schema change** — new table:

```ts
// server/database/migrations/<ts>_create_refresh_tokens.ts
this.schema.createTable('refresh_tokens', (table) => {
  table.bigIncrements('id').primary()
  table.integer('user_id').notNullable().references('id').inTable('users').onDelete('CASCADE')
  table.string('token_hash', 128).notNullable().unique()    // sha256 of the raw token
  table.string('device_label', 128).nullable()              // e.g., "iPhone 15 Pro"
  table.timestamp('expires_at', { useTz: true }).notNullable()
  table.timestamp('revoked_at', { useTz: true }).nullable()
  table.timestamp('last_used_at', { useTz: true }).nullable()
  table.timestamp('created_at', { useTz: true }).notNullable()
  table.index(['user_id', 'revoked_at'])
})
```

**Endpoints:**

- `POST /api/auth/login` — **modified.** Returns:
  ```json
  { "access_token": "...", "refresh_token": "...", "access_expires_in": 3600, "refresh_expires_in": 2592000 }
  ```
  Web client backward-compat: if the request includes `User-Agent: ...PriceFRAMEWeb...` *or* an opt-out header (TBD), keep the existing flat-token response. Cleaner: bump web client at the same time.
- `POST /api/auth/refresh` — body: `{"refresh_token": "..."}`. Validates: token-hash exists, not revoked, not expired, user `is_active`. **Rotates the refresh token** (issues a new one, revokes the old). Returns the same shape as `/login`.
- `POST /api/auth/logout` — accepts an `Authorization: Bearer <access_token>` + body `{"refresh_token": "..."}` and revokes the refresh token. Also kills the session row (existing behavior).
- `POST /api/auth/revoke-all` — convenience for "log me out of all devices."

**Access token TTL:** 60 min (matches today's physical session). **Refresh token TTL:** 30 days; rotates on every refresh; sliding.

### Implementation notes

- `auth_middleware` accepts access tokens as before — no changes upstream.
- `RefreshTokenService.issue(userId, deviceLabel)`, `.refresh(rawToken)`, `.revoke(rawToken)`.
- Raw refresh tokens never logged; hashed at rest.
- Rate-limited at 10 / min per user on the refresh endpoint.

### Tests

- Japa: login returns both tokens; refresh rotates and returns new tokens; replay of old refresh fails; revoked / expired tokens fail; revoke-all kills all device tokens.

---

## PR #4 — `POST /api/v1/agent-audit-callbacks` (agent → PriceFRAME audit hook)

### Why

The agent has its own detailed audit log (`agent_audit_log`). PriceFRAME has its own (`audit_logs`). Without a bridge, a query like "show me everything that changed quote 123" gives an incomplete picture. This endpoint lets the agent write a one-line summary to PriceFRAME's `audit_logs` for every write it triggers, with the agent's `run_id` as the correlation key.

### Spec

**Path:** `POST /api/v1/agent-audit-callbacks`

**Auth:**
- `Authorization: Bearer <user JWT>` — required, identifies the acting user.
- `X-Agent-Service-Signature: <hex HMAC-SHA256 of body + timestamp>` — required, signed with a shared service secret (`PRICEFRAME_SERVICE_SECRET`) so PriceFRAME knows the call came from the agent and not a forged client request.
- `X-Agent-Timestamp: <unix ms>` — required, request rejected if > 5 min skew.

**Request body:**

```json
{
  "agent_run_id": "01HXYZ...",
  "agent_tool_call_id": "01HXYZ...",
  "entity": "quote_corridor",
  "entity_id": 4567,
  "action": "update_corridor_pricing",
  "changes": { "appliedFxSpread": ["0.0075", "0.0060"], "stdFixedFeeUSD": ["1.50", "1.25"] },
  "user_ip": "10.0.1.42",
  "user_agent": "xFRAME Ai Agent / web / chrome-130"
}
```

**Response (201):**

```json
{ "audit_log_id": 998877 }
```

### Implementation notes

- New controller `server/app/features/admin/controllers/agent_audit_callbacks_controller.ts`.
- Writes a row into the existing `audit_logs` table with `actor_type='agent'`, `metadata` JSON pointing at `agent_run_id` and `agent_tool_call_id`.
- Returns `audit_log_id` so the agent can persist it in its own `agent_tool_calls.priceframe_audit_log_id` for cross-system lookup.
- Idempotency: unique index on `(agent_tool_call_id)` in `audit_logs.metadata->>agent_tool_call_id` via a partial functional index. Replay returns the existing row.

### Tests

- Japa: 201 happy path; 401 missing JWT; 403 bad signature; 400 stale timestamp; 200 idempotent replay.

---

## PR #5 — Seed `agent.*` permission codes

### Why

The agent re-checks permissions on every tool execution. We need codes to check against.

### Spec

**Migration:** seeder + idempotent migration that inserts into existing `permissions` table:

| Code | Description |
|---|---|
| `agent.enabled` | Base access to the xFRAME Ai Agent at all |
| `agent.quotes.read` | List/get own quotations, list available corridors, lookup FX, lookup Salesforce PR, preview pricing changes |
| `agent.quotes.create` | Create a quotation; bulk add corridors to a quotation |
| `agent.quotes.edit` | Update corridor pricing; set FX spread |
| `agent.quotes.recalc` | Trigger recompute of quote aggregates |
| `agent.approvals.submit` | Submit a quotation for approval |
| `agent.salesforce.read` | Lookup Salesforce pricing requests / opportunities |

**Profile assignment:** all 7 codes assigned to the **Sales Representative** profile by default (TBD: confirm the existing role/profile code in PriceFRAME — open question #4 in Phase 3).

If the Sales Representative profile doesn't exist yet, this PR additionally creates it (with the existing permissions a sales rep needs today plus the new `agent.*` ones).

### Implementation notes

- Idempotent migration: `INSERT ... ON CONFLICT DO NOTHING` on `permissions.code`.
- Same for `profile_permissions` link rows.
- Reversible: a `down()` that deletes the link rows; we don't delete the permission codes themselves (safer).

### Tests

- Japa: a Sales Rep user has all `agent.*` codes in `authContext.permissions` after login; a non-Sales-Rep user does not.

---

## PR #6 — `GET /api/v1/fee-annex-versions/:id/chunks` (v1.5, RAG)

### Why

RAG is deferred to v1.5; this PR also waits. But specifying it now keeps PriceFRAME's side of the contract honest when v1.5 starts.

### Spec

**Path:** `GET /api/v1/fee-annex-versions/:id/chunks`

**Auth:** `auth_middleware` + `permission_middleware(['quotes.read'])`.

**Query:** `chunk_size` (default 1500 chars), `overlap` (default 200 chars).

**Response:**

```json
{
  "fee_annex_version_id": 42,
  "quote_id": 123,
  "version": 4,
  "modified_at": "2026-05-01T...",
  "chunks": [
    { "index": 0, "text": "...", "char_start": 0, "char_end": 1500, "section_hint": "Fees" },
    { "index": 1, "text": "...", "char_start": 1300, "char_end": 2800, "section_hint": "FX" }
  ]
}
```

### Implementation notes

- Strips HTML tags from `fee_annex_versions.content` (it's stored as rich HTML).
- Splits on heading boundaries when possible (`<h1>`, `<h2>`), falling back to fixed-size with overlap.
- `section_hint` derived from the most recent preceding heading.
- The agent embeds each chunk with Gemini `text-embedding-004` and stores in `agent_knowledge_chunks` on its side.

### Tests

- Japa: chunks cover the full content; overlap is correct; section_hint propagates.

---

## PR #7 — `GET /api/v1/notes/for-rag?since=cursor` (v1.5, RAG)

### Why

`notes` rows attached to quotes carry partner-specific context the agent could leverage during RAG retrieval. Same v1.5 deferral as #6.

### Spec

**Path:** `GET /api/v1/notes/for-rag?since=<id-cursor>&limit=200`

**Auth:** `auth_middleware` + a new `agent.rag.index` permission (granted to the service account only — not to end users).

**Response:**

```json
{
  "notes": [
    {
      "id": 11,
      "entity_type": "quote",
      "entity_id": 123,
      "content": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "next_cursor": 11
}
```

### Implementation notes

- Service-account auth uses the same HMAC header trick from PR #4 (`X-Agent-Service-Signature`), with **no** end-user JWT — this is a batch indexing endpoint, not a per-user read.
- Returns only `is_pinned` or non-deleted notes.

### Tests

- Japa: pagination via cursor; respects deletion; rejects non-service callers.

---

## Sequencing & dependency graph

```
#5 (permissions) ─┬─► #1 (pricing-context, gated by quotes.read)
                  │
                  ├─► #2 (preview, gated by quotes.read) ──► PricingEngineService unlocks recalc-refactor
                  │
                  ├─► #4 (audit callback)
                  │
                  └─► #3 (refresh-token; technically independent — can land anytime)

#6, #7 wait for v1.5 (RAG turn-on).
```

**Suggested PR order**: #5 → #3 → #1 → #2 → #4. (#5 unlocks the rest; #3 has no agent dependency and unblocks mobile work; #1 and #2 are the agent's main reads; #4 ships last because it requires both the agent and PriceFRAME to be talking.)

---

## What this catalog explicitly does NOT include

These are flagged out of scope for v1 PriceFRAME changes:

- New endpoints for the **approver-side** agent workflow (approve / reject / recompute via agent). v2.
- A change-feed / webhook from PriceFRAME → agent on quote changes (push-style cache invalidation). v2 or later.
- Salesforce write proxies through the agent. Salesforce writes stay human-initiated.
- File / blob storage in PriceFRAME — the agent has its own S3-compatible bucket.
- pgvector extension in PriceFRAME's Postgres — RAG lives on the agent's side.
- Replacing PriceFRAME's in-memory rate limiter with Redis. Out of scope; only the agent gets Redis-backed limits.

Anything on this "out of scope" list that becomes urgent should be its own delta-PR with its own justification.

---

## Approval gates before any of this lands

1. Architecture proposal (`02-architecture-proposal.md`) is signed off.
2. This catalog is signed off — at minimum, agreement on PRs #1–#5 as the v1 critical path.
3. PriceFRAME team has bandwidth allocated for ~5.5 dev-days of small reviews.
4. Vertex AI access confirmed (so the agent has somewhere to call besides AI Studio).

Once those four are checked, PR #5 can start within the day. The rest follow the sequence above.
