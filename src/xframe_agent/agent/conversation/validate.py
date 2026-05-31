"""Validate a single conversational answer against its contract FieldSpec."""
from __future__ import annotations

import re
from typing import Any


class ValidationError(ValueError):
    """Raised when an answer violates its FieldSpec; message is user-facing."""


def validate_answer(spec: dict[str, Any], value: Any) -> Any:
    ftype = spec.get("type", "string")
    required = bool(spec.get("required", False))
    validation = spec.get("validation") or {}

    if value in (None, "", []):
        if required:
            raise ValidationError(f"{spec.get('label', spec.get('id'))} is required")
        return value

    if ftype == "enum":
        allowed = {o["value"] for o in (spec.get("enum_options") or [])}
        if allowed and value not in allowed:
            raise ValidationError(f"'{value}' is not a valid option")
        return value

    if ftype == "multi_enum":
        allowed = {o["value"] for o in (spec.get("enum_options") or [])}
        values = value if isinstance(value, list) else [value]
        if allowed:
            bad = [v for v in values if v not in allowed]
            if bad:
                raise ValidationError(f"invalid options: {bad}")
        return values

    if ftype in ("number", "currency", "percentage"):
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError("expected a number") from exc
        if "min" in validation and num < validation["min"]:
            raise ValidationError(f"must be ≥ {validation['min']}")
        if "max" in validation and num > validation["max"]:
            raise ValidationError(f"must be ≤ {validation['max']}")
        return num

    if ftype == "string":
        text = str(value)
        pattern = validation.get("pattern")
        if pattern and not re.match(pattern, text):
            raise ValidationError("value does not match the required format")
        return text

    return value
