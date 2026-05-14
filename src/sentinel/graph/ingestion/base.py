"""Base classes for source-system ingesters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from sentinel.graph.client import GraphClient


@dataclass
class IngestStats:
    source: str
    nodes_upserted: int = 0
    edges_created: int = 0
    embeddings_computed: int = 0
    errors: int = 0


class Ingester(ABC):
    """Base class. All source-system ingesters inherit from this."""

    source_name: str = "abstract"

    def __init__(self, graph: GraphClient, embedder: "Embedder") -> None:  # type: ignore[name-defined]  # noqa: F821
        self.graph = graph
        self.embedder = embedder
        self.stats = IngestStats(source=self.source_name)

    @abstractmethod
    async def run(self, since: str | None = None) -> IngestStats:
        """Pull from the source system and upsert into Neo4j."""
        ...
