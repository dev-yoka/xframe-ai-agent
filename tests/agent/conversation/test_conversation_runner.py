import pytest
from xframe_agent.agent.conversation.runner import emit_next_prompt, _resolve_options
from xframe_agent.agent.conversation.cursor import Cursor
from xframe_agent.agent.conversation.sequencer import FieldPrompt

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


# ---------------------------------------------------------------------------
# _resolve_options unit tests
# ---------------------------------------------------------------------------

def _make_region_prompt(options=None, options_source=None):
    return FieldPrompt(
        phase_id="identity",
        field_id="corridor_regions",
        label="Region",
        type="multi_enum",
        control="multi_select",
        required=True,
        requires_explicit_confirm=False,
        options=options,
        options_source=options_source or {
            "endpoint": "/api/v1/corridors/active/filters",
            "value_path": "geographicalRegions",
        },
    )


def _make_country_prompt():
    return FieldPrompt(
        phase_id="identity",
        field_id="corridor_countries",
        label="Country",
        type="multi_enum",
        control="multi_select",
        required=True,
        requires_explicit_confirm=False,
        options=None,
        options_source={
            "endpoint": "/api/v1/corridors/active/filters",
            "value_path": "countries",
            "depends_on": ["corridor_regions"],
        },
    )


class _FakePF:
    async def get_json(self, endpoint, *, jwt_raw, params=None):
        return {
            "geographicalRegions": ["Africa", "Europe"],
            "countriesByRegion": {
                "Africa": ["Nigeria", "Ghana"],
                "Europe": ["UK", "France"],
            },
            "countries": ["Nigeria", "Ghana", "UK", "France"],
        }


class _FakeAuthCtx:
    jwt_raw = "fake.jwt.token"


@pytest.mark.asyncio
async def test_resolve_options_returns_regions():
    prompt = _make_region_prompt()
    result = await _resolve_options(prompt, {}, _FakeAuthCtx(), _FakePF())
    assert result == [{"value": "Africa", "label": "Africa"}, {"value": "Europe", "label": "Europe"}]


@pytest.mark.asyncio
async def test_resolve_options_filters_countries_by_region():
    prompt = _make_country_prompt()
    draft = {"summary": {"corridor_regions": ["Africa"]}}
    result = await _resolve_options(prompt, draft, _FakeAuthCtx(), _FakePF())
    assert result == [{"value": "Nigeria", "label": "Nigeria"}, {"value": "Ghana", "label": "Ghana"}]


@pytest.mark.asyncio
async def test_resolve_options_returns_none_when_priceframe_is_none():
    prompt = _make_region_prompt()
    result = await _resolve_options(prompt, {}, _FakeAuthCtx(), None)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_options_returns_none_when_auth_ctx_is_none():
    prompt = _make_region_prompt()
    result = await _resolve_options(prompt, {}, None, _FakePF())
    assert result is None


@pytest.mark.asyncio
async def test_resolve_options_returns_none_when_options_already_set():
    prompt = _make_region_prompt(options=[{"value": "APAC", "label": "APAC"}])
    result = await _resolve_options(prompt, {}, _FakeAuthCtx(), _FakePF())
    assert result is None


@pytest.mark.asyncio
async def test_resolve_options_returns_none_on_exception():
    class _BrokenPF:
        async def get_json(self, endpoint, *, jwt_raw, params=None):
            raise RuntimeError("network error")

    prompt = _make_region_prompt()
    result = await _resolve_options(prompt, {}, _FakeAuthCtx(), _BrokenPF())
    assert result is None


@pytest.mark.asyncio
async def test_resolve_options_countries_without_region_filter_returns_all():
    """When no regions are selected yet, return all countries."""
    prompt = _make_country_prompt()
    draft = {"summary": {"corridor_regions": []}}  # no regions selected
    result = await _resolve_options(prompt, draft, _FakeAuthCtx(), _FakePF())
    assert result == [
        {"value": "Nigeria", "label": "Nigeria"},
        {"value": "Ghana", "label": "Ghana"},
        {"value": "UK", "label": "UK"},
        {"value": "France", "label": "France"},
    ]
