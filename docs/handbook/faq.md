# FAQ

## Concepts

### Is this an LLM wrapper?

No. It's a goal-directed **agent**: the LLM is one component of a larger system that includes tool calling, durable state, human-in-the-loop approval, audit logging, and provider failover. The LLM never executes anything — it only proposes actions that the harness validates and runs under user-scoped credentials.

### Why two runners (`AgentLoop` and `ModelRunner`)?

Historical: `AgentLoop` was the deterministic demo path so E2E tests could run without a real LLM. `ModelRunner` is the production LLM-driven path. The v1 HTTP layer still calls `AgentLoop`; wiring `ModelRunner` in is the top backlog item ([§15.1](./15-improvements.md)).

### What's the relationship between PriceFRAME and the agent?

PriceFRAME is the system of record — it owns quotes, corridors, customers, RBAC, audit. The agent is a **conversational front-end** that calls PriceFRAME's REST API on behalf of the user. The agent has its own database for conversation/run state but never holds elevated PriceFRAME credentials.

### Why does the agent not store its own copy of pricing data?

To avoid synchronization issues. PriceFRAME is authoritative. If the agent cached pricing, every PriceFRAME mutation would risk staleness. The agent fetches fresh data each time it needs it.

### What's the difference between a "conversation" and a "run"?

A **conversation** is a persistent thread (like an email thread); it has messages over time. A **run** is one execution of the agent loop triggered by a user message — typically resulting in a few tool calls and an assistant response. One conversation has many runs.

---

## Behavior

### Why does the model sometimes call read tools twice in a row?

Usually the model is being cautious — verifying recent state before a write. If it's *the same* read with *identical args* three times in a row, the runner aborts with `loop_detected`. See [§10.3](./10-debugging-guide.md).

### Why did my run pause when I asked for a recalculation?

If the tool is one of the LOW_RISK_WRITE tools (`create_quotation`, `bulk_add_corridors`, etc.), it pauses for explicit user approval. `recalculate_quote_aggregates` has `requires_approval -> False` and should NOT pause — if it does, that's the `AgentLoop` bug described in [§15.5](./15-improvements.md).

### Why didn't the model use the tool I expected?

Three common reasons:

1. **Permission filter** — the user lacks the permission, so the tool was hidden from the LLM. Check `GET /tools` for what the user actually sees.
2. **Prompt ambiguity** — the system prompt didn't make it clear when to use the tool.
3. **Description mismatch** — the tool's `description` doesn't describe what the user asked for. Refine the description in the tool class.

### Why does the model see PII redacted?

User messages are passed through `redact()` (`agent/redaction.py`) before reaching the LLM. Emails, phones, card numbers, 6-digit codes are replaced with `<PII:email>` etc. The original never leaves the agent. This is intentional — see [§12.4](./12-security-safety.md).

### Why does the model see strange `<tool_output>` tags?

That's the **prompt-injection containment**. Tool results are wrapped with delimiters + `[Untrusted: ...]` marker. The system prompt instructs the model to treat them as data, not instructions. See [§07.4](./07-prompt-engineering.md).

---

## API / Integration

### How do I authenticate?

`POST /api/v1/agent/auth/login { email, password }` → returns a JWT. Pass it as `Authorization: Bearer <token>` on every subsequent call.

For SSE (`EventSource` can't set headers), pass `?token=<token>` in the URL.

### What's an Idempotency-Key for?

`POST` endpoints that create resources accept `Idempotency-Key: <unique-string>`. If you retry with the same key (network hiccup, user double-tap), the server returns the original response instead of creating a duplicate. TTL: 7 days.

### How do I subscribe to live updates?

`GET /api/v1/agent/runs/{run_id}/stream` with `Accept: text/event-stream`. The server pushes events. Save the SSE event `id` (which is the `seq`); on reconnect, send `Last-Event-ID: <last_seq>` to resume from there.

### Can the agent call other systems beyond PriceFRAME?

Today, no — every tool implementation calls PriceFRAME. Adding a Salesforce or external CRM tool would mean a new `ToolDefinition` subclass + a new client similar to `PriceFrameClient`.

---

## Operations

### How do I see what a model said?

If Langfuse is configured (`LANGFUSE_*` env), every LLM call is traced. Otherwise, the assistant text is in `agent_messages` and the tool calls are in `agent_tool_calls`.

### How do I see why a run failed?

```sql
SELECT event_type, payload FROM agent_run_events
WHERE run_id = $1 AND event_type LIKE 'v1.run.%'
ORDER BY seq;
```

The `v1.run.error` event's payload includes `cause` (e.g., `cost_budget_exceeded`) and `message`.

### Where do logs go?

JSON to stdout (structlog). In production, capture via your log aggregator (Loki, ELK, Datadog).

### What metrics do I get?

`/metrics` returns Prometheus format. By default, FastAPI's instrumentator emits `http_*` series. Custom run/tool metrics are a recommended addition — see [§11.3](./11-observability.md).

### How do I roll back a deployment?

Migrations are additive in v1 so rolling back the container image is safe. See [§13.9](./13-deployment.md).

---

## Cost

### How much does one run cost?

Typical Create Pricing Request: $0.005-$0.015 USD across the whole flow. Simple reads: $0.0001-$0.001.

### What's the hard ceiling per run?

`COST_HARD_PER_RUN_USD=0.60` by default. Beyond that, the run aborts with `cause=cost_budget_exceeded`.

### How do I see total cost per day / per user?

SQL on `agent_run_events`'s payload — see [§11.4](./11-observability.md).

---

## Development

### How do I add a new tool?

1. Subclass `ToolDefinition` in `tools/priceframe_*.py` (or a new file).
2. Define `name`, `description`, `permission`, `risk`, `input_model`, `output_model`.
3. Implement `_execute(args, ctx, priceframe)`.
4. Add the instance to `REGISTERED_TOOLS` in `tools/registry.py`.
5. Add tests in `tests/test_tool_base.py` pattern or `tests/test_runner.py` for orchestration.
6. Regenerate OpenAPI: `uv run python scripts/export_openapi.py`.

### How do I add a new conversation kind?

1. Create `agent/prompts/<kind>.py` with `get_system_prompt(...)`.
2. Branch in `ModelRunner.run()` (`runner.py:99-118`).
3. Add a test along the lines of `test_create_pricing_request_flow.py`.

### How do I run tests faster?

`uv run pytest -n auto` (requires pytest-xdist) for parallel.

### Why does CI fail with "openapi.yaml diff"?

You changed a schema (request/response/tool input). Regenerate and commit:

```bash
uv run python scripts/export_openapi.py
git add openapi.yaml
```

### Why does mypy complain about `Any`?

The project runs mypy in strict mode. Avoid `Any` unless absolutely necessary; use `object` or precise types. For interop with un-typed third-party libs, add `# type: ignore[reason]` with a comment.

---

## Security

### Can a user trick the model into reading another user's data?

No — tools call PriceFRAME with the user's own JWT. PriceFRAME's permission middleware enforces data isolation server-side. Even if the model proposed a query against another user's resources, PriceFRAME would 403.

### What stops the agent from writing things the user didn't approve?

The **HITL pause**. Every LOW_RISK_WRITE and HIGH_RISK_WRITE tool requires explicit user approval via `POST /runs/{id}/decisions`. The model can only *propose*.

### Could a malicious customer name field execute commands?

Defended in depth: `wrap_tool_output` containment + system-prompt rule + HITL on writes. Even if all of these fail, the user sees the approval card before any write lands. See [§12.3](./12-security-safety.md).

### How are secrets stored?

`pydantic-settings` with `repr=False` on secret fields → they don't appear in logs/tracebacks. Production: env vars from `.env.production` (not in image) + GCP SA key as Docker secret. See [§12.6](./12-security-safety.md).

### Can the model see the user's password?

No. The password is sent to `POST /auth/login`, proxied to PriceFRAME, and never enters any LLM call. The token returned is also never sent to the LLM.

---

## Future

### When will RAG / vector search be added?

The `agent_user_memory` table is scaffolded; embeddings are not wired. Earliest plan: pgvector + a periodic summarizer. See [§15.10](./15-improvements.md).

### Can the agent talk in languages other than English?

The model can (modern LLMs are multilingual). System prompts are English; rewriting them in target languages is straightforward. Tool descriptions remain English (they're identifiers the model maps).

### Can we use OpenAI instead of Anthropic / Gemini?

Yes — add a new `Provider` implementation in `provider/openai.py` following the existing pattern. Insert it into the `ProviderFailoverRouter` constructor.

### Will the agent learn from user feedback?

Not today. Reinforcement learning from user signals (approve / reject patterns) is a research direction; the data is in `agent_tool_calls.status` if anyone wants to train on it.

---

**Still have questions?** Check the [README](./README.md) index, [glossary](./glossary.md), or open an issue.
