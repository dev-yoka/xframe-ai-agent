from xframe_agent.agent.conversation.start import init_conversation_draft_payload, should_start_conversation
from xframe_agent.agent.conversation.cursor import Cursor, get_cursor


def test_should_start_when_flag_on() -> None:
    assert should_start_conversation(intent="create_pricing_request", flags={"conversation": True}) is True


def test_should_not_start_when_flag_off() -> None:
    assert should_start_conversation(intent="create_pricing_request", flags={"conversation": False}) is False


def test_should_not_start_for_other_intent() -> None:
    assert should_start_conversation(intent="something_else", flags={"conversation": True}) is False


def test_init_payload_sets_cursor_zero() -> None:
    assert get_cursor(init_conversation_draft_payload()) == Cursor(0, 0)
