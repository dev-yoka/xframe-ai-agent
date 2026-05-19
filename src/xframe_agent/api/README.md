# api

FastAPI router packages.

Public API:

- `xframe_agent.api.v1.router:router`: versioned API mounted by `main.py`.

Extension point: add sub-routers under `api/v1/` and include them from `router.py`. All agent endpoints remain under `/api/v1/agent`; attachment, memory, and device-token routers land in Phase E/F.
