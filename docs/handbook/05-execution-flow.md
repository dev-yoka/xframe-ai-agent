# 05 — Agent Execution Flow

> **Reading this section answers:** what happens, in order, when a user sends a message? Where is every line of state written? Where can things branch?

## 5.1 The 11 steps after a user message

```mermaid
flowchart TD
  A[1. HTTP request arrives] --> B[2. Middleware chain]
  B --> C[3. Auth dependency<br/>JWT verify + profile fetch]
  C --> D[4. Idempotency replay check]
  D --> E[5. Persist AgentMessage + AgentRun]
  E --> F[6. Build messages: system + history + new]
  F --> G[7. Stream from provider]
  G --> H{8. tool_use emitted?}
  H -->|No, just text| K[9. Persist assistant message]
  H -->|Yes| I[8a. Validate args against tool.input_model]
  I --> J{8b. requires_approval?}
  J -->|Yes| L[Pause: status=awaiting_decision]
  J -->|No| M[8c. Execute tool, append result]
  M --> G
  K --> N[10. Emit v1.run.completed]
  N --> O[11. Return response]
  L --> P[Return paused; resume via /decisions]
```

This section traces each numbered step in detail.

---

## 5.2 Step 1: HTTP request arrives

```http
POST /api/v1/agent/conversations/{id}/runs HTTP/1.1
Host: agent.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiI...
Content-Type: application/json
Idempotency-Key: c8f4a912-...

{ "content": "Create a pricing request for Acme Corp", "source": "text" }
```

Routed by nginx (proxy_buffering off, read_timeout 3600s) to the uvicorn worker.

## 5.3 Step 2: Middleware chain

Order matters — see `main.py:51-60`:

1. **`RequestIdMiddleware`**: extract or generate `X-Request-ID`. Bind to `structlog.contextvars` so every log line in this request includes it.
2. **`RateLimitMiddleware`**: increment token bucket for `(client_ip, request.url.path)`. Redis `EVAL` Lua script — atomic. If exceeded, return 429 immediately with `Retry-After`.
3. **`CORSMiddleware`**: validate `Origin`, handle preflight if `OPTIONS`. Set CORS response headers.

After middleware, FastAPI invokes the route handler.

## 5.4 Step 3: Auth dependency

`get_auth_context` (`auth/dependencies.py:18-38`):

```python
1. token = request.headers["Authorization"].removeprefix("Bearer ").strip()
   # ...or from ?token= for SSE
2. claims = verify_priceframe_jwt(token, settings)
   # local HS256 verification using PRICEFRAME_JWT_SECRET; no network call
3. ctx = await get_auth_context_from_profile(
       jwt_raw=token,
       client=PriceFrameClient.from_settings(settings),
       claims=claims,
       ttl=settings.priceframe_profile_cache_ttl_seconds,
   )
4. return ctx
```

Step 3 calls `PriceFrameClient.get_profile(jwt_raw)` which hits `GET /api/auth/profile` on PriceFRAME. The result is **cached for 60 seconds** keyed by `(token_hash, session_id)`. So 100 requests from one user within 60s only produce one PriceFRAME profile fetch.

`ctx` is `AuthContext(user_id, role_code, profile_code, permissions, jwt_raw, session_id)`.

## 5.5 Step 4: Idempotency replay

In `POST /runs` handler (`conversations.py:181-217`):

```python
replay = await get_replay(session, user_id=auth.user_id, key=idempotency_key)
if replay:
    response.headers["Idempotency-Replayed"] = "true"
    return RunCreateResponse.model_validate(replay.response_payload)
```

If the same user previously POSTed with the same `Idempotency-Key`, return the stored response with status 200 (not 202). Skip the rest of the handler.

If not, proceed and `store_replay()` at the end of the handler with TTL 7 days.

## 5.6 Step 5: Persist message + run

```python
async def create_run_record(...):
    # 1. find conversation, 404 if not user's
    conversation = await require_conversation(session, auth, conversation_id)
    # 2. insert AgentMessage role=user
    message = AgentMessage(conversation_id=..., role="user", content=payload.content, ...)
    session.add(message); await session.flush()
    # 3. insert AgentRun status=queued
    run = AgentRun(conversation_id=..., status="queued", input_message_id=message.id)
    session.add(run); await session.flush()
    # 4. link back
    message.run_id = run.id
    conversation.updated_at = utc_now()
    return run
```

After this:

- `agent_messages` has +1 row (the user's message).
- `agent_runs` has +1 row in `status=queued`.
- No events yet.

## 5.7 Step 6: Build the message list

The runner constructs the list it will send to the LLM:

```python
messages: list[ChatMessage] = []

# 6a. system prompt (V1.4) — only for create_pricing_request or empty history
if conv.kind == "create_pricing_request" or not history:
    messages.append(ChatMessage(
        role="system",
        content=[ContentBlock(type="text", payload={"text": get_system_prompt(
            role_code=ctx.role_code,
            profile_code=ctx.profile_code,
            permissions=ctx.permissions,
        )})],
    ))

# 6b. prior conversation history (assistant + tool results from previous runs)
messages.extend(history)

# 6c. the new user message
# (already in history if conversations.py loaded it, else appended here)
```

**What does the model see?**

```
[system]   You are xFRAME AI Agent...
           User: role=ROLE_AM_SALES, profile=PROFILE_SALES, permissions=[agent.quotes.read, ...]
           9 steps for Create Pricing Request...
           Never auto-execute writes. Wait for approval...

[user]     "Create a pricing request for Acme Corp"
```

Plus the **tool catalog** is sent alongside (the `tools` parameter to `provider.stream()`), filtered by `tool_registry.available_for(ctx)` — so only tools the user has permission for are visible.

## 5.8 Step 7: Stream from provider

`ModelRunner._call_provider()` (`runner.py:249-273`):

```python
async for event in self._router.stream(
    messages, tools,
    model=self._model,
    max_output_tokens=self._settings.max_output_tokens_per_run,
):
    assistant_text, usage = _consume_event(event, proposals, assistant_text, usage)
```

`ProviderFailoverRouter.stream()`:

1. Try first provider (e.g., `GeminiVertexProvider`). If healthy and not in cooldown:
2. Call `provider.stream(messages, tools, model=..., max_output_tokens=...)`. This is an async generator.
3. Re-yield each `StreamEvent` to the runner.
4. If a `ProviderError(failover=True)` or 30s timeout: mark unhealthy for 300s, try next provider.
5. If all providers fail: re-raise last error → `ModelRunner` catches and finalizes run with `cause=provider_error`.

`StreamEvent` kinds:

| Kind | Payload |
|---|---|
| `text_delta` | `{"delta": "Hello"}` — token chunk |
| `tool_use` | `{"name": "get_quotation", "args": {"id": 42}, "call_id": "c1"}` |
| `usage` | `{"input_tokens": 1234, "output_tokens": 56}` — final |

The runner accumulates:

- `assistant_text` from all `text_delta`s
- `proposals: list[ProposedCall]` from `tool_use`s
- `usage` from the final `usage` event

After the stream closes, `budget.record_usage(model, input_tokens, output_tokens)` — may raise `BudgetExceededError`.

## 5.9 Step 8: Branch on tool_use

### Step 8a: schema validation

For each proposal:

```python
tool = tool_registry.get(proposal.name)
if tool is None:
    append_run_event(... event_type="v1.tool.error", payload={"cause": "unknown_tool", ...})
    continue   # skip this proposal

try:
    parsed = tool.input_model.model_validate(proposal.args)
except ValueError as exc:
    append_run_event(... event_type="v1.tool.error",
        payload={"cause": "schema_validation_failed", "detail": str(exc)})
    continue
```

The model can hallucinate field names or types — Pydantic validation is the wall.

### Step 8b: requires_approval check

```python
budget.record_tool_call()
requires_approval = await tool.requires_approval(parsed, context)
```

`requires_approval`:

| Tool | Returns |
|---|---|
| `list_my_quotations`, `get_quotation`, etc. (READ) | `False` |
| `recalculate_quote_aggregates` | `False` (explicit override) |
| `preview_pricing_change` | `False` (LOW_RISK_WRITE but no side effect persisted) |
| `create_quotation`, `bulk_add_corridors`, `update_corridor_pricing`, `set_fx_spread` | `True` |
| `submit_for_approval` | `True` |

**If True:**

```python
# Create the AgentToolCall in proposed state
tool_call = AgentToolCall(
    run_id=run.id,
    tool_name=tool.name,
    status="proposed",
    args=parsed.model_dump(mode="json"),
    requires_approval=True,
    step_id=step.id,
)
session.add(tool_call); await session.flush()

# Emit events
append_run_event(... "v1.tool.proposed", {"tool_call_id": ..., "args": ..., "requires_approval": True})
append_run_event(... "v1.run.awaiting_decision", {"tool_call_id": ...})

run.status = "awaiting_decision"
run.updated_at = utc_now()
await session.commit()
return run  # ← runner exits
```

**If False:** classify the proposal:

- `tool.risk == "READ"` → push to `readers` list.
- else → push to `writers` list.

After all proposals classified, execute:

```python
sem = asyncio.Semaphore(settings.max_parallel_tool_calls)
async def _exec_read(...): async with sem: return await self._execute_one(...)
done = await asyncio.gather(*(_exec_read(...) for ... in readers))

for w in writers:
    result = await self._execute_one(w)
```

### Step 8c: tool execution

`_execute_one` (`runner.py:399-429`):

```python
append_run_event(... "v1.tool.started", {"tool_call_id": ..., "tool_name": ...})

# Calls tool.execute → checks ctx.has_permission → tool._execute(args, ctx, priceframe)
result_model = await tool.execute(parsed, ctx, self._priceframe)
dumped = result_model.model_dump(mode="json")
projected = tool.project_for_model(dumped)  # ← strip non-visible fields

record.status = "succeeded"
record.result = dumped
record.completed_at = utc_now()

append_run_event(... "v1.tool.completed", {"tool_call_id": ..., "result": projected})
return ToolExecutionResult(proposal, projected, tool.risk)
```

Inside `tool._execute`, the PriceFRAME HTTP call happens:

```python
async def _execute(self, args, ctx, priceframe):
    payload = {"customerId": args.customer_id, "title": args.title, ...}
    response = await priceframe.post_json(
        "/api/quotes",
        jwt_raw=ctx.jwt_raw,
        json=payload,
        headers={"Idempotency-Key": tool_call_id},  # for write tools
    )
    return JsonOutput(data=response)
```

After execution, the results are wrapped and appended to messages for the next loop iteration:

```python
for result in tool_results:
    messages.append(ChatMessage(
        role="tool",
        content=[ContentBlock(type="tool_result", payload={
            "tool_call_id": result.proposal.call_id,
            "wrapped": wrap_tool_output(
                tool_name=result.proposal.name,
                call_id=result.proposal.call_id,
                payload=result.output,
            ),
        })],
    ))
```

Then back to **Step 7** — stream again with the augmented messages.

## 5.10 Step 9: persist assistant message

When the model finishes with just text (no `tool_use`):

```python
if assistant_text:
    redacted = redact(assistant_text)   # PII pass on output too
    msg = AgentMessage(
        conversation_id=run.conversation_id,
        user_id=ctx.user_id,
        role="assistant",
        content=redacted.text,
        source="agent",
        run_id=run.id,
    )
    session.add(msg); await session.flush()
    append_run_event(... "v1.message.delta", {"message_id": msg.id, "delta": redacted.text})
    run.output_message_id = msg.id
```

## 5.11 Step 10: terminate the run

```python
if not proposals:   # model returned text only
    run.status = "completed"
    run.completed_at = utc_now()
    append_run_event(... "v1.run.completed", {"budget": budget.snapshot()})
    await session.commit()
    return run
```

The `budget.snapshot()` shape:

```json
{
  "steps": 3,
  "tool_calls": 2,
  "input_tokens": 4321,
  "output_tokens": 287,
  "cost_usd": 0.001143,
  "elapsed_s": 7.245
}
```

## 5.12 Step 11: HTTP response

The handler returns `RunCreateResponse(run_id=..., status="completed")`. FastAPI serializes to JSON. CORS middleware adds headers. Returns 202 (or 200 on idempotent replay).

---

## 5.13 The SSE replay endpoint

Parallel to all of the above, the mobile client typically opens an SSE connection **before** or **just after** the POST:

```http
GET /api/v1/agent/runs/{run_id}/stream
Authorization: Bearer ...
Last-Event-ID: 0
Accept: text/event-stream
```

(or `?token=...&last_event_id=0` because `EventSource` can't set headers.)

The handler (`api/v1/runs.py:201-250`):

```python
async def stream_run(...):
    async def generator():
        cursor = last_event_id
        emitted_terminal = False
        while True:
            events = await list_run_events(session, run_id=..., after_seq=cursor, limit=2000)
            for e in events:
                cursor = e.seq
                yield f"id: {e.seq}\nevent: {e.event_type}\ndata: {json.dumps(payload(e))}\n\n"
                if e.event_type in TERMINAL_EVENTS:
                    emitted_terminal = True
            if emitted_terminal or run.status in TERMINAL_STATUSES:
                break
            yield f"event: v1.heartbeat\ndata: ...\n\n"
            await asyncio.sleep(settings.sse_heartbeat_seconds)
    return EventSourceResponse(generator())
```

**Key properties:**

- The stream **reads from Postgres**, not memory — so any number of clients can subscribe, including reconnects.
- `id: <seq>` is the SSE event ID. Clients store it; on reconnect they send `Last-Event-ID: <seq>` and skip what they already saw.
- Heartbeats every 15s keep the connection alive through any intermediary timeouts.
- Terminal events: `v1.run.awaiting_decision`, `v1.run.completed`, `v1.run.error` end the stream.

## 5.14 Decisions endpoint (resume after pause)

After `v1.run.awaiting_decision`, the run is paused. The client renders the proposed tool call and presents Approve/Reject/Edit buttons. On Approve:

```http
POST /api/v1/agent/runs/{run_id}/decisions
{ "tool_call_id": "...", "decision": "approve" }
```

Handler (`api/v1/runs.py:58-198`):

1. Fetch `AgentToolCall` by ID, verify it's `proposed`.
2. Build the tool, validate args, call `tool.execute(args, ctx, priceframe)` (using the *current* user's JWT — important: this is fresh auth).
3. On success: `tool_call.status = "succeeded"`, append `v1.tool.completed`, post the HMAC-signed audit callback to PriceFRAME's `/api/v1/agent-audit-callbacks`.
4. Insert a row into local `agent_audit_log`.
5. Resume the run: re-enter `ModelRunner.run()` (or `AgentLoop.run()` in current HTTP wiring) with the tool result appended to history.
6. Loop continues until terminal.

For Reject: `tool_call.status = "rejected"`, append `v1.tool.rejected`, advance the run with the rejection in context (the model can react).

For Edit: validate the user-supplied edited args, persist, then execute (effectively approve-with-modified-args).

---

## 5.15 Worked example: token-level trace

A simplified run for "What's the status of quote 42?":

```
[Step 1: model_call, seq=1] v1.step.started {step: 1, kind: "model_call"}

Provider stream:
  text_delta: "Let me "
  text_delta: "look that up..."
  tool_use: {name: "get_quotation", args: {id: 42}, call_id: "c1"}
  usage: {input_tokens: 1500, output_tokens: 18}

  → ProposedCall(name="get_quotation", args={"id": 42}, call_id="c1")
  → AgentMessage(role=assistant, content="Let me look that up...") created
  → v1.message.delta {message_id: m1, delta: "Let me look that up..."}

v1.step.completed {step: 1, kind: "model_call", usage: {...}}

[Step 2: tool_call, seq=2] tool dispatch
  GetQuotationTool.requires_approval() → False
  v1.tool.proposed {tool_call_id: tc1, tool_name: "get_quotation", args: {id: 42}, requires_approval: false}
  v1.tool.started {tool_call_id: tc1, tool_name: "get_quotation"}
  → HTTP: GET https://priceframe-yg.buy-frame.com/api/v1/quotes/42/pricing-context
  ← 200 OK { data: { title: "Acme Pricing", status: "draft", total: 12000 } }
  → project_for_model picks only "data" key
  v1.tool.completed {tool_call_id: tc1, result: {data: {title: ..., status: "draft", total: 12000}}}

messages.append(ChatMessage(role=tool, content=[ContentBlock(payload={
  tool_call_id: "c1",
  wrapped: "<tool_output name='get_quotation' call_id='c1'>[Untrusted...] {...}</tool_output>"
})]))

[Step 3: model_call, seq=3] v1.step.started

Provider stream (now with tool_result in history):
  text_delta: "Quote 42 "
  text_delta: "is a draft for "
  text_delta: "$12,000."
  usage: {input_tokens: 1812, output_tokens: 11}

  → AgentMessage(role=assistant, content="Quote 42 is a draft for $12,000.")
  → v1.message.delta

v1.step.completed
v1.run.completed {budget: {steps:3, tool_calls:1, input_tokens:3312, output_tokens:29, cost_usd: 0.000142}}
```

In the database after the run:

- `agent_messages` — 3 rows (user, assistant "Let me look...", assistant "Quote 42 is...")
- `agent_runs` — 1 row, `status=completed`
- `agent_run_steps` — 3 rows
- `agent_run_events` — 8 rows
- `agent_tool_calls` — 1 row, `status=succeeded`, args+result populated

---

**Next:** [§06 PriceFRAME integration](./06-priceframe-integration.md) for the deep API + HMAC + retry contract.
