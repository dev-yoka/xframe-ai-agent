# Phase 1 — PriceFRAME Workspace & Domain Discovery

> Goal of this report: ground every later architecture decision in what the code actually does today. The agent is domain-specific, so Section 1.5 (domain analysis) is the load-bearing section.

**Source of truth:** verified against the live codebase by three parallel exploration passes on branch `xFRAME_v1` (commit `e786b4a0`).

## 1.1 Repository Structure

**Layout** — npm workspaces monorepo, three workspaces:

```
PriceFRAME/
├── client/      React 18 + TS + Vite              ← web client (React 18.3.1)
├── server/      AdonisJS 6 + Lucid ORM            ← REST API (port 3333)
├── shared/      Zod + Decimal.js (types + validators + utils)
└── package.json (workspaces: client, server, shared)
```

- **Package manager:** npm (lockfile `package-lock.json` committed)
- **Monorepo tool:** plain npm workspaces. **No Turborepo, Nx, or Lerna** (no `turbo.json` / `nx.json`).
- **Build orchestration:** sequential — `npm run build` does `build:shared → build:server → build:client`. Dev uses `concurrently` to run all three.
- **TypeScript project references:** root `tsconfig.json` references child workspace tsconfigs; `shared/tsconfig.json` is `composite: true` with declaration maps. Client `tsconfig.app.json` has `strict: false` (pragmatic, not strict).
- **Deploy topology** (README): nginx (80/443) serves `client/dist/` statically and proxies `/api/*` → AdonisJS at port 3002 (env-configurable). **No Dockerfile or docker-compose committed.**
- **No CI:** no `.github/workflows/`. CI is currently external/manual.

## 1.2 Backend (`server/`)

- **Framework:** AdonisJS 6 (v6.17.2), Node ≥18, TypeScript-native.
- **API style:** pure **REST** (~150–160 endpoints). One **SSE** endpoint exists today: `GET /api/jobs/:id/stream` for corridor ETL job status (`server/start/routes.ts:1539-1548`). **No GraphQL, no WebSocket, no tRPC, no gRPC.**
- **Versioning:** all routes prefixed `/api/` only — no `/v1` segment today. We'll introduce `/api/v1` for agent endpoints.
- **Module layout** (`server/app/`):
  - `features/` — domain-driven feature modules: `identity`, `quotes`, `corridors`, `atvs`, `approvals`, `admin`, `salesforce`, `google_drive`, `notes`, `notifications`. Each holds its own `controllers/`, `services/`, sometimes `models/`. This is the conventional pattern; the agent module should follow it.
  - `models/` — 28–29 Lucid models at the top level.
  - `middleware/` — `auth_middleware.ts`, `permission_middleware.ts`, `rate_limit_middleware.ts`, `container_bindings_middleware.ts`, `force_json_response_middleware.ts`.
  - `services/` — cross-cutting: `winston_logger.ts`, `google_drive_service.ts`, `html_to_docx_service.ts`, `identity/auth_service.ts`, `audit_logger.ts`, `permission_scopes.ts`.
  - `exceptions/handler.ts` — global handler (`server/app/exceptions/handler.ts:1-35`).
- **Path aliases** (`server/package.json` imports + `tsconfig.json`): `#controllers/*`, `#services/*`, `#models/*`, `#validators/*`, `#features/*`, `#middleware/*`, `#config/*`, `#utils/*`, `#database/*`, `#start/*`, `#policies/*`, `#abilities/*`.
- **Auth model:**
  - Custom JWT bearer + DB-backed `sessions` table — verified in `server/app/middleware/auth_middleware.ts:1-125`. Bearer in `Authorization: Bearer <token>` or `?token=` query param (the latter is what enables EventSource auth on the existing SSE).
  - JWT secret: `JWT_SECRET`. Logical JWT TTL: 7d. Physical session TTL: 60 min (`SESSION_TTL_MINUTES`), refreshed every request.
  - **Hybrid Firebase Auth** optional: if a user has `firebase_uid`, the middleware revalidates the Firebase user (`config/firebase.ts:1-80`).
  - **RBAC** via `Role → Profile → Permission` (many-to-many `profile_permissions`). `permission_middleware.ts:1-17` matches required codes against `request.authContext.permissions`. **No multi-tenancy / no org_id columns observed** — single-tenant.
  - `request.authContext` shape: `{ user, role, profile, permissions[], groups[], session }` — the agent will get all of this for free.
- **Validation:** Two layers — server-side Vine (AdonisJS native), and shared **Zod 4.0.15** schemas in `shared/src/validators/{identity,notifications,notes,corridors,quotes,approvals,atvs,filters}.ts`. Newer features prefer the shared Zod schemas.
- **Service layer organization:** business logic lives in `app/features/<feature>/services/*Service.ts`. Controllers are thin. **This is exactly the right surface for agent tools to wrap.**
- **Background jobs:** **no queue library** (no BullMQ, Bee, Bullboard, Agenda). "Jobs" are inline ETL tracked by DB rows in `jobs` and `atv_jobs` tables, with progress streamed via the SSE endpoint. The agent runtime will need a queue (recommend `bullmq` on Redis, justification deferred to Phase 3).
- **Logging:** AdonisJS pino logger (`server/config/logger.ts`) + a **Winston** singleton (`server/app/services/winston_logger.ts`). The agent should reuse Winston for run/tool-call logs.
- **Tracing/APM:** none today. No Sentry, no OpenTelemetry, no Datadog. Phase 3 will recommend Langfuse or OTel.
- **Config & secrets:** `server/.env` (gitignored), `.env.example` documents the keys: `JWT_SECRET`, `PG_*`, `SESSION_DRIVER`, `CORS_ORIGIN`, `GOOGLE_DRIVE_*`, `SALESFORCE_*`. Accessed via `import env from '#start/env'`. Firebase settings live in DB (`admin_settings` table), not env.
- **Rate limiting:** in-memory `Map` bucket per IP+URL, default 1000 req / 15 min (`rate_limit_middleware.ts`). **Single-instance only — does not survive horizontal scaling.** This is a known weak spot for agent endpoints; flag for Phase 3.
- **Outbound HTTP:** `axios`, `googleapis`, `firebase-admin`, `nodemailer`. **No `@anthropic-ai/sdk` or `openai` package installed today** — we get a clean slate.

## 1.3 Database

- **ORM:** Lucid (AdonisJS 6) — confirmed `server/config/database.ts`.
- **Connections:** Postgres in production (`pg` driver) — accepts either `DATABASE_URL` or `PG_HOST/PORT/USER/PASSWORD/DB_NAME`. SQLite for dev convenience.
- **Migrations:** 38 timestamped migration files in `server/database/migrations/`, chronological. Auto-run via `node ace migration:run`. Seeders present.
- **Decimal precision:** **Decimal.js 10.6.0** used for money math (shared dep). Several migrations widen numeric precision (`1773*_precision_widening*`). The agent must never coerce money to JS `number`.
- **Core entities — the spine** (most-referenced):

| # | Model | Table | Role |
|---|---|---|---|
| 1 | `Quote` | `quotes` | The central quotation record — has aggregates + many snapshot JSON columns |
| 2 | `QuoteCorridor` | `quote_corridors` | A corridor's pricing inside a quote — the unit of pricing |
| 3 | `Approval` / `ApprovalHistory` | `approvals` / `approval_history` | Workflow state + audit timeline |
| 4 | `CorridorVersion` / `CorridorList` / `CorridorRawRecord` | versioned corridor reference data |
| 5 | `User` / `Role` / `Profile` / `Permission` / `UserGroup` | identity + RBAC |
| 6 | `CurrencyRate` / `Currency` / `SourceCurrency` / `PayoutCurrency` / `FundingCurrency` | FX |
| 7 | `FeeAnnexVersion` / `FeeAnnexComment` | fee-schedule documents (versioned, signed) |
| 8 | `AuditLog` | per-action audit trail (`user_id, entity, entity_id, action, changes JSON, ip, user_agent`) |

Supporting: `Note`, `Notification`, `Session`, `PasswordResetToken`, `AdminSetting`, `Country`, `Region`, `BdRegion`, `Service`, `TransactionType`, `OfferedService`, `CorridorATV`, `CorridorWatchedFieldsConfig`.

- **Agent persistence (to be added in Phase 3):** `agent_conversations`, `agent_messages`, `agent_runs`, `agent_run_steps`, `agent_tool_calls`, `agent_attachments`, `agent_audit_log`, plus pgvector tables if RAG is in scope.

## 1.4 Frontend (`client/`)

- **Stack:** React 18.3.1 + TS 5.7.2 + Vite 6.3.1. Manual chunk splitting (React / Query / UI / utils) in `client/vite.config.ts:38-50` for low-memory builds.
- **Router:** React Router DOM v6.30.1 (`BrowserRouter`). `App.tsx` toggles on `isLoggedIn`; `AppContent.tsx` is tab-based: `all-quotes`, `dashboard`, `approvals`, `admin`, `profile`. **Single-tab guard** via `BroadcastChannel` + `localStorage`.
- **Styling:** Tailwind 3.3.3 with extended semantic tokens (`fee`, `fx`, `volume`, `financial`, `corridor`, `approval`, `summary`). Headless UI 1.7.18 + Heroicons 2.1.1 + Framer Motion 11.0.3. Custom animations `fade-in`, `slide-up`, `slide-down`.
- **Component model:** feature-folder layout `client/src/features/{auth,admin,approvals,dashboard,notes,notifications,profile,quotes,shared}`. Shared primitives in `features/shared/components/`: `Button`, `Modal`, `Input`, `Select`, `MultiSelect`, `LoadingSpinner`, `Breadcrumbs`, `ErrorBoundary`, `ToastProvider`.
- **State:** TanStack Query 5 for server state (QueryClient mounted in `App.tsx`, devtools in dev). Feature-local Zustand stores — examples:
  - `features/quotes/stores/{quotes.pricing.store.ts, quotes.current.store.ts, quotes.approvals.store.ts, quotes.conflicts.store.ts, pricing.engine.store.ts}`
  - `features/quotes/store/{useAppStore.ts, useSetupFeeStore.ts, useCorridorResultsStore.ts, useUIStateStore.ts}`
  - `features/admin/atvs/store/*`, `features/admin/corridors/store/*`
  - `features/approvals/stores/approvals.view.store.ts`, `features/notifications/stores/notifications.ui.store.ts`
- **API client:** single axios instance at `client/src/services/api.ts` (10s timeout, `VITE_API_URL`). **No request/response interceptor** — auth header attached manually per call from `localStorage('auth_token')` inside `AuthContext`. This is the second weak spot (no central 401 handler, no centralized error normalization). Flag for Phase 3.
- **Auth UI:** `features/auth/components/Login.tsx` — email/password + Google OAuth via Firebase. `SessionExpiredModal` shown on 401. No refresh-token rotation visible.
- **Existing AI/streaming surfaces:** **none**. No chat sidebar, no `EventSource`, no `WebSocket`, no markdown renderer. The agent UI is greenfield on the client side.
- **Virtualization & docs:** `@tanstack/react-virtual` 3.13.12, `xlsx` 0.18.5, `mammoth` 1.8.0 (DOCX), `jsPDF` 3.0.2 + `jspdf-autotable` 5.0.2, `html2canvas` 1.4.1. **`innerHTML` is used unsanitized** in `features/quotes/components/Legal.tsx` — pre-existing risk, worth noting because the agent will render rich content.
- **Toasts & modals:** `ToastProvider` (3.5s auto-dismiss) and the shared `Modal` component are the natural primitives for tool-call notifications and human-in-the-loop confirmations.

## 1.5 Domain analysis (critical)

### What this app is

PriceFRAME digitizes Thunes' Excel-based cross-border payment pricing tool. It manages **quotations** that bundle **corridors** (route + service + payer + currency tuples) with **pricing** (fixed fee, variable %, FX spread, tiered) and computes **revenue, FX margin, total margin, gross margin %, take rate** at corridor and quote level. Quotations move through an **approval workflow** before becoming binding fee annexes, with optional **Salesforce** sync for opportunities/pricing requests.

### What users do today (top user-facing operations)

1. Create / list / view / clone / update / soft-delete quotations
2. Set quotation metadata (name, opportunity type, contract length, waived months, source/funding/fee currency, fee-debit-from, pricing strategy, FX-pricing model, owner, use cases, "show FX source/spread in contract" flags)
3. Add/remove corridors to a quote (single or bulk from reference library)
4. Upload corridor versions from Excel (admin ETL → `corridor_versions` + `corridors_list` + `corridor_raw_records`, with a `jobs` row streamed via SSE)
5. Publish a corridor version (mark `is_active`)
6. Edit corridor pricing: `std_fixed_fee_usd`, `variable_fee_pct`, `fee_discount_pct`, `applied_fx_spread`, `minimum_spread`, treasury FX cost, cost fixed/variable per trx
7. Configure tiered pricing (`pricing_model = 'Trx Fee Tiered Pricing'`; T0–T3 rate cards in `tiered_data` JSON)
8. Set revenue-share splits (`partner_share`, `thunes_share`, `final_fx_spread`)
9. Recalculate quote aggregates (sums + weighted averages)
10. Submit a quote (or specific corridor) for approval (`ANY_ONE` or `ALL_MEMBERS` policy, with chain steps)
11. Approve / reject / comment / withdraw / revoke / recompute an approval
12. Save and version a Fee Annex (rich HTML content), add comments, download as PDF (Puppeteer 24.35.0) and DOCX (docx 9.5.1)
13. Generate downloadable artifacts: SD Quotation, Bulk Configurator, fee annex PDF
14. Lookup live FX rates (`CurrencyRate.xeMidMktAvg`), cached
15. Salesforce: search PRs/opportunities, link a quote to a PR, refresh Salesforce snapshot on a quote, upload SD quotation & fee annex back to SF
16. Pin/comment/notes on a quote (`notes` feature)
17. Notifications (in-app + group), unread count, keepalive
18. User/role/profile/group/permission admin; password reset; MFA enroll/reset/force; Firebase user link/unlink
19. Admin settings: Firebase config, security, SMTP, MFA stats, corridor + ATV publish/health
20. Conflict detection & resolution when a quote's corridors are stale vs the active corridor version (`checkConflicts`, `resolveConflicts`, `softDeleteCorridors`)

### Domain rules / constraints / invariants

- **Quote status FSM:** `draft → active → awaiting_approval → approved | rejected → closed`; `deleted` is terminal.
- **Corridor identity tuple:** (region, country, transaction_type, service, payer, receiving_partner, payout_currency, funding_currency).
- **Approval requirement** (`needs_approval`): set when the corridor's metrics violate guidelines — e.g., applied FX spread below hard-currency minimum. Today these guidelines live in `client/src/features/quotes/lib/approval-guidelines.ts` (hardcoded). **Refactor candidate before agent integration.**
- **Money precision:** Decimal.js everywhere; aggregates round to 6 decimals to fit numeric columns (`recalcAggregates` in `app/features/quotes/services/quotes_service.ts`).
- **Tiered pricing math:** T0–T3 allocation × rate per tier, with weighted contribution to quote aggregates.
- **Visibility:** `QuoteVisibilityService.getVisibleOwnerIds` filters by role + permission (no tenant scoping; role-driven).
- **Salesforce PR ID uniqueness:** DB unique index (`1772618999280_*` migration).
- **Soft delete on corridors** within a quote: `corridor_state = 'deleted'`, tracked also in `quotes.deletedCorridors` array.
- **Audit:** `audit_logs` row written on quote/corridor change; `approval_history` row written on every approval event with `actor_sources_json` capturing `{type: user|role|profile|group, id|code}`.
- **MFA + Firebase:** enabled/enrolled fields on User; `auth_provider ∈ {email, firebase}`.

### Where business logic lives — and what should move

- **Server-side** (correct location): `QuotesService`, `QuoteCorridorsService` (transactions, soft-delete logic, change-history), `ApprovalService` (state machine + notifications), `PricingDefaultsService`, `CorridorVersionConflictService`, `CorridorsEtlService`, `QuoteCloneService`, `QuoteDocumentService` / `FeeAnnexPdfService`, `CurrencyRateCache`, `SalesforceQuoteService`, `AtvImportService`, `QuoteVisibilityService`.
- **Client-side leakage** (refactor candidates **before** agent reads them as ground truth):
  - `client/src/features/quotes/lib/pricing-calculations.ts` — computes revenue fee, FX margin, take rate, gross-margin %, tier outputs, fee-currency conversions. **Duplicated math.** The agent must never re-derive these in the model or the client; we need one server pricing engine that both UI and agent call.
  - `client/src/features/quotes/lib/approval-guidelines.ts` — approval thresholds (FX spread minimums by currency hardness) hardcoded. Should be data-driven (`AdminSetting` or a dedicated `approval_rules` table).
  - `client/src/features/quotes/lib/validatePricingInput.ts` — duplicates shared validators.
- **Net:** before agent v1 GA we'll want a **server-side pricing engine** (new `app/features/quotes/services/pricing_engine_service.ts` or similar) that the existing UI calls, the agent tools call, and tests can drive directly. Sized in Phase 3 roadmap as a precursor task.

### Candidate tools (top 15) — agent surface that maps directly to services today

Tool naming follows snake_case. Risk levels: `READ` / `LOW_RISK_WRITE` / `HIGH_RISK_WRITE`. Approval column = whether human confirmation is required before execution (in addition to existing permission gates).

| # | Tool | Wraps | Inputs (high level) | Side effects | Risk | Approval |
|---|---|---|---|---|---|---|
| 1 | `list_quotations` | `QuotesController::index` | filters: owner, status, type, search; paging, sort | none | READ | no |
| 2 | `get_quotation` | `QuotesController::show` + `QuoteCorridorsController::getByQuote` | `quoteId` | none | READ | no |
| 3 | `list_corridors_available` | `CorridorsController::getActive` | region, country, service, payer, payout_currency, version_id? | none | READ | no |
| 4 | `get_currency_rate` | `CurrencyRateCache` / `CurrencyRateController` | currency code | none | READ | no |
| 5 | `lookup_salesforce_pr` | `SalesforceService::getPricingRequest` / `searchSalesforcePRs` | query / pr_id | external read | READ | no |
| 6 | `create_quotation` | `QuotesService::create` | name, opportunityType, filters, owner, currencies, strategy | INSERT quote + audit | LOW_RISK_WRITE | yes (writes pricing) |
| 7 | `clone_quotation` | `QuoteCloneService::clone` | source_quote_id, new_name? | INSERT quote + corridors + audit | LOW_RISK_WRITE | yes |
| 8 | `add_corridor_to_quote` / `bulk_add_corridors` | `QuoteCorridorsService::create` / `bulkCreate` | quote_id, corridor identity + pricing fields[] | INSERT corridors in transaction + recalc + audit | LOW_RISK_WRITE | yes if `needs_approval` becomes true |
| 9 | `update_corridor_pricing` | `QuoteCorridorsService::update` | corridor_id, quote_id, std/variable/fx/cost/tier fields | UPDATE + change_history + audit + recalc | LOW_RISK_WRITE | yes |
| 10 | `set_fx_spread` | same as #9 with single-field semantic | corridor_id, applied_fx_spread, reason | UPDATE + audit | LOW_RISK_WRITE | yes if below `minimum_spread` |
| 11 | `recalculate_quote_aggregates` | `QuotesService::recalcAggregates` | quote_id | UPDATE quote aggregate columns | LOW_RISK_WRITE | no |
| 12 | `check_corridor_conflicts` / `resolve_corridor_conflicts` | `QuoteCorridorsController::checkConflicts` / `resolveConflicts` | quote_id, resolution choices | UPDATE on resolve | LOW_RISK_WRITE | yes on resolve |
| 13 | `submit_for_approval` | `ApprovalsController::store` (quote- or quote-corridor-scoped) | record_type, record_id, policy, approvers, due_date, chain, comment | INSERT approval + history + notifications | HIGH_RISK_WRITE | yes |
| 14 | `approve_quotation` / `reject_quotation` / `comment_on_approval` | `ApprovalsController::approve/reject/comment` | approval_id, comment | UPDATE approval, INSERT history, may unlock quote | HIGH_RISK_WRITE | yes (with explicit user click) |
| 15 | `generate_fee_annex_pdf` / `save_fee_annex_version` | `QuoteDocumentController::*` / Fee Annex services | quote_id, content?, format | READ + render + (save) version | LOW_RISK_WRITE for save | yes for save, no for download |

**Tools `update_quotation_metadata`, `link_to_salesforce_pr`, `refresh_salesforce_snapshot`, `upload_sd_quotation`** are obvious additions to cover the rest of the lifecycle and will be specified in Phase 3.

**Tools the agent should NOT have at v1** (justified case-by-case in Phase 3): destroy/delete quotation, publish corridor version, change user roles/permissions, modify admin settings, send group notifications, password resets.

### RAG-worthy knowledge sources

1. `client/src/features/quotes/lib/approval-guidelines.ts` (until refactored into the DB)
2. `fee_annex_versions.content` — rich HTML fee schedules, partner terms — the strongest semantic-retrieval target
3. `corridors_list.corridor_details` JSON — semi-structured pricing metadata
4. `admin_settings` rows that hold policy text / SLAs
5. `notes` rows attached to quotes (partner-specific context)

Vector store recommendation deferred to Phase 3; if approved, **pgvector** in the existing Postgres is the obvious low-friction default — no new infra.

## 1.6 Cross-cutting

- **TypeScript strictness:** mixed. `shared/` is strict + composite. `server/` extends `@adonisjs/tsconfig` (strict). `client/tsconfig.app.json` is **`strict: false`** — pragmatic decision pre-dating this work. Agent code on the client will be added strict by default; converting the rest of the client to strict is out of scope.
- **Tests:** Japa runner on server (`@japa/runner` + assert + api-client + plugin-adonisjs), bootstrap at `server/tests/bootstrap.ts`. **Visible test coverage is thin** — the bootstrap is present but few test files. Client has Cypress 15.6.0 installed; no spec files committed. No Jest/Vitest. **The agent module will land with first-class unit + integration tests because there is no inherited test discipline to follow.**
- **Lint/format:** ESLint 9 + typescript-eslint + react-hooks plugin on client. AdonisJS ESLint + Prettier presets on server. **No `.husky/`, no `lint-staged`, no pre-commit hooks.**
- **CI/CD:** none committed. Deploys are manual via nginx + AdonisJS build artifacts.
- **Existing AI/ML:** none. No `@anthropic-ai/sdk`, `openai`, `langchain`, `llamaindex`, embeddings packages, or vector DBs in `package.json`. Clean slate.
- **Security**:
  - Rate limiting: in-memory bucket per process (won't survive scale-out) — see 1.2.
  - Sanitization: server validates with Vine + Zod; client has one unsanitized `innerHTML` site in `Legal.tsx` to be aware of.
  - Audit logging: `audit_logs` + `approval_history` are good foundations; we'll extend with `agent_audit_log` for run-level traceability.
  - Secrets: env-based, `JWT_SECRET` + `SALESFORCE_*` + `GOOGLE_DRIVE_*`. No KMS / Vault. Phase 3 will recommend a rotation policy for the new LLM/STT keys.
  - Auth token stored in `localStorage` on the client (not HttpOnly cookie) — pre-existing XSS exposure.
- **File / blob storage:** no `@adonisjs/drive`, no S3. The only "blob" handling today is local-disk caching of Google Drive templates under `var/data/templates/*`. **Voice and image uploads will need real storage** (S3 / GCS / on-disk volume) — to be decided in Phase 2/3 based on your compliance constraints.
- **Document generation:** Puppeteer (PDF), docx (DOCX), exceljs/xlsx (spreadsheets), handlebars + jsdom + html-to-text. Useful primitives if the agent needs to produce artifacts.

---

## Risks and pre-agent refactors (called out now so they don't surprise us in Phase 3)

| Risk / debt | Where | Why it matters for the agent | Recommended timing |
|---|---|---|---|
| Pricing math duplicated on client | `client/src/features/quotes/lib/pricing-calculations.ts` | Agent must call a single source of truth | Refactor before agent v1 GA (precursor task) |
| Approval guidelines hardcoded in client | `client/.../approval-guidelines.ts` | Agent advice & gating depend on these | Move to DB before agent reads them |
| In-process rate limiter | `rate_limit_middleware.ts` | Agent endpoints will be hot + need per-user/org/tool limits | Replace with Redis-backed limiter when introducing queue |
| Single axios client, no interceptors, token in localStorage | `client/src/services/api.ts`, `AuthContext.tsx` | Streaming + 401 + retry need central handling | Introduce typed agent client; refactor existing client later |
| No CI, no pre-commit, thin tests | repo root | Agent quality bar requires CI + eval harness | Build CI as part of agent module |
| No queue, no APM, no tracing | server | Agent runs are long-lived, multi-step, must be observable | Land bullmq + Langfuse/OTel with the agent |
| No file/blob storage | server | Voice + image input is a hard requirement | Add S3-compatible storage as part of agent infra |
| `innerHTML` without sanitization | `Legal.tsx` | The agent may render rich content | Use a sanitizer (DOMPurify) when the agent renders HTML |

## Open assumptions to confirm in Phase 2

These are flagged for the upcoming clarifying-questions batch, not decided here:

1. Which domain workflows are in scope for v1 (the candidate set: pricing-edit assistant, quotation-creation copilot, approval triage, FX-rate Q&A, "explain this quote" read-only mode).
2. Whether the agent can write at all in v1 or starts read-only.
3. Whether multi-tenant scoping will appear (it's absent today).
4. Mobile platform (React Native / Expo / native / undecided).
5. Voice scope (input-only vs full conversation, TTS yes/no).
6. Data residency / PII / compliance (cross-border payments → likely sensitive but to be confirmed).
7. Provider: Anthropic-only vs multi-provider abstraction.
8. RAG: needed or not, and which sources first.

(The full Phase 2 question list is held until you sign off on Phase 1.)

---

## Verification

This report was assembled by three parallel exploration passes reading source files (no edits). Every load-bearing claim cites a file path. To double-check before approval, the user can:

- `cat server/start/routes.ts | head -200` — confirm REST + SSE
- `ls server/app/models/` — confirm 28+ models
- `ls server/app/features/` — confirm feature-folder layout
- `cat package.json | jq .workspaces` — confirm npm workspaces (no Turborepo)
- `grep -r "anthropic\|openai" package.json server/package.json client/package.json` — confirm clean slate (should return nothing)
