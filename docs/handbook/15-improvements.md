# 15 — Improvement Recommendations

> A prioritized backlog of architectural improvements based on the current codebase. Each entry: what, why, effort, and impact.

## Priority levels

- 🔴 **High** — blocks scale, correctness, or significant capability
- 🟡 **Medium** — clear value, no urgency
- 🟢 **Low** — quality of life, future-proofing
- ✅ **Done** — completed in a later iteration; kept here for traceability

---

## 15.1 ✅ Wire `ModelRunner` into the HTTP path — DONE

**Status:** Shipped.

**What was done:** Added `agent/dispatch.py:execute_run` that picks `ModelRunner` (when `settings.provider_configured`) or `AgentLoop` (deterministic fallback). `POST /messages`, `POST /runs`, and `worker.run_agent_job` now all route through `execute_run`. Also added `agent/history.py:load_history` to assemble `ChatMessage` history from `agent_messages`, and `provider/factory.py:build_router` to construct a `ProviderFailoverRouter` from configured providers in order (Vertex → Anthropic).

**Files:** `agent/dispatch.py` (new), `agent/history.py` (new), `provider/factory.py` (new), `api/v1/conversations.py`, `worker.py`, `settings.py` (`default_model` field added), `tests/test_dispatch.py` (new).

**Known limitation:** the router currently passes one `default_model` string to whichever provider it selects. If Anthropic falls over from Gemini, it would receive `"gemini-2.5-flash"` and fail. Either pick one provider OR add per-provider model overrides (next iteration).

---

## 15.2 🔴 Conversation summarization for long histories

**What:** when conversation length exceeds a threshold (say 30 messages), summarize older turns into one system message before sending to the LLM.

**Why:** input tokens grow linearly with conversation length → cost grows roughly linearly with turn count *and* model gets distracted by stale context.

**Effort:** ~2 days.

**Impact:** order-of-magnitude cost reduction for long conversations; better model performance.

**Sketch:**

```python
def trim_history(history: list[ChatMessage], max_tokens: int) -> list[ChatMessage]:
    if estimate_tokens(history) < max_tokens:
        return history
    summary = call_llm_for_summary(history[:-10])
    return [ChatMessage(role="system", content=[ContentBlock(type="text", payload={"text": summary})])] + history[-10:]
```

Run as a pre-step in `ModelRunner.run()`.

---

## 15.3 🟡 Provider context caching

**What:** use vendor caching features:

- Anthropic: `cache_control: {"type": "ephemeral"}` on system message + tool schemas.
- Vertex: context caching API.

**Why:** the system prompt + tool catalog is ~2-5K tokens that's identical across all calls in a conversation. Caching cuts the per-call cost ~80% for input.

**Effort:** ~1 day per provider (smaller for Anthropic).

**Impact:** large cost reduction at moderate scale.

**Where:** `provider/anthropic.py` and `provider/gemini_vertex.py` `stream()` methods.

---

## 15.4 ✅ Feed tool errors back to the model — DONE

**Status:** Shipped.

**What was done:** `ModelRunner._dispatch_proposals` now appends a synthetic `ToolExecutionResult` carrying `{"error": {"cause": ..., "detail": ...}}` whenever:
- `tool_registry.get(name)` returns None (`cause=unknown_tool`)
- `tool.input_model.model_validate(args)` fails (`cause=schema_validation_failed`)
- `tool.execute` raises `PriceFrameError` (`cause=priceframe_error`)
- `tool.execute` raises `ValueError` from local validation (`cause=tool_validation_error`)

These are wrapped via `wrap_tool_output` like real results and appended to the model's message history. The model sees the failure on the next iteration and can correct or apologize. The `v1.tool.error` durable event is still emitted in parallel.

**Files:** `agent/runner.py` (added `_build_error_result`, `_record_tool_failure`), `tests/test_runner.py` (new test `test_runner_feeds_unknown_tool_error_back_to_model`).

---

## 15.5 ✅ Use `requires_approval()` for tool record approval — Already Done

**Status:** Already correct in code (`agent/loop.py:115`). The deferred-execution behavior in `AgentLoop` (always pausing to `awaiting_decision`) is intentional — the decisions endpoint executes the tool. `ModelRunner` (now wired in §15.1) executes inline when `requires_approval=False`. No code change needed; this was a documentation gap in the original §15 list.

---

## 15.6 🟡 Run-resume endpoint

**What:** today, after `/decisions` approves a tool call, the run needs to continue. Currently the decisions handler executes the tool but the loop continuation is not wired through `ModelRunner.run()`.

**Why:** the model needs to see the tool result and decide the next step. Without resumption, the conversation just stops after each approval.

**Effort:** ~1 day.

**Impact:** essential for multi-step flows where multiple writes happen.

**Sketch:**

```python
# In /decisions handler after successful approve
if settings.run_execution_mode == "inline":
    runner = ModelRunner(...)
    await runner.run(session, run=run, context=auth, history=_load_history(session, conv_id))
else:
    await enqueue_agent_run(settings, run_id=run.id, auth_context=auth)
```

---

## 15.7 🟡 SSE replay limit pagination

**What:** SSE replay reads up to `SSE_REPLAY_EVENT_LIMIT=2000` events. For runs with >2000 events (chatty test runs or large flows), the client gets truncated history.

**Why:** correctness; current limit silently caps.

**Effort:** ~half day.

**Impact:** correctness; supports longer runs.

**Sketch:** in the SSE generator, loop until no more events or terminal state:

```python
while True:
    batch = await list_run_events(session, run_id=..., after_seq=cursor, limit=2000)
    if not batch: break
    for e in batch:
        yield ...
        cursor = e.seq
    if emitted_terminal or run.status in TERMINAL_STATUSES:
        break
```

---

## 15.8 🟡 Idempotency key cleanup job

**What:** `agent_idempotency_keys` accumulates rows; `expires_at` is set but no cleanup.

**Why:** table grows unboundedly. Replay lookup queries get slower over time.

**Effort:** ~2 hours.

**Impact:** operational.

**Sketch:** add an arq cron:

```python
@cron(hour=3, minute=0)
async def clean_expired_idempotency_keys(ctx):
    async with session_factory() as s:
        await s.execute(delete(AgentIdempotencyKey).where(AgentIdempotencyKey.expires_at < utc_now()))
        await s.commit()
```

---

## 15.9 🟡 Stuck-run reaper

**What:** runs in `running` or `queued` for > 10× wall-clock budget should be auto-cancelled.

**Why:** crashes leave runs orphaned in those states; no automatic cleanup.

**Effort:** ~2 hours.

**Impact:** operational hygiene.

**Sketch:** arq cron at 1-minute interval:

```python
threshold = utc_now() - timedelta(seconds=settings.max_wall_clock_per_run_s * 10)
await s.execute(
    update(AgentRun)
    .where(AgentRun.status.in_(("queued", "running")), AgentRun.updated_at < threshold)
    .values(status="error", error="reaper: stuck > 10x wallclock", completed_at=utc_now())
)
```

Also emit `v1.run.error { cause: "reaper" }`.

---

## 15.10 🟢 Vector embeddings + RAG

**What:** populate `agent_user_memory` from conversation summaries; embed; retrieve top-K relevant items into the system prompt.

**Why:** persistent learning across conversations ("the user prefers India corridor with 0.015 spread").

**Effort:** ~1 week. Needs pgvector + embedding model + summarizer + retrieval logic.

**Impact:** materially better personalization for power users.

---

## 15.11 🟢 Property-based tests with Hypothesis

**What:** add Hypothesis to `dev` deps; write property tests for redaction, wrapping, budget arithmetic.

**Why:** catches edge cases unit tests miss.

**Effort:** ~half day initial.

**Impact:** quality.

---

## 15.12 🟢 Structured "tool_error" stream events for client UX

**What:** today `v1.tool.error` is emitted to the durable log but the SSE event taxonomy could be more granular (`v1.tool.permission_denied`, `v1.tool.upstream_5xx`, etc.).

**Why:** the frontend can show better-targeted error UI.

**Effort:** ~1 day.

**Impact:** UX polish.

---

## 15.13 🟢 Run-level metrics

**What:** Prometheus counters/histograms for `agent_runs_total{status}`, `agent_tool_calls_total{tool_name,status}`, `agent_provider_latency_seconds{provider}`, etc.

**Why:** dashboards in §11 reference these; today they aren't all emitted.

**Effort:** ~1 day.

**Impact:** operational visibility.

---

## 15.14 🟢 Multi-conversation kinds

**What:** the `kind` field on conversations supports arbitrary strings, but only `create_pricing_request` has a prompt. Adding more kinds (`approve_pending_quotes`, `corridor_analysis`, etc.) gives each flow a tailored system prompt + tool subset.

**Why:** simpler prompts per use case → better model performance.

**Effort:** ~1 day per kind.

**Impact:** scales the product.

**Sketch:**

```python
# In runner.run()
prompt_loader = PROMPT_REGISTRY.get(conv_kind, default_system_prompt)
system_text = prompt_loader(role_code=..., profile_code=..., permissions=...)
```

Per-kind tool filtering can also restrict the visible tool catalog further.

---

## 15.15 🟢 Native voice → tool flow

**What:** `POST /voice/transcriptions` exists (Groq Whisper) but the result is just text — there's no pipeline to feed it directly into a run.

**Why:** sales reps in the field talk faster than they type.

**Effort:** ~1 day.

**Impact:** UX.

**Sketch:** add a `POST /conversations/{id}/voice-messages` that transcribes inline, persists as `AgentMessage(source="voice")`, then starts a run.

---

## 15.16 🟢 Multi-model routing

**What:** route by query complexity — cheap model (Gemini Flash) for trivial reads, smarter model (Claude Sonnet) for complex multi-tool plans.

**Why:** 5-10x cost reduction for the long tail of "show me my quotes" type requests.

**Effort:** ~2 days (classifier + routing logic).

**Impact:** cost.

---

## 15.17 🟢 Auto-retry on PII redaction mistakes

**What:** if a model output looks like it tried to include a redacted value (e.g., the model writes `<PII:email>` literally in its response), detect and ask the model to rephrase.

**Why:** the literal placeholder leaking through is rare but jarring.

**Effort:** ~half day.

**Impact:** polish.

---

## 15.18 🟢 Adopt `httpx` streaming for PriceFRAME large reads

**What:** for endpoints that may return large payloads (`/api/corridors/active` could grow), stream the response instead of buffering.

**Why:** memory + time-to-first-byte.

**Effort:** ~half day.

**Impact:** only matters at scale.

---

## 15.19 🟢 Distributed tracing (OpenTelemetry)

**What:** instrument with OTel for end-to-end spans across agent → provider → PriceFRAME.

**Why:** Langfuse covers LLM but not PriceFRAME / DB; OTel ties them.

**Effort:** ~2 days.

**Impact:** operational.

---

## 15.20 🟢 Per-user budgets

**What:** `LoopBudget` is per-run. Add per-user daily / monthly budgets that read from a `agent_user_budgets` table.

**Why:** prevent a single rogue user from running up costs.

**Effort:** ~2 days.

**Impact:** cost governance.

---

## Prioritization summary

```mermaid
quadrantChart
    title Improvement priority matrix
    x-axis Low effort --> High effort
    y-axis Low impact --> High impact
    quadrant-1 Quick wins
    quadrant-2 Major projects
    quadrant-3 Fillers
    quadrant-4 Thankless tasks

    "15.1 Wire ModelRunner": [0.35, 0.95]
    "15.4 Feed tool errors back": [0.15, 0.75]
    "15.5 Honor requires_approval": [0.1, 0.55]
    "15.6 Run-resume endpoint": [0.35, 0.85]
    "15.7 SSE pagination": [0.15, 0.45]
    "15.8 Idempotency cleanup": [0.1, 0.3]
    "15.9 Stuck-run reaper": [0.1, 0.4]
    "15.2 Summarization": [0.6, 0.8]
    "15.3 Context caching": [0.4, 0.7]
    "15.10 Vector RAG": [0.85, 0.6]
    "15.14 Multi-kind": [0.5, 0.55]
    "15.16 Multi-model routing": [0.55, 0.7]
    "15.19 OTel tracing": [0.55, 0.4]
```

---

**Next:** [glossary](./glossary.md) for term definitions or [faq](./faq.md) for common questions.
