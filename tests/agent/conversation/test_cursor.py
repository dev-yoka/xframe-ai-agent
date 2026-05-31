from xframe_agent.agent.conversation.cursor import get_cursor, set_cursor, Cursor


def test_get_cursor_defaults_to_zero():
    assert get_cursor({}) == Cursor(phase_index=0, field_index=0)


def test_set_then_get_roundtrips():
    payload = {"summary": {"sending_partner_name": "Acme"}}
    updated = set_cursor(payload, Cursor(phase_index=1, field_index=2))
    assert get_cursor(updated) == Cursor(phase_index=1, field_index=2)
    assert updated["summary"]["sending_partner_name"] == "Acme"


def test_set_cursor_is_pure():
    payload = {}
    set_cursor(payload, Cursor(1, 1))
    assert payload == {}
