"""Tests for the retrieval engine — intent classification and role scoping."""

from __future__ import annotations

import pytest

from sentinel.retrieval.engine import (
    Citation,
    QueryIntent,
    RetrievalEngine,
    classify_intent,
)


# ─── intent classification ────────────────────────────────────────────────────


@pytest.mark.parametrize("query,expected", [
    ("why does payments-svc have a 5% error budget", QueryIntent.DECISION),
    ("who owns the fraud-api", QueryIntent.OWNERSHIP),
    ("what depends on the auth service", QueryIntent.IMPACT),
    ("what happened during the payments outage", QueryIntent.INCIDENT),
    ("can I deploy to prod-us-east", QueryIntent.PROVISIONING),
    ("what is the retry policy for payments-svc", QueryIntent.LOOKUP),
    ("who maintains the otel-collectors library", QueryIntent.OWNERSHIP),
    ("do i have access to infra-core", QueryIntent.PROVISIONING),
])
def test_classify_intent(query: str, expected: QueryIntent):
    assert classify_intent(query) == expected


# ─── role scoping ────────────────────────────────────────────────────────────


def test_scope_pr_in_readable_repo(sre_topology, mock_graph, mock_embedder):
    engine = RetrievalEngine(mock_graph, mock_embedder)
    citations = [
        Citation(kind="pr", id="my-org/infra-core#42", title="Fix K8s probe", url=None),
        Citation(kind="pr", id="my-org/hr-secrets#1", title="HR data", url=None),
    ]
    scoped = engine._scope(citations, sre_topology)
    ids = [c.id for c in scoped]
    assert "my-org/infra-core#42" in ids
    assert "my-org/hr-secrets#1" not in ids


def test_scope_filters_sensitive_confluence(sre_topology, mock_graph, mock_embedder):
    engine = RetrievalEngine(mock_graph, mock_embedder)
    citations = [
        Citation(kind="confluence", id="CF-1", title="Engineering Architecture", url=None),
        Citation(kind="confluence", id="CF-2", title="HR Compensation Guidelines", url=None,
                 excerpt="compensation and performance reviews"),
    ]
    scoped = engine._scope(citations, sre_topology)
    ids = [c.id for c in scoped]
    assert "CF-1" in ids
    assert "CF-2" not in ids


def test_scope_passes_all_slack_by_default(sre_topology, mock_graph, mock_embedder):
    engine = RetrievalEngine(mock_graph, mock_embedder)
    citations = [
        Citation(kind="slack", id="C123:1234567890.000", title="sre-general thread", url=None),
    ]
    scoped = engine._scope(citations, sre_topology)
    assert len(scoped) == 1


def test_scope_empty_topology_allows_everything(mock_graph, mock_embedder):
    """Empty readable_repos means PR scoping is bypassed (bootstrap case)."""
    from sentinel.catalog.base import AccessTopology
    empty_topology = AccessTopology(
        repos={"owned": [], "shared_write": [], "read": []},
        test_repos={"owned": [], "read": []},
        shared_libs=[],
        clusters=[],
        vault_scope="",
        observability_role="viewer",
        ci_pipelines={},
        alert_rules="none",
        test_management="none",
    )
    engine = RetrievalEngine(mock_graph, mock_embedder)
    citations = [Citation(kind="pr", id="my-org/any-repo#1", title="PR", url=None)]
    scoped = engine._scope(citations, empty_topology)
    # Empty readable_repos means no filter — passes through
    assert len(scoped) == 1


# ─── ticket extraction ────────────────────────────────────────────────────────


def test_extract_tickets_matches_various_formats(mock_graph, mock_embedder):
    engine = RetrievalEngine(mock_graph, mock_embedder)
    traversal = [
        {"connected": {"id": "ENG-123", "title": "Fix auth bug"}},
        {"connected": {"id": "PAY-892", "title": "Payment retry"}},
        {"connected": {"id": "INC-2024-089", "title": "Incident"}},
        {"connected": {"id": "some-other-node", "title": "Not a ticket"}},
        {"connected": {"id": "INFRA-44", "title": "K8s issue"}},
    ]
    tickets = engine._extract_tickets(traversal)
    assert "ENG-123" in tickets
    assert "PAY-892" in tickets
    assert "INC-2024-089" in tickets
    assert "INFRA-44" in tickets
    assert "some-other-node" not in tickets


@pytest.mark.asyncio
async def test_retrieve_empty_graph_returns_empty_result(
    sre_profile, sre_topology, mock_graph, mock_embedder
):
    """When graph returns nothing, RetrievalResult is empty with a warning."""
    mock_graph.vector_search.return_value = []
    engine = RetrievalEngine(mock_graph, mock_embedder)
    result = await engine.retrieve("what is the retry policy", sre_topology)
    assert result.entry_points == []
    assert result.connected == []
    assert len(result.warnings) > 0
