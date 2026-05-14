"""Retrieval layer - vector entry + graph traversal + role scoping."""

from sentinel.retrieval.engine import (
    Citation,
    QueryIntent,
    RetrievalEngine,
    RetrievalResult,
    classify_intent,
)

__all__ = [
    "Citation",
    "QueryIntent",
    "RetrievalEngine",
    "RetrievalResult",
    "classify_intent",
]
