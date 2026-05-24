"""Backend-owned guided workflow contracts for chat UIs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from xframe_agent.models.agent import utc_now
from xframe_agent.provider.base import ChatMessage

CREATE_PRICING_REQUEST_WORKFLOW = "create_pricing_request"
CREATE_PRICING_REQUEST_CONTRACT_VERSION = "v1"
CREATE_PRICING_REQUEST_INITIAL_STEP = "summary"
CREATE_PRICING_REQUEST_TOTAL_STEPS = 7
WORKFLOW_SUBMISSION_PREFIX = f"workflow:{CREATE_PRICING_REQUEST_WORKFLOW}"

_GENERIC_CREATE_REQUESTS = {
    "create a pricing request",
    "create pricing request",
    "start a pricing request",
    "start pricing request",
    "new pricing request",
}


def latest_user_text(messages: Sequence[ChatMessage]) -> str | None:
    """Return the latest user text from provider-agnostic message history."""

    for message in reversed(messages):
        if message.role != "user":
            continue
        parts: list[str] = []
        for block in message.content:
            text = block.payload.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts).strip()
    return None


def is_generic_create_pricing_request(text: str | None) -> bool:
    """Detect generic create-pricing-request prompts that need UI controls."""

    if text is None:
        return False
    normalized = " ".join(text.lower().strip().split())
    return normalized in _GENERIC_CREATE_REQUESTS


def create_pricing_request_input_payload() -> dict[str, Any]:
    """Build the structured input request event consumed by web and mobile clients."""

    today = utc_now().date().isoformat()
    return {
        "workflow": CREATE_PRICING_REQUEST_WORKFLOW,
        "contract_id": CREATE_PRICING_REQUEST_WORKFLOW,
        "contract_version": CREATE_PRICING_REQUEST_CONTRACT_VERSION,
        "step_id": CREATE_PRICING_REQUEST_INITIAL_STEP,
        "step_index": 0,
        "total_steps": CREATE_PRICING_REQUEST_TOTAL_STEPS,
        "title": "Create pricing request",
        "description": "Confirm the basics. Defaults are prefilled so you can continue quickly.",
        "submit_label": "Create draft proposal",
        "defaults": {
            "name": f"Pricing request - {today}",
            "opportunity_type": "New partner",
            "currency": "USD",
            "partner_name": "",
            "salesforce_pr_id": "",
            "regions": [],
            "countries": [],
        },
        "fields": [
            {"id": "name", "type": "text", "label": "Request name", "required": True},
            {
                "id": "opportunity_type",
                "type": "single_select",
                "label": "Opportunity type",
                "required": True,
                "options": ["New partner", "Pricing Change", "Upsell"],
            },
            {
                "id": "currency",
                "type": "single_select",
                "label": "Currency",
                "required": True,
                "options": ["USD", "EUR", "GBP", "SGD", "AUD"],
            },
            {
                "id": "partner_name",
                "type": "text",
                "label": "Customer / partner",
                "required": False,
            },
            {
                "id": "salesforce_pr_id",
                "type": "text",
                "label": "Salesforce PR ID",
                "required": False,
            },
            {"id": "regions", "type": "multi_select", "label": "Regions", "required": False},
            {
                "id": "countries",
                "type": "multi_select",
                "label": "Countries",
                "required": False,
                "depends_on": ["regions"],
            },
        ],
    }


def create_pricing_request_step_entered_payload() -> dict[str, Any]:
    """Build the workflow navigation event consumed by guided chat clients."""

    return {
        "workflow": CREATE_PRICING_REQUEST_WORKFLOW,
        "contract_id": CREATE_PRICING_REQUEST_WORKFLOW,
        "contract_version": CREATE_PRICING_REQUEST_CONTRACT_VERSION,
        "step_id": CREATE_PRICING_REQUEST_INITIAL_STEP,
        "step_index": 0,
        "total_steps": CREATE_PRICING_REQUEST_TOTAL_STEPS,
    }


def parse_create_pricing_request_submission(text: str | None) -> dict[str, Any] | None:
    """Parse a UI-submitted create-pricing-request payload if present."""

    if text is None:
        return None
    stripped = text.strip()
    if not stripped.lower().startswith(WORKFLOW_SUBMISSION_PREFIX):
        return None
    raw_json = stripped[len(WORKFLOW_SUBMISSION_PREFIX) :].strip()
    if not raw_json:
        raise ValueError("Create pricing request workflow payload is missing")
    payload = json.loads(raw_json)
    if not isinstance(payload, Mapping):
        raise ValueError("Create pricing request workflow payload must be a JSON object")
    return normalize_create_pricing_request_args(payload)


def normalize_create_pricing_request_args(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Convert UI workflow values into create_quotation tool args."""

    today = utc_now().date().isoformat()
    name = _clean_string(payload.get("name")) or _clean_string(payload.get("title"))
    currency = (_clean_string(payload.get("currency")) or "USD").upper()
    opportunity_type = _clean_string(payload.get("opportunity_type")) or _clean_string(
        payload.get("opportunityType")
    )
    args: dict[str, Any] = {
        "name": name or f"Pricing request - {today}",
        "opportunity_type": opportunity_type or "New partner",
        "currency": currency[:3],
        "regions": _clean_string_list(payload.get("regions")),
        "countries": _clean_string_list(payload.get("countries")),
    }

    partner_name = _clean_string(payload.get("partner_name")) or _clean_string(
        payload.get("partnerName")
    )
    if partner_name:
        args["partner_name"] = partner_name

    salesforce_pr_id = _clean_string(payload.get("salesforce_pr_id")) or _clean_string(
        payload.get("salesforcePrId")
    )
    if salesforce_pr_id:
        args["salesforce_pr_id"] = salesforce_pr_id

    notes = _clean_string(payload.get("notes"))
    if notes:
        args["notes"] = notes

    return args


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())
    return cleaned
