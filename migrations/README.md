# Migrations

Alembic owns the xFRAME Ai Agent database schema. These migrations only target the agent's own Postgres database and must never modify PriceFRAME tables.

Public entry points:

- `uv run alembic revision --autogenerate -m "..."`
- `uv run alembic upgrade head`

Extension point: add SQLAlchemy models under `src/xframe_agent/models/`; `migrations/env.py` imports the shared `Base.metadata` for autogeneration.
