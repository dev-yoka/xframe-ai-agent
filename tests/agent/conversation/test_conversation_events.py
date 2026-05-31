from xframe_agent.agent.conversation.events import (
    EVENT_FIELD_PROMPT,
    EVENT_FIELD_ACCEPTED,
    EVENT_CONVERSATION_RECAP,
    EVENT_CONVERSATION_COMMITTED,
    field_prompt_payload,
    recap_payload,
)
from xframe_agent.agent.conversation.sequencer import FieldPrompt


def test_event_type_constants():
    assert EVENT_FIELD_PROMPT == "v1.field.prompt"
    assert EVENT_FIELD_ACCEPTED == "v1.field.accepted"
    assert EVENT_CONVERSATION_RECAP == "v1.conversation.recap"
    assert EVENT_CONVERSATION_COMMITTED == "v1.conversation.committed"


def test_field_prompt_payload_shape():
    prompt = FieldPrompt(
        phase_id="identity",
        field_id="name",
        label="Partner",
        type="string",
        control="text",
        required=True,
        requires_explicit_confirm=False,
    )
    payload = field_prompt_payload(prompt, suggestion=None)
    assert payload["field_id"] == "name"
    assert payload["control"] == "text"
    assert payload["requires_explicit_confirm"] is False
    assert payload["suggestion"] is None


def test_field_prompt_payload_includes_suggestion():
    prompt = FieldPrompt(
        phase_id="money",
        field_id="fx",
        label="FX",
        type="percentage",
        control="percentage",
        required=True,
        requires_explicit_confirm=True,
    )
    payload = field_prompt_payload(prompt, suggestion={"value": 1.5, "basis": "median of 12"})
    assert payload["suggestion"]["value"] == 1.5


def test_recap_payload_partitions_collected_and_defaulted():
    payload = recap_payload(collected={"name": "Acme"}, defaulted={"currency": "USD"})
    assert payload["collected"] == {"name": "Acme"}
    assert payload["defaulted"] == {"currency": "USD"}
