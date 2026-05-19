# models

SQLAlchemy ORM models for the agent-owned database live here.

Phase B intentionally leaves this package empty because no agent persistence tables are created until Phase D. Future models must inherit from `xframe_agent.db.Base` and ship with Alembic migrations.
