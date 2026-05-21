# Part 16 — Glossary

> The complete vocabulary of this book, alphabetical. Each entry: plain definition + xFRAME context where applicable.

---

**`AgentConversation`** — ORM row for a long-running chat thread. Has `kind` (V1.4) that determines which system prompt is injected. Cascades to messages, runs.

**`AgentLoop`** — Deterministic regex-driven runner (`agent/loop.py`). Pauses every tool call to the decisions endpoint. Used when no LLM provider is configured.

**`AgentMessage`** — ORM row for one chat message. Roles: `user`, `assistant`, `system`, `tool`. Linked to its originating `AgentRun` via `run_id`.

**`AgentRun`** — ORM row for one unit of agent execution. States: `queued`, `running`, `awaiting_decision`, `completed`, `error`, `cancelled`.

**`AgentRunEvent`** — Append-only journal row in `agent_run_events`. `(run_id, seq)` unique. Source of truth for SSE replay.

**`AgentRunStep`** — One iteration within a run, kind `model_call` or `tool_call`.

**`AgentToolCall`** — ORM row for one tool invocation. Tracks `status` (proposed/pending/succeeded/failed/rejected), `requires_approval`, args, result, `priceframe_audit_log_id`.

**Alembic** — SQLAlchemy's migration tool. xFRAME has 2 forward-only migrations.

**Anthropic** — LLM provider (Claude). Fallback after Gemini Vertex in xFRAME's failover order.

**API prefix** — Path under which agent endpoints live; default `/api/v1/agent` (env `API_PREFIX`).

**Approval gate** — Pause when a tool's `requires_approval()` returns True. Sets run to `awaiting_decision`, exits runner, awaits `POST /runs/{id}/decisions`.

**`arq`** — Python async job queue backed by Redis. Used for `RUN_EXECUTION_MODE=arq` background runs.

**Async/await** — Python's cooperative concurrency model. xFRAME is fully async to handle many concurrent SSE subscribers without threads.

**Audit callback** — POST from agent to PriceFRAME's `/api/v1/agent-audit-callbacks` after every executed write. HMAC-signed.

**`AuthContext`** — Frozen dataclass: `user_id, role_code, profile_code, permissions, jwt_raw, session_id`. The "who is this request" object.

**Autonomous agent** — One that decides next steps itself, not following a hardcoded workflow. xFRAME is autonomous within HITL guardrails.

**Bearer token** — JWT carried in `Authorization: Bearer <token>` header (or `?token=...` for SSE).

**Budget** — `LoopBudget`. Per-run limits on steps, tool calls, tokens, cost, wall-clock.

**`call_id`** — Unique identifier the LLM provides for each `tool_use` block. Tool result echoes it for matching.

**`cause`** — Short error code in `v1.run.error` payloads (e.g., `cost_budget_exceeded`, `loop_detected`).

**Chain-of-thought (CoT)** — Prompting technique: ask model to "think step by step" before answering. Implicit version: provide a numbered task list (xFRAME's 9-step prompt).

**`ChatMessage`** — Pydantic model in `provider/base.py`. Provider-agnostic message format: `role` + `content: list[ContentBlock]`.

**Chunking** — Splitting documents for embedding. 400-800 tokens with overlap is the prose sweet spot.

**ClamAV** — Open-source antivirus. xFRAME scans attachments via its INSTREAM TCP protocol.

**Closed-weight model** — LLM you access via API only (GPT, Claude, Gemini). Contrast with open-weight.

**Composite tool** — Tool that wraps multiple operations as one call. Saves round-trips, loses flexibility.

**Context caching** — Vendor feature: cache repeated prompt prefixes for cheaper subsequent calls. Anthropic `cache_control`, Vertex context cache. Roadmap §15.3.

**Context window** — Maximum input tokens a model accepts. Gemini 2.5 Flash: 1M. Claude Sonnet: 200K.

**`ContentBlock`** — Pydantic model. `type` is `"text"` or `"tool_result"`; `payload` is the data.

**Cosine similarity** — Numeric similarity between two embedding vectors. 1 = identical direction, 0 = perpendicular, -1 = opposite.

**Cost soft/hard ceiling** — `COST_SOFT_PER_RUN_USD` triggers warning (unused); `COST_HARD_PER_RUN_USD` raises `BudgetExceededError`.

**Cursor pagination** — V1.7 addition. `GET /conversations?limit=20&cursor=<id>`. Returns `next_cursor` + `has_more`.

**Decisions endpoint** — `POST /api/v1/agent/runs/{id}/decisions`. Where user approves/rejects/edits a proposed tool call.

**Deterministic agent** — One that responds the same way to the same input. xFRAME's `AgentLoop` is deterministic; `ModelRunner` is statistical (LLM-driven).

**Embedding** — Numeric vector representing the semantic content of text. Used for similarity search.

**Event sourcing** — Persistence pattern: store state transitions (events), not just current state. xFRAME's `agent_run_events` is event-sourced.

**`EventSource`** — Browser API for consuming SSE. Can't set headers — xFRAME accepts `?token=...` query param.

**Failover router** — `ProviderFailoverRouter`. Tries providers in order; quarantines failures for 5 min.

**Few-shot prompting** — Including 2-5 example input/output pairs in the prompt. xFRAME has one (happy path).

**Fine-tuning** — Updating the LLM's weights with your data. Expensive; alternatives (prompting, RAG) usually preferred.

**Function calling** — See **Tool calling**.

**Gemini Vertex** — Google's hosted Gemini API via Vertex AI on GCP. xFRAME's primary LLM provider.

**Gemini AI Studio** — Google's developer-tier Gemini API. xFRAME's `gemini_aistudio.py` is gated behind `ALLOW_REAL_DATA=true`.

**GDPR** — General Data Protection Regulation. Drives PII redaction, right-to-be-forgotten, retention policies.

**HITL** — Human-in-the-loop. Pause for explicit human approval before risky tool calls.

**HMAC** — Hash-based Message Authentication Code. xFRAME signs audit callbacks with HMAC-SHA256.

**HNSW** — Hierarchical Navigable Small World. The default vector index for pgvector. O(log n) similarity search.

**`httpx`** — Async HTTP client used by `PriceFrameClient`.

**Hybrid search** — Combines lexical (BM25) + semantic (embedding) ranking. Better recall than either alone.

**Idempotency-Key** — HTTP header. Agent stores responses keyed by `(user_id, key)` for 7 days. Also passed to PriceFRAME on write tools.

**Indirect prompt injection** — Attack via data the model reads (tool results), not direct user input. The dangerous variant.

**Inline run mode** — `RUN_EXECUTION_MODE=inline`. Runs execute in the HTTP request handler. Contrast with `arq` queued runs.

**JSON Schema** — Standard for describing JSON document shape. Tools' `input_model` Pydantic class generates JSON Schema automatically via `model_json_schema()`.

**JWT** — JSON Web Token. PriceFRAME-issued. HS256 + `PRICEFRAME_JWT_SECRET`. xFRAME verifies locally.

**Langfuse** — Open-source LLM observability platform. xFRAME supports it via `LANGFUSE_*` env.

**LLM** — Large Language Model. Next-token predictor trained on huge text corpora.

**`LoopBudget`** — Dataclass in `agent/budget.py`. Tracks per-run consumption. Raises `BudgetExceededError` past ceilings.

**`LoopDetectedError`** — Raised when same `(tool_name, sorted_args)` proposed 3x in a row.

**Maker-checker** — Financial-services pattern: one role proposes, another approves. xFRAME's HITL is a variant.

**MCP (Model Context Protocol)** — Open standard for connecting LLMs to tools and data. xFRAME doesn't expose itself as an MCP server (yet).

**Memory tiers** — Working / conversation / episodic / semantic / procedural. See Chapter 9.

**`ModelRunner`** — The LLM-driven runner (`agent/runner.py`). Streaming, parallel reads, serial writes, HITL pause, system prompt injection, loop detection.

**Multi-agent** — Multiple specialized agents coordinated by an orchestrator. xFRAME is intentionally single-agent.

**Open-weight model** — LLM whose weights are public (Llama, Mistral, Qwen). Contrast with closed-weight.

**OpenAPI** — Machine-readable API spec. `openapi.yaml` is regenerated from FastAPI; CI checks for drift.

**Pagination** — Cursor-based on `GET /conversations` (V1.7 addition).

**`pgvector`** — Postgres extension for vector similarity search. xFRAME's natural RAG choice (when added).

**PII** — Personally Identifiable Information. xFRAME redacts emails, phones, cards, MFA codes pattern-by-pattern.

**Presigned URL** — Time-limited URL clients use to download from S3 directly. xFRAME generates these for attachments.

**`PriceFrameClient`** — The `httpx`-based HTTP client. Retries, error mapping, HMAC callback signing.

**`PriceFrameError`** — Exception hierarchy: `PriceFrameAuthError` (401), `PriceFrameForbiddenError` (403), `PriceFrameNotFoundError` (404), `PriceFrameResponseError` (5xx, others), `PriceFrameTimeoutError`.

**`project_for_model`** — Class method on `ToolDefinition`. Filters tool result fields to `model_visible_fields` before LLM sees them.

**Prompt injection** — Attack where data the model reads contains instructions intended to override the system prompt. Defended via `wrap_tool_output` + system-prompt rules + HITL.

**Property-based testing** — Test pattern using Hypothesis. Generates random inputs; asserts properties hold. Roadmap §15.11.

**`ProposedCall`** — Dataclass in `runner.py`: `(name, args, call_id)` extracted from a `tool_use` event.

**Provider** — Protocol in `provider/base.py`. Any LLM API implementing `async def stream(messages, tools, *, model, max_output_tokens) -> AsyncIterator[StreamEvent]`.

**`ProviderError`** — Exception. `failover=True` (default) tells the router to try the next provider; `failover=False` aborts.

**Pydantic** — Library for declarative data validation. All schemas, models, settings use Pydantic v2.

**RAG** — Retrieval-Augmented Generation. Retrieve relevant text → put in prompt → generate answer. xFRAME doesn't use RAG today; Chapter 7 explains the concept and §15.10 is the roadmap.

**ReAct** — Reasoning + Acting prompting pattern. Interleaved Thought/Action/Observation. xFRAME implements ReAct minimally via tool calling.

**`redact()`** — Function in `agent/redaction.py`. Substitutes PII patterns with placeholders like `<PII:email>`.

**Reflection** — Agent technique where the model critiques its own output. Chapter 91.

**`RequestIdMiddleware`** — Adds `X-Request-ID` to every request; binds to structlog context.

**Retrieval-augmented generation** — See **RAG**.

**Risk classification** — `READ`, `LOW_RISK_WRITE`, `HIGH_RISK_WRITE` on each `ToolDefinition`. Drives approval + audit behavior.

**Role** — In chat messages: `system`, `user`, `assistant`, `tool`. In auth: PriceFRAME role codes like `ROLE_AM_SALES`.

**Run kind** — `AgentConversation.kind` field. Selects system prompt. V1.4 has `create_pricing_request` and `general`.

**`Settings`** — Pydantic-settings model. All configuration from env vars.

**SLO** — Service Level Objective. Target metric ("99.9% availability"). Tracked via SLIs (actual measurements).

**SLI** — Service Level Indicator. The actual measurement (from Prometheus, logs, event log).

**SOC2** — Security audit standard. xFRAME's architecture is friendly to SOC2 once organizational processes exist.

**SSE** — Server-Sent Events. Streaming protocol for `GET /runs/{id}/stream`. One-way, text-based, supports `Last-Event-ID` for replay.

**Step** — One iteration of the runner loop. Either `model_call` (LLM round) or `tool_call` (tool round).

**STRIDE** — Threat modeling framework (Spoofing, Tampering, Repudiation, Info disclosure, DoS, Elevation of privilege).

**`StreamEvent`** — Pydantic model emitted by providers. Kinds: `text_delta`, `tool_use`, `usage`.

**System prompt** — Persistent instructions sent to LLM with `role="system"`. Injected by `ModelRunner` for `create_pricing_request` kind.

**Tool** — Function the agent can invoke. Defined by a `ToolDefinition` subclass. 12 in xFRAME v1.

**Tool calling** — The primitive that lets LLMs invoke functions. xFRAME's central mechanism.

**Tool catalog / `tool_registry`** — Singleton in `tools/registry.py`. `available_for(ctx)` filters by `tool.permission in ctx.permissions`.

**`ToolDefinition`** — Generic base class. Subclasses declare `name`, `description`, `permission`, `risk`, `input_model`, `output_model`, etc.

**Tool output wrapping** — `wrap_tool_output()`. Wraps tool results in `<tool_output>` with `[Untrusted: ...]` marker.

**`tool_use`** — Structured block the LLM emits to call a function. Carries `name`, `args`, `call_id`.

**Transformer** — The neural network architecture underlying modern LLMs (2017+).

**ULID** — Universally Unique Lexicographically Sortable Identifier. 26 chars. xFRAME uses for IDs that are exposed to clients.

**`uv`** — Modern Python package manager. Replaces pip + virtualenv + pip-tools.

**Vector database** — Storage for embeddings, with similarity-search indexes. pgvector, Qdrant, Pinecone.

**Vertex AI** — Google's managed AI platform. Hosts Gemini.

**Worker** — arq process that pulls jobs from Redis and executes them.

**Wrapping** — See **`wrap_tool_output`**.

**Zero-shot** — Prompting with no examples. xFRAME's task description is mostly zero-shot (with one happy-path example).

---

**End of Part 16.**

**Next:** [Part 17 — Appendices](./part-17-appendices.md).
