"""
Schema initializer. Reads schema.cypher and applies it to Neo4j.
Idempotent — every statement uses IF NOT EXISTS. Safe to run on every startup.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from sentinel.graph.client import GraphClient

log = structlog.get_logger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.cypher"


async def apply_schema(graph: GraphClient, path: Path | None = None) -> None:
    """Apply the schema DDL to Neo4j. Each statement is run independently."""
    schema_path = path or _SCHEMA_PATH
    if not schema_path.exists():
        log.warning("schema.cypher not found, skipping schema init", path=str(schema_path))
        return

    raw = schema_path.read_text()
    # Strip comments, split on semicolons
    statements = [
        s.strip()
        for s in raw.split(";")
        if s.strip() and not s.strip().startswith("//")
    ]

    applied = 0
    errors = 0
    for stmt in statements:
        # Skip pure-comment lines
        clean = "\n".join(
            line for line in stmt.splitlines() if not line.strip().startswith("//")
        ).strip()
        if not clean:
            continue
        try:
            await graph.write(clean)
            applied += 1
        except Exception as e:
            # Log but don't abort — partial schema is better than no schema
            log.warning("schema statement failed", error=str(e), stmt=clean[:80])
            errors += 1

    log.info("schema init complete", applied=applied, errors=errors)
