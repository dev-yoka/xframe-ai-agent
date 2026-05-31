"""Conversation cursor stored inside AgentWorkflowDraft.payload['_meta'].

No schema migration: the cursor rides in the existing JSON payload under a
reserved ``_meta`` key so field data (keyed by step id) is never shadowed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

_META_KEY = "_meta"
_CURSOR_KEY = "conversation_cursor"


@dataclass(frozen=True)
class Cursor:
    phase_index: int = 0
    field_index: int = 0


def get_cursor(payload: dict[str, Any] | None) -> Cursor:
    if not payload:
        return Cursor()
    raw = (payload.get(_META_KEY) or {}).get(_CURSOR_KEY) or {}
    return Cursor(
        phase_index=int(raw.get("phase_index", 0)),
        field_index=int(raw.get("field_index", 0)),
    )


def set_cursor(payload: dict[str, Any] | None, cursor: Cursor) -> dict[str, Any]:
    updated = copy.deepcopy(payload) if payload else {}
    meta = dict(updated.get(_META_KEY) or {})
    meta[_CURSOR_KEY] = {
        "phase_index": cursor.phase_index,
        "field_index": cursor.field_index,
    }
    updated[_META_KEY] = meta
    return updated
