import pytest
from xframe_agent.agent.conversation.recap import build_recap, commit_draft

CONTRACT = {
    "id": "create_pricing_request", "version": "v1",
    "conversation": {"phases": [
        {"id": "identity", "field_ids": ["sending_partner_name", "opportunity_type"]}]},
    "steps": [{"id": "summary", "fields": [
        {"id": "sending_partner_name", "label": "Partner", "type": "string", "required": True},
        {"id": "opportunity_type", "label": "Opp", "type": "enum", "required": True,
         "enum_options": [{"value": "new", "label": "New"}]},
        {"id": "default_fee_currency", "label": "Cur", "type": "enum", "required": True},
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
