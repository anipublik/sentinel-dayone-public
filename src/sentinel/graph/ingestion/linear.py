"""
Linear ingester. Pulls issues (tickets) via Linear's GraphQL API.

Requires LINEAR_API_KEY. Optionally filter by team IDs via LINEAR_TEAM_IDS
(comma-separated). Links issues to repos via title/description heuristics.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from sentinel.graph.ingestion.base import IngestStats, Ingester

log = structlog.get_logger(__name__)

_GRAPHQL_URL = "https://api.linear.app/graphql"

_ISSUES_QUERY = """
query Issues($after: String, $filter: IssueFilter) {
  issues(first: 50, after: $after, filter: $filter) {
    nodes {
      id
      identifier
      title
      description
      state { name }
      priority
      team { id name key }
      assignee { email name }
      creator { email name }
      url
      createdAt
      updatedAt
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_PRIORITY_LABELS = {0: "no priority", 1: "urgent", 2: "high", 3: "medium", 4: "low"}


class LinearIngester(Ingester):
    """Linear (tickets / issues) ingester."""

    source_name = "linear"

    def __init__(self, graph: Any, embedder: Any) -> None:
        super().__init__(graph, embedder)
        self.api_key = os.environ["LINEAR_API_KEY"]
        team_ids_raw = os.environ.get("LINEAR_TEAM_IDS", "")
        self.team_ids = [t.strip() for t in team_ids_raw.split(",") if t.strip()]
        self.client = httpx.AsyncClient(
            headers={"Authorization": self.api_key, "Content-Type": "application/json"},
            timeout=60.0,
        )

    async def run(self, since: str | None = None) -> IngestStats:
        log.info("linear ingestion starting", team_ids=self.team_ids, since=since)
        filter_: dict[str, Any] = {}
        if self.team_ids:
            filter_["team"] = {"id": {"in": self.team_ids}}
        if since:
            filter_["updatedAt"] = {"gte": since}

        cursor: str | None = None
        while True:
            variables: dict[str, Any] = {"filter": filter_}
            if cursor:
                variables["after"] = cursor

            resp = await self.client.post(
                _GRAPHQL_URL,
                json={"query": _ISSUES_QUERY, "variables": variables},
            )
            if resp.status_code >= 400:
                log.error("linear api error", status=resp.status_code)
                self.stats.errors += 1
                break

            body = resp.json()
            if "errors" in body:
                log.error("linear graphql errors", errors=body["errors"])
                self.stats.errors += 1
                break

            issues_data = body["data"]["issues"]
            for issue in issues_data["nodes"]:
                try:
                    await self._upsert_issue(issue)
                except Exception as e:
                    log.warning("issue upsert failed", issue_id=issue.get("id"), error=str(e))
                    self.stats.errors += 1

            page_info = issues_data["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]

        await self.client.aclose()
        log.info("linear ingestion complete", **self.stats.__dict__)
        return self.stats

    async def _upsert_issue(self, issue: dict[str, Any]) -> None:
        issue_id = issue["identifier"]  # e.g., "ENG-123"
        text = f"{issue['title']}\n\n{issue.get('description') or ''}"

        try:
            embedding = await self.embedder.embed(text)
            self.stats.embeddings_computed += 1
        except Exception as e:
            log.warning("embedding failed for linear issue", issue_id=issue_id, error=str(e))
            embedding = [0.0] * self.embedder.dims
            self.stats.errors += 1

        team = issue.get("team") or {}
        assignee = issue.get("assignee") or {}
        creator = issue.get("creator") or {}
        labels = [lbl["name"] for lbl in (issue.get("labels") or {}).get("nodes", [])]

        await self.graph.write(
            """
            MERGE (t:Ticket {id: $id})
            SET t.title = $title,
                t.body = $body,
                t.status = $status,
                t.priority = $priority,
                t.priority_label = $priority_label,
                t.team_name = $team_name,
                t.team_key = $team_key,
                t.assignee = $assignee,
                t.author = $author,
                t.url = $url,
                t.labels = $labels,
                t.created_at = $created_at,
                t.updated_at = $updated_at,
                t.source = 'linear',
                t.embedding = $embedding
            WITH t
            MERGE (team:Team {id: $team_id})
            ON CREATE SET team.name = $team_name
            MERGE (t)-[:BELONGS_TO]->(team)
            """,
            id=issue_id,
            title=issue["title"],
            body=issue.get("description") or "",
            status=(issue.get("state") or {}).get("name", "unknown"),
            priority=issue.get("priority", 0),
            priority_label=_PRIORITY_LABELS.get(issue.get("priority", 0), "unknown"),
            team_name=team.get("name", "unknown"),
            team_key=team.get("key", ""),
            team_id=team.get("id", "unknown"),
            assignee=assignee.get("email") or assignee.get("name", ""),
            author=creator.get("email") or creator.get("name", ""),
            url=issue.get("url", ""),
            labels=labels,
            created_at=issue.get("createdAt"),
            updated_at=issue.get("updatedAt"),
            embedding=embedding,
        )
        self.stats.nodes_upserted += 1
        self.stats.edges_created += 1
