# Part 11 — Testing and Debugging

> Seven chapters on how to verify and troubleshoot xFRAME. The 40-test suite mapped to subsystems, the fixture patterns that make agent testing tractable, the golden-trace eval harness, runbooks for stuck runs, distributed tracing with Langfuse, property-based testing (future), and load/chaos approaches.

---

## Chapter 69 — The 40-Test Suite, Categorized

### 69.1 The matrix

| File | Tests | Subsystem | Validates |
|---|---|---|---|
| `tests/test_health.py` | 1 | Liveness | `/health` returns 200 |
| `tests/test_agent_api.py` | 2 | HTTP + SSE | Conversation lifecycle, SSE events, tool discovery |
| `tests/test_auth_jwt.py` | 3 | Auth | JWT verification, profile cache hit/miss |
| `tests/test_auth_login.py` | 2 | Auth (schema) | LoginRequest/Response Pydantic shapes |
| `tests/test_budget.py` | 5 | Budget | All 5 LoopBudget ceilings raise correct cause |
| `tests/test_provider.py` | 1 | Provider guard | AI Studio refuses ALLOW_REAL_DATA=true |
| `tests/test_redaction_wrapping.py` | 6 | Safety | PII patterns, tool-output containment, control chars |
| `tests/test_tool_base.py` | 4 | Tool contract | requires_approval policy, project_for_model, permission check |
| `tests/test_runner.py` | 5 | Runner | Read auto-execute, write pause, loop detection, provider error, error feedback |
| `tests/test_create_pricing_request_flow.py` | 2 | Runner + prompt | System prompt injection, create_quotation pause |
| `tests/test_phase_e_api.py` | 6 | API integration | Decisions, attachments, memory, voice |
| `tests/test_dispatch.py` | 2 | Dispatch | execute_run picks AgentLoop or ModelRunner |
| `evals/test_eval_ci.py` | 1 | Eval | Golden traces structural replay |

**Total: 40 tests + 1 eval = 41 invocations.**

### 69.2 What's well-covered

| Concern | Tests |
|---|---|
| `LoopBudget` correctness | 5 |
| Runner happy + sad paths | 5 + dispatch (2) |
| Safety functions | 6 + 4 tool-base |
| Auth surface | 3 + 2 |
| End-to-end via HTTP | 2 + 6 |

### 69.3 What's under-covered (and why it's OK for v1)

| Gap | Risk | Why deferred |
|---|---|---|
| Real provider failover | Medium | Requires two real providers; cost + complexity |
| HMAC signing roundtrip | Medium | Requires PriceFRAME end stubbed correctly |
| Rate limit middleware | Low-medium | Limited business risk; easy to add |
| Long-conversation token explosion | Low | Defended by budget already |
| SSE reconnection | Medium | Mobile-side concern; needs eventsource-test |
| Prompt injection end-to-end | Medium | Building-block tests cover it; E2E would need real LLM |

Each gap is documented in §15 (improvements) when relevant.

### 69.4 Test running

```bash
# All tests
uv run pytest

# One file with verbose
uv run pytest tests/test_runner.py -v

# One specific test
uv run pytest tests/test_runner.py::test_runner_executes_read_and_completes -v

# With coverage
uv run pytest --cov=src/xframe_agent --cov-report=html

# Eval only
uv run pytest evals/

# Parallel (assumes test isolation; xFRAME's tests are sqlite-per-test so safe)
uv run pytest -n auto
```

Typical full run: ~5 seconds. CI runs add static checks (ruff format, ruff check, mypy, OpenAPI drift) so the gate is ~15-30s total.

### 69.5 Test isolation

xFRAME's tests use **per-test sqlite-in-tmpfile** for DB isolation:

```python
@pytest.fixture
async def db(tmp_path: Path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield settings, engine, factory
    await engine.dispose()
```

No shared state between tests. Parallel-safe. No cleanup between runs needed.

### 🔑 Chapter 69 takeaways

- 40 tests + 1 eval, ~5s run time.
- High coverage on budget, runner, safety; lower on provider integration and rate limiting.
- sqlite-in-tmpfile per test = clean isolation.
- CI gate: ruff format + ruff check + mypy + pytest + openapi drift.

---

## Chapter 70 — `FakeProvider` and `FakePriceFrame` Patterns

### 70.1 The core idea

Real LLM calls in tests would be:

- **Slow** — seconds per test.
- **Expensive** — cents per run.
- **Flaky** — model outputs vary.
- **External dependency** — CI needs API keys.

So we mock them. Two key mocks:

- `FakeProvider` — replaces `Provider` with scripted `StreamEvent` sequences.
- `FakePriceFrame` — replaces `PriceFrameClient` with canned responses.

### 70.2 `FakeProvider` — scriptable LLM

`tests/test_runner.py`:

```python
class FakeProvider:
    name = "fake"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self._cursor = 0

    async def stream(self, messages, tools, *, model, max_output_tokens):
        script = self._scripts[self._cursor]
        self._cursor += 1
        for event in script:
            yield event
```

Each "script" is a list of events for one `stream()` call. The runner calls `stream` once per model-call iteration; each iteration gets the next script.

Usage:

```python
provider = FakeProvider([
    # First iteration: model decides to call a tool
    [
        StreamEvent(kind="tool_use", payload={
            "name": "get_quotation", "args": {"id": 42}, "call_id": "c1"
        }),
        StreamEvent(kind="usage", payload={"input_tokens": 10, "output_tokens": 5}),
    ],
    # Second iteration: model produces text and finishes
    [
        StreamEvent(kind="text_delta", payload={"delta": "Quote 42 is..."}),
        StreamEvent(kind="usage", payload={"input_tokens": 20, "output_tokens": 8}),
    ],
])
```

Predictable, deterministic, fast.

### 70.3 `FakeProvider` variants

`tests/test_create_pricing_request_flow.py` has a slightly different shape:

```python
class FakeProvider:
    name = "fake"

    def __init__(self, script: list[StreamEvent]) -> None:
        self._script = script
        self.calls: list[list[ChatMessage]] = []

    async def stream(self, messages, tools, *, model, max_output_tokens):
        self.calls.append(list(messages))   # capture for assertions
        for event in self._script:
            yield event
```

This version **records what was sent**. Test can assert on it:

```python
assert provider.calls[0][0].role == "system"
assert "xFRAME AI Agent" in provider.calls[0][0].content[0].payload["text"]
```

The two shapes serve different needs (multi-script for state machine tests, single-script with capture for prompt assertions).

### 70.4 `FakePriceFrame` — canned HTTP

`tests/test_runner.py`:

```python
class FakePriceFrame:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None

    async def get_json(self, *args, **kw):
        return {"id": 123, "name": "PR-1"}
```

The runner only knows it has *something* that responds to `get_json` (and the other methods). The duck-typed instance is enough.

Richer version in `tests/test_phase_e_api.py`:

```python
class FakePriceFrameClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.audit_payloads: list[dict] = []

    async def post_json(self, path, *, jwt_raw, json=None, headers=None):
        self.calls.append(("post", path, json, headers, jwt_raw))
        return {"id": 5042}

    async def post_agent_audit_callback(self, *, jwt_raw, service_secret, payload):
        self.audit_payloads.append(payload)
        return {"audit_log_id": 8801}

    # ... close, get_json, etc.
```

Test asserts:

```python
assert client.calls[0][1] == "/api/quotes"
assert client.audit_payloads[0]["tool_call_id"] == "01HX..."
```

### 70.5 Dependency override

For HTTP tests, FastAPI provides `app.dependency_overrides`:

```python
@pytest.fixture
async def agent_client(test_settings, fake_auth_context):
    app = create_app(test_settings)
    app.dependency_overrides[get_auth_context] = lambda: fake_auth_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
```

`get_auth_context` would normally do JWT verification + profile fetch. The override returns a pre-built `AuthContext` directly — bypassing all auth.

Inside the test:

```python
response = await agent_client.post("/api/v1/agent/conversations", json={"title": "t"})
```

No real JWT needed. No PriceFRAME stub for `/api/auth/profile`. Just direct HTTP.

### 70.6 `monkeypatch.setattr` for tighter mocks

For tests that need to replace a function deep in the codebase:

```python
def fake_build_router(settings):
    return ProviderFailoverRouter(providers=[provider])

monkeypatch.setattr(dispatch, "build_router", fake_build_router)
```

Now `dispatch.execute_run` calls our fake router. The original `build_router` is restored at test end.

xFRAME uses this for the `test_dispatch.py` tests — verifying that `execute_run` chooses ModelRunner when a provider router exists, even though no real providers are configured in the test environment.

### 70.7 Time control

Some tests need controlled time:

```python
@pytest.fixture
def frozen_time(monkeypatch):
    fixed = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr("xframe_agent.models.agent.utc_now", lambda: fixed)
    return fixed
```

Used for tests where event timestamps matter for assertions.

xFRAME's existing tests don't use this heavily (timestamps in events are usually irrelevant to assertions). But for testing TTL behavior (idempotency expiry, profile cache invalidation), time control is essential.

### 🔑 Chapter 70 takeaways

- `FakeProvider` for the LLM; scripts of `StreamEvent`s.
- `FakePriceFrame` for HTTP; canned responses, optional call capture.
- `app.dependency_overrides` for auth-free HTTP tests.
- `monkeypatch.setattr` for deep mocks like `build_router`.

---

## Chapter 71 — Golden Trace Evals

### 71.1 What evals are

A **golden trace** is a recorded expected trajectory for a known input. Eval = "given this input, did the agent do approximately the right thing?"

xFRAME's eval harness lives in `evals/`:

```
evals/
├── README.md
├── test_eval_ci.py          # the runner
├── replay.py                # load and replay golden traces
├── judge.py                 # compare actual to expected
├── golden/
│   ├── create-pricing-request-happy-path.json
│   ├── create-pricing-request-with-fx-override.json
│   ├── handles-stale-corridor.json
│   ├── refuses-write-without-confirmation.json
│   └── prompt-injection-attempt.json
└── fixtures/
    └── synthetic_quote.json
```

5 traces. CI runs them in "structural" mode (asserting declared expectations without touching an LLM). Optionally, switch to "provider" mode for live LLM replay.

### 71.2 Anatomy of a golden trace

```json
{
  "name": "create-pricing-request-happy-path",
  "input": "Create a pricing request for Acme Corp in USD covering corridors US→MX and US→CO.",
  "expected_tools": [
    "lookup_salesforce_pr",
    "list_corridors_available",
    "get_currency_rate",
    "create_quotation",
    "bulk_add_corridors",
    "preview_pricing_change",
    "recalculate_quote_aggregates",
    "submit_for_approval"
  ],
  "expected_final_status": "completed"
}
```

The trace declares **what the agent should do**, not specific text outputs. Tool sequence + final status = the deterministic core.

Free-text outputs are non-deterministic; comparing them character-by-character is brittle. Golden traces compare structural behavior.

### 71.3 Structural mode (CI default)

```python
# evals/test_eval_ci.py (sketch)
def test_all_golden_traces_replay_with_expected_structure():
    for trace in load_golden_traces():
        # In structural mode, just verify the trace declares an expected_tools list
        # and an expected_final_status. Don't actually run anything.
        assert trace.expected_tools is not None
        assert trace.expected_final_status in {"completed", "awaiting_decision", "rejected"}
```

This is a smoke test for the eval harness itself — making sure traces parse and have required fields. Fast, no API calls, runs on every PR.

### 71.4 Provider mode (optional)

```bash
XFRAME_EVAL_MODE=provider \
XFRAME_JUDGE_MODE=llm \
GEMINI_VERTEX_PROJECT=my-project \
uv run pytest evals/
```

In provider mode:

1. Start a real `ModelRunner` with `FakePriceFrame` (so PriceFRAME calls are stubbed).
2. Send the trace's `input` as a user message.
3. Capture the actual tool sequence.
4. Compare to `expected_tools`.

Pass/fail criteria can be **flexible**:

- Exact sequence match (strict).
- Tool set match (lenient — order doesn't matter).
- Subset match (the expected tools are a subset of actual).
- LLM-judge match (an LLM judges similarity).

Default: tool set match. Most agents are robust to slight order variations.

### 71.5 The judge

`evals/judge.py`:

```python
def judge_tool_sequence(expected: list[str], actual: list[str], mode: str = "set") -> bool:
    if mode == "exact":
        return expected == actual
    if mode == "set":
        return set(expected) == set(actual)
    if mode == "subset":
        return set(expected).issubset(set(actual))
    raise ValueError(f"Unknown judge mode: {mode}")


def judge_free_text(expected: str, actual: str, rubric: str | None = None) -> bool:
    if os.getenv("XFRAME_JUDGE_MODE") != "llm":
        return expected.strip() == actual.strip()
    # LLM judge: ask a model to compare against the rubric
    response = call_judge_llm(expected=expected, actual=actual, rubric=rubric)
    return response.matches
```

LLM-as-judge is **fashionable but tricky**:

- Different judge models give different verdicts.
- Bias toward verbose/confident responses.
- Cost per eval.

For tool-sequence comparison, simple set/subset judging is robust. Reserve LLM judging for natural-language outputs.

### 71.6 Adding a new golden trace

When you ship a new prompt or tool change, add a golden trace:

1. Run the flow manually in a test environment.
2. Capture the tool sequence (from `agent_tool_calls`).
3. Write the JSON:

```json
{
  "name": "your-new-flow",
  "input": "User message that triggers the flow.",
  "expected_tools": ["..."],
  "expected_final_status": "completed"
}
```

4. Place in `evals/golden/`.
5. Run `uv run pytest evals/` to verify.

For provider-mode evals, also include any environment specifics (which model, which user role).

### 71.7 Regression catching

The most valuable thing evals do: **catch behavioral regressions**.

Suppose you tweak the system prompt. Before merging:

```bash
XFRAME_EVAL_MODE=provider uv run pytest evals/
```

If a trace fails, the prompt change caused a regression. Fix or update the trace (with a comment justifying the new expected behavior).

Without evals, you'd ship the change and notice the regression a week later in production. With evals, you catch it pre-merge.

### 71.8 Limitations

- **Tied to current tool catalog.** Adding/removing tools requires updating traces.
- **Doesn't catch UX bugs.** A trace passes if tools were called; doesn't verify the text was good.
- **Provider mode is expensive at scale.** Run on scheduled jobs, not every PR.
- **LLM stochasticity** — even with the same input, the model may call tools in slightly different orders.

These are inherent to behavioral testing of probabilistic systems. You triangulate with unit tests + integration tests + evals.

### 🔑 Chapter 71 takeaways

- Golden traces declare expected tool sequence + final status.
- Structural mode (CI default) is fast smoke; provider mode is full replay.
- Set/subset judging is robust; LLM judging is for free text.
- Add a new trace when you ship new prompts or flows.

---

## Chapter 72 — Debugging a Stuck Run

### 72.1 The triage checklist

A user reports "my run never finished." First steps:

1. **Get the run ID.** From the user or the URL.
2. **Check the status.**
   ```sql
   SELECT id, status, error, created_at, started_at, completed_at
   FROM agent_runs WHERE id = '01HX...';
   ```
3. **Look at events.**
   ```sql
   SELECT seq, event_type, payload, created_at
   FROM agent_run_events WHERE run_id = '01HX...' ORDER BY seq;
   ```

The last event tells you where the run got stuck.

### 72.2 Common stuck states and fixes

| Last event | Run status | Cause | Fix |
|---|---|---|---|
| `v1.step.started` (model_call) | `running` | Provider hanging | Check provider health; consider canceling |
| `v1.tool.started` | `running` | PriceFRAME hanging on a specific call | Check PriceFRAME logs for that endpoint |
| `v1.tool.proposed` (requires_approval=true) | `awaiting_decision` | User didn't approve | Expected behavior; or escalate |
| `v1.tool.completed` | `running` | Next model call hung | As above |
| `v1.run.completed` | `completed` | Run is fine; user is wrong | Verify with user |
| `v1.run.error` | `error` | Look at `cause` | See cause-specific debugging |
| (no events) | `queued` | Worker never picked up the job | Check worker; check Redis queue |

### 72.3 Cause-by-cause

`v1.run.error` events include a `cause`:

| `cause` | Meaning | Action |
|---|---|---|
| `step_budget_exceeded` | Too many iterations | Increase `MAX_STEPS_PER_RUN` or refine prompt |
| `tool_call_budget_exceeded` | Too many tools called | Same; consider if model is in confused loop |
| `input_token_budget_exceeded` | Conversation got too long | Trim history; consider summarization |
| `output_token_budget_exceeded` | Model produced too much | Add "be concise" to prompt |
| `cost_budget_exceeded` | Hit cost ceiling | Raise ceiling if intentional |
| `wall_clock_budget_exceeded` | Run took > 60s | Profile; check slow tools |
| `loop_detected` | Same tool 3× | Look at the tool args; usually missing data |
| `provider_error` | All providers failed | Check provider status, quotas, keys |

### 72.4 Manually canceling a stuck run

If you need to unblock immediately:

```sql
UPDATE agent_runs
SET status = 'cancelled',
    cancelled_at = NOW(),
    updated_at = NOW(),
    error = 'manual: stuck'
WHERE id = '01HX...';
```

Then optionally append a final event:

```sql
INSERT INTO agent_run_events (run_id, seq, event_type, payload, created_at)
SELECT '01HX...', COALESCE(MAX(seq), 0) + 1, 'v1.run.error',
       '{"cause": "manual_cancel", "message": "stuck > 5min"}'::json,
       NOW()
FROM agent_run_events WHERE run_id = '01HX...';
```

Mobile clients on the SSE stream will see the new event and close.

### 72.5 Diagnosing "no events"

A run in `queued` status with no events means:

- Worker isn't running (check container status).
- Worker is too slow (queue backed up).
- Redis is down (worker can't dequeue).

Check:

```bash
docker ps --filter name=xframe-worker     # is worker running?
redis-cli LLEN agent-runs                  # how many pending?
docker logs xframe-worker --tail 50        # what's it saying?
```

### 72.6 The reaper job (recommended addition)

Roadmap item §15.9: a scheduled job that finds long-stuck runs and cancels them:

```python
@cron(minute=0, second=0)  # every hour
async def reap_stuck_runs(ctx):
    threshold = utc_now() - timedelta(seconds=settings.max_wall_clock_per_run_s * 10)
    async with session_factory() as s:
        await s.execute(
            update(AgentRun)
            .where(
                AgentRun.status.in_(("queued", "running")),
                AgentRun.updated_at < threshold,
            )
            .values(
                status="error",
                error="reaper: stuck > 10x wallclock",
                completed_at=utc_now(),
            )
        )
        await s.commit()
```

Saves operators from manual SQL when runs leak.

### 🔑 Chapter 72 takeaways

- The first three steps: status, events, error message.
- Last event tells you where the run got stuck.
- `cause` codes on `v1.run.error` are your primary diagnostic.
- Manual cancel is a SQL UPDATE; the reaper job (future) automates this.

---

## Chapter 73 — Tracing with Langfuse

### 73.1 What Langfuse gives you

Langfuse is an open-source observability platform for LLM apps. With xFRAME, it captures:

- Every LLM call with full prompt + response.
- Latency breakdown.
- Token usage.
- Errors.
- Linked traces (one trace per `run_id`).

### 73.2 Setup

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://langfuse:3001    # self-hosted
```

If set, the agent exports traces. If unset, no-op.

For self-host:

```bash
docker compose up -d langfuse-db langfuse
open http://localhost:3001    # signup, get keys
```

### 73.3 What a trace looks like

```
trace_id: 01HX...
metadata: {run_id, user_id, conversation_id}
spans:
  - llm.stream (gemini-vertex)
    input: full messages list
    output: text + tool_use blocks
    duration: 1234ms
    tokens: { input: 1520, output: 38 }
  - tool.get_quotation
    input: { id: 42 }
    output: { data: {...} }
    duration: 87ms
  - llm.stream (gemini-vertex)
    ...
```

Click through to see exact messages sent and received. Invaluable for prompt debugging.

### 73.4 Debugging prompts with Langfuse

Typical workflow:

1. User reports "the agent didn't understand my request."
2. Find the conversation; get the trace ID (logged on every LLM call).
3. Open Langfuse, look at the model's actual response.
4. See it called the wrong tool, or no tool at all.
5. Inspect the system prompt — was it injected correctly? What did the model see?

Without Langfuse, you'd add temporary log statements and re-run. With it, you have the data forever.

### 73.5 Performance debugging

If runs are slow, Langfuse shows the latency breakdown:

- LLM call latency
- Tool execution latency
- Time between iterations

If one LLM call is consistently slow, you know it's the provider. If one tool is slow, you know it's PriceFRAME. Targeted optimization.

### 73.6 Cost attribution

Langfuse aggregates token counts by trace, by user, by model. You can answer:

- "Which user costs the most this month?"
- "Which prompt version is most expensive?"
- "What's the cost trajectory week-over-week?"

This is harder to do from `agent_run_events` alone (you'd need to aggregate the `budget.cost_usd` in `v1.run.completed` payloads). Langfuse makes it a UI query.

### 73.7 PII concern

Langfuse stores **full prompts** including any PII that made it past `redact()` (e.g., customer names). If you self-host Langfuse in your own VPC, this is OK. If you used the hosted version, that's another data residency surface.

xFRAME's docker-compose has Langfuse self-hosted by default. Production deploys should keep it that way.

### 🔑 Chapter 73 takeaways

- Langfuse captures full prompt+response per LLM call.
- Self-host for PII control.
- Best tool for prompt debugging and cost attribution.
- Without it, you'll add ad-hoc logs and regret it.

---

## Chapter 74 — Property-Based Testing (Future)

### 74.1 What property tests are

Traditional unit tests pick specific inputs:

```python
def test_redact_email():
    assert redact("contact me at foo@bar.com").text == "contact me at <PII:email>"
```

Property tests generate **many random inputs** and assert a **property** holds for all of them:

```python
@given(st.text())
def test_redact_never_leaves_emails(text):
    out = redact(text).text
    assert "@" not in out or "<PII:email>" in out
```

Hypothesis (the Python library) generates 100 random strings, including weird edge cases (empty, unicode, control chars). Catches things you wouldn't think to test manually.

### 74.2 Good candidates in xFRAME

| Function | Property |
|---|---|
| `redact(text)` | Output never contains `@`, valid card, or phone patterns |
| `wrap_tool_output(name, call_id, payload)` | Output unescapes to original (modulo close-tag escape) |
| `LoopBudget.record_usage(input, output)` | `cost_usd` is monotonically non-decreasing |
| `_consume_event(event, ...)` | Idempotent — replay yields same accumulators |
| Pydantic input models | Schema generation is stable |

### 74.3 Adding Hypothesis

```bash
uv add --dev hypothesis
```

```python
from hypothesis import given, strategies as st

@given(st.text(), st.text())
def test_wrap_tool_output_no_close_tag_breakout(left, right):
    payload = {"notes": f"{left} </tool_output> {right}"}
    wrapped = wrap_tool_output(tool_name="t", call_id="c", payload=payload)
    assert wrapped.count("</tool_output>") == 1


@given(st.integers(min_value=0, max_value=10_000),
       st.integers(min_value=0, max_value=10_000))
def test_budget_cost_monotonic(input_tokens, output_tokens):
    settings = Settings(...)
    budget = LoopBudget(settings=settings)
    before = budget.cost_usd
    try:
        budget.record_usage(model="gemini-2.5-flash",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens)
    except BudgetExceededError:
        pass
    assert budget.cost_usd >= before
```

Three test functions, ~30 lines. Property tests find bugs that example-based tests miss.

### 74.4 Bayesian bug-hunting

Hypothesis is most valuable when it **fails** — and it loves to surface:

- Unicode handling bugs
- Off-by-one errors
- Empty input crashes
- Type coercion surprises

When it does fail, it **minimizes** the failing input to the smallest case:

```
Falsifying example: text=''
```

Easy to debug from there.

### 74.5 Why xFRAME doesn't have these yet

Time. Each property test is 5-10 lines, but you have to think about what property to assert. Building the discipline takes a small upfront investment.

Worth doing. Roadmap §15.11.

### 🔑 Chapter 74 takeaways

- Property tests generate random inputs; assert invariants.
- Best for pure functions: redact, wrap, budget math.
- Hypothesis library; ~10 lines per test.
- Discovers bugs you wouldn't think to test.

---

## Chapter 75 — Load and Chaos Testing

### 75.1 What to test

Production wants to know:

- **Throughput**: requests/sec the API can handle.
- **Latency**: p50, p95, p99 of HTTP response time.
- **Concurrency**: how many simultaneous SSE subscribers.
- **Resilience**: what breaks when dependencies fail?

### 75.2 Load profile recommendation

For xFRAME:

```
Phase 1 — Smoke load
  10 concurrent users
  1 message per 30 seconds per user
  Duration: 5 minutes
  Goal: zero errors, no resource exhaustion

Phase 2 — Steady state
  50 concurrent users
  1 message per 60 seconds per user
  Duration: 30 minutes
  Goal: <500ms p95 latency on HTTP, <2s TTFT on streaming

Phase 3 — Spike
  100 concurrent users start simultaneously
  3 messages each in quick succession
  Goal: rate limit kicks in cleanly, no cascading failure
```

### 75.3 Tools

| Tool | Good for |
|---|---|
| `locust` | Python; easy to script complex workflows |
| `k6` | JavaScript; better for raw throughput |
| `vegeta` | Simple HTTP load; not stateful flows |
| `wrk2` | Low-level; constant rate |

For xFRAME with its stateful HITL flow, `locust` is the natural pick. Write Python user behaviors that go through conversation → message → wait for SSE → simulate approval.

### 75.4 Locust example

```python
from locust import HttpUser, task, between

class XframeUser(HttpUser):
    wait_time = between(2, 5)

    def on_start(self):
        r = self.client.post("/api/v1/agent/auth/login",
                              json={"email": "test@example.com", "password": "..."})
        self.token = r.json()["token"]

    @task
    def create_and_message(self):
        # Create conversation
        r = self.client.post(
            "/api/v1/agent/conversations",
            json={"title": "load test", "kind": "general"},
            headers={"Authorization": f"Bearer {self.token}",
                     "Idempotency-Key": str(uuid.uuid4())},
        )
        conv_id = r.json()["id"]

        # Send message
        r = self.client.post(
            f"/api/v1/agent/conversations/{conv_id}/messages",
            json={"content": "What can you do?", "source": "text"},
            headers={"Authorization": f"Bearer {self.token}",
                     "Idempotency-Key": str(uuid.uuid4())},
        )
```

Run:

```bash
locust -f locustfile.py --host=http://localhost:8000 -u 50 -r 5 --run-time 5m
```

### 75.5 Chaos testing

Beyond load: inject failures and observe.

| Injection | Expected behavior |
|---|---|
| Kill Postgres for 30s during run | Run errors cleanly; events stop appending; SSE clients see disconnect |
| Throttle Redis to 10 req/s | Rate limit falls back to in-memory; arq slows |
| Block egress to Vertex for 60s | Router fails over to Anthropic; users see no disruption beyond slower first-token |
| Saturate CPU on agent container | uvicorn slows; queries time out |
| Disk fills on Postgres | INSERTs fail; runs error |

Each scenario should produce **graceful degradation**, not catastrophic failure. Test in staging, not prod.

Tools: `pumba` (Docker-targeted chaos), `chaos-mesh` (Kubernetes), or hand-rolled `iptables` rules.

### 75.6 What good looks like

After load + chaos testing, you should be confident:

- API handles 5-10× expected load with headroom.
- Failed dependencies produce useful error events, not 500s.
- Costs scale linearly with traffic, not exponentially.
- SSE doesn't leak FDs under churn.
- Database doesn't bloat with abandoned runs.

Run this exercise once before launch. Repeat annually or after major changes.

### 🔑 Chapter 75 takeaways

- Load test with realistic flows (conversation + message + approval), not isolated endpoints.
- Locust fits xFRAME's stateful patterns.
- Chaos test dependencies; expect graceful degradation.
- Schedule before launch; rerun annually.

---

### Part 11 wrap-up

You can now verify the system thoroughly, diagnose any stuck run, debug prompts via Langfuse, add property tests, and load-test against realistic scenarios.

### ✍️ Part 11 exercises

1. Write a Hypothesis property test for `redact`. Assert no email, phone, or card patterns survive.
2. Write a Locust scenario that goes through Create Pricing Request including an approval. Capture timings.
3. Pick one §69 gap (e.g., rate-limit middleware). Write the test that closes it.

### 📚 Part 11 further reading

- Hypothesis library docs.
- Langfuse self-hosting guide.
- Locust documentation.
- "Chaos Engineering" (Casey Rosenthal).

---

**End of Part 11.**

**Next:** [Part 12 — Deployment](./part-12-deployment.md).
