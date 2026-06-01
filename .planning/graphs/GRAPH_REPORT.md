# Graph Report - /Users/bhairava/WorkSpace/repos/xframe-ai-agent  (2026-06-01)

## Corpus Check
- 138 files · ~324,529 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1499 nodes · 4948 edges · 38 communities detected
- Extraction: 42% EXTRACTED · 58% INFERRED · 0% AMBIGUOUS · INFERRED: 2848 edges (avg confidence: 0.6)
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
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]

## God Nodes (most connected - your core abstractions)
1. `Settings` - 294 edges
2. `AuthContext` - 269 edges
3. `RunBudget` - 139 edges
4. `ToolDefinition` - 126 edges
5. `Base` - 99 edges
6. `Attachment storage and scan helpers.` - 80 edges
7. `ChatMessage` - 80 edges
8. `StreamEvent` - 69 edges
9. `ContentBlock` - 58 edges
10. `PriceFrameError` - 57 edges

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
Cohesion: 0.03
Nodes (161): list_run_events(), Return durable events after a resume cursor., increment_suggestion_no_signal(), increment_suggestion_sources(), observe_web_research_cost(), Increment per-source counters for one fan-out result.      Pass the ``sources_us, Increment the no_signal counter for ``field_id``., Record one web-research call's estimated USD cost. (+153 more)

### Community 1 - "Community 1"
Cohesion: 0.03
Nodes (149): Return a timezone-aware UTC timestamp., utc_now(), from_settings(), conversation_response(), create_conversation(), create_run_record(), delete_conversation(), delete_workflow_draft() (+141 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (138): Attachment upload and metadata endpoints., Raised when a user cannot execute a tool., ToolPermissionError, BaseSettings, BudgetExceededError, LoopBudget, Per-run budget tracking and ceiling enforcement.  Settings ceilings (`max_steps_, Raised when any per-run budget ceiling has been crossed. (+130 more)

### Community 3 - "Community 3"
Cohesion: 0.05
Nodes (102): AnthropicProvider, Anthropic fallback provider.  The ``anthropic`` SDK is imported lazily so local, Fallback provider adapter using Claude., _to_anthropic_message(), AssistAction, _call_llm(), interpret_freeform(), On-demand LLM assist: parse free-text, answer 'why', or navigate. Bounded + degr (+94 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (90): project_for_model(), ApprovalGuidelinesInput, GetApprovalGuidelinesTool, GetCurrencyRateTool, GetQuotationTool, IdInput, JsonOutput, ListCorridorsAvailableTool (+82 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (84): AgentAttachment, AgentAttachmentPage, AgentAuditLog, AgentConversation, AgentDeviceToken, AgentIdempotencyKey, AgentMessage, AgentRun (+76 more)

### Community 6 - "Community 6"
Cohesion: 0.04
Nodes (74): AttachmentResponse, ConversationCreate, ConversationDetailResponse, ConversationListResponse, ConversationResponse, ConversationUpdate, DecisionRequest, MemoryListResponse (+66 more)

### Community 7 - "Community 7"
Cohesion: 0.06
Nodes (66): _extract_access_token(), login(), LoginRequest, LoginResponse, _mapping(), me(), MeResponse, Authentication endpoints: login, refresh, and profile. (+58 more)

### Community 8 - "Community 8"
Cohesion: 0.08
Nodes (43): Cursor, committed_payload(), field_accepted_payload(), field_prompt_payload(), Run event persistence and SSE formatting., recap_payload(), emit_next_prompt(), EmitResult (+35 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (44): _build_ctx(), _coerce_filter_keys(), _coerce_sources(), _confidence_score(), _default_value_for(), _draft_value(), _enum_value(), _essential_field_ids() (+36 more)

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (41): ErrorDetail, ErrorResponse, main(), Export the FastAPI OpenAPI schema snapshot., Structured logging setup and secret redaction., Structlog processor that removes secrets from log events., Configure stdlib logging and structlog., redact_secrets() (+33 more)

### Community 11 - "Community 11"
Cohesion: 0.06
Nodes (27): attachment_response(), get_attachment(), _safe_filename(), upload_attachment(), client(), Shared pytest fixtures., test_settings(), Protocol (+19 more)

### Community 12 - "Community 12"
Cohesion: 0.08
Nodes (23): get_database_url(), Alembic migration environment., run_migrations_offline(), run_migrations_online(), get_langfuse_client(), Langfuse client factory., Return a Langfuse client when credentials are configured., get_settings() (+15 more)

### Community 13 - "Community 13"
Cohesion: 0.1
Nodes (22): _bucket(), _decode_ctx(), _evaluate_one(), FieldReport, HistoricalStub, _make_contract(), overall_within_25(), _percentile() (+14 more)

### Community 14 - "Community 14"
Cohesion: 0.12
Nodes (17): Pre-flight PII redaction before payloads reach a provider.  Replaces emails, pho, Redact known PII patterns from ``text``.      The order is deliberate: card numb, redact(), RedactedText, Redaction, Redaction + tool-output wrapping unit tests., test_redact_card_numbers(), test_redact_email_and_phone() (+9 more)

### Community 15 - "Community 15"
Cohesion: 0.16
Nodes (15): GoldenTrace, load_golden_traces(), _provider_replay(), Golden trace replay helpers.  By default :func:`replay_trace` does **structural*, Load versioned synthetic golden traces., Replay a synthetic trace.      In structural mode (default), returns the declare, Hook for live provider replay.      Wiring lives in ``evals/nightly.py`` (which, replay_trace() (+7 more)

### Community 16 - "Community 16"
Cohesion: 0.3
Nodes (14): agent_client(), _run_event_types(), _seed_draft(), _start_wizard(), test_conversation_commit_emits_committed_event(), test_conversation_start_preserves_existing_draft(), test_conversation_start_seeds_draft_and_emits_first_prompt(), test_field_answer_invalid_pattern_returns_422() (+6 more)

### Community 17 - "Community 17"
Cohesion: 0.15
Nodes (9): BaseHTTPMiddleware, Id, _rate_limit_key(), RateLimitMiddleware, Redis sliding-window rate limit middleware., Apply a per-client sliding-window request limit., Request ID middleware., Attach a stable request ID to logs and responses. (+1 more)

### Community 18 - "Community 18"
Cohesion: 0.29
Nodes (9): _load(), Cross-repo contract parity: the conversation projection must only reference fiel, Money fields may be spread across pricing + targets phases., test_conversation_block_is_present(), test_conversation_field_ids_exist_in_steps(), test_conversation_phase_ids_are_declared(), test_identity_phase_contains_expected_fields(), test_money_fields_are_in_conversation() (+1 more)

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (7): test_enum_rejects_unknown_value(), test_percentage_out_of_range_raises(), test_required_empty_raises(), test_string_pattern_violation_raises(), test_valid_string_passes(), Validate a single conversational answer against its contract FieldSpec., validate_answer()

### Community 20 - "Community 20"
Cohesion: 0.5
Nodes (3): _json_type(), phase d agent core  Revision ID: 202605190001 Revises: Create Date: 2026-05-19, upgrade()

### Community 21 - "Community 21"
Cohesion: 0.5
Nodes (3): _json_type(), phase e beta attachments writes memory  Revision ID: 202605200001 Revises: 20260, upgrade()

### Community 22 - "Community 22"
Cohesion: 0.5
Nodes (3): _json_type(), agent workflow drafts  Revision ID: 202605240002 Revises: 202605210001 Create Da, upgrade()

### Community 23 - "Community 23"
Cohesion: 0.5
Nodes (1): add conversation kind  Revision ID: 202605210001 Revises: 202605200001 Create Da

### Community 24 - "Community 24"
Cohesion: 0.67
Nodes (3): build_fixture(), build_quote(), Deterministically build the 50-quote suggestion-quality fixture.  Run via ``pyth

### Community 25 - "Community 25"
Cohesion: 0.5
Nodes (3): Migration coverage checks for model/schema drift., The conversation kind model field must exist in Alembic migrations., test_agent_conversation_kind_column_is_migrated()

### Community 26 - "Community 26"
Cohesion: 0.5
Nodes (3): new_id(), Small ULID-like identifiers without an extra runtime dependency., Return a 26-character, time-sortable identifier.

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Versioned API router.

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (0): 

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (0): 

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (0): 

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (0): 

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (0): 

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Both caps fully consumed — no further spend is possible.

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (0): 

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (0): 

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (0): 

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (0): 

## Knowledge Gaps
- **81 isolated node(s):** `phase d agent core  Revision ID: 202605190001 Revises: Create Date: 2026-05-19`, `phase e beta attachments writes memory  Revision ID: 202605200001 Revises: 20260`, `agent workflow drafts  Revision ID: 202605240002 Revises: 202605210001 Create Da`, `add conversation kind  Revision ID: 202605210001 Revises: 202605200001 Create Da`, `CI entry point for synthetic golden trace checks.` (+76 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 27`** (2 nodes): `Versioned API router.`, `router.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `Both caps fully consumed — no further spend is possible.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Settings` connect `Community 2` to `Community 0`, `Community 1`, `Community 3`, `Community 5`, `Community 7`, `Community 10`, `Community 11`, `Community 12`, `Community 17`?**
  _High betweenness centrality (0.256) - this node is a cross-community bridge._
- **Why does `AuthContext` connect `Community 2` to `Community 0`, `Community 1`, `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 9`, `Community 10`, `Community 13`?**
  _High betweenness centrality (0.193) - this node is a cross-community bridge._
- **Why does `Attachment storage and scan helpers.` connect `Community 6` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 5`, `Community 7`, `Community 11`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Are the 290 inferred relationships involving `Settings` (e.g. with `FakeProvider` and `Tests for ``agent.dispatch.execute_run`` routing logic.`) actually correct?**
  _`Settings` has 290 INFERRED edges - model-reasoned connections that need verification._
- **Are the 266 inferred relationships involving `AuthContext` (e.g. with `SampleResult` and `FieldReport`) actually correct?**
  _`AuthContext` has 266 INFERRED edges - model-reasoned connections that need verification._
- **Are the 134 inferred relationships involving `RunBudget` (e.g. with `_StubPriceFrame` and `_StubGroundingClient`) actually correct?**
  _`RunBudget` has 134 INFERRED edges - model-reasoned connections that need verification._
- **Are the 122 inferred relationships involving `ToolDefinition` (e.g. with `_ProjOut` and `_ProjIn`) actually correct?**
  _`ToolDefinition` has 122 INFERRED edges - model-reasoned connections that need verification._