"""Deterministically build the 50-quote suggestion-quality fixture.

Run via ``python -m evals.suggestion_quality.build_fixture`` to regenerate
``quotes_fixture.json``. The output is checked into the repo so CI doesn't
need to regenerate it — this script exists so a future engineer can extend
the fixture (more corridors, new fields) without hand-editing JSON.

The fixture deliberately models corridor-driven price dispersion plus a small
amount of within-corridor variance, so the blender's median-aggregation has
realistic data to chew on. Two "low-data" corridors are seeded with only a
couple of quotes each — these are the cases where the eval expects the
historical band to fail min_sample_size and the blender to either fall back
to market-only (when available) or emit no_signal.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "quotes_fixture.json"

# Field id -> (corridor-driven base value, per-quote jitter range).
# Values are denominated per the contract's declared unit (PCT_AMOUNT for
# fees, BPS for spreads). The numbers are synthetic but follow the rough
# bands the team has seen historically so the eval is not unfair.
SUGGESTABLE_FIELDS: dict[str, dict[str, float]] = {
    "default_transaction_fee": {"low": 0.6, "mid": 1.4, "high": 2.4, "jitter": 0.15},
    "default_fx_spread_percent": {"low": 0.25, "mid": 0.45, "high": 0.85, "jitter": 0.05},
    "standard_commitment_fee": {"low": 250.0, "mid": 600.0, "high": 1500.0, "jitter": 75.0},
    "quoted_setup_price": {"low": 5000.0, "mid": 12000.0, "high": 25000.0, "jitter": 1000.0},
    "service_request_fee_reversal": {"low": 10.0, "mid": 20.0, "high": 40.0, "jitter": 2.0},
    "emergency_funding_fee": {"low": 50.0, "mid": 120.0, "high": 250.0, "jitter": 10.0},
    "tier_1_fee": {"low": 0.5, "mid": 1.1, "high": 2.0, "jitter": 0.1},
    "tier_2_fee": {"low": 0.4, "mid": 0.9, "high": 1.7, "jitter": 0.08},
    "tier_3_fee": {"low": 0.3, "mid": 0.7, "high": 1.4, "jitter": 0.06},
    "target_margin_percent": {"low": 12.0, "mid": 22.0, "high": 35.0, "jitter": 2.0},
    "target_gm_percent": {"low": 18.0, "mid": 32.0, "high": 48.0, "jitter": 3.0},
}

# Corridor -> price band. "mid" is the historical median bucket.
# The two low-data corridors get only a couple of quotes to exercise the
# blender's "market only / no_signal" branches.
CORRIDORS: dict[str, dict[str, str]] = {
    "USA-IND": {"band": "mid", "service": "C2C"},
    "USA-PHL": {"band": "mid", "service": "C2C"},
    "USA-MEX": {"band": "low", "service": "C2C"},
    "GBR-NGA": {"band": "high", "service": "B2B"},
    "GBR-IND": {"band": "mid", "service": "C2C"},
    "ARE-IND": {"band": "mid", "service": "B2B"},
    "ARE-PAK": {"band": "mid", "service": "C2C"},
    "CAN-IND": {"band": "high", "service": "B2B"},
    "AUS-PHL": {"band": "high", "service": "C2C"},
    "SAU-EGY": {"band": "low", "service": "C2C"},
    # Low-data corridors — only two quotes each.
    "DEU-TUR": {"band": "high", "service": "B2B"},
    "JPN-VNM": {"band": "high", "service": "C2C"},
}

QUOTE_COUNT_PER_CORRIDOR: dict[str, int] = {
    "USA-IND": 6,
    "USA-PHL": 5,
    "USA-MEX": 5,
    "GBR-NGA": 5,
    "GBR-IND": 5,
    "ARE-IND": 4,
    "ARE-PAK": 4,
    "CAN-IND": 4,
    "AUS-PHL": 4,
    "SAU-EGY": 4,
    "DEU-TUR": 2,
    "JPN-VNM": 2,
}

CUSTOMER_SEGMENTS = ["RETAIL", "ENTERPRISE", "MID_MARKET"]


def build_quote(
    *,
    quote_id: int,
    corridor: str,
    rng: random.Random,
) -> dict[str, object]:
    meta = CORRIDORS[corridor]
    band = meta["band"]
    service = meta["service"]
    segment = rng.choice(CUSTOMER_SEGMENTS)

    values: dict[str, float] = {}
    for field_id, spec in SUGGESTABLE_FIELDS.items():
        base = float(spec[band])
        jitter = float(spec["jitter"])
        value = base + rng.uniform(-jitter, jitter)
        values[field_id] = round(value, 4)

    return {
        "quote_id": quote_id,
        "corridor": corridor,
        "service": service,
        "customer_segment": segment,
        "fields": values,
    }


def build_fixture() -> dict[str, object]:
    # Deterministic seed — this fixture is checked into the repo so tests are
    # reproducible. Not used for any cryptographic purpose.
    rng = random.Random(20260524)  # noqa: S311
    quotes: list[dict[str, object]] = []
    quote_id = 1000
    for corridor, count in QUOTE_COUNT_PER_CORRIDOR.items():
        for _ in range(count):
            quotes.append(build_quote(quote_id=quote_id, corridor=corridor, rng=rng))
            quote_id += 1

    assert len(quotes) == 50, f"Expected 50 quotes, got {len(quotes)}"

    return {
        "version": 1,
        "generated_with": "evals/suggestion_quality/build_fixture.py",
        "quotes": quotes,
        "suggestable_fields": list(SUGGESTABLE_FIELDS.keys()),
        "corridor_metadata": {
            corridor: {
                "band": CORRIDORS[corridor]["band"],
                "quote_count": QUOTE_COUNT_PER_CORRIDOR[corridor],
                "service": CORRIDORS[corridor]["service"],
            }
            for corridor in CORRIDORS
        },
    }


if __name__ == "__main__":
    payload = build_fixture()
    FIXTURE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    quotes = payload["quotes"]
    assert isinstance(quotes, list)
    print(f"Wrote {len(quotes)} quotes to {FIXTURE_PATH}")
