"""Ingestion package. Re-exports the public surface expected by api/main.py and retrieval."""

from sentinel.graph.ingestion.base import IngestStats, Ingester
from sentinel.graph.ingestion.confluence import ConfluenceIngester
from sentinel.graph.ingestion.embedder import Embedder
from sentinel.graph.ingestion.github import GitHubIngester
from sentinel.graph.ingestion.linear import LinearIngester
from sentinel.graph.ingestion.slack import SlackIngester

__all__ = [
    "Embedder",
    "IngestStats",
    "Ingester",
    "GitHubIngester",
    "SlackIngester",
    "LinearIngester",
    "ConfluenceIngester",
]

REGISTRY: dict[str, type[Ingester]] = {
    "github": GitHubIngester,
    "slack": SlackIngester,
    "linear": LinearIngester,
    "confluence": ConfluenceIngester,
}
