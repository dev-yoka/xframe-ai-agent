# Graph Report - /Users/bhairava/WorkSpace/repos/xframe-ai-agent  (2026-05-31)

## Corpus Check
- 113 files · ~190,804 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1267 nodes · 4107 edges · 30 communities detected
- Extraction: 44% EXTRACTED · 56% INFERRED · 0% AMBIGUOUS · INFERRED: 2292 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]

## God Nodes (most connected - your core abstractions)
1. `Settings` - 256 edges
2. `AuthContext` - 232 edges
3. `RunBudget` - 129 edges
4. `ToolDefinition` - 118 edges
5. `Base` - 87 edges
6. `Attachment storage and scan helpers.` - 80 edges
7. `ChatMessage` - 70 edges
8. `StreamEvent` - 63 edges
9. `ProviderFailoverRouter` - 51 edges
10. `ModelRunner` - 51 edges

## Surprising Connections (you probably didn't know these)
- `Alembic migration environment.` --uses--> `Base`  [INFERRED]
  /Users/bhairava/WorkSpace/repos/xframe-ai-agent/migrations/env.py → /Users/bhairava/WorkSpace/repos/xframe-ai-agent/src/xframe_agent/db/base.py
- `Suggestion-quality eval (M2-OBSERVE-02).  Walk the 50-quote fixture, mask one su` --uses--> `AuthContext`  [INFERRED]
  /Users/bhairava/WorkSpace/repos/xframe-ai-agent/evals/suggestion_quality/eval.py → /Users/bhairava/WorkSpace/repos/xframe-ai-agent/src/xframe_agent/auth/jwt.py
- `Tiny single-field, single-step contract — enough to drive the fan-out.` --uses--> `AuthContext`  [INFERRED]
  /Users/bhairava/WorkSpace/repos/xframe-ai-agent/evals/suggestion_quality/eval.py → /Users/bhairava/WorkSpace/repos/xframe-ai-agent/src/xframe_agent/auth/jwt.py
- `In-process replacement for PriceFrameClient's agent suggestions endpoint.      C` --uses--> `AuthContext`  [INFERRED]
  /Users/bhairava/WorkSpace/repos/xframe-ai-agent/evals/suggestion_quality/eval.py → /Users/bhairava/WorkSpace/repos/xframe-ai-agent/src/xframe_agent/auth/jwt.py
- `Re-decode the agent's base64-JSON ctx envelope.      GetFieldSuggestionsTool sen` --uses--> `AuthContext`  [INFERRED]
  /Users/bhairava/WorkSpace/repos/xframe-ai-agent/evals/suggestion_quality/eval.py → /Users/bhairava/WorkSpace/repos/xframe-ai-agent/src/xframe_agent/auth/jwt.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.04
Nodes (150): list_run_events(), Return durable events after a resume cursor., FieldSuggestionsInput, GetFieldSuggestionsTool, Build a complete proposed payload for a wizard step + emit SSE events.  The M2.1, Project draft state onto declared filter keys (summary first, then top-level)., Pick a sensible static default for an essential field, or ``None``.      Default, Best-effort fetch of one historical suggestion value.      Returns the numeric v (+142 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (111): AnthropicProvider, Anthropic fallback provider.  The ``anthropic`` SDK is imported lazily so local, Fallback provider adapter using Claude., _to_anthropic_message(), ChatMessage, ContentBlock, Provider, ProviderError (+103 more)

### Community 2 - "Community 2"
Cohesion: 0.03
Nodes (111): AgentAttachment, AgentAttachmentPage, AgentAuditLog, AgentConversation, AgentDeviceToken, AgentIdempotencyKey, AgentMessage, AgentRun (+103 more)

### Community 3 - "Community 3"
Cohesion: 0.03
Nodes (88): AgentWorkflowDraft, Auto-saved workflow wizard state for one conversation., attachment_response(), get_attachment(), Attachment upload and metadata endpoints., _safe_filename(), upload_attachment(), BaseSettings (+80 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (86): from_settings(), _aware_utc(), event_payload(), Run event persistence and SSE formatting., Return the versioned event payload sent to clients., Record the wall-clock duration of a workflow step on a terminal event.      Look, record_step_duration_from_events(), judge_free_text() (+78 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (62): AttachmentResponse, ConversationCreate, ConversationDetailResponse, ConversationListResponse, ConversationResponse, ConversationUpdate, DecisionRequest, MemoryListResponse (+54 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (52): project_for_model(), AuthContext, Authenticated PriceFRAME user context for agent requests., ApprovalGuidelinesInput, CurrencyInput, EmptyInput, GetApprovalGuidelinesTool, GetCurrencyRateTool (+44 more)

### Community 7 - "Community 7"
Cohesion: 0.1
Nodes (41): Raised when a user cannot execute a tool., ToolPermissionError, PriceFrameClient, _raise_for_status(), HTTP client for PriceFRAME REST APIs., POST a JSON payload to PriceFRAME with JWT pass-through., POST a public JSON payload to PriceFRAME without bearer auth., PUT a JSON payload to PriceFRAME with JWT pass-through. (+33 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (41): _extract_bearer_token(), get_auth_context(), FastAPI auth dependencies., Verify PriceFRAME JWT and introspect the backing PriceFRAME session., Create a dependency that requires a specific PriceFRAME permission code., require_permission(), AuthTokenError, _optional_int() (+33 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (47): ErrorDetail, ErrorResponse, create_app(), FastAPI application factory., Create the FastAPI application., Attach Prometheus instrumentation when enabled., setup_metrics(), agent_client() (+39 more)

### Community 10 - "Community 10"
Cohesion: 0.1
Nodes (44): _build_ctx(), _coerce_filter_keys(), _coerce_sources(), _confidence_score(), _default_value_for(), _draft_value(), _enum_value(), _essential_field_ids() (+36 more)

### Community 11 - "Community 11"
Cohesion: 0.07
Nodes (23): get_database_url(), Alembic migration environment., run_migrations_offline(), run_migrations_online(), get_langfuse_client(), Langfuse client factory., Return a Langfuse client when credentials are configured., get_settings() (+15 more)

### Community 12 - "Community 12"
Cohesion: 0.14
Nodes (28): _extract_access_token(), login(), LoginRequest, LoginResponse, _mapping(), me(), MeResponse, Authentication endpoints: login, refresh, and profile. (+20 more)

### Community 13 - "Community 13"
Cohesion: 0.1
Nodes (22): _bucket(), _decode_ctx(), _evaluate_one(), FieldReport, HistoricalStub, _make_contract(), overall_within_25(), _percentile() (+14 more)

### Community 14 - "Community 14"
Cohesion: 0.12
Nodes (17): Pre-flight PII redaction before payloads reach a provider.  Replaces emails, pho, Redact known PII patterns from ``text``.      The order is deliberate: card numb, redact(), RedactedText, Redaction, Redaction + tool-output wrapping unit tests., test_redact_card_numbers(), test_redact_email_and_phone() (+9 more)

### Community 15 - "Community 15"
Cohesion: 0.15
Nodes (9): BaseHTTPMiddleware, Id, _rate_limit_key(), RateLimitMiddleware, Redis sliding-window rate limit middleware., Apply a per-client sliding-window request limit., Request ID middleware., Attach a stable request ID to logs and responses. (+1 more)

### Community 16 - "Community 16"
Cohesion: 0.33
Nodes (9): _content_from_message(), _events_from_response(), _int_value(), _list(), _mapping(), _message_text(), _request_payload(), _response_error() (+1 more)

### Community 17 - "Community 17"
Cohesion: 0.5
Nodes (3): _json_type(), phase d agent core  Revision ID: 202605190001 Revises: Create Date: 2026-05-19, upgrade()

### Community 18 - "Community 18"
Cohesion: 0.5
Nodes (3): _json_type(), phase e beta attachments writes memory  Revision ID: 202605200001 Revises: 20260, upgrade()

### Community 19 - "Community 19"
Cohesion: 0.5
Nodes (3): _json_type(), agent workflow drafts  Revision ID: 202605240002 Revises: 202605210001 Create Da, upgrade()

### Community 20 - "Community 20"
Cohesion: 0.5
Nodes (1): add conversation kind  Revision ID: 202605210001 Revises: 202605200001 Create Da

### Community 21 - "Community 21"
Cohesion: 0.67
Nodes (3): build_fixture(), build_quote(), Deterministically build the 50-quote suggestion-quality fixture.  Run via ``pyth

### Community 22 - "Community 22"
Cohesion: 0.5
Nodes (3): Migration coverage checks for model/schema drift., The conversation kind model field must exist in Alembic migrations., test_agent_conversation_kind_column_is_migrated()

### Community 23 - "Community 23"
Cohesion: 0.5
Nodes (3): new_id(), Small ULID-like identifiers without an extra runtime dependency., Return a 26-character, time-sortable identifier.

### Community 24 - "Community 24"
Cohesion: 1.0
Nodes (1): Versioned API router.

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (0): 

### Community 26 - "Community 26"
Cohesion: 1.0
Nodes (1): Both caps fully consumed — no further spend is possible.

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (0): 

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (0): 

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **69 isolated node(s):** `phase d agent core  Revision ID: 202605190001 Revises: Create Date: 2026-05-19`, `phase e beta attachments writes memory  Revision ID: 202605200001 Revises: 20260`, `agent workflow drafts  Revision ID: 202605240002 Revises: 202605210001 Create Da`, `add conversation kind  Revision ID: 202605210001 Revises: 202605200001 Create Da`, `CI entry point for synthetic golden trace checks.` (+64 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 24`** (2 nodes): `Versioned API router.`, `router.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 26`** (1 nodes): `Both caps fully consumed — no further spend is possible.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Settings` connect `Community 3` to `Community 0`, `Community 1`, `Community 2`, `Community 4`, `Community 6`, `Community 7`, `Community 8`, `Community 9`, `Community 11`, `Community 12`, `Community 15`?**
  _High betweenness centrality (0.270) - this node is a cross-community bridge._
- **Why does `AuthContext` connect `Community 6` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 8`, `Community 9`, `Community 10`, `Community 12`, `Community 13`?**
  _High betweenness centrality (0.195) - this node is a cross-community bridge._
- **Why does `Attachment storage and scan helpers.` connect `Community 5` to `Community 1`, `Community 2`, `Community 3`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.085) - this node is a cross-community bridge._
- **Are the 252 inferred relationships involving `Settings` (e.g. with `FakeProvider` and `Tests for ``agent.dispatch.execute_run`` routing logic.`) actually correct?**
  _`Settings` has 252 INFERRED edges - model-reasoned connections that need verification._
- **Are the 229 inferred relationships involving `AuthContext` (e.g. with `SampleResult` and `FieldReport`) actually correct?**
  _`AuthContext` has 229 INFERRED edges - model-reasoned connections that need verification._
- **Are the 124 inferred relationships involving `RunBudget` (e.g. with `_StubPriceFrame` and `_StubGroundingClient`) actually correct?**
  _`RunBudget` has 124 INFERRED edges - model-reasoned connections that need verification._
- **Are the 114 inferred relationships involving `ToolDefinition` (e.g. with `_ProjOut` and `_ProjIn`) actually correct?**
  _`ToolDefinition` has 114 INFERRED edges - model-reasoned connections that need verification._