FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /usr/local/bin/uv
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --no-dev --no-install-project
RUN uv pip install --system .

EXPOSE 8000

CMD ["uvicorn", "xframe_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
