import pytest
from xframe_agent.agent.conversation.recap import build_recap, commit_draft

CONTRACT = {
    "id": "create_pricing_request", "version": "v1",
    "conversation": {"phases": [
        {"id": "identity", "field_ids": ["sending_partner_name", "opportunity_type",
                                         "corridor_regions", "corridor_countries"]}]},
    "steps": [{"id": "summary", "fields": [
        {"id": "sending_partner_name", "label": "Partner", "type": "string", "required": True},
        {"id": "opportunity_type", "label": "Opp", "type": "enum", "required": True,
         "enum_options": [{"value": "new", "label": "New"}]},
        {"id": "default_fee_currency", "label": "Cur", "type": "enum", "required": True},
        {"id": "corridor_regions", "label": "Regions", "type": "multi_enum", "required": False},
        {"id": "corridor_countries", "label": "Countries", "type": "multi_enum", "required": False},
    ]}],
}

def test_build_recap_partitions_collected_vs_defaulted():
    draft = {"summary": {"sending_partner_name": "Acme", "opportunity_type": "new"}}
    collected, defaulted = build_recap(CONTRACT, draft)
    assert collected["sending_partner_name"] == "Acme"
    assert "sending_partner_name" not in defaulted

@pytest.mark.asyncio
async def test_commit_draft_creates_quote_without_submitting(monkeypatch):
    calls = []
    class FakeTool:
        def __init__(self, name): self.name = name
        async def execute(self, args, auth, pf):
            calls.append(self.name)
            class R: data = {"quote_id": "q-123"}
            return R()
    monkeypatch.setattr("xframe_agent.agent.conversation.recap._create_tool", lambda: FakeTool("create_quotation"))
    monkeypatch.setattr("xframe_agent.agent.conversation.recap._corridors_tool", lambda: FakeTool("bulk_add_corridors"))
    result = await commit_draft(CONTRACT, {"summary": {"sending_partner_name": "Acme", "opportunity_type": "new"}},
                                auth_ctx=object(), priceframe=object())
    assert result["quote_id"] == "q-123"
    assert "submit_for_approval" not in calls
    assert "create_quotation" in calls


@pytest.mark.asyncio
async def test_commit_draft_when_corridor_resolution_returns_empty(monkeypatch):
    """When corridor lookup returns no matches, bulk_add_corridors is skipped."""
    calls = []

    class FakeCreateTool:
        async def execute(self, args, auth, pf):
            calls.append("create_quotation")
            class R:
                data = {"quote_id": "q-empty-corridors"}
            return R()

    class FakeLookupTool:
        async def execute(self, args, auth, pf):
            calls.append("lookup_corridors")
            # Return empty corridor list — nothing matches the user's selection.
            class R:
                data = {"corridors": []}
            return R()

    class FakePF:
        """Fake PriceFrameClient that returns an empty corridor list."""
        async def get_json(self, path: str, *, jwt_raw: str = "", **kw: object) -> list[object]:
            return []  # no corridors → resolution returns []

    monkeypatch.setattr(
        "xframe_agent.agent.conversation.recap._create_tool",
        lambda: FakeCreateTool(),
    )

    draft = {
        "summary": {
            "sending_partner_name": "Acme",
            "opportunity_type": "new",
            "corridor_regions": ["EMEA"],
            "corridor_countries": ["Nigeria"],  # present but lookup returns no matches
        }
    }
    result = await commit_draft(CONTRACT, draft, auth_ctx=object(), priceframe=FakePF())

    assert result["quote_id"] == "q-empty-corridors"
    assert "create_quotation" in result["applied"]
    # bulk_add_corridors must be skipped entirely when corridor resolution yields []
    assert "bulk_add_corridors" not in result["applied"]
    # No failure should be recorded — empty resolution is a clean no-op, not an error
    assert result["failed"] == []
