"""Tests for the agent orchestrator — citation enforcement and mode routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel.agent.orchestrator import Agent, _has_citations
from sentinel.retrieval.engine import Citation, QueryIntent, RetrievalResult


# ─── citation classifier ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("See PR #445 for context.", True),
    ("Decided in ADR-007.", True),
    ("Discussed in #sre-general on Aug 14.", True),
    ("Filed as PAY-892 in Linear.", True),
    ("ENG-123 tracks this work.", True),
    ("INC-2024-089 was the triggering incident.", True),
    ("See confluence/architecture/payments.", True),
    ("This is an answer with no citations at all.", False),
    ("The system uses retry logic.", False),
])
def test_has_citations(text: str, expected: bool):
    assert _has_citations(text) == expected


# ─── ask — empty graph short circuits ────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_empty_graph_no_llm_call(sre_profile, sre_topology, mock_graph, mock_embedder):
    """When graph is empty, the agent should NOT call Claude."""
    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=RetrievalResult(
        query="test", intent=QueryIntent.LOOKUP,
        entry_points=[], connected=[],
    ))

    mock_catalog = MagicMock()
    mock_catalog.fetch = AsyncMock(return_value=sre_profile)

    mock_anthropic = MagicMock()
    mock_anthropic.messages = MagicMock()
    mock_anthropic.messages.create = AsyncMock()

    agent = Agent(
        catalog=mock_catalog,
        retrieval=mock_retrieval,
        anthropic_client=mock_anthropic,
    )
    result = await agent.ask(sre_profile.employee_id, "what is the retry policy")

    assert result["had_context"] is False
    assert result["citations"] == []
    mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_ask_with_context_calls_claude(sre_profile, mock_graph, mock_embedder):
    """When retrieval returns context, Claude is called exactly once."""
    citation = Citation(kind="adr", id="ADR-007", title="Error Budget Policy", url=None, score=0.9)
    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=RetrievalResult(
        query="why 5%",
        intent=QueryIntent.DECISION,
        entry_points=[citation],
        connected=[],
    ))

    mock_catalog = MagicMock()
    mock_catalog.fetch = AsyncMock(return_value=sre_profile)

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="The 5% error budget was decided in ADR-007.")]

    mock_anthropic = MagicMock()
    mock_anthropic.messages = MagicMock()
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    agent = Agent(
        catalog=mock_catalog,
        retrieval=mock_retrieval,
        anthropic_client=mock_anthropic,
    )
    result = await agent.ask(sre_profile.employee_id, "why 5%")

    assert result["had_context"] is True
    mock_anthropic.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_ask_regenerates_on_missing_citations(sre_profile):
    """When the first answer has no citations, the agent calls Claude a second time."""
    citation = Citation(kind="pr", id="org/repo#1", title="PR 1", url=None, score=0.9)
    mock_retrieval = MagicMock()
    mock_retrieval.retrieve = AsyncMock(return_value=RetrievalResult(
        query="test",
        intent=QueryIntent.LOOKUP,
        entry_points=[citation],
        connected=[],
    ))

    mock_catalog = MagicMock()
    mock_catalog.fetch = AsyncMock(return_value=sre_profile)

    no_citation_resp = MagicMock()
    no_citation_resp.content = [MagicMock(text="This answer has no citations.")]
    with_citation_resp = MagicMock()
    with_citation_resp.content = [MagicMock(text="See PR #1 for context.")]

    mock_anthropic = MagicMock()
    mock_anthropic.messages = MagicMock()
    mock_anthropic.messages.create = AsyncMock(
        side_effect=[no_citation_resp, with_citation_resp]
    )

    agent = Agent(
        catalog=mock_catalog,
        retrieval=mock_retrieval,
        anthropic_client=mock_anthropic,
    )
    result = await agent.ask(sre_profile.employee_id, "test")
    assert mock_anthropic.messages.create.call_count == 2
    assert "PR #1" in result["answer"]
