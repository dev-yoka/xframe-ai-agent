# Part 15 — Improvements and Roadmap

> Three chapters: what's already shipped post-v1, the near-term roadmap, and the long-term vision.

---

## Chapter 98 — Shipped Improvements (§15.1, §15.4, §15.5)

### 98.1 §15.1 — Wired ModelRunner into HTTP

**Status: ✅ Shipped (PR #7).**

Before: `POST /messages`, `POST /runs`, and `worker.run_agent_job` called `AgentLoop` (deterministic) even when a provider was configured. `ModelRunner` was implemented but unused from HTTP.

After: `agent/dispatch.py:execute_run` selects `ModelRunner` when `settings.provider_configured` is True, else falls back to `AgentLoop`. All three entry points route through this dispatch.

**New files**:
- `agent/dispatch.py` — `execute_run` selector
- `agent/history.py` — `load_history` for ChatMessage construction
- `provider/factory.py` — `build_router` from settings
- `tests/test_dispatch.py` — covers both routes

**Files modified**:
- `api/v1/conversations.py` — calls `execute_run`
- `worker.py` — same
- `agent/runner.py` — error-feedback path added (§15.4)
- `settings.py` — `default_model` field

40 tests pass, ruff/mypy clean. No schema changes.

### 98.2 §15.4 — Tool errors fed back to model

**Status: ✅ Shipped (PR #7).**

Before: when a tool failed (unknown name, schema validation, PriceFRAME 4xx), the runner emitted `v1.tool.error` and silently dropped the proposal. The model never saw the failure.

After: `_dispatch_proposals` and `_execute_one` catch four error categories:
- `unknown_tool` — tool name not registered
- `schema_validation_failed` — args don't match Pydantic model
- `priceframe_error` — PriceFRAME returned 4xx/5xx
- `tool_validation_error` — tool's local validation raised ValueError

Each is appended as a synthetic `ToolExecutionResult` with `{"error": {"cause": "...", "detail": "..."}}` payload. The model sees the error on the next iteration and can self-correct.

`v1.tool.error` events are still emitted durably for audit.

### 98.3 §15.5 — `requires_approval` already correct

**Status: ✅ Already correct in code.**

Original handbook listed this as a bug. On close reading: `AgentLoop` already calls `await tool.requires_approval(parsed_args, context)` at `loop.py:115`. The deferred-execution behavior (always pausing to `awaiting_decision`) is intentional — `AgentLoop` defers all tool execution to the decisions endpoint by design. `ModelRunner` executes inline when `requires_approval` returns False.

Handbook updated to reflect this; no code change needed.

### 98.4 Cumulative impact

After these three changes, the system properly uses the LLM-driven runner in production (when providers configured), gracefully handles tool errors, and has consistent approval behavior.

The architecture is now what the system was designed to be — closing the gap between the deterministic Phase D/E demo path and the V1 production path.

### 🔑 Chapter 98 takeaways

- §15.1 wired the LLM path into HTTP + worker.
- §15.4 made errors data, not crashes.
- §15.5 was already correct; documentation gap.

---

## Chapter 99 — Near-Term Roadmap

Six items, ordered by impact × effort. All are sized in days; multiple could ship in a sprint.

### 99.1 §15.2 — Conversation summarization

**Impact: high. Effort: ~2 days.**

Long conversations re-send all prior messages → cost grows quadratically. Solution:

```python
def trim_history(history: list[ChatMessage], max_tokens: int) -> list[ChatMessage]:
    if estimate_tokens(history) < max_tokens:
        return history
    summary = call_llm_for_summary(history[:-10])
    return [ChatMessage(role="system", content=[ContentBlock(...summary...)])] + history[-10:]
```

Apply in `ModelRunner.run()` as a pre-step. Saves significant tokens on long sessions.

### 99.2 §15.3 — Provider context caching

**Impact: high (cost). Effort: ~1 day per provider.**

Anthropic's `cache_control` and Vertex's context caching cut input cost by ~80% on the repeating prefix (system prompt + tool catalog).

Implementation:

```python
# Anthropic
client.messages.stream(
    system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
    tools=TOOLS,
    messages=...,
)
```

```python
# Vertex
client.aio.models.generate_content_stream(
    model=model,
    contents=contents,
    config=GenerateContentConfig(
        cached_content=cache_name,  # pre-created cache
        ...,
    ),
)
```

The first call creates the cache; subsequent calls hit it. Reduces cost on long conversations dramatically.

### 99.3 §15.6 — Run-resume after decision

**Impact: high (UX). Effort: ~1 day.**

Today: `POST /runs/{id}/decisions` executes the tool. The model never sees the result unless the user sends a new message.

Fix: after a successful approval, the decisions endpoint should re-invoke `ModelRunner.run()` (or enqueue another worker job) with the tool result appended to history.

Sketch:

```python
# In /decisions handler after successful approve
if settings.run_execution_mode == "inline":
    await execute_run(session, settings=settings, run_id=run.id, context=auth)
else:
    await enqueue_agent_run(settings, run_id=run.id, auth_context=auth)
```

Removes the chatty "Continue?" pattern. Run flows feel natural.

### 99.4 §15.7 — SSE replay pagination

**Impact: medium (correctness). Effort: ~half day.**

Current SSE reads up to `SSE_REPLAY_EVENT_LIMIT=2000` events. Long runs get truncated.

Fix: loop until no more events:

```python
while True:
    batch = await list_run_events(session, run_id=..., after_seq=cursor, limit=2000)
    if not batch: break
    for e in batch:
        yield ...
        cursor = e.seq
    if emitted_terminal or run.status in TERMINAL_STATUSES: break
```

Correctness gain; supports longer runs.

### 99.5 §15.8 — Idempotency key cleanup job

**Impact: medium (operational). Effort: ~2 hours.**

`agent_idempotency_keys` accumulates with no cleanup. Add an arq cron:

```python
@cron(hour=3, minute=0)
async def clean_expired_idempotency_keys(ctx):
    async with session_factory() as s:
        await s.execute(delete(AgentIdempotencyKey).where(AgentIdempotencyKey.expires_at < utc_now()))
        await s.commit()
```

Prevents unbounded table growth.

### 99.6 §15.9 — Stuck-run reaper

**Impact: medium (operational). Effort: ~2 hours.**

Crashed processes leave `agent_runs` in `running` indefinitely. Reaper job:

```python
@cron(minute="*/5")  # every 5 minutes
async def reap_stuck_runs(ctx):
    threshold = utc_now() - timedelta(seconds=settings.max_wall_clock_per_run_s * 10)
    await session.execute(
        update(AgentRun)
        .where(
            AgentRun.status.in_(("queued", "running")),
            AgentRun.updated_at < threshold,
        )
        .values(status="error", error="reaper: stuck", completed_at=utc_now())
    )
```

Plus a `v1.run.error {cause: "reaper"}` event.

### 99.7 Sequence

A sensible quarter plan:

- Week 1: §15.6 (run-resume) — high UX value.
- Week 2: §15.3 (context caching) — high cost value.
- Week 3: §15.2 (summarization) — high cost + UX.
- Week 4: §15.7, §15.8, §15.9 in parallel — operational hygiene.

After this quarter, xFRAME is materially better on cost, UX, and reliability.

### 🔑 Chapter 99 takeaways

- Six near-term items, all sized in days.
- Run-resume + caching + summarization are the biggest wins.
- Operational hygiene (cleanup, reaper) is small but adds up.

---

## Chapter 100 — Long-Term Vision

The remaining §15 items + broader directions. None are urgent; all are interesting.

### 100.1 §15.10 — Vector embeddings + RAG over user history

**Impact: high (capability). Effort: ~1 week.**

Add pgvector + a periodic summarizer + a `search_my_history` tool. Sketch in Chapter 38.

Enables: "Show me deals similar to this one." Personalization. Long-term agent memory.

### 100.2 §15.11 — Property-based tests

**Impact: low-medium (quality). Effort: ~half day.**

Add Hypothesis. Write property tests for `redact`, `wrap_tool_output`, `LoopBudget`.

Catches edge cases unit tests miss.

### 100.3 §15.12 — Granular `tool_error` event types

**Impact: low (UX polish). Effort: ~1 day.**

Today's `v1.tool.error` carries `cause` as a string. Split into specific event types for richer frontend handling:

- `v1.tool.permission_denied`
- `v1.tool.priceframe_upstream_error`
- `v1.tool.schema_invalid`
- `v1.tool.validation_failed`

Each can drive a different UI affordance.

### 100.4 §15.13 — Run-level Prometheus metrics

**Impact: medium (observability). Effort: ~1 day.**

Add counters and histograms:

```python
agent_runs_total{status, cause}
agent_tool_calls_total{tool_name, status}
agent_provider_latency_seconds{provider, model}
agent_priceframe_latency_seconds{method, path_template, status}
```

Enables proper Grafana dashboards from §11.8.

### 100.5 §15.14 — Multi-kind conversation prompts

**Impact: medium (capability). Effort: ~1 day per kind.**

Today: only `create_pricing_request` has a custom prompt. Add:

- `approve_pending_quotes`
- `corridor_analysis`
- `quote_history`
- (anything else the product needs)

Per-kind tool subsets via `KIND_TOOL_FILTERS`. Each kind has tailored UX.

### 100.6 §15.15 — Voice → tool flow

**Impact: medium (UX). Effort: ~1 day.**

`POST /voice/transcriptions` exists but isn't wired to chat. Add `POST /conversations/{id}/voice-messages` that transcribes, persists `AgentMessage(source="voice")`, then starts a run.

Sales reps in the field talk faster than they type.

### 100.7 §15.16 — Multi-model routing

**Impact: high (cost). Effort: ~2 days.**

Classify queries by complexity:

- Simple lookups → Gemini Flash (cheap)
- Multi-step planning → Claude Sonnet (smarter)

Saves 5-10x cost on the long tail.

### 100.8 §15.17 — PII placeholder cleanup

**Impact: low. Effort: ~half day.**

Sometimes the model echoes `<PII:email>` literally in responses. Detect and ask the model to rephrase.

UX polish.

### 100.9 §15.18 — Stream large PriceFRAME reads

**Impact: low (perf). Effort: ~half day.**

For endpoints that may return large payloads, use `httpx.AsyncClient.stream()` instead of buffering.

Matters only at scale.

### 100.10 §15.19 — OpenTelemetry tracing

**Impact: medium (observability). Effort: ~2 days.**

Instrument with OTel for end-to-end spans across:

- Agent HTTP → Runner → Provider → PriceFRAME → DB

Langfuse covers LLM-side; OTel ties everything.

### 100.11 §15.20 — Per-user budgets

**Impact: high (cost governance). Effort: ~2 days.**

Daily/monthly limits per user. Prevents runaway spend from one heavy user.

```python
class AgentUserBudget(Base):
    user_id: int (PK)
    daily_limit_usd: float
    monthly_limit_usd: float
    current_day_spend: float
    current_month_spend: float
    reset_day_at: datetime
    reset_month_at: datetime
```

Check on run start. Reject if over.

### 100.12 New directions not in original §15

Beyond the listed items, future capabilities:

- **MCP server exposure** (Chapter 97) — let other agents use PriceFRAME tools.
- **Multi-agent orchestration** (Chapter 92) — when single-agent strains.
- **Reflection wrappers** (Chapter 91) — for high-stakes writes.
- **Push notifications** — `agent_device_tokens` is scaffolded; pipeline not wired.
- **Web hooks** for completed runs — third-party integrations.
- **Bulk-export endpoint** for GDPR.
- **Admin-side dashboards** — see all runs across users.
- **Plugin SDK** — let third parties write tools that pin into xFRAME.

### 100.13 The five-year horizon

Speculative, but worth thinking about:

- **Model improvements** — frontier models get cheaper, faster, smarter. Costs may drop 10x in 5 years.
- **Tool ecosystems** — MCP or successor becomes standard. xFRAME participates.
- **Autonomous agents** — less HITL on routine flows; more on high-stakes.
- **Multi-modal** — voice + vision become natural inputs.
- **Cross-agent collaboration** — xFRAME's agent talks to a customer's agent.

The current architecture is **friendly** to all of these. Tools are extensible. Prompts are dynamic. Provider abstraction is in place. Defense-in-depth is structural.

What might break? Anything assuming a single-agent, single-domain world. Plan for plurality.

### 🔑 Chapter 100 takeaways

- Many opportunities, all of them additive.
- RAG (§15.10), multi-model routing (§15.16), per-user budgets (§15.20) are the high-value next steps.
- New directions: MCP, multi-agent, reflection, voice integration.
- The architecture is positioned for 5+ years of evolution.

---

### Part 15 wrap-up

You now have the complete picture: what's shipped, what's next, and where the system is headed long-term. The roadmap is grounded in real engineering decisions, not speculation.

### ✍️ Part 15 exercises

1. Pick one near-term item (§99). Write the full implementation plan with tests.
2. Estimate the cost savings from §15.3 (context caching) at 10K conversations/month.
3. Argue: should xFRAME prioritize §15.10 (RAG) or §15.16 (multi-model routing)? Make the case for each.

---

**End of Part 15.**

**Next:** [Part 16 — Glossary](./part-16-glossary.md).
