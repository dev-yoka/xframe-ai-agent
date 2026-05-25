"""Suggestion-quality eval harness (M2 Phase 10).

Walks 50 synthetic historical quotes, masks one suggestable field per quote,
and measures how well the agent's blender re-derives the masked value from
the remaining context. Hermetic — the eval stubs PriceFrameClient with a
deterministic in-process historical-suggestions endpoint so CI can run it
without network access.
"""
