# Part 7 — Tools and Integrations

> Six chapters on the tool layer — the most extensible part of xFRAME. Part 3 surveyed the files; here we focus on contract decisions, every tool annotated, the REST contract with PriceFRAME, the HMAC ceremony for audit, and a step-by-step walkthrough of adding a brand-new tool.

---

## Chapter 46 — The `ToolDefinition` Contract in Depth

### 46.1 Why a contract at all?

You could make each tool a plain function:

```python
async def get_quotation(quote_id: int, jwt: str) -> dict: ...
```

That works. But you'd:

- Build your own registry.
- Manually generate JSON Schemas for each one.
- Reinvent permission checks per-tool.
- Reinvent risk classification.
- Reinvent output projection.
- Reinvent approval gating.

The `ToolDefinition` base class **codifies the common parts** so each tool focuses on the business logic.

### 46.2 The seven class vars

```python
class ToolDefinition(Generic[InputModel, OutputModel], ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[InputModel]]
    output_model: ClassVar[type[OutputModel]]
    permission: ClassVar[str]
    risk: ClassVar[Risk]
    cost_class: ClassVar[CostClass]
    model_visible_fields: ClassVar[tuple[str, ...] | None] = None
```

| Var | Purpose | Example |
|---|---|---|
| `name` | Stable identifier the LLM uses | `"create_quotation"` |
| `description` | What the LLM sees in the catalog | `"Create a draft quotation in PriceFRAME."` |
| `input_model` | Pydantic class — generates JSON Schema | `CreateQuotationInput` |
| `output_model` | Pydantic class for results | `JsonOutput` |
| `permission` | RBAC string checked against `AuthContext` | `"agent.quotes.create"` |
| `risk` | `"READ"` / `"LOW_RISK_WRITE"` / `"HIGH_RISK_WRITE"` | `"LOW_RISK_WRITE"` |
| `cost_class` | `"cheap"` / `"medium"` / `"expensive"` | `"cheap"` |
| `model_visible_fields` | Output keys the LLM sees (None = all) | `("data",)` or `None` |

The `Generic[InputModel, OutputModel]` makes the class **typed** end-to-end. mypy can verify your `_execute` returns the right shape.

### 46.3 Why `ClassVar`?

`ClassVar` tells Python (and type checkers) that these are **class-level** attributes, not instance ones. The `ToolDefinition` instances in `REGISTERED_TOOLS` carry no per-instance state — they're effectively singletons.

This matters because:

- Multiple concurrent runs can use the same tool instance — no shared state to clobber.
- The class metadata is what flows into `to_provider_schema()`.
- Subclasses *declare* their tool's identity rather than instantiating it.

### 46.4 The four methods

```python
async def requires_approval(self, args, ctx) -> bool:
    return self.risk != "READ"

async def execute(self, args, ctx, priceframe) -> OutputModel:
    if not ctx.has_permission(self.permission):
        raise ToolPermissionError(f"Missing {self.permission}")
    return await self._execute(args, ctx, priceframe)

async def _execute(self, args, ctx, priceframe) -> OutputModel:
    raise NotImplementedError

@classmethod
def project_for_model(cls, dumped: dict) -> dict:
    if cls.model_visible_fields is None:
        return dumped
    return {k: v for k, v in dumped.items() if k in cls.model_visible_fields}

@classmethod
def to_provider_schema(cls) -> dict:
    return {
        "name": cls.name,
        "description": cls.description,
        "parameters": cls.input_model.model_json_schema(),
    }
```

Two `async` methods (`requires_approval`, `execute`) — overridable. One abstract `async` method (`_execute`) — must be implemented. Two `@classmethod` helpers used by the harness.

### 46.5 Why the `execute` / `_execute` split

Surface API: `await tool.execute(args, ctx, priceframe)`.

`execute` does **permission verification**. If `ctx.has_permission(self.permission)` is False, it raises `ToolPermissionError`. Only then does it call `_execute`.

`_execute` is the **business logic**. Subclasses implement this. They can assume the permission has been verified.

This split is the **separation of concerns**:

- Framework concerns (auth) in `execute`.
- Business concerns (PriceFRAME call) in `_execute`.

If you needed to add **rate limiting per tool** or **caching per tool**, you'd add it in `execute`. Business logic stays untouched.

### 46.6 `requires_approval` — the overrideable

Default:

```python
async def requires_approval(self, args, ctx) -> bool:
    return self.risk != "READ"
```

Read tools: False (no approval). Anything else: True.

A tool can override:

```python
class RecalculateQuoteAggregatesTool(...):
    risk: ClassVar[Risk] = "READ"  # Treated as read for approval purposes...

    async def requires_approval(self, args, ctx) -> bool:
        return False  # ... and explicitly skip approval even though it mutates
```

Or with conditional logic:

```python
async def requires_approval(self, args, ctx) -> bool:
    # Below $1000, auto-approve for senior reps
    if ctx.role_code == "ROLE_SENIOR_SALES" and args.amount < 1000:
        return False
    return True
```

The harness reads `requires_approval(args, ctx)` per call. Tier-up by user, by amount, by hour of day — all possible.

### 46.7 `project_for_model` — the privacy lever

```python
class GetQuotationTool(...):
    model_visible_fields: ClassVar[tuple[str, ...]] = ("data",)
```

When `tool.execute` returns a `JsonOutput` with `data` and other metadata, only the `data` key reaches the LLM via `project_for_model`. The persisted `agent_tool_calls.result` keeps the full output.

Use cases:

- **Hide internal metadata** the LLM doesn't need.
- **Mask sensitive fields** (e.g., return a quote summary but not raw account numbers).
- **Reduce token cost** for large outputs.

If `model_visible_fields = None`, no projection (everything goes through). Default for most tools.

### 46.8 `to_provider_schema` — auto-generated from Pydantic

```python
@classmethod
def to_provider_schema(cls) -> dict:
    return {
        "name": cls.name,
        "description": cls.description,
        "parameters": cls.input_model.model_json_schema(),
    }
```

The Pydantic `model_json_schema()` generates a JSON Schema object from the field declarations. Add a field with `Field(min_length=1, gt=0)` and the schema picks up the constraints automatically.

This is huge: you never hand-write JSON Schema. You write a Pydantic class. The schema is correct by construction.

### 46.9 What the contract does NOT cover

- **Retries** — not at the tool layer. `PriceFrameClient._request` retries 5xx; tool execution is one-shot.
- **Caching** — no decorator pattern; tools always call live.
- **Idempotency keys to PriceFRAME** — passed manually inside each tool's `_execute`. Not enforced by the contract.
- **Per-tool budgets** — `LoopBudget` is per-run; no per-tool counter.
- **Observability hooks** — events are emitted by the harness, not the tool. Tools don't `print`.

Adding any of these would be a contract change. The current shape is intentionally minimal.

### 🔑 Chapter 46 takeaways

- 7 class vars, 4 methods. That's the entire contract.
- Subclasses implement `_execute` + (optionally) override `requires_approval`.
- Pydantic input models → automatic JSON Schema, no manual schema writing.
- The contract is small on purpose; adding features means extending it.

---

## Chapter 47 — Read Tools: All Six, Annotated

### 47.1 `list_my_quotations`

```python
class ListMyQuotationsInput(BaseModel):
    status: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

class ListMyQuotationsTool(ToolDefinition[ListMyQuotationsInput, JsonOutput]):
    name = "list_my_quotations"
    description = "List the authenticated user's quotations, optionally filtered by status."
    input_model = ListMyQuotationsInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "cheap"

    async def _execute(self, args, ctx, priceframe):
        params = {"owner_id": "me", "limit": args.limit}
        if args.status:
            params["status"] = args.status
        response = await priceframe.get_json(
            "/api/quotes", jwt_raw=ctx.jwt_raw, params=params,
        )
        return JsonOutput(data=response)
```

**Notes:**

- `owner_id="me"` — PriceFRAME interprets this as "the user from the JWT." The agent doesn't pass the user ID explicitly because the JWT carries it.
- `limit` capped at 100 to prevent runaway responses.
- No status filter by default — return all states.

### 47.2 `get_quotation`

```python
class GetQuotationInput(BaseModel):
    id: int = Field(gt=0)

class GetQuotationTool(ToolDefinition[GetQuotationInput, JsonOutput]):
    name = "get_quotation"
    description = "Fetch the full pricing context for one quotation by ID."
    input_model = GetQuotationInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "medium"
    model_visible_fields: ClassVar[tuple[str, ...]] = ("data",)

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.get_json(
            f"/api/v1/quotes/{args.id}/pricing-context", jwt_raw=ctx.jwt_raw,
        )
        return JsonOutput(data=response)
```

**Notes:**

- `model_visible_fields = ("data",)` strips metadata wrappers PriceFRAME adds.
- `cost_class = "medium"` because the pricing-context endpoint can return a lot.
- Path interpolation: `f"/api/v1/quotes/{args.id}/..."`. Pydantic's `gt=0` validation ensures `args.id` is a positive int, so no injection risk.

### 47.3 `list_corridors_available`

```python
class ListCorridorsAvailableInput(BaseModel):
    pass

class ListCorridorsAvailableTool(ToolDefinition[ListCorridorsAvailableInput, JsonOutput]):
    name = "list_corridors_available"
    description = "List corridors currently active in PriceFRAME."
    input_model = ListCorridorsAvailableInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "cheap"

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.get_json("/api/corridors/active", jwt_raw=ctx.jwt_raw)
        return JsonOutput(data=response)
```

**Notes:**

- Empty input. The model calls with `{}`.
- Returns the global active list, not filtered. Concern for large catalogs (see roadmap).

⚠️ **Scalability gotcha**: as PriceFRAME's corridor catalog grows, this becomes expensive. Future: accept filter args (region, currency) and pass through to a server-side filtered endpoint.

### 47.4 `get_currency_rate`

```python
class GetCurrencyRateInput(BaseModel):
    currency: str = Field(min_length=3, max_length=3)

class GetCurrencyRateTool(ToolDefinition[GetCurrencyRateInput, JsonOutput]):
    name = "get_currency_rate"
    description = "Get the latest market rate for a 3-letter currency code."
    input_model = GetCurrencyRateInput
    output_model = JsonOutput
    permission = "agent.quotes.read"
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "cheap"

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.get_json(
            "/api/app-config/currency-rates",
            jwt_raw=ctx.jwt_raw,
            params={"currency": args.currency},
        )
        return JsonOutput(data=response)
```

**Notes:**

- `currency` constrained to exactly 3 chars (ISO 4217 like USD, INR, EUR).
- This is the canonical "simplest tool." A great template for new tools.

### 47.5 `lookup_salesforce_pr`

```python
class LookupSalesforcePrInput(BaseModel):
    query: str = Field(min_length=1)

class LookupSalesforcePrTool(ToolDefinition[LookupSalesforcePrInput, JsonOutput]):
    name = "lookup_salesforce_pr"
    description = "Search Salesforce pricing requests by customer name or opportunity ID."
    input_model = LookupSalesforcePrInput
    output_model = JsonOutput
    permission = "agent.salesforce.read"   # Different permission!
    risk: ClassVar[Risk] = "READ"
    cost_class: ClassVar[CostClass] = "medium"

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.get_json(
            "/api/quotes/salesforce/search",
            jwt_raw=ctx.jwt_raw,
            params={"q": args.query},
        )
        return JsonOutput(data=response)
```

**Notes:**

- Permission is `agent.salesforce.read`, not `agent.quotes.read`. Salesforce integration is a separate capability gated independently.
- Users without Salesforce access don't see this tool in the catalog.

### 47.6 `recalculate_quote_aggregates`

```python
class RecalculateQuoteAggregatesInput(BaseModel):
    id: int = Field(gt=0)

class RecalculateQuoteAggregatesTool(ToolDefinition[...]):
    name = "recalculate_quote_aggregates"
    description = "Recompute totals after pricing changes."
    input_model = RecalculateQuoteAggregatesInput
    output_model = JsonOutput
    permission = "agent.quotes.recalc"
    risk: ClassVar[Risk] = "READ"   # ← Wait, but it's a POST?
    cost_class: ClassVar[CostClass] = "medium"

    async def requires_approval(self, args, ctx) -> bool:
        return False   # Even though it mutates, no approval

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.post_json(
            f"/api/quotes/{args.id}/recalculate-aggregates",
            jwt_raw=ctx.jwt_raw,
            json={},  # no body
        )
        return JsonOutput(data=response)
```

**Notes — this tool is special:**

- Classified `risk = "READ"` even though it does a POST that mutates the quote's totals.
- Explicit `requires_approval -> False` override.
- The justification: aggregates are **deterministic and idempotent**. Running it twice gives the same result. No business decision is being made. Forcing approval would be friction without benefit.

**Risk classification is policy, not mechanics.** The HTTP verb doesn't dictate it; the business semantics do.

### 47.7 Patterns across the six

| Pattern | Frequency |
|---|---|
| Single Pydantic input class | 6/6 |
| `JsonOutput` as output | 6/6 |
| `cost_class="cheap"` for simple lookups | 3/6 |
| `model_visible_fields` set | 1/6 (`get_quotation`) |
| `requires_approval` override | 1/6 (`recalculate_quote_aggregates`) |
| Path interpolation from input | 2/6 |
| Query params from input | 3/6 |
| No body | 5/6 (all but `recalculate_aggregates`) |

The shape is uniform. Once you've read one, you can read the others quickly.

### 🔑 Chapter 47 takeaways

- 6 read tools, ~150 lines of code total.
- Each is a one-Pydantic-class + one-async-method blob.
- Special cases (Salesforce permission, recalc-as-read) make sense on close reading.
- Use `get_currency_rate` as the template when adding new read tools.

---

## Chapter 48 — Write Tools: All Six, Annotated

### 48.1 `create_quotation`

```python
class CreateQuotationInput(BaseModel):
    title: str = Field(min_length=1)
    customer_id: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    notes: str | None = None

class CreateQuotationTool(ToolDefinition[CreateQuotationInput, JsonOutput]):
    name = "create_quotation"
    description = "Create a draft quotation in PriceFRAME."
    input_model = CreateQuotationInput
    output_model = JsonOutput
    permission = "agent.quotes.create"
    risk: ClassVar[Risk] = "LOW_RISK_WRITE"
    cost_class: ClassVar[CostClass] = "medium"

    async def _execute(self, args, ctx, priceframe):
        payload = {
            "title": args.title,
            "customerId": args.customer_id,
            "currency": args.currency,
        }
        if args.notes:
            payload["notes"] = args.notes
        response = await priceframe.post_json(
            "/api/quotes", jwt_raw=ctx.jwt_raw, json=payload,
        )
        return JsonOutput(data=response)
```

**Notes:**

- snake_case in → camelCase out. The translation happens in `_execute`. PriceFRAME's API is camelCase; xFRAME's tool inputs are snake_case (Pythonic + idiomatic for the LLM).
- Optional fields only included if present (`if args.notes:`). Cleaner PriceFRAME requests.

### 48.2 `bulk_add_corridors` — with the `CorridorDraft` sub-model

```python
class CorridorDraft(BaseModel):
    corridor_id: int = Field(gt=0)
    volume: Decimal | None = None
    term_months: int | None = Field(default=None, ge=1)
    applied_rate: Decimal | None = None
    fx_spread: Decimal | None = None

class BulkAddCorridorsInput(BaseModel):
    quote_id: int = Field(gt=0)
    corridors: list[CorridorDraft] = Field(min_length=1)

class BulkAddCorridorsTool(ToolDefinition[BulkAddCorridorsInput, JsonOutput]):
    name = "bulk_add_corridors"
    description = "Add multiple corridors to an existing quotation in one call."
    input_model = BulkAddCorridorsInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk: ClassVar[Risk] = "LOW_RISK_WRITE"
    cost_class: ClassVar[CostClass] = "medium"

    async def _execute(self, args, ctx, priceframe):
        corridors_payload: list[dict[str, Any]] = []
        for c in args.corridors:
            corridor_dict: dict[str, Any] = {"corridorId": c.corridor_id}
            if c.volume is not None:
                corridor_dict["volume"] = str(c.volume)
            if c.term_months is not None:
                corridor_dict["termMonths"] = c.term_months
            if c.applied_rate is not None:
                corridor_dict["appliedRate"] = str(c.applied_rate)
            if c.fx_spread is not None:
                corridor_dict["fxSpread"] = str(c.fx_spread)
            corridors_payload.append(corridor_dict)

        response = await priceframe.post_json(
            f"/api/quotes/{args.quote_id}/corridors/bulk",
            jwt_raw=ctx.jwt_raw,
            json={"corridors": corridors_payload},
        )
        return JsonOutput(data=response)
```

**Notes:**

- Decimal → string conversion: `str(c.volume)`. Why string? JSON has no Decimal type; floats lose precision. String preserves "0.020000" exactly.
- Optional fields use `is not None` (not truthiness) — `0` is a legitimate value for some fields.
- Pydantic validates the list isn't empty (`min_length=1`).

### 48.3 `update_corridor_pricing`

```python
class UpdateCorridorPricingInput(BaseModel):
    corridor_id: int = Field(gt=0)
    applied_rate: Decimal | None = None
    fx_spread: Decimal | None = None
    volume: Decimal | None = None
    term_months: int | None = Field(default=None, ge=1)

class UpdateCorridorPricingTool(...):
    name = "update_corridor_pricing"
    description = "Update pricing fields on one corridor."
    permission = "agent.quotes.edit"
    risk: ClassVar[Risk] = "LOW_RISK_WRITE"

    async def _execute(self, args, ctx, priceframe):
        payload: dict[str, Any] = {}
        if args.applied_rate is not None:
            payload["appliedRate"] = str(args.applied_rate)
        if args.fx_spread is not None:
            payload["fxSpread"] = str(args.fx_spread)
        ...

        response = await priceframe.put_json(
            f"/api/quote-corridors/{args.corridor_id}",
            jwt_raw=ctx.jwt_raw,
            json=payload,
        )
        return JsonOutput(data=response)
```

**Notes:**

- `PUT` for partial update (PriceFRAME convention).
- All fields optional; the model may update just one.
- Same Decimal→string treatment.

### 48.4 `preview_pricing_change`

```python
class QuoteScopedPayloadInput(BaseModel):
    quote_id: int = Field(gt=0)
    payload: dict[str, Any]

class PreviewPricingChangeTool(...):
    name = "preview_pricing_change"
    description = "Preview computed pricing for a quote without persisting changes."
    permission = "agent.quotes.recalc"
    risk: ClassVar[Risk] = "READ"   # ← preview doesn't mutate
    cost_class: ClassVar[CostClass] = "medium"

    async def _execute(self, args, ctx, priceframe):
        response = await priceframe.post_json(
            f"/api/v1/quotes/{args.quote_id}/pricing/preview",
            jwt_raw=ctx.jwt_raw,
            json=args.payload,
        )
        return JsonOutput(data=response)
```

**Notes:**

- `risk = "READ"` despite being a POST — because it's non-persistent.
- `payload: dict[str, Any]` — accepts arbitrary preview payloads. The PriceFRAME endpoint has its own validation.
- This tool exposes more flexibility (free-form JSON in `payload`) than the other writes. Justified because it's read-only on PriceFRAME's side.

### 48.5 `set_fx_spread` — with local validation

```python
class FxSpreadInput(BaseModel):
    corridor_id: int = Field(gt=0)
    applied_fx_spread: str
    minimum_spread: str

class SetFxSpreadTool(...):
    name = "set_fx_spread"
    description = "Set the applied FX spread on a corridor, with a minimum guard."
    permission = "agent.quotes.edit"
    risk: ClassVar[Risk] = "LOW_RISK_WRITE"

    async def _execute(self, args, ctx, priceframe):
        applied = Decimal(args.applied_fx_spread)
        minimum = Decimal(args.minimum_spread)
        if applied < minimum:
            raise ValueError(
                f"applied_fx_spread ({applied}) is below minimum_spread ({minimum})"
            )

        response = await priceframe.put_json(
            f"/api/quote-corridors/{args.corridor_id}",
            jwt_raw=ctx.jwt_raw,
            json={"appliedFxSpread": args.applied_fx_spread, "minimumSpread": args.minimum_spread},
        )
        return JsonOutput(data=response)
```

**Notes — this tool is unusual:**

- Inputs are `str`, not `Decimal`. Why? The LLM occasionally emits malformed numerics; receiving them as strings lets us validate explicitly with `Decimal(...)`.
- **Local validation** before the API call. Fail-fast on `applied < minimum`. The `ValueError` is caught by `_execute_one` (post §15.4) and surfaced to the model.
- Avoids a round-trip to PriceFRAME just to learn the request was invalid.

This pattern — input as string, parse + validate in `_execute` — is appropriate when:

- The LLM is bad at the type (uncommon Decimal formats).
- Local validation is cheap and meaningful.
- A round-trip on failure would be wasteful.

### 48.6 `submit_for_approval`

```python
class ApprovalInput(BaseModel):
    quote_id: int = Field(gt=0)
    comment: str | None = None

class SubmitForApprovalTool(...):
    name = "submit_for_approval"
    description = "Submit a quotation to the approval workflow."
    permission = "agent.approvals.submit"
    risk: ClassVar[Risk] = "HIGH_RISK_WRITE"   # ← the only HIGH_RISK_WRITE
    cost_class: ClassVar[CostClass] = "medium"

    async def _execute(self, args, ctx, priceframe):
        payload: dict[str, Any] = {
            "policy": "quote_pricing",
            "approvers": {"type": "group", "codes": ["pricing_team"]},
            "reasons": {"source": "agent"},
        }
        if args.comment:
            payload["initiatorComment"] = args.comment

        response = await priceframe.post_json(
            f"/api/quotes/{args.quote_id}/approvals",
            jwt_raw=ctx.jwt_raw,
            json=payload,
        )
        return JsonOutput(data=response)
```

**Notes:**

- The only `HIGH_RISK_WRITE` tool in v1. Once submitted, the quote enters the compliance workflow.
- Hardcoded `approvers.codes = ["pricing_team"]`. A future improvement: configurable approver groups per role or amount tier.
- The system prompt has an explicit rule: don't call this without user's "yes."

### 48.7 The HMAC audit callback (sidebar)

Write tools' execution doesn't itself call the HMAC audit callback. That happens in the **decisions endpoint** (`api/v1/runs.py`) after a write succeeds:

```python
audit = await priceframe.post_agent_audit_callback(
    jwt_raw=auth.jwt_raw,
    service_secret=settings.priceframe_service_secret,
    payload={"tool_call_id": tool_call.id, ...},
)
tool_call.priceframe_audit_log_id = audit["audit_log_id"]
```

So `tool._execute` is "make the write." The audit is layered on by the orchestration above.

This separation lets you change audit policy without touching tool code.

### 47.8 What I'd add next

Top candidates for new tools:

1. `list_pending_approvals` — for the `approve_pending_quotes` flow (Chapter 44).
2. `reject_approval` — its companion.
3. `get_customer` — direct lookup by ID, useful for the agent to confirm intent.
4. `search_my_history` — RAG-backed search of past quotes (Chapter 38).
5. `attach_document` — upload supporting documents to a quote.

Each is a one-class + one-method change, plus a tests entry. Adding all 5 would take a half-day.

### 🔑 Chapter 48 takeaways

- 6 write tools, ~250 lines total.
- snake_case in, camelCase out — consistent translation pattern.
- Decimals become strings to preserve precision.
- `set_fx_spread` shows the pattern for local validation; `submit_for_approval` is the highest risk.

---

## Chapter 49 — The PriceFRAME REST Contract

### 49.1 Endpoints used by the agent

| Tool | Method | Path |
|---|---|---|
| (auth) | `POST` | `/api/auth/login` |
| (auth) | `POST` | `/api/auth/refresh` |
| (auth) | `GET` | `/api/auth/profile` |
| `list_my_quotations` | `GET` | `/api/quotes?owner_id=me&...` |
| `get_quotation` | `GET` | `/api/v1/quotes/{id}/pricing-context` |
| `list_corridors_available` | `GET` | `/api/corridors/active` |
| `get_currency_rate` | `GET` | `/api/app-config/currency-rates?currency=X` |
| `lookup_salesforce_pr` | `GET` | `/api/quotes/salesforce/search?q=...` |
| `recalculate_quote_aggregates` | `POST` | `/api/quotes/{id}/recalculate-aggregates` |
| `preview_pricing_change` | `POST` | `/api/v1/quotes/{id}/pricing/preview` |
| `create_quotation` | `POST` | `/api/quotes` |
| `bulk_add_corridors` | `POST` | `/api/quotes/{quote_id}/corridors/bulk` |
| `update_corridor_pricing` | `PUT` | `/api/quote-corridors/{id}` |
| `set_fx_spread` | `PUT` | `/api/quote-corridors/{id}` (different body) |
| `submit_for_approval` | `POST` | `/api/quotes/{quote_id}/approvals` |
| (audit) | `POST` | `/api/v1/agent-audit-callbacks` |

That's the full surface. ~15 endpoints. Documented in `docs/ai-agent/03-priceframe-delta-prs.md`.

### 49.2 Versioning observation

Endpoints split between `/api/...` and `/api/v1/...`:

- `/api/quotes` — older, simpler endpoints.
- `/api/v1/quotes/{id}/pricing-context` — newer composite reads.
- `/api/v1/agent-audit-callbacks` — agent-specific, v1-only.

PriceFRAME is mid-migration to a versioned API. The agent uses whichever endpoint exists today. As PriceFRAME shifts, the agent follows.

### 49.3 Request conventions

**Headers (every call):**

```
Authorization: Bearer <jwt_raw>
Accept: application/json
Content-Type: application/json  (on POST/PUT)
```

**Optional headers (writes):**

```
Idempotency-Key: <tool_call_id>
```

**Audit callback only:**

```
X-Agent-Timestamp: <ms_epoch>
X-Agent-Service-Signature: <hex>
```

### 49.4 Body conventions

PriceFRAME uses **camelCase** in JSON bodies. The agent's tools translate from Python snake_case:

```python
{"customer_id": 42, "applied_rate": "0.072"}
↓
{"customerId": 42, "appliedRate": "0.072"}
```

Translation lives in each tool's `_execute`. Not in a generic middleware because:

- Only a few writes need it.
- Field names sometimes diverge more than just casing (`fx_spread` → `fxSpread`).
- Generic middleware would obscure intent.

### 49.5 Response conventions

PriceFRAME typically returns:

```json
{"data": { ... }, "meta": { ... }}
```

The agent's tools wrap this in `JsonOutput(data=response)`. The `meta` field is included in `dumped` (full response) but stripped from `projected` (sent to LLM) when `model_visible_fields = ("data",)`.

### 49.6 Error conventions

PriceFRAME returns standard HTTP statuses:

| Status | Meaning | Agent maps to |
|---|---|---|
| 200 OK | success | `JsonOutput(data=response)` |
| 400 Bad Request | client error | `PriceFrameResponseError` |
| 401 Unauthorized | bad/expired JWT | `PriceFrameAuthError` |
| 403 Forbidden | insufficient permission | `PriceFrameForbiddenError` |
| 404 Not Found | missing resource | `PriceFrameNotFoundError` |
| 422 Unprocessable | validation failure | `PriceFrameResponseError` |
| 5xx | server error | `PriceFrameResponseError`, retried |

Error bodies typically:

```json
{"error": {"code": "...", "message": "...", "details": [...]}}
```

The agent surfaces these as exception messages. Post §15.4, they flow back to the model as tool_result errors.

### 49.7 Idempotency contract

For writes, the agent sends:

```
Idempotency-Key: <tool_call_id>
```

PriceFRAME caches the response (keyed by user + key). A retry with the same key returns the cached response, even if the underlying request differs. This means:

- ✅ Network timeout + retry: safe.
- ✅ Approval, then user-initiated retry: safe.
- ⚠️ Same tool_call_id with different args (shouldn't happen in xFRAME): PriceFRAME might return the prior response, ignoring new args.

The `tool_call_id` is a ULID generated when the `AgentToolCall` is created — unique, never reused.

### 49.8 What changes if PriceFRAME changes

If PriceFRAME adds an endpoint version (`/api/v2/...`):

- Update the path string in the tool's `_execute`.
- If schemas change, update the Pydantic models.
- Run integration tests.
- Deploy.

That's it. The agent doesn't have hardcoded knowledge of PriceFRAME's internals; just the endpoints + payload shapes the tools use.

### 🔑 Chapter 49 takeaways

- ~15 endpoints total. Mostly read; 6 writes; 1 audit callback.
- Conventions: camelCase JSON, Bearer auth, optional Idempotency-Key on writes.
- Errors map to a clean exception hierarchy.
- Schema changes are localized to one tool's `_execute`.

---

## Chapter 50 — HMAC-Signed Audit Callbacks

### 50.1 Why a separate audit channel

After every write, PriceFRAME needs to know:

- **Who** initiated it? (User JWT)
- **Was it really the agent?** (HMAC signature)
- **What was the context?** (`tool_call_id`, args, result)

The user's JWT alone isn't enough — anyone with a leaked token could write to PriceFRAME's audit log. The HMAC proves the writer is the **agent service** itself.

So: writes happen with JWT. **Audit callbacks happen with JWT + HMAC**.

### 50.2 The signing ceremony

```python
async def post_agent_audit_callback(self, *, jwt_raw, service_secret, payload):
    timestamp = str(int(time.time() * 1000))  # milliseconds since epoch
    sig_body = json.dumps(dict(payload), separators=(",", ":"))  # canonical
    signature = hmac.new(
        service_secret.encode("utf-8"),
        f"{timestamp}.{sig_body}".encode(),
        sha256,
    ).hexdigest()

    headers = {
        "Authorization": f"Bearer {jwt_raw}",
        "X-Agent-Timestamp": timestamp,
        "X-Agent-Service-Signature": signature,
        "Content-Type": "application/json",
    }
    response = await self._client.post(
        "/api/v1/agent-audit-callbacks",
        headers=headers, content=sig_body,
    )
    return response.json()
```

Three things signed together:

- **Timestamp** (ms epoch) — protects against replay.
- **Body** — canonical JSON (`separators=(",", ":")` strips whitespace for deterministic hash).
- **Concatenation** — `f"{timestamp}.{sig_body}"` — the dot is a delimiter PriceFRAME also uses for verification.

### 50.3 Canonical JSON

The body must be **byte-identical** between signer and verifier. JSON serialization has many free choices (whitespace, key order, encoding). Force a canonical form:

- `separators=(",", ":")` — no spaces after delimiters.
- (Python default) `sort_keys=False` — but the agent doesn't sort here. Production hardening: sort keys.

⚠️ **A real production deployment should sort keys** to guarantee identical signatures even if the dict order varies. xFRAME's current implementation works in practice because dicts maintain insertion order and the same code constructs them — but it's a thin assumption.

### 50.4 Verification on PriceFRAME's side (sketch)

PriceFRAME receives:

```http
POST /api/v1/agent-audit-callbacks
Authorization: Bearer eyJ...
X-Agent-Timestamp: 1716200000123
X-Agent-Service-Signature: abc123def456...
Content-Type: application/json

{"tool_call_id":"01HX...",...}
```

PriceFRAME's verifier:

1. Read `X-Agent-Timestamp`. If `abs(now() - timestamp) > 5min`, reject (replay protection).
2. Read raw body bytes.
3. Compute `HMAC(service_secret, f"{timestamp}.{body}")`.
4. Compare to `X-Agent-Service-Signature`. If different, reject.
5. Verify the JWT in Authorization is valid for the user mentioned in the body.
6. If all checks pass, insert an `audit_logs` row.

### 50.5 Why this prevents the attacks it does

**Attack 1: leaked user JWT.** An attacker with a user JWT can call PriceFRAME's audit-callback endpoint to forge entries. The HMAC stops them — they don't have `PRICEFRAME_SERVICE_SECRET`.

**Attack 2: leaked service secret + no JWT.** An attacker with `PRICEFRAME_SERVICE_SECRET` but no user JWT can sign requests but can't authenticate as a user. PriceFRAME rejects unauth'd or invalid-JWT requests.

**Attack 3: replay.** An attacker intercepts a legit signed callback and re-sends it. The timestamp window (5 min) makes this fail unless replay is fast.

**Attack 4: signature stripping.** Attacker sends without HMAC headers. PriceFRAME requires them; reject.

All four threats are mitigated.

### 50.6 What if PriceFRAME is unreachable for audit?

The `post_agent_audit_callback` call could fail (network, 5xx). What happens to the write that just succeeded?

In xFRAME's current decisions endpoint:

```python
try:
    audit = await priceframe.post_agent_audit_callback(...)
    tool_call.priceframe_audit_log_id = audit["audit_log_id"]
except PriceFrameError as e:
    logger.warning("audit_callback_failed", error=str(e))
    # tool_call stays without priceframe_audit_log_id
```

The write **stays committed in PriceFRAME**. The audit callback might fail silently. This is an **integrity gap**:

- PriceFRAME has the data change but no agent attribution.
- Audit compliance may require this be reconciled.

Production hardening: a reaper job that finds `AgentToolCall` rows with `status=succeeded` but `priceframe_audit_log_id IS NULL`, and retries the callback.

### 50.7 Secret rotation

Both `PRICEFRAME_JWT_SECRET` and `PRICEFRAME_SERVICE_SECRET` should rotate. JWT secret rotation invalidates **all user sessions** — coordinate with PriceFRAME, expect a forced logout. Service secret rotation breaks **audit callbacks** during the transition — coordinate by deploying both old + new on PriceFRAME (verify either), then rotate, then remove old.

Neither is hot-rotatable today. Operational maturity item.

### 🔑 Chapter 50 takeaways

- Audit callbacks are HMAC-signed for service authenticity, JWT-bound for user attribution.
- Timestamp window prevents replay.
- Canonical JSON serialization is critical.
- The current implementation has a gap when audit-callback fails after a write commits.

---

## Chapter 51 — Adding a Brand-New Tool, End to End

### 51.1 The goal

Add `cancel_quotation` — a write tool that cancels a quotation in PriceFRAME. Permission `agent.quotes.edit`. Risk `LOW_RISK_WRITE` (could be HIGH if business says so; you decide). PriceFRAME endpoint: `POST /api/quotes/{id}/cancel`.

### 51.2 Step 1 — Pydantic input model

In `src/xframe_agent/tools/priceframe_write.py`, add:

```python
class CancelQuotationInput(BaseModel):
    id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)
```

`id` required positive; `reason` optional with sanity limit.

### 51.3 Step 2 — Tool subclass

```python
class CancelQuotationTool(ToolDefinition[CancelQuotationInput, JsonOutput]):
    name = "cancel_quotation"
    description = "Cancel a quotation. Once cancelled, a quote cannot be reopened."
    input_model = CancelQuotationInput
    output_model = JsonOutput
    permission = "agent.quotes.edit"
    risk: ClassVar[Risk] = "LOW_RISK_WRITE"
    cost_class: ClassVar[CostClass] = "cheap"

    async def _execute(self, args, ctx, priceframe):
        payload: dict[str, Any] = {}
        if args.reason:
            payload["reason"] = args.reason
        response = await priceframe.post_json(
            f"/api/quotes/{args.id}/cancel",
            jwt_raw=ctx.jwt_raw,
            json=payload,
        )
        return JsonOutput(data=response)
```

Standard pattern. snake_case in, snake_case out (PriceFRAME doesn't need camelCase for this single-field endpoint). Optional field only included if set.

### 51.4 Step 3 — Register in `registry.py`

```python
# tools/registry.py
from xframe_agent.tools.priceframe_write import (
    BulkAddCorridorsTool,
    CancelQuotationTool,   # ← new
    CreateQuotationTool,
    ...
)

REGISTERED_TOOLS = (
    ...,
    CancelQuotationTool(),
    ...
)
```

That's it. The LLM can now propose `cancel_quotation` calls for users with `agent.quotes.edit`.

### 51.5 Step 4 — Write a test

`tests/test_tool_base.py` already covers permission and validation patterns. Add:

```python
async def test_cancel_quotation_validates_input():
    tool = CancelQuotationTool()
    # Reject id=0
    with pytest.raises(ValueError):
        tool.input_model.model_validate({"id": 0})
    # Accept id=1, no reason
    parsed = tool.input_model.model_validate({"id": 1})
    assert parsed.id == 1
    assert parsed.reason is None
    # Accept reason
    parsed = tool.input_model.model_validate({"id": 1, "reason": "duplicate"})
    assert parsed.reason == "duplicate"


async def test_cancel_quotation_requires_approval():
    tool = CancelQuotationTool()
    ctx = AuthContext(user_id=1, role_code="r", profile_code="p",
                      permissions=("agent.quotes.edit",), jwt_raw="x", session_id=1)
    assert await tool.requires_approval(CancelQuotationInput(id=1), ctx) is True


async def test_cancel_quotation_execute_calls_priceframe():
    class FakeClient:
        async def post_json(self, path, *, jwt_raw, json, headers=None):
            return {"ok": True, "path": path, "json": json}

    tool = CancelQuotationTool()
    ctx = AuthContext(user_id=1, role_code="r", profile_code="p",
                      permissions=("agent.quotes.edit",), jwt_raw="x", session_id=1)
    args = CancelQuotationInput(id=42, reason="dup")
    result = await tool.execute(args, ctx, FakeClient())
    assert result.data["path"] == "/api/quotes/42/cancel"
    assert result.data["json"] == {"reason": "dup"}
```

Three tests: input validation, approval default, execution path. The minimum to ship.

### 51.6 Step 5 — Regenerate OpenAPI

```bash
uv run python scripts/export_openapi.py
git diff openapi.yaml
```

The diff will show:

- New `CancelQuotationInput` schema component.
- The tool descriptor appearing in `GET /tools` response (which lists tools by user permission, not statically — so the schema captures the *shape*, not the literal list).

Commit the diff alongside the tool change.

### 51.7 Step 6 — Verify via the API

With a local stack up and a test JWT:

```bash
# List tools for a user with agent.quotes.edit
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/agent/tools | jq '.tools[] | .name' | grep cancel
# → "cancel_quotation"
```

The tool appears. If you remove `agent.quotes.edit` from the test user's permissions, it disappears.

### 51.8 Step 7 — System prompt mention (optional)

If the new tool changes the canonical flow, update the prompt. For `cancel_quotation`, it's not part of the create flow, so no change needed. But if you wanted the LLM to *know* to offer cancellation:

```python
# Add to the prompt's rules:
- If the user wants to cancel a quote, call `cancel_quotation` with the quote ID
  and (optional) reason. Always confirm before cancelling.
```

### 51.9 Step 8 — Document the new endpoint

Update `docs/ai-agent/03-priceframe-delta-prs.md` with the new endpoint expectation. The PriceFRAME team needs to know what URL the agent will hit.

### 51.10 Total work

- 1 Pydantic class (~5 lines)
- 1 Tool subclass (~15 lines)
- 1 registry entry (1 line)
- 3 tests (~30 lines)
- 1 OpenAPI regeneration
- (Optional) prompt update + docs update

**~50 lines of code, ~30 minutes** to ship a new tool end-to-end.

This is what good abstraction enables.

### 🔑 Chapter 51 takeaways

- Adding a tool is a ~50-line, ~30-minute change.
- The contract takes care of schema, registry, permission, approval, projection.
- The tests should cover input validation, approval policy, and the actual call.
- Permissions are the gate — without `agent.quotes.edit` on the user's profile, the LLM doesn't see the tool.

---

### Part 7 wrap-up

You can now read every tool, modify any of them, and add brand-new ones confidently. The tool layer is the **extension surface** of xFRAME — most product growth happens here.

### ✍️ Part 7 exercises

1. Implement `cancel_quotation` end-to-end following Chapter 51. Run the tests; show `GET /tools` lists it.
2. The `bulk_add_corridors` payload translation has 9 if-blocks. Refactor it to a small loop / dict comprehension. Show the diff. Did clarity improve or suffer?
3. Design a `list_pending_approvals` tool: input model, permission, risk, PriceFRAME endpoint. Justify each choice.

### 📚 Part 7 further reading

- Pydantic v2 — JSON Schema generation docs.
- OpenAPI spec — how function-call tools are serialized for OpenAI/Anthropic.
- "Designing Web APIs" (Mike Amundsen) — REST conventions.

---

**End of Part 7.**

**Next:** [Part 8 — Frontend and UX](./part-08-frontend-ux.md).
