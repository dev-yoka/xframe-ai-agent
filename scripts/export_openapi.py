"""Export the FastAPI OpenAPI schema snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from xframe_agent.main import create_app
from xframe_agent.settings import Settings


def main() -> None:
    settings = Settings(
        health_check_externals=False, prometheus_enabled=False, rate_limit_enabled=False
    )
    app = create_app(settings)
    schema = app.openapi()
    output = Path("openapi.yaml")
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
