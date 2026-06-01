import pytest
from xframe_agent.agent.conversation.recap import build_recap, commit_draft

CONTRACT = {
    "id": "create_pricing_request", "version": "v1",
    "conversation": {"phases": [
        {"id": "identity", "field_ids": ["sending_partner_name", "opportunity_type",
                                         "corridor_regions", "corridor_countries"]}]},
    "steps": [{"id": "summary", "fields": [
        {"id": "sending_partner_name", "label": "Partner", "type": "string", "required": True},
        {"id": "opportunity_type", "label": "Opp", "type": "enum", "required": True,
         "enum_options": [{"value": "new", "label": "New"}]},
        {"id": "default_fee_currency", "label": "Cur", "type": "enum", "required": True},
        {"id": "corridor_regions", "label": "Regions", "type": "multi_enum", "required": False},
        {"id": "corridor_countries", "label": "Countries", "type": "multi_enum", "required": False},
    ]}],
}


def test_build_recap_partitions_collected_vs_defaulted():
    draft = {"summary": {"sending_partner_name": "Acme", "opportunity_type": "new"}}
    collected, defaulted = build_recap(CONTRACT, draft)
    assert collected["sending_partner_name"] == "Acme"
    assert "sending_partner_name" not in defaulted


class _FakePF:
    """Fake PriceFrameClient: captures post_json calls, returns a quote response."""

    def __init__(self, corridors=None, post_json_raises=False, quote_id="q-123"):
        self._corridors = corridors or []
        self._post_json_raises = post_json_raises
        self._quote_id = quote_id
        self.post_calls: list[dict] = []

    async def post_json(self, path, *, jwt_raw="", json=None, **kw):
        if self._post_json_raises:
            raise RuntimeError("post_json boom")
        self.post_calls.append({"path": path, "json": json})
        return {"success": True, "data": {"id": self._quote_id}}

    async def get_json(self, path, *, jwt_raw="", **kw):
        return self._corridors


@pytest.mark.asyncio
async def test_commit_draft_creates_quote_without_submitting(monkeypatch):
    pf = _FakePF()
    bulk_calls = []

    class FakeBulkTool:
        async def execute(self, args, auth, pf):
            bulk_calls.append("bulk_add_corridors")
            class R: data = {"ok": True}
            return R()

    monkeypatch.setattr("xframe_agent.agent.conversation.recap._corridors_tool", lambda: FakeBulkTool())

    result = await commit_draft(
        CONTRACT,
        {"summary": {"sending_partner_name": "Acme", "opportunity_type": "new"}},
        auth_ctx=object(), priceframe=pf,
    )
    assert result["quote_id"] == "q-123"
    assert "create_quotation" in result["applied"]
    assert "submit_for_approval" not in result["applied"]
    # Verify that the POST /api/quotes call included the partner name
    assert pf.post_calls
    assert pf.post_calls[0]["json"]["name"] == "Acme"


@pytest.mark.asyncio
async def test_commit_draft_includes_all_collected_fields(monkeypatch):
    """commit_draft must map conversation answers into the correct snapshot keys."""
    pf = _FakePF()
    monkeypatch.setattr("xframe_agent.agent.conversation.recap._corridors_tool", lambda: _FakeBulkTool())

    draft = {"summary": {
        "sending_partner_name": "Acme",
        "opportunity_type": "new",
        "sending_partner_region": "EMEA",
        "sending_partner_country": "United Kingdom",
        "default_transaction_fee": 2.5,
        "target_margin_percent": 30.0,
    }, "setup_fee": {
        "fee_type": "Network Joining Fee",
        "payment_schedule": "100% on signature",
        "quoted_setup_price": 5000,
        "standard_commitment_fee": 6000,
        "waived_months": 2,
    }}
    result = await commit_draft(CONTRACT, draft, auth_ctx=object(), priceframe=pf)
    assert result["quote_id"] == "q-123"
    sent = pf.post_calls[0]["json"]

    # Top-level structured fields
    assert sent["name"] == "Acme"

    # quotingDetailsSnapshot: partner region/country use the exact keys read by store.ts
    qd = sent.get("quotingDetailsSnapshot", {})
    assert qd.get("sendingPartnerRegion") == "EMEA"
    assert qd.get("sendingPartnerCountry") == "United Kingdom"

    # pricingToolSnapshot: CRITICAL key name corrections verified from frontend
    pts = sent.get("pricingToolSnapshot", {})
    assert pts.get("feeType") == "network"             # 'Network Joining Fee' → 'network'
    assert pts.get("paymentSchedule") == "100"         # '100% on signature' → '100'
    assert pts.get("salesRepQuotedPrice") == "5000"    # quoted_setup_price → salesRepQuotedPrice
    assert pts.get("networkJoiningFee") == 5000.0      # mirrored for feeType='network'
    assert pts.get("standardCommitmentFee") == 6000.0
    assert pts.get("waivedMonths") == 2

    # pricingProjectionsSnapshot wraps targets in plData sub-object
    pps = sent.get("pricingProjectionsSnapshot", {})
    assert pps.get("plData", {}).get("targetMarginPercent") == 30.0


class _FakeBulkTool:
    async def execute(self, args, auth, pf):
        class R: data = {"ok": True}
        return R()


@pytest.mark.asyncio
async def test_commit_draft_when_corridor_resolution_returns_empty(monkeypatch):
    """When corridor lookup returns no matches, bulk_add_corridors is skipped."""
    pf = _FakePF(corridors=[])  # get_json returns [] → no corridor IDs resolved

    draft = {
        "summary": {
            "sending_partner_name": "Acme",
            "opportunity_type": "new",
            "corridor_regions": ["EMEA"],
            "corridor_countries": ["Nigeria"],
        }
    }
    result = await commit_draft(CONTRACT, draft, auth_ctx=object(), priceframe=pf)

    assert result["quote_id"] == "q-123"
    assert "create_quotation" in result["applied"]
    assert "bulk_add_corridors" not in result["applied"]
    assert result["failed"] == []
