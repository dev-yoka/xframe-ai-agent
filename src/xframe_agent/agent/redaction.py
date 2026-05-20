"""Pre-flight PII redaction before payloads reach a provider.

Replaces emails, phone numbers, and (optionally) known sensitive markers with
placeholders such as ``<PII:email>`` so the model never sees raw values. The
returned :class:`RedactedText` carries an audit-friendly list of substitutions
that can be persisted on ``agent_messages.redactions_json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(\+?\d{1,3}[ .-]?)?(\(?\d{2,4}\)?[ .-]?)?\d{3,4}[ .-]?\d{3,4}(?!\w)"
)
_MFA_RE = re.compile(r"\b\d{6}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(slots=True)
class Redaction:
    kind: str
    original: str
    start: int
    end: int


@dataclass(slots=True)
class RedactedText:
    text: str
    redactions: list[Redaction] = field(default_factory=list)

    def to_audit(self) -> list[dict[str, object]]:
        return [
            {"kind": r.kind, "start": r.start, "end": r.end, "len": len(r.original)}
            for r in self.redactions
        ]


def redact(text: str) -> RedactedText:
    """Redact known PII patterns from ``text``.

    The order is deliberate: card numbers first (most specific), then emails,
    then phone numbers, then bare 6-digit codes. Control characters are stripped
    last so they cannot escape a delimited tool-output block downstream.
    """

    redactions: list[Redaction] = []

    def _sub(pattern: re.Pattern[str], placeholder: str, kind: str, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            original = match.group(0)
            redactions.append(
                Redaction(
                    kind=kind,
                    original=original,
                    start=match.start(),
                    end=match.end(),
                )
            )
            return placeholder

        return pattern.sub(replace, value)

    result = text
    result = _sub(_CARD_RE, "<PII:card>", "card", result)
    result = _sub(_EMAIL_RE, "<PII:email>", "email", result)
    result = _sub(_PHONE_RE, "<PII:phone>", "phone", result)
    result = _sub(_MFA_RE, "<PII:code>", "code", result)
    result = _CONTROL_RE.sub("", result)

    return RedactedText(text=result, redactions=redactions)
