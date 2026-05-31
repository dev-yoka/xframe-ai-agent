import pytest
from xframe_agent.agent.conversation.runner import emit_next_prompt
from xframe_agent.agent.conversation.cursor import Cursor

CONTRACT = {
    "id": "create_pricing_request", "version": "v1",
    "conversation": {"phases": [{"id": "identity", "field_ids": ["sending_partner_name"]}]},
    "steps": [{"id": "summary", "fields": [
        {"id": "sending_partner_name", "label": "Partner", "type": "string", "required": True}]}],
}

class FakeSession:
    def __init__(self): self.events = []

@pytest.mark.asyncio
async def test_emit_next_prompt_emits_field_prompt(monkeypatch):
    captured = []
    async def fake_append(session, *, run_id, event_type, payload):
        captured.append((event_type, payload))
    monkeypatch.setattr("xframe_agent.agent.conversation.runner.append_run_event", fake_append)
    result = await emit_next_prompt(FakeSession(), run_id="r1", contract=CONTRACT, draft_payload={},
                                    cursor=Cursor(0, 0), auth_ctx=None, priceframe=None)
    assert captured[0][0] == "v1.field.prompt"
    assert captured[0][1]["field_id"] == "sending_partner_name"
    assert result.kind == "prompt"

@pytest.mark.asyncio
async def test_emit_next_prompt_emits_recap_when_done(monkeypatch):
    captured = []
    async def fake_append(session, *, run_id, event_type, payload):
        captured.append((event_type, payload))
    monkeypatch.setattr("xframe_agent.agent.conversation.runner.append_run_event", fake_append)
    result = await emit_next_prompt(FakeSession(), run_id="r1", contract=CONTRACT,
                                    draft_payload={"summary": {"sending_partner_name": "Acme"}},
                                    cursor=Cursor(1, 0), auth_ctx=None, priceframe=None)
    assert captured[0][0] == "v1.conversation.recap"
    assert result.kind == "recap"
