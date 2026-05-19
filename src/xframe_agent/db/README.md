# db

SQLAlchemy 2.x async database primitives for the agent's own Postgres database.

Public API:

- `Base`: declarative base for future ORM models.
- `make_engine(settings)`: build an async engine from settings.
- `make_session_factory(engine)`: build an async session factory.
- `check_database(settings)`: lightweight health probe.

Extension point: Phase D adds ORM models and Alembic migrations using the same `Base.metadata`.
