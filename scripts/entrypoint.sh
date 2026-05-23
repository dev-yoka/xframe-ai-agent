#!/bin/bash
set -euo pipefail

echo "Running database migrations..."
alembic upgrade head

echo "Starting xframe-agent..."
exec uvicorn xframe_agent.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
