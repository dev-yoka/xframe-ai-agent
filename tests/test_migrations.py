"""Migration coverage checks for model/schema drift."""

from __future__ import annotations

import re
from pathlib import Path


def test_agent_conversation_kind_column_is_migrated() -> None:
    """The conversation kind model field must exist in Alembic migrations."""

    migration_dir = Path("migrations/versions")
    migration_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in migration_dir.glob("*.py")
        if path.name != "__init__.py"
    )

    create_match = re.search(
        r'op\.create_table\(\s*"agent_conversations",(?P<body>.*?)\n    \)\n    op\.create_index',
        migration_text,
        flags=re.DOTALL,
    )
    creates_with_kind = (
        create_match is not None and re.search(r'Column\(\s*"kind"', create_match.group("body"))
    )
    adds_kind = re.search(
        r'add_column\(\s*"agent_conversations"\s*,\s*sa\.Column\(\s*"kind"',
        migration_text,
        flags=re.DOTALL,
    )

    assert creates_with_kind or adds_kind
