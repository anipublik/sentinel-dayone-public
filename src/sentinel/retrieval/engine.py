"""
Retrieval engine. Three things compose here:

  1. Vector entry: embed the query, find top-k matching nodes.
  2. Graph traversal: walk edges from entry points to assemble context.
  3. Role scoping: filter by what the asking employee is allowed to see.

The engine returns a structured RetrievalResult. The agent layer turns
this into a terminal-style answer with citations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from sentinel.catalog import AccessTopology
from sentinel.graph.client import GraphClient
from sentinel.graph.ingestion.embedder import Embedder

log = structlog.get_logger(__name__)


class QueryIntent(str, Enum):
    LOOKUP = "lookup"
    DECISION = "decision"
    OWNERSHIP = "ownership"
    IMPACT = "impact"
    INCIDENT = "incident"
    PROVISIONING = "provisioning"


@dataclass
class Citation:
    kind: str       # pr | slack | adr | ticket | incident | confluence | unknown
    id: str
    title: str
    url: str | None
    score: float = 0.0
    excerpt: str = ""


@dataclass
class RetrievalResult:
    query: str
    intent: QueryIntent
    entry_points: list[Citation] = field(default_factory=list)
    connected: list[Citation] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    related_tickets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_traversal: list[dict[str, Any]] = field(default_factory=list)


# ─── intent classification ────────────────────────────────────────────────────

_INTENT_KEYWORDS = {
    QueryIntent.DECISION: ("why", "rationale", "decided", "decision", "reason", "chose"),
    QueryIntent.OWNERSHIP: ("who owns", "owner of", "who maintains", "who manages", "owned by"),
    QueryIntent.IMPACT: ("what depends", "what breaks", "downstream", "impact of", "affects"),
    QueryIntent.INCIDENT: ("outage", "incident", "what happened", "postmortem", "oncall"),
    QueryIntent.PROVISIONING: (
        "can i write", "can i deploy", "do i have access", "what can i",
        "my access", "my permissions", "what repos",
    ),
}


def classify_intent(query: str) -> QueryIntent:
    q = query.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(k in q for k in keywords):
            return intent
    return QueryIntent.LOOKUP


# ─── retrieval engine ─────────────────────────────────────────────────────────

_TRAVERSAL_DEPTH = {
    QueryIntent.LOOKUP: 1,
    QueryIntent.OWNERSHIP: 2,
    QueryIntent.DECISION: 3,
    QueryIntent.IMPACT: 2,
    QueryIntent.INCIDENT: 3,
    QueryIntent.PROVISIONING: 2,
}

_VECTOR_INDEXES = [
    "slack_embeddings",
    "pr_embeddings",
    "adr_embeddings",
    "conf_embeddings",
    "ticket_embeddings",
]


class RetrievalEngine:
    """The retrieval layer. Stateless. One instance per process."""

    def __init__(self, graph: GraphClient, embedder: Embedder) -> None:
        self.graph = graph
        self.embedder = embedder

    async def retrieve(
        self,
        query: str,
        topology: AccessTopology,
        intent: QueryIntent | None = None,
        top_k: int = 8,
    ) -> RetrievalResult:
        intent = intent or classify_intent(query)
        log.info("retrieval starting", query=query, intent=intent.value)

        embedding = await self.embedder.embed(query)
        import os as _os
        min_score = 0.35 if not _os.environ.get("OPENAI_API_KEY") else 0.7
        entry_points = await self._vector_search(embedding, top_k, min_score=min_score)
        entry_points = self._scope(entry_points, topology)

        if not entry_points:
            return RetrievalResult(
                query=query,
                intent=intent,
                warnings=["no relevant context found in graph for this query and role"],
            )

        depth = _TRAVERSAL_DEPTH[intent]
        traversal = await self._traverse(entry_points, depth)
        connected = self._extract_connected(traversal, entry_points)
        connected = self._scope(connected, topology)
        owners = self._extract_owners(traversal)
        tickets = self._extract_tickets(traversal)

        return RetrievalResult(
            query=query,
            intent=intent,
            entry_points=entry_points,
            connected=connected,
            owners=owners,
            related_tickets=tickets,
            raw_traversal=traversal,
        )

    async def _vector_search(
        self, embedding: list[float], top_k: int, min_score: float = 0.7
    ) -> list[Citation]:
        import asyncio
        results = await asyncio.gather(
            *(self.graph.vector_search(idx, embedding, top_k=top_k, min_score=min_score) for idx in _VECTOR_INDEXES),
            return_exceptions=True,
        )
        citations: dict[str, Citation] = {}
        for r in results:
            if isinstance(r, BaseException):
                log.warning("vector index unavailable", error=str(r))
                continue
            for row in r:
                node = row["node"]
                kind = self._kind_from_labels(row["labels"])
                cid = node.get("id") or node.get("global_id") or node.get("full_name")
                if not cid or cid in citations:
                    continue
                citations[cid] = Citation(
                    kind=kind,
                    id=cid,
                    title=node.get("title") or node.get("name") or cid,
                    url=node.get("url"),
                    score=row["score"],
                    excerpt=(node.get("body") or node.get("description") or node.get("text") or "")[:200],
                )
        return sorted(citations.values(), key=lambda c: c.score, reverse=True)[:top_k]

    @staticmethod
    def _kind_from_labels(labels: list[str]) -> str:
        mapping = {
            "SlackThread": "slack",
            "PullRequest": "pr",
            "ADR": "adr",
            "ConfluencePage": "confluence",
            "Ticket": "ticket",
            "Incident": "incident",
        }
        for label in labels:
            if label in mapping:
                return mapping[label]
        return "unknown"

    def _scope(self, citations: list[Citation], topology: AccessTopology) -> list[Citation]:
        """
        Filter citations by the user's access topology.

        PR nodes: must be from a repo the user can read.
        Slack nodes: excluded channels were filtered at ingestion time;
                     additionally exclude threads that mention only out-of-scope services.
        Confluence/ADR/Ticket nodes: filtered by labels that match excluded topics
                                      (hr, finance, legal, exec). We don't enforce repo
                                      scope on docs — docs are cross-team by design.
        """
        readable_repos: set[str] = set(
            topology.repos.get("owned", [])
            + topology.repos.get("shared_write", [])
            + topology.repos.get("read", [])
            + topology.test_repos.get("owned", [])
            + topology.test_repos.get("read", [])
            + topology.shared_libs
        )

        # Word-boundary regex avoids false positives:
        # 'hr' in 'thread', 'exec' in 'executive', etc.
        import re as _re
        _SENSITIVE_PAT = _re.compile(
            r"\b(hr|finance|legal|exec|board|compensation|performance|salary)\b",
            _re.IGNORECASE,
        )

        def _matches_sensitive(text: str) -> bool:
            return bool(_SENSITIVE_PAT.search(text))

        scoped: list[Citation] = []
        for c in citations:
            if c.kind == "pr":
                # PR id format: org/repo#number — extract repo name
                repo = c.id.rsplit("#", 1)[0].split("/", 1)[-1]
                if readable_repos and repo not in readable_repos:
                    continue

            elif c.kind in ("confluence", "adr"):
                # Block sensitive labelled pages
                if _matches_sensitive(c.title or "") or _matches_sensitive(c.excerpt or ""):
                    continue

            elif c.kind == "slack":
                # Belt-and-suspenders: block threads from sensitive channels.
                # Ingester excluded them at write time; this catches anything
                # seeded via other means.
                if _matches_sensitive(c.title or ""):
                    continue

            scoped.append(c)
        return scoped

    async def _traverse(self, entry_points: list[Citation], depth: int) -> list[dict[str, Any]]:
        ids = [c.id for c in entry_points]
        query = f"""
        MATCH (n)
        WHERE n.id IN $ids OR n.global_id IN $ids OR n.full_name IN $ids
        OPTIONAL MATCH path = (n)-[*1..{depth}]-(connected)
        RETURN n, path, connected
        LIMIT 50
        """
        return await self.graph.run(query, ids=ids)

    def _extract_connected(
        self, traversal: list[dict[str, Any]], entry_points: list[Citation]
    ) -> list[Citation]:
        entry_ids = {c.id for c in entry_points}
        connected: dict[str, Citation] = {}
        for row in traversal:
            node = row.get("connected")
            if not node:
                continue
            cid = node.get("id") or node.get("global_id") or node.get("full_name")
            if not cid or cid in entry_ids or cid in connected:
                continue
            connected[cid] = Citation(
                kind=self._kind_from_node(node),
                id=cid,
                title=node.get("title") or node.get("name") or cid,
                url=node.get("url"),
                excerpt=(node.get("body") or node.get("description") or node.get("text") or "")[:200],
            )
        return list(connected.values())

    @staticmethod
    def _kind_from_node(node: dict[str, Any]) -> str:
        if "global_id" in node and "#" in str(node.get("global_id", "")):
            return "pr"
        if "channel" in node:
            return "slack"
        if "started_at" in node:
            return "incident"
        if "status" in node and "title" in node:
            return "ticket"
        if "space_key" in node:
            return "confluence"
        return "unknown"

    @staticmethod
    def _extract_owners(traversal: list[dict[str, Any]]) -> list[str]:
        owners: set[str] = set()
        for row in traversal:
            node = row.get("connected") or {}
            if "owner_team" in node:
                owners.add(node["owner_team"])
            if "author" in node:
                owners.add(node["author"])
        return sorted(owners)

    @staticmethod
    def _extract_tickets(traversal: list[dict[str, Any]]) -> list[str]:
        tickets: set[str] = set()
        for row in traversal:
            node = row.get("connected") or {}
            node_id = node.get("id", "")
            if isinstance(node_id, str) and "-" in node_id:
                # Match patterns: ENG-123, PAY-892, SRE-44, INC-2024-089
                import re
                if re.match(r"^[A-Z]{2,6}-[\d-]+$", node_id):
                    tickets.add(node_id)
        return sorted(tickets)
