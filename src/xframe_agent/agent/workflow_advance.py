"""Per-tab workflow step advance evaluation and bundled tool decisions.

The wizard front-end calls ``POST /runs/{run_id}/step_advance`` whenever a user
clicks Next on a per-tab step. This module:

1. Loads the workflow contract for the conversation's draft.
2. Inspects the step's ``approval_mode`` and ``on_complete`` block.
3. Resolves the ``args_template`` against the persisted draft state (and any
   run-level state such as a previously-created quote id).
4. Persists ``AgentToolCall`` rows for each resolved tool call in the bundle
   and emits a single ``v1.workflow.step.advance_requested`` event.

The client then approves or rejects via ``POST /runs/{run_id}/step_decisions``.
Approval executes the bundled tool calls serially (writes never run in parallel)
and emits ``v1.workflow.step.advance_approved``. Rejection emits
``v1.workflow.step.advance_rejected`` with the user-supplied reason.

If the step is ``batch_at_submit`` or has no ``on_complete`` block, the
endpoint short-circuits to an immediate-approve outcome (no card surfaced).

Token grammar for ``args_template`` values (string forms only):
- ``{{field:<step>.<field_id>}}``  -> draft.payload[step][field_id]
- ``{{draft.<key>}}``               -> draft.payload[key] OR draft.payload.summary[key]
                                       OR runtime-resolved key (currently:
                                       ``created_quote_id`` from a recent
                                       create_quotation tool call on this run).
- Plain literal otherwise (number, bool, list, dict pass through unchanged).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from xframe_agent.agent.events import append_run_event
from xframe_agent.agent.suggestions import emit_suggestions
from xframe_agent.auth.jwt import AuthContext
from xframe_agent.models import (
    AgentRun,
    AgentRunStep,
    AgentToolCall,
    AgentWorkflowDraft,
)
from xframe_agent.models.agent import utc_now
from xframe_agent.observability.tracing import workflow_step_span
from xframe_agent.priceframe import PriceFrameClient
from xframe_agent.tools.registry import tool_registry

CONTRACT_DIR = Path(__file__).resolve().parent.parent / "workflows" / "contracts"

_FIELD_TOKEN = re.compile(r"^\{\{field:([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\}\}$")
_DRAFT_TOKEN = re.compile(r"^\{\{draft\.([a-zA-Z0-9_.]+)\}\}$")

RESOLVE_OK = "ok"
RESOLVE_MISSING_QUOTE = "missing_created_quote_id"
RESOLVE_UNKNOWN_TOKEN = "unknown_token"  # noqa: S105 - status code, not a secret


class StepAdvanceError(Exception):
    """Raised when a step advance cannot proceed (e.g. invalid step id)."""


@dataclass(slots=True)
class ResolvedToolCall:
    tool_name: str
    args: dict[str, Any]


@dataclass(slots=True)
class AdvanceDecision:
    """Outcome of evaluating a step advance request."""

    status: str  # "approved" | "requested" | "blocked"
    tool_calls: list[ResolvedToolCall]
    persisted_tool_call_ids: list[str]
    blocked_reason: str | None = None
    detail: dict[str, Any] | None = None


@lru_cache(maxsize=4)
def load_contract(contract_id: str, contract_version: str) -> dict[str, Any]:
    """Load a synced workflow contract JSON artifact.

    Cached because contracts are static at deploy time. ``StepAdvanceError`` is
    raised when the artifact is missing rather than surfaced as ``FileNotFound``
    so the API layer can map it to a clean 422.
    """

    filename = f"{contract_id}_{contract_version}.json"
    path = CONTRACT_DIR / filename
    if not path.exists():
        raise StepAdvanceError(f"Workflow contract not found: {contract_id}/{contract_version}")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def get_step(contract: Mapping[str, Any], step_id: str) -> Mapping[str, Any] | None:
    for step in contract.get("steps", []):
        if isinstance(step, Mapping) and step.get("id") == step_id:
            return step
    return None


async def latest_created_quote_id(
    session: AsyncSession,
    *,
    run_id: str,
    conversation_id: str | None = None,
) -> int | None:
    """Find the most recent successful ``create_quotation`` quote id.

    Looks first in this run, then across the conversation (so a quote created
    in a previous run within the same wizard session is still reachable).
    """

    stmt = (
        select(AgentToolCall)
        .where(
            AgentToolCall.tool_name == "create_quotation",
            AgentToolCall.status == "succeeded",
            AgentToolCall.run_id == run_id,
        )
        .order_by(desc(AgentToolCall.completed_at))
        .limit(1)
    )
    result = await session.execute(stmt)
    record = result.scalar_one_or_none()
    if record is None and conversation_id is not None:
        stmt = (
            select(AgentToolCall)
            .join(AgentRun, AgentRun.id == AgentToolCall.run_id)
            .where(
                AgentToolCall.tool_name == "create_quotation",
                AgentToolCall.status == "succeeded",
                AgentRun.conversation_id == conversation_id,
            )
            .order_by(desc(AgentToolCall.completed_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
    if record is None or record.result is None:
        return None
    return _quote_id_from_result(record.result, record.args)


def _quote_id_from_result(
    result: Mapping[str, Any] | None,
    args: Mapping[str, Any] | None,
) -> int | None:
    if isinstance(result, Mapping):
        data = result.get("data")
        if isinstance(data, Mapping):
            nested = data.get("data")
            if isinstance(nested, Mapping):
                candidate = nested.get("id")
                if isinstance(candidate, int):
                    return candidate
                if isinstance(candidate, str) and candidate.isdecimal():
                    return int(candidate)
            direct = data.get("id") or data.get("quoteId") or data.get("quote_id")
            if isinstance(direct, int):
                return direct
            if isinstance(direct, str) and direct.isdecimal():
                return int(direct)
    if isinstance(args, Mapping):
        candidate = args.get("quote_id") or args.get("quoteId")
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdecimal():
            return int(candidate)
    return None


def resolve_args_template(
    template: Mapping[str, Any],
    *,
    draft_payload: Mapping[str, Any],
    runtime_values: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Resolve ``{{field:...}}`` and ``{{draft....}}`` tokens in a template.

    Returns the resolved dict plus a list of unresolved tokens (so the caller
    can decide to block the advance).
    """

    unresolved: list[str] = []
    resolved: dict[str, Any] = {}
    for key, value in template.items():
        resolved[key], more_unresolved = _resolve_value(
            value,
            draft_payload=draft_payload,
            runtime_values=runtime_values,
        )
        unresolved.extend(more_unresolved)
    return resolved, unresolved


def _resolve_value(
    value: Any,
    *,
    draft_payload: Mapping[str, Any],
    runtime_values: Mapping[str, Any],
) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        field_match = _FIELD_TOKEN.match(value)
        if field_match is not None:
            step_id, field_id = field_match.group(1), field_match.group(2)
            section = draft_payload.get(step_id)
            if isinstance(section, Mapping) and field_id in section:
                return section.get(field_id), []
            return None, [value]
        draft_match = _DRAFT_TOKEN.match(value)
        if draft_match is not None:
            key = draft_match.group(1)
            if key in runtime_values:
                return runtime_values[key], []
            # Allow {{draft.summary}} or {{draft.<step>.<field>}}
            parts = key.split(".")
            cursor: Any = draft_payload
            for part in parts:
                if isinstance(cursor, Mapping) and part in cursor:
                    cursor = cursor[part]
                else:
                    cursor = None
                    break
            if cursor is not None:
                return cursor, []
            # Common helper: {{draft.created_quote_id}} when no quote yet
            return None, [value]
        return value, []
    if isinstance(value, Mapping):
        resolved_map: dict[str, Any] = {}
        unresolved: list[str] = []
        for inner_key, inner_value in value.items():
            resolved_map[inner_key], inner_unresolved = _resolve_value(
                inner_value,
                draft_payload=draft_payload,
                runtime_values=runtime_values,
            )
            unresolved.extend(inner_unresolved)
        return resolved_map, unresolved
    if isinstance(value, list):
        resolved_list: list[Any] = []
        unresolved = []
        for item in value:
            resolved_item, inner_unresolved = _resolve_value(
                item,
                draft_payload=draft_payload,
                runtime_values=runtime_values,
            )
            resolved_list.append(resolved_item)
            unresolved.extend(inner_unresolved)
        return resolved_list, unresolved
    return value, []


async def evaluate_step_advance(
    session: AsyncSession,
    *,
    run: AgentRun,
    context: AuthContext,
    step_id: str,
    draft: AgentWorkflowDraft | None,
) -> AdvanceDecision:
    """Resolve the bundled ToolDecision for one step click of Next.

    The caller is responsible for emitting SSE events; this function returns
    the decision so the API layer (or runner) can persist + announce it.
    """

    if draft is None:
        raise StepAdvanceError("No workflow draft is associated with this conversation")

    contract = load_contract(draft.contract_id, draft.contract_version)
    step = get_step(contract, step_id)
    if step is None:
        raise StepAdvanceError(f"Step '{step_id}' not in contract {draft.contract_id}")

    approval_mode = step.get("approval_mode")
    on_complete = step.get("on_complete") or None

    if approval_mode == "batch_at_submit":
        return AdvanceDecision(
            status="approved",
            tool_calls=[],
            persisted_tool_call_ids=[],
            detail={"reason": "batch_at_submit"},
        )

    if approval_mode != "per_tab":
        raise StepAdvanceError(
            f"Step '{step_id}' has unsupported approval_mode '{approval_mode}'"
        )

    if not on_complete:
        return AdvanceDecision(
            status="approved",
            tool_calls=[],
            persisted_tool_call_ids=[],
            detail={"reason": "no_on_complete"},
        )

    quote_id = await latest_created_quote_id(
        session,
        run_id=run.id,
        conversation_id=draft.conversation_id,
    )
    runtime_values: dict[str, Any] = {}
    if quote_id is not None:
        runtime_values["created_quote_id"] = quote_id

    template = on_complete.get("args_template") or {}
    resolved_args, unresolved = resolve_args_template(
        template,
        draft_payload=draft.payload or {},
        runtime_values=runtime_values,
    )

    if unresolved:
        # Special handling: missing created_quote_id is the canonical "approvals
        # step with no quote yet" failure mode. Surface a specific reason so the
        # client can show a helpful narration.
        reason = (
            RESOLVE_MISSING_QUOTE
            if any("draft.created_quote_id" in token for token in unresolved)
            else RESOLVE_UNKNOWN_TOKEN
        )
        return AdvanceDecision(
            status="blocked",
            tool_calls=[],
            persisted_tool_call_ids=[],
            blocked_reason=reason,
            detail={"unresolved_tokens": unresolved},
        )

    tool_name = on_complete.get("tool")
    if not isinstance(tool_name, str):
        raise StepAdvanceError(f"Step '{step_id}' on_complete has no tool name")

    tool = tool_registry.get(tool_name)
    if tool is None:
        return AdvanceDecision(
            status="blocked",
            tool_calls=[],
            persisted_tool_call_ids=[],
            blocked_reason="unknown_tool",
            detail={"tool": tool_name},
        )

    try:
        parsed = tool.input_model.model_validate(resolved_args)
    except ValueError as exc:
        return AdvanceDecision(
            status="blocked",
            tool_calls=[],
            persisted_tool_call_ids=[],
            blocked_reason="schema_validation_failed",
            detail={"tool": tool_name, "error": str(exc)},
        )

    dumped_args = parsed.model_dump(mode="json")
    requires_approval = await tool.requires_approval(parsed, context)

    # Persist as an AgentToolCall row (re-using the existing decision flow).
    # ``kind`` encodes the workflow step id so the step-decisions endpoint can
    # look up only the bundle that belongs to this step (and ignore any tool
    # calls the underlying model loop might have made).
    step_record = AgentRunStep(
        run_id=run.id,
        seq=0,  # workflow-driven, not from the model loop
        kind=f"workflow_step:{step_id}",
        status="awaiting_decision",
    )
    session.add(step_record)
    await session.flush()

    tool_call = AgentToolCall(
        run_id=run.id,
        step_id=step_record.id,
        tool_name=tool_name,
        status="proposed" if requires_approval else "pending",
        args=dumped_args,
        requires_approval=requires_approval,
    )
    session.add(tool_call)
    await session.flush()

    return AdvanceDecision(
        status="requested",
        tool_calls=[ResolvedToolCall(tool_name=tool_name, args=dumped_args)],
        persisted_tool_call_ids=[tool_call.id],
    )


async def emit_advance_event(
    session: AsyncSession,
    *,
    run: AgentRun,
    step_id: str,
    decision: AdvanceDecision,
) -> None:
    """Persist the appropriate SSE event for an :class:`AdvanceDecision`."""

    common_payload: dict[str, Any] = {
        "step_id": step_id,
        "status": decision.status,
    }
    if decision.persisted_tool_call_ids:
        common_payload["tool_call_ids"] = decision.persisted_tool_call_ids
    if decision.tool_calls:
        common_payload["tool_calls"] = [
            {"tool_name": call.tool_name, "args": call.args}
            for call in decision.tool_calls
        ]
    if decision.detail:
        common_payload["detail"] = decision.detail

    if decision.status == "requested":
        run.status = "awaiting_decision"
        run.updated_at = utc_now()
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.workflow.step.advance_requested",
            payload=common_payload,
        )
    elif decision.status == "approved":
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.workflow.step.advance_approved",
            payload=common_payload,
        )
    elif decision.status == "blocked":
        common_payload["reason"] = decision.blocked_reason
        await append_run_event(
            session,
            run_id=run.id,
            event_type="v1.workflow.step.advance_blocked",
            payload=common_payload,
        )


def next_step_after(
    contract: Mapping[str, Any],
    current_step_id: str,
) -> Mapping[str, Any] | None:
    """Return the step immediately following ``current_step_id`` in the contract.

    Uses the declared ``steps`` order — which is also the wizard's tab order —
    so per-tab advance always lands on the next tab the user will see. Returns
    ``None`` when the current step is the last one (e.g. ``approvals``), which
    signals "no successor to enter".
    """

    steps = contract.get("steps") if isinstance(contract, Mapping) else None
    if not isinstance(steps, list):
        return None
    found = False
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if found:
            return step
        if step.get("id") == current_step_id:
            found = True
    return None


def step_entered_payload(
    contract: Mapping[str, Any],
    step: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the ``v1.workflow.step.entered`` payload for an arbitrary step.

    Mirrors :func:`create_pricing_request_step_entered_payload` but works for any
    step in any contract, so per-tab advances can announce the next tab without
    duplicating the workflow-specific helper.
    """

    steps = contract.get("steps") if isinstance(contract, Mapping) else []
    total_steps = len(steps) if isinstance(steps, list) else 0
    step_id = step.get("id")
    step_index = 0
    if isinstance(steps, list):
        for index, candidate in enumerate(steps):
            if isinstance(candidate, Mapping) and candidate.get("id") == step_id:
                step_index = index
                break
    contract_id = contract.get("id") if isinstance(contract, Mapping) else None
    contract_version = contract.get("version") if isinstance(contract, Mapping) else None
    return {
        "workflow": contract_id,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "step_id": step_id,
        "step_index": step_index,
        "total_steps": total_steps,
    }


async def emit_step_entered_with_suggestions(
    session: AsyncSession,
    *,
    run_id: str,
    contract: Mapping[str, Any],
    step: Mapping[str, Any],
    draft_state: Mapping[str, Any] | None,
    auth_ctx: AuthContext,
    priceframe: PriceFrameClient | None,
    session_span: Any | None = None,
) -> None:
    """Emit ``v1.workflow.step.entered`` then fan out historical suggestions.

    Ordering is intentional: SSE consumers depend on receiving the ``entered``
    event before the per-field ``v1.suggestion.ready`` / ``v1.suggestion.no_signal``
    events for that step. The fan-out is best-effort — failures are swallowed so
    the workflow always advances.

    When ``session_span`` is supplied the per-step Langfuse span hierarchy is
    nested under the active ``agent.workflow_session`` so the suggestion
    fan-out shows up as a grandchild of the run trace (Phase 11 open item).
    """

    contract_id = (
        contract.get("id") if isinstance(contract, Mapping) else None
    ) or (contract.get("contract_id") if isinstance(contract, Mapping) else None)
    contract_version = (
        contract.get("version") if isinstance(contract, Mapping) else None
    ) or (contract.get("contract_version") if isinstance(contract, Mapping) else None)
    step_id_value: Any = step.get("id") if isinstance(step, Mapping) else None
    if step_id_value is not None and hasattr(step_id_value, "value"):
        step_id_value = step_id_value.value
    step_id_str = str(step_id_value) if step_id_value is not None else "unknown"

    await append_run_event(
        session,
        run_id=run_id,
        event_type="v1.workflow.step.entered",
        payload=step_entered_payload(contract, step),
    )
    with workflow_step_span(
        session_span,
        step_id=step_id_str,
        contract_id=str(contract_id) if contract_id is not None else None,
        contract_version=str(contract_version) if contract_version is not None else None,
    ) as step_span:
        try:
            await emit_suggestions(
                session,
                run_id=run_id,
                contract=contract,
                step=step,
                draft_state=draft_state,
                auth_ctx=auth_ctx,
                priceframe=priceframe,
                parent_span=step_span,
            )
        except Exception:  # noqa: BLE001 - suggestions are best-effort
            return


__all__ = [
    "AdvanceDecision",
    "ResolvedToolCall",
    "StepAdvanceError",
    "emit_advance_event",
    "emit_step_entered_with_suggestions",
    "evaluate_step_advance",
    "latest_created_quote_id",
    "load_contract",
    "next_step_after",
    "resolve_args_template",
    "step_entered_payload",
]
