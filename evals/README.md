# evals

Synthetic golden-trace harness for the xFRAME Ai Agent.

Public API:

- `evals.replay:load_golden_traces`
- `evals.replay:replay_trace`

Extension point: replace the Phase D structural placeholder with provider-backed replay once the live Gemini/Anthropic loop is connected and deterministic fixtures are available.
