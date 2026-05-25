"""Langfuse span hierarchy for the M2 wizard runtime (Phase 10).

The wizard emits a four-level trace per conversation:

    agent.workflow_session              (root, one per run)
    └─ agent.workflow_step              (one per step entered, tagged step_id)
       ├─ agent.suggestion_fanout       (one per fan-out, tagged step_id)
       │  └─ agent.tool                 (one per tool call, tagged tool_name +
       │                                 step_id + field_id)
       └─ agent.tool                    (model loop tool calls)

The wrappers are *no-ops* when the Langfuse client is not configured (no API
key set, or the optional dependency isn't installed). This lets development
and CI runs stay fast while production still gets a full trace.

Implementation notes:

* Each context manager swallows any client error so an instrumentation bug
  can never break the workflow. Failures are logged at WARNING level.
* ``span()`` returns a no-op object that exposes ``update`` / ``end`` /
  ``span`` so callers can chain ``with`` without checking for ``None``.
* Callers should treat the wrappers as best-effort — they pass span data
  via kwargs and the wrapper picks the keys Langfuse understands.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


_SESSION_NAME = "agent.workflow_session"
_STEP_NAME = "agent.workflow_step"
_FANOUT_NAME = "agent.suggestion_fanout"
_TOOL_NAME = "agent.tool"


class _NoOpSpan:
    """Stand-in span used when Langfuse is unavailable.

    Exposes the small surface area the callers depend on (``update``,
    ``end``, ``span``) so the rest of the codebase can stay symmetric.
    """

    __slots__ = ()

    def update(self, **kwargs: Any) -> None:  # noqa: D401 - context shim
        return None

    def end(self, **kwargs: Any) -> None:
        return None

    def span(self, **kwargs: Any) -> _NoOpSpan:
        return self


def _client() -> Any | None:
    """Return the lazily-initialised Langfuse client or ``None``."""

    try:
        from xframe_agent.settings import get_settings

        settings = get_settings()
        if not getattr(settings, "langfuse_configured", False):
            return None
        from xframe_agent.observability.langfuse import get_langfuse_client

        return get_langfuse_client(settings)
    except Exception as exc:  # noqa: BLE001 - tracing is best-effort
        logger.debug("langfuse client unavailable: %s", exc)
        return None


@contextmanager
def workflow_session_span(
    *,
    run_id: str,
    conversation_id: str | None = None,
    user_id: int | str | None = None,
    contract_id: str | None = None,
    contract_version: str | None = None,
) -> Iterator[Any]:
    """Open the root span for one wizard run.

    Yields a span-like object that supports ``.span(...)`` for nested
    children, plus ``.update(...)`` for late metadata. Yields a no-op when
    Langfuse isn't configured.
    """

    client = _client()
    if client is None:
        yield _NoOpSpan()
        return
    metadata: dict[str, Any] = {"run_id": run_id}
    if conversation_id is not None:
        metadata["conversation_id"] = conversation_id
    if contract_id is not None:
        metadata["contract_id"] = contract_id
    if contract_version is not None:
        metadata["contract_version"] = contract_version
    try:
        trace = client.trace(
            name=_SESSION_NAME,
            user_id=str(user_id) if user_id is not None else None,
            session_id=conversation_id,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 - tracing is best-effort
        logger.warning("langfuse trace create failed: %s", exc)
        yield _NoOpSpan()
        return
    try:
        yield trace
    finally:
        try:
            trace.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse trace end failed: %s", exc)


@contextmanager
def workflow_step_span(
    parent: Any | None,
    *,
    step_id: str,
    contract_id: str | None = None,
    contract_version: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Open a child span for one workflow step."""

    if parent is None:
        yield _NoOpSpan()
        return
    payload_metadata: dict[str, Any] = {"step_id": step_id}
    if contract_id is not None:
        payload_metadata["contract_id"] = contract_id
    if contract_version is not None:
        payload_metadata["contract_version"] = contract_version
    if metadata:
        payload_metadata.update(metadata)
    try:
        span = parent.span(name=_STEP_NAME, metadata=payload_metadata)
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse step span failed: %s", exc)
        yield _NoOpSpan()
        return
    try:
        yield span
    finally:
        try:
            span.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse step span end failed: %s", exc)


@contextmanager
def suggestion_fanout_span(
    parent: Any | None,
    *,
    step_id: str,
    field_count: int | None = None,
) -> Iterator[Any]:
    """Open a child span for a suggestion fan-out (historical + market)."""

    if parent is None:
        yield _NoOpSpan()
        return
    metadata: dict[str, Any] = {"step_id": step_id}
    if field_count is not None:
        metadata["field_count"] = field_count
    try:
        span = parent.span(name=_FANOUT_NAME, metadata=metadata)
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse fanout span failed: %s", exc)
        yield _NoOpSpan()
        return
    try:
        yield span
    finally:
        try:
            span.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse fanout span end failed: %s", exc)


@contextmanager
def tool_span(
    parent: Any | None,
    *,
    tool_name: str,
    step_id: str | None = None,
    field_id: str | None = None,
    input_summary: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Open a leaf span for one tool call."""

    if parent is None:
        yield _NoOpSpan()
        return
    metadata: dict[str, Any] = {"tool_name": tool_name}
    if step_id is not None:
        metadata["step_id"] = step_id
    if field_id is not None:
        metadata["field_id"] = field_id
    if input_summary is not None:
        metadata["input"] = dict(input_summary)
    try:
        span = parent.span(name=_TOOL_NAME, metadata=metadata)
    except Exception as exc:  # noqa: BLE001
        logger.debug("langfuse tool span failed: %s", exc)
        yield _NoOpSpan()
        return
    try:
        yield span
    finally:
        try:
            span.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse tool span end failed: %s", exc)


__all__ = [
    "suggestion_fanout_span",
    "tool_span",
    "workflow_session_span",
    "workflow_step_span",
]
