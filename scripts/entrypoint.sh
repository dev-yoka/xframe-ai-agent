#!/bin/bash
set -e

echo "Running database migrations..."
uv run alembic upgrade head

echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port 8000 --proxy-headers
