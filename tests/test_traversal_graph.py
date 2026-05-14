"""Tests for traversal graph serialization."""

from sentinel.retrieval.engine import Citation, QueryIntent, RetrievalResult
from sentinel.retrieval.traversal_graph import build_traversal_graph


def test_build_traversal_graph_from_citations():
    result = RetrievalResult(
        query="why error budget",
        intent=QueryIntent.DECISION,
        entry_points=[
            Citation(kind="adr", id="ADR-007", title="5% error budget", url=None, score=0.9),
        ],
        connected=[
            Citation(kind="slack", id="slack:thread:1", title="#sre-general", url=None),
            Citation(kind="incident", id="INC-2024-089", title="Outage", url=None),
        ],
        raw_traversal=[
            {"n": {"id": "ADR-007", "title": "5% error budget"}, "connected": {"id": "INC-2024-089", "title": "Outage", "started_at": "2024"}},
        ],
    )
    graph = build_traversal_graph(result)
    assert graph["intent"] == "decision"
    assert len(graph["nodes"]) == 3
    assert any(n["role"] == "entry" for n in graph["nodes"])
    assert any(e["from"] == "ADR-007" for e in graph["edges"])
