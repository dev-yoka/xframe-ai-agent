"""Pure, deterministic next-prompt computation for the guided conversation.

Given (contract, draft_payload, cursor) -> FieldPrompt | Recap | Done.
No I/O, no LLM. Drives the lean happy path defined by contract['conversation'].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xframe_agent.agent.conversation.cursor import Cursor

_CONTROL_BY_TYPE = {
    "string": "text",
    "number": "number",
    "currency": "currency",
    "percentage": "percentage",
    "enum": "single_select",
    "multi_enum": "multi_select",
    "boolean": "boolean",
    "date": "date",
}


@dataclass(frozen=True)
class FieldPrompt:
    phase_id: str
    field_id: str
    label: str
    type: str
    control: str
    required: bool
    requires_explicit_confirm: bool
    options: list[dict[str, str]] | None = None
    options_source: dict[str, Any] | None = None
    ui: dict[str, Any] | None = None


@dataclass(frozen=True)
class Recap:
    pass


@dataclass(frozen=True)
class Done:
    pass


def _phases(contract: dict[str, Any]) -> list[dict[str, Any]]:
    return list((contract.get("conversation") or {}).get("phases") or [])


def _field_index(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for step in contract.get("steps") or []:
        for f in step.get("fields") or []:
            if isinstance(f.get("id"), str):
                out[f["id"]] = f
    return out


def next_step(
    contract: dict[str, Any],
    draft_payload: dict[str, Any],
    cursor: Cursor,
) -> FieldPrompt | Recap | Done:
    phases = _phases(contract)
    if cursor.phase_index >= len(phases):
        return Recap()
    phase = phases[cursor.phase_index]
    field_ids = phase.get("field_ids") or []
    if cursor.field_index >= len(field_ids):
        return Recap()
    field_id = field_ids[cursor.field_index]
    spec = _field_index(contract).get(field_id)
    if spec is None:
        return Recap()
    return FieldPrompt(
        phase_id=phase.get("id", ""),
        field_id=field_id,
        label=spec.get("label", field_id),
        type=spec.get("type", "string"),
        control=_CONTROL_BY_TYPE.get(spec.get("type", "string"), "text"),
        required=bool(spec.get("required", False)),
        requires_explicit_confirm=bool(spec.get("requires_explicit_confirm", False)),
        options=spec.get("enum_options"),
        options_source=spec.get("options_source"),
        ui=spec.get("ui"),
    )


def advance_cursor(contract: dict[str, Any], cursor: Cursor) -> Cursor:
    from xframe_agent.agent.conversation.cursor import Cursor as _Cursor

    phases = _phases(contract)
    if cursor.phase_index >= len(phases):
        return cursor
    field_ids = phases[cursor.phase_index].get("field_ids") or []
    if cursor.field_index + 1 < len(field_ids):
        return _Cursor(cursor.phase_index, cursor.field_index + 1)
    return _Cursor(cursor.phase_index + 1, 0)
