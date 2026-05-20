# Phase E — Beta Handoff

> **Current reference:** [09-xframe-ai-agent-complete-reference.md](./09-xframe-ai-agent-complete-reference.md) consolidates APIs, tools, lifecycle, and known gaps.

Date: 2026-05-20

Repos:

- `/Users/bhairava/WorkSpace/repos/xframe-ai-agent` on `phase-D/mvp`
- `/Users/bhairava/WorkSpace/repos/PriceFRAME` on local `xFRAME_v1`

## Built

- PriceFRAME PR #4 locally: `POST /api/v1/agent-audit-callbacks` with end-user JWT auth, HMAC signature headers, idempotent `agent_tool_call_id` replay, `audit_logs.metadata`, shared Zod schema, README/OpenAPI snippet, changelog entry, and Japa tests.
- PriceFRAME PR #2 locally: `POST /api/v1/quotes/:id/pricing/preview` with server-side Decimal.js pricing preview, non-persistent before/after aggregates, warning output, shadow-compare hooks in `QuotesService.recalcAggregates`, README/OpenAPI snippet, changelog entry, and Japa tests.
- PriceFRAME web chat surface: `client/src/features/ai_agent/` panel with authenticated agent REST calls, fetch-based SSE parsing with Authorization header support, Markdown rendering via `react-markdown` + `rehype-sanitize`, attachment upload, voice transcription, and Accept/Edit/Reject tool decision controls.
- xframe-ai-agent Phase E schema/API: `agent_attachments`, `agent_attachment_pages`, `agent_user_memory`, tool-call approval/audit columns, attachment upload/read, memory list/delete, voice transcription, ClamAV scan helpers, arq scan job, S3/MinIO storage, and updated OpenAPI snapshot.
- xframe-ai-agent write path: write tools 7/8/9/10/12 are registered, permission-filtered, approval-gated, idempotency-keyed to PriceFRAME, and call PriceFRAME audit callbacks on successful writes. A deterministic `tool:` directive path emits `v1.tool.proposed` for local/demo coverage.

## Deferred / Gaps

- The real Gemini/Anthropic model loop is still not orchestrating natural-language tool calls. Provider adapter shells exist, and the write/decision plumbing is live, but production-quality model tool selection remains the main gap before a true beta demo.
- Redis LIST buffering for SSE is still represented by durable DB replay plus settings; the current stream replay source is `agent_run_events`.
- PriceFRAME PR #2b approval-guidelines table was not implemented in this pass; preview warnings are computed in the new server pricing engine service.
- The full PriceFRAME client build is still blocked by pre-existing TypeScript errors outside `client/src/features/ai_agent/`.

## Verification

- xframe-ai-agent: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run python scripts/export_openapi.py`.
- PriceFRAME server: `npm --workspace server run typecheck`.
- PriceFRAME tests: `npm --workspace server run test -- --files tests/functional/agent_audit_callbacks.spec.ts`; `npm --workspace server run test -- --files tests/functional/pricing_preview.spec.ts`.
- PriceFRAME shared: `npm --workspace shared run build`.
- PriceFRAME AI chat files: isolated `tsc --noEmit` over `client/src/features/ai_agent/*` passed.
