# Phase A — Phase 1 Verification

**Date:** 2026-05-19  
**Mode:** read-only verification, except this handoff document.

## Repo State

- Current branch: `xFRAME_v1`
- Current HEAD: `e786b4a06d476188f935db7a56f420800aab7f78`
- This matches the Phase 1 baseline commit from `01-workspace-and-domain.md`.
- Tracked code was clean before this deliverable. `docs/ai-agent/` is present locally but untracked at this commit.

## Spot Checks

| Phase 1 claim | Result | Evidence |
|---|---:|---|
| Backend uses feature-folder layout under `server/app/features/`. | Confirmed | `server/app/features/{admin,approvals,atvs,corridors,google_drive,identity,notes,notifications,quotes,salesforce}` with local `controllers/`, `services/`, and some `models/` subfolders. |
| SSE endpoint exists at `server/start/routes.ts:1539-1548`. | Confirmed | `GET /api/jobs/:id/stream` mounts `corridors_sse_controller.stream`, under `/api`, with `auth()` and `permission(['system.settings.all'])`. |
| `auth_middleware.ts` is custom JWT + DB session auth with query-token support. | Confirmed | `server/app/middleware/auth_middleware.ts` accepts `Authorization` or `?token=`, verifies `JWT_SECRET`, checks active `sessions`, refreshes session expiry, optionally validates Firebase UID, and sets `request.authContext` with user, role, profile, permissions, groups, session. |
| Pricing math is duplicated/leaking into the client. | Confirmed | `client/src/features/quotes/lib/pricing-calculations.ts` computes FX margin, revenue fee, total revenue, total margin, gross margin, take rate, and tier outputs. Server `QuotesService.recalcAggregates()` currently rolls up already-stored corridor numbers using `Number(...)`. |
| `Legal.tsx` has unsanitized HTML insertion risk. | Confirmed | `client/src/features/quotes/components/Legal.tsx` uses `dangerouslySetInnerHTML={{ __html: feeAnnexContent }}` and multiple `innerHTML` reads/writes; no sanitizer reference was found in that file. |
| No `@anthropic-ai/sdk` or `openai` dependency in package manifests. | Confirmed | `package.json`, `client/package.json`, `server/package.json`, and `shared/package.json` have no matches for those packages. |

## Open Question #4 — Sales Representative

Resolved: **Sales Representative exists as a profile, not as an exact role.**

- Seeder source: `server/database/seeders/003_profiles_seeder.ts` defines `code: 'PROFILE_SALES'`, `name: 'Sales Representative'`.
- Connected local Postgres confirms an active profile row: `PROFILE_SALES` / `Sales Representative`.
- Connected local Postgres has no role named exactly `Sales Representative`.
- Sales-related roles do exist, including `ROLE_AM_SALES`, `ROLE_AM_SALES_AFRICA`, `ROLE_AM_SALES_ME`, `ROLE_BD_SALES_AFRICA`, and `ROLE_SVP_SALES_AFRICA`.
- For delta-PR #5, seed the new `agent.*` permissions onto profile code **`PROFILE_SALES`**.
- No existing `agent.*` permission rows were found in code or in the connected local Postgres.

## Drift / Notes

- No drift found in the targeted Phase 1 code claims above.
- The connected local Postgres contains extra active `global_sales` role/profile rows that are not defined in the current seeders. They do not change the `PROFILE_SALES` answer.
- The source docs and this handoff are local under `docs/ai-agent/` but are not tracked by the current `e786b4a0` commit yet.
