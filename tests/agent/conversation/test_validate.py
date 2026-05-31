import pytest
from xframe_agent.agent.conversation.validate import validate_answer, ValidationError

STRING_SPEC = {"id": "name", "type": "string", "required": True, "validation": {"pattern": "^.{3,120}$"}}
ENUM_SPEC = {"id": "opp", "type": "enum", "required": True, "enum_options": [{"value": "new", "label": "New"}]}
PCT_SPEC = {"id": "fx", "type": "percentage", "required": True, "validation": {"min": 0, "max": 100, "decimals": 2}}


def test_valid_string_passes():
    assert validate_answer(STRING_SPEC, "Acme Remit") == "Acme Remit"


def test_string_pattern_violation_raises():
    with pytest.raises(ValidationError):
        validate_answer(STRING_SPEC, "ab")


def test_enum_rejects_unknown_value():
    with pytest.raises(ValidationError):
        validate_answer(ENUM_SPEC, "unknown")


def test_percentage_out_of_range_raises():
    with pytest.raises(ValidationError):
        validate_answer(PCT_SPEC, 150)


def test_required_empty_raises():
    with pytest.raises(ValidationError):
        validate_answer(STRING_SPEC, "")
