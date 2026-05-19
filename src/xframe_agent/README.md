# xframe_agent

Root Python package for the xFRAME Ai Agent service. It exposes the FastAPI app factory in `main.py`, environment settings in `settings.py`, and cross-cutting logging in `logging.py`.

Public API:

- `xframe_agent.main:create_app`
- `xframe_agent.main:app`
- `xframe_agent.settings:get_settings`

Extension point: Phase E adds write confirmation, attachment pipelines, memory, and audit callbacks without changing PriceFRAME access patterns.
