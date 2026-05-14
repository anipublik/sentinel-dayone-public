"""
Chaos-drill seed data: synthetic payments-outage storyline for local demos.

Run via: sentinel seed drill
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog

from sentinel.graph.client import GraphClient
from sentinel.graph.ingestion.local_embedder import LocalEmbedder

log = structlog.get_logger(__name__)

DRILL_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "error-budget",
        "query": "why does payments-svc have a 5% error budget?",
        "intent": "decision",
        "hint": "Should surface ADR-007 and the #sre-general thread.",
    },
    {
        "id": "outage",
        "query": "what happened during the payments outage?",
        "intent": "incident",
        "hint": "Should cite INC-2024-089 and impacted services.",
    },
    {
        "id": "owner",
        "query": "who owns payments-svc?",
        "intent": "ownership",
        "hint": "Should surface the Payments team and related repos.",
    },
    {
        "id": "circuit-breaker",
        "query": "which PR added the circuit breaker after the outage?",
        "intent": "lookup",
        "hint": "Should cite acme/payments-api#445.",
    },
    {
        "id": "follow-up",
        "query": "what ticket tracks the post-outage reliability work?",
        "intent": "lookup",
        "hint": "Should cite PAY-892.",
    },
]


@dataclass
class DrillSeedStats:
    nodes_upserted: int = 0
    edges_created: int = 0
    embeddings_computed: int = 0


def _get_embedder() -> Any:
    if os.environ.get("OPENAI_API_KEY"):
        from sentinel.graph.ingestion.embedder import Embedder
        return Embedder()
    log.info("OPENAI_API_KEY not set — using LocalEmbedder for drill seed")
    return LocalEmbedder()


async def seed_chaos_drill(graph: GraphClient | None = None) -> DrillSeedStats:
    """Seed Neo4j with a connected payments-outage narrative."""
    owns_graph = graph is None
    graph = graph or GraphClient()
    embedder = _get_embedder()
    stats = DrillSeedStats()

    async def embed(text: str) -> list[float]:
        vec = await embedder.embed(text)
        stats.embeddings_computed += 1
        return vec

    service = {
        "id": "payments-svc",
        "name": "payments-svc",
        "owner_team": "Payments",
        "description": "Core payments API handling card capture and settlement.",
    }
    repo = {
        "full_name": "acme/payments-api",
        "name": "payments-api",
        "owner_team": "Payments",
        "url": "https://github.com/acme/payments-api",
    }
    adr = {
        "id": "ADR-007",
        "title": "5% error budget for payments-svc",
        "body": (
            "After INC-2024-089 we adopted a 5% monthly error budget for payments-svc. "
            "Rationale: tolerate brief dependency blips while capping customer-visible failures. "
            "Decided in #sre-general with Payments and Platform."
        ),
        "url": "https://github.com/acme/payments-api/blob/main/docs/adr/007-error-budget.md",
    }
    slack = {
        "id": "slack:sre-general:1723500000",
        "channel": "sre-general",
        "title": "#sre-general — error budget discussion",
        "text": (
            "Thread on adopting a 5% error budget for payments-svc after the August outage. "
            "@platform-team and @payments-oncall agreed on burn-rate alerts."
        ),
        "url": "https://slack.example.com/archives/C0123456/p1723500000000000",
    }
    incident = {
        "id": "INC-2024-089",
        "title": "Payments API elevated 5xx rate",
        "description": (
            "payments-svc returned elevated 5xx for 47 minutes due to a stale feature flag "
            "and retry storm against the card-network adapter."
        ),
        "started_at": "2024-08-14T14:22:00Z",
        "url": "https://incidents.example.com/INC-2024-089",
    }
    pr = {
        "global_id": "acme/payments-api#445",
        "number": 445,
        "title": "Add circuit breaker around card-network adapter",
        "body": (
            "Implements circuit breaker and backoff per ADR-007 after INC-2024-089. "
            "Closes PAY-892."
        ),
        "state": "merged",
        "author": "jrivera",
        "url": "https://github.com/acme/payments-api/pull/445",
    }
    ticket = {
        "id": "PAY-892",
        "title": "Harden payments-svc against retry storms",
        "body": "Follow-up from INC-2024-089. Track circuit breaker rollout and error budget dashboards.",
        "status": "in_progress",
        "url": "https://linear.app/acme/issue/PAY-892",
    }
    conf = {
        "id": "conf:payments-architecture",
        "title": "Payments platform architecture overview",
        "body": "Documents payments-svc dependencies, SLOs, and the 5% error budget policy.",
        "space_key": "ENG",
        "url": "https://confluence.example.com/display/ENG/payments-architecture",
    }

    adr_emb = await embed(f"{adr['title']}\n\n{adr['body']}")
    slack_emb = await embed(f"{slack['title']}\n\n{slack['text']}")
    pr_emb = await embed(f"{pr['title']}\n\n{pr['body']}")
    ticket_emb = await embed(f"{ticket['title']}\n\n{ticket['body']}")
    conf_emb = await embed(f"{conf['title']}\n\n{conf['body']}")

    writes: list[tuple[str, dict[str, Any]]] = [
        (
            """
            MERGE (s:Service {id: $id})
            SET s.name = $name, s.owner_team = $owner_team, s.description = $description
            """,
            service,
        ),
        (
            """
            MERGE (r:Repository {full_name: $full_name})
            SET r.name = $name, r.owner_team = $owner_team, r.url = $url
            """,
            repo,
        ),
        (
            """
            MERGE (a:ADR {id: $id})
            SET a.title = $title, a.body = $body, a.url = $url, a.embedding = $embedding
            """,
            {**adr, "embedding": adr_emb},
        ),
        (
            """
            MERGE (s:SlackThread {id: $id})
            SET s.channel = $channel, s.title = $title, s.text = $text,
                s.url = $url, s.embedding = $embedding
            """,
            {**slack, "embedding": slack_emb},
        ),
        (
            """
            MERGE (i:Incident {id: $id})
            SET i.title = $title, i.description = $description,
                i.started_at = $started_at, i.url = $url
            """,
            incident,
        ),
        (
            """
            MERGE (p:PullRequest {global_id: $global_id})
            SET p.number = $number, p.title = $title, p.body = $body,
                p.state = $state, p.author = $author, p.url = $url, p.embedding = $embedding
            """,
            {**pr, "embedding": pr_emb},
        ),
        (
            """
            MERGE (t:Ticket {id: $id})
            SET t.title = $title, t.body = $body, t.status = $status,
                t.url = $url, t.embedding = $embedding
            """,
            {**ticket, "embedding": ticket_emb},
        ),
        (
            """
            MERGE (c:ConfluencePage {id: $id})
            SET c.title = $title, c.body = $body, c.space_key = $space_key,
                c.url = $url, c.embedding = $embedding
            """,
            {**conf, "embedding": conf_emb},
        ),
    ]

    for query, params in writes:
        await graph.write(query, **params)
        stats.nodes_upserted += 1

    edge_writes = [
        "MATCH (svc:Service {id:'payments-svc'}), (r:Repository {full_name:'acme/payments-api'}) MERGE (svc)-[:DEPLOYED_FROM]->(r)",
        "MATCH (i:Incident {id:'INC-2024-089'}), (svc:Service {id:'payments-svc'}) MERGE (i)-[:IMPACTED]->(svc)",
        "MATCH (a:ADR {id:'ADR-007'}), (i:Incident {id:'INC-2024-089'}) MERGE (a)-[:TRIGGERED_BY]->(i)",
        "MATCH (a:ADR {id:'ADR-007'}), (s:SlackThread {id:'slack:sre-general:1723500000'}) MERGE (a)-[:DECIDED_IN]->(s)",
        "MATCH (s:SlackThread {id:'slack:sre-general:1723500000'}), (svc:Service {id:'payments-svc'}) MERGE (s)-[:MENTIONS]->(svc)",
        "MATCH (p:PullRequest {global_id:'acme/payments-api#445'}), (r:Repository {full_name:'acme/payments-api'}) MERGE (p)-[:MODIFIES]->(r)",
        "MATCH (p:PullRequest {global_id:'acme/payments-api#445'}), (a:ADR {id:'ADR-007'}) MERGE (p)-[:REFERENCES]->(a)",
        "MATCH (p:PullRequest {global_id:'acme/payments-api#445'}), (t:Ticket {id:'PAY-892'}) MERGE (p)-[:CLOSES]->(t)",
        "MATCH (t:Ticket {id:'PAY-892'}), (i:Incident {id:'INC-2024-089'}) MERGE (t)-[:REFERENCES]->(i)",
        "MATCH (t:Ticket {id:'PAY-892'}), (r:Repository {full_name:'acme/payments-api'}) MERGE (t)-[:RELATES_TO]->(r)",
        "MERGE (team:Team {id:'Payments'}) SET team.name='Payments' WITH team MATCH (svc:Service {id:'payments-svc'}) MERGE (team)-[:OWNS]->(svc)",
    ]
    for q in edge_writes:
        await graph.write(q)
        stats.edges_created += 1

    log.info("chaos drill seed complete", **stats.__dict__)
    if owns_graph:
        await graph.close()
    return stats
