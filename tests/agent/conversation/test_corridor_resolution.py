"""Commit-time corridor id resolution from selected region/countries."""

from __future__ import annotations

from typing import Any

import pytest

from xframe_agent.agent.conversation.recap import commit_draft

CONTRACT: dict[str, Any] = {
    "id": "create_pricing_request",
    "version": "v1",
    "conversation": {
        "phases": [
            {
                "id": "identity",
                "field_ids": [
                    "sending_partner_name",
                    "opportunity_type",
                    "corridor_regions",
                    "corridor_countries",
                ],
            }
        ]
    },
    "steps": [
        {
            "id": "summary",
            "fields": [
                {"id": "sending_partner_name", "type": "string", "required": True},
                {"id": "opportunity_type", "type": "enum", "required": True},
                {"id": "corridor_regions", "type": "multi_enum", "required": True},
                {"id": "corridor_countries", "type": "multi_enum", "required": True},
            ],
        }
    ],
}

ACTIVE_CORRIDORS = [
    {"id": 11, "identity": {"region": "APAC", "country": "India"}},
    {"id": 12, "identity": {"region": "APAC", "country": "Philippines"}},
    {"id": 13, "identity": {"region": "EMEA", "country": "Nigeria"}},
    {"id": 14, "identity": {"region": "APAC", "country": "India"}},  # dup country, distinct id
]


class _FakeResult:
    def __init__(self, data: Any) -> None:
        self.data = data


def _draft(**overrides: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "sending_partner_name": "Acme",
        "opportunity_type": "new",
        "corridor_regions": ["APAC"],
        "corridor_countries": ["India"],
    }
    section.update(overrides)
    return {"summary": section}


@pytest.mark.asyncio
async def test_selecting_countries_resolves_ids_and_bulk_adds(monkeypatch: Any) -> None:
    create_calls: list[Any] = []
    bulk_calls: list[Any] = []
    lookup_calls: list[Any] = []

    class FakeCreate:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            create_calls.append(args)
            return _FakeResult({"quote_id": 101})

    class FakeBulk:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            bulk_calls.append(args)
            return _FakeResult({"ok": True})

    class FakeLookup:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            lookup_calls.append(args)
            return _FakeResult({"corridors": ACTIVE_CORRIDORS})

    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._create_tool", lambda: FakeCreate()
    )
    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._corridors_tool", lambda: FakeBulk()
    )
    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._corridor_lookup_tool", lambda: FakeLookup()
    )

    result = await commit_draft(
        CONTRACT, _draft(), auth_ctx=object(), priceframe=object()
    )

    assert result["quote_id"] == 101
    assert result["failed"] == []
    assert "create_quotation" in result["applied"]
    assert "bulk_add_corridors" in result["applied"]
    assert len(lookup_calls) == 1
    # India in APAC -> corridor ids 11 and 14 (12 is Philippines, 13 is EMEA).
    assert len(bulk_calls) == 1
    resolved_ids = sorted(c.corridor_id for c in bulk_calls[0].corridors)
    assert resolved_ids == [11, 14]


@pytest.mark.asyncio
async def test_optional_filters_narrow_resolution(monkeypatch: Any) -> None:
    corridors = [
        {"id": 21, "identity": {"region": "APAC", "country": "India", "service": "Push"}},
        {"id": 22, "identity": {"region": "APAC", "country": "India", "service": "Pull"}},
    ]
    bulk_calls: list[Any] = []

    class FakeCreate:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            return _FakeResult({"quote_id": 102})

    class FakeBulk:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            bulk_calls.append(args)
            return _FakeResult({"ok": True})

    class FakeLookup:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            return _FakeResult(corridors)

    monkeypatch.setattr("xframe_agent.agent.conversation.recap._create_tool", lambda: FakeCreate())
    monkeypatch.setattr("xframe_agent.agent.conversation.recap._corridors_tool", lambda: FakeBulk())
    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._corridor_lookup_tool", lambda: FakeLookup()
    )

    result = await commit_draft(
        CONTRACT,
        _draft(corridor_services=["Push"]),
        auth_ctx=object(),
        priceframe=object(),
    )

    assert result["failed"] == []
    assert [c.corridor_id for c in bulk_calls[0].corridors] == [21]


@pytest.mark.asyncio
async def test_lookup_failure_records_failed_but_keeps_quote(monkeypatch: Any) -> None:
    bulk_calls: list[Any] = []

    class FakeCreate:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            return _FakeResult({"quote_id": 103})

    class FakeBulk:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            bulk_calls.append(args)
            return _FakeResult({"ok": True})

    class FakeLookup:
        async def execute(self, args: Any, auth: Any, pf: Any) -> _FakeResult:
            raise RuntimeError("corridor lookup boom")

    monkeypatch.setattr("xframe_agent.agent.conversation.recap._create_tool", lambda: FakeCreate())
    monkeypatch.setattr("xframe_agent.agent.conversation.recap._corridors_tool", lambda: FakeBulk())
    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._corridor_lookup_tool", lambda: FakeLookup()
    )

    result = await commit_draft(
        CONTRACT, _draft(), auth_ctx=object(), priceframe=object()
    )

    assert result["quote_id"] == 103
    assert "create_quotation" in result["applied"]
    assert "bulk_add_corridors" not in result["applied"]
    assert bulk_calls == []
    assert any(f["step"] == "resolve_corridor_ids" for f in result["failed"])
