"""Neo4j client wrapper. Thin convenience layer over the async driver."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from neo4j import AsyncGraphDatabase, AsyncSession

log = structlog.get_logger(__name__)


class GraphClient:
    """Async Neo4j client. One instance per process. Use as a context manager."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "sentinel-dev")
        self._driver = AsyncGraphDatabase.driver(self.uri, auth=(self.user, self.password))

    async def close(self) -> None:
        await self._driver.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._driver.session() as s:
            yield s

    async def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a query and return all records as dicts."""
        async with self.session() as s:
            result = await s.run(query, **params)
            return [r.data() async for r in result]

    async def write(self, query: str, **params: Any) -> None:
        """Execute a write query without expecting results."""
        async with self.session() as s:
            await s.run(query, **params)

    async def vector_search(
        self,
        index_name: str,
        embedding: list[float],
        top_k: int = 10,
        min_score: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Vector similarity search against one of the embedding indexes."""
        query = """
        CALL db.index.vector.queryNodes($index, $k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        RETURN node, score, labels(node) AS labels
        """
        return await self.run(
            query,
            index=index_name,
            k=top_k,
            embedding=embedding,
            min_score=min_score,
        )
