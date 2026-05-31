import pytest
from xframe_agent.agent.conversation.assist import interpret_freeform


@pytest.mark.asyncio
async def test_interpret_returns_set_field(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm(prompt: str, **kw: object) -> str:
        return '{"action": "set_field", "field_id": "sending_partner_name", "value": "Acme"}'

    monkeypatch.setattr("xframe_agent.agent.conversation.assist._call_llm", fake_llm)
    action = await interpret_freeform(
        "the partner is Acme", current_field_id="sending_partner_name", contract={}
    )
    assert action.action == "set_field"
    assert action.value == "Acme"


@pytest.mark.asyncio
async def test_interpret_falls_back_to_explain_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(prompt: str, **kw: object) -> str:
        raise RuntimeError("budget exceeded")

    monkeypatch.setattr("xframe_agent.agent.conversation.assist._call_llm", boom)
    action = await interpret_freeform("why?", current_field_id="fx", contract={})
    assert action.action == "explain"
    assert action.degraded is True
