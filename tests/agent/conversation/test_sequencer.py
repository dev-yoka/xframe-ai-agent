from xframe_agent.agent.conversation.sequencer import next_step, FieldPrompt, Recap, Done, advance_cursor
from xframe_agent.agent.conversation.cursor import Cursor

CONTRACT = {
    "id": "create_pricing_request", "version": "v1",
    "conversation": {"phases": [
        {"id": "identity", "field_ids": ["sending_partner_name", "opportunity_type"]},
        {"id": "money", "field_ids": ["default_fx_spread_percent"]},
    ]},
    "steps": [
        {"id": "summary", "fields": [
            {"id": "sending_partner_name", "label": "Partner", "type": "string", "required": True},
            {"id": "opportunity_type", "label": "Opportunity", "type": "enum", "required": True,
             "enum_options": [{"value": "new", "label": "New partner"}]},
        ]},
        {"id": "pricing", "fields": [
            {"id": "default_fx_spread_percent", "label": "FX", "type": "percentage",
             "required": True, "requires_explicit_confirm": True},
        ]},
    ],
}


def test_first_prompt_is_first_field():
    result = next_step(CONTRACT, {}, Cursor(0, 0))
    assert isinstance(result, FieldPrompt)
    assert result.field_id == "sending_partner_name"
    assert result.control == "text"
    assert result.requires_explicit_confirm is False


def test_enum_field_yields_chips_control():
    result = next_step(CONTRACT, {}, Cursor(0, 1))
    assert isinstance(result, FieldPrompt)
    assert result.field_id == "opportunity_type"
    assert result.control == "single_select"
    assert result.options == [{"value": "new", "label": "New partner"}]


def test_money_field_flagged_for_confirm():
    result = next_step(CONTRACT, {}, Cursor(1, 0))
    assert isinstance(result, FieldPrompt)
    assert result.field_id == "default_fx_spread_percent"
    assert result.requires_explicit_confirm is True


def test_recap_after_last_field():
    assert isinstance(next_step(CONTRACT, {}, Cursor(2, 0)), Recap)


def test_advance_cursor_moves_within_then_across_phases():
    assert advance_cursor(CONTRACT, Cursor(0, 0)) == Cursor(0, 1)
    assert advance_cursor(CONTRACT, Cursor(0, 1)) == Cursor(1, 0)
    assert advance_cursor(CONTRACT, Cursor(1, 0)) == Cursor(2, 0)
