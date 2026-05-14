"""
GitHub ingester. Pulls repos, pull requests, issues, and CODEOWNERS.

Auth: GitHub App install token (preferred) or PAT for dev.
Set GITHUB_ORG + GITHUB_TOKEN in .env.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from sentinel.graph.ingestion.base import IngestStats, Ingester

log = structlog.get_logger(__name__)


class GitHubIngester(Ingester):
    source_name = "github"

    def __init__(self, graph, embedder) -> None:
        super().__init__(graph, embedder)
        self.org = os.environ["GITHUB_ORG"]
        self.token = os.environ["GITHUB_TOKEN"]
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=60.0,
        )

    async def run(self, since: str | None = None) -> IngestStats:
        log.info("github ingestion starting", org=self.org, since=since)
        async for repo in self._list_repos():
            await self._upsert_repo(repo)
            async for pr in self._list_prs(repo["name"], since):
                await self._upsert_pr(repo["full_name"], pr)
            async for issue in self._list_issues(repo["name"], since):
                await self._upsert_issue(repo["full_name"], issue)
        log.info("github ingestion complete", **self.stats.__dict__)
        return self.stats

    async def _list_repos(self):
        page = 1
        while True:
            resp = await self.client.get(
                f"/orgs/{self.org}/repos",
                params={"per_page": 100, "page": page, "type": "all"},
            )
            if resp.status_code >= 400:
                log.error("github repo list failed", status=resp.status_code)
                self.stats.errors += 1
                return
            repos = resp.json()
            if not repos:
                return
            for r in repos:
                yield r
            if len(repos) < 100:
                return
            page += 1

    async def _list_prs(self, repo_name: str, since: str | None):
        page = 1
        while True:
            resp = await self.client.get(
                f"/repos/{self.org}/{repo_name}/pulls",
                params={"per_page": 100, "page": page, "state": "all",
                        "sort": "updated", "direction": "desc"},
            )
            if resp.status_code >= 400:
                return
            prs = resp.json()
            if not prs:
                return
            for pr in prs:
                if since and pr["updated_at"] < since:
                    return
                yield pr
            if len(prs) < 100:
                return
            page += 1

    async def _list_issues(self, repo_name: str, since: str | None):
        """Issues excluding PRs (GitHub returns PRs in /issues too)."""
        page = 1
        while True:
            params: dict[str, Any] = {"per_page": 100, "page": page,
                                       "state": "all", "sort": "updated",
                                       "direction": "desc"}
            if since:
                params["since"] = since
            resp = await self.client.get(
                f"/repos/{self.org}/{repo_name}/issues", params=params
            )
            if resp.status_code >= 400:
                return
            issues = resp.json()
            if not issues:
                return
            for issue in issues:
                if "pull_request" not in issue:  # exclude PRs
                    yield issue
            if len(issues) < 100:
                return
            page += 1

    async def _upsert_repo(self, repo: dict[str, Any]) -> None:
        await self.graph.write(
            """
            MERGE (r:Repository {full_name: $full_name})
            SET r.name = $name,
                r.description = $description,
                r.owner_team = $owner_team,
                r.default_branch = $default_branch,
                r.is_archived = $is_archived,
                r.updated_at = $updated_at,
                r.url = $url
            """,
            full_name=repo["full_name"],
            name=repo["name"],
            description=repo.get("description") or "",
            owner_team=(repo.get("owner") or {}).get("login", ""),
            default_branch=repo.get("default_branch", "main"),
            is_archived=repo.get("archived", False),
            updated_at=repo.get("updated_at"),
            url=repo.get("html_url", ""),
        )
        self.stats.nodes_upserted += 1

    async def _upsert_pr(self, repo_full_name: str, pr: dict[str, Any]) -> None:
        global_id = f"{repo_full_name}#{pr['number']}"
        body_for_embedding = f"{pr['title']}\n\n{pr.get('body') or ''}"
        try:
            embedding = await self.embedder.embed(body_for_embedding)
            self.stats.embeddings_computed += 1
        except Exception as e:
            log.warning("embedding failed for PR", global_id=global_id, error=str(e))
            embedding = [0.0] * self.embedder.dims
            self.stats.errors += 1

        await self.graph.write(
            """
            MERGE (p:PullRequest {global_id: $global_id})
            SET p.number = $number,
                p.title = $title,
                p.body = $body,
                p.state = $state,
                p.author = $author,
                p.url = $url,
                p.created_at = $created_at,
                p.merged_at = $merged_at,
                p.embedding = $embedding
            WITH p
            MATCH (r:Repository {full_name: $repo_full_name})
            MERGE (p)-[:MODIFIES]->(r)
            WITH p
            MERGE (author:Person {id: $author})
            ON CREATE SET author.email = $author
            MERGE (author)-[:AUTHORED]->(p)
            """,
            global_id=global_id,
            number=pr["number"],
            title=pr["title"],
            body=pr.get("body") or "",
            state=pr["state"],
            author=(pr.get("user") or {}).get("login", "unknown"),
            url=pr["html_url"],
            created_at=pr["created_at"],
            merged_at=pr.get("merged_at"),
            embedding=embedding,
            repo_full_name=repo_full_name,
        )
        self.stats.nodes_upserted += 1
        self.stats.edges_created += 2

    async def _upsert_issue(self, repo_full_name: str, issue: dict[str, Any]) -> None:
        issue_id = f"{repo_full_name}#issue-{issue['number']}"
        text = f"{issue['title']}\n\n{issue.get('body') or ''}"
        try:
            embedding = await self.embedder.embed(text)
            self.stats.embeddings_computed += 1
        except Exception as e:
            log.warning("embedding failed for issue", issue_id=issue_id, error=str(e))
            embedding = [0.0] * self.embedder.dims
            self.stats.errors += 1

        labels = [lbl["name"] for lbl in issue.get("labels", [])]
        await self.graph.write(
            """
            MERGE (t:Ticket {id: $id})
            SET t.title = $title,
                t.body = $body,
                t.status = $status,
                t.author = $author,
                t.url = $url,
                t.labels = $labels,
                t.created_at = $created_at,
                t.source = 'github',
                t.embedding = $embedding
            WITH t
            MATCH (r:Repository {full_name: $repo_full_name})
            MERGE (t)-[:RELATES_TO]->(r)
            """,
            id=issue_id,
            title=issue["title"],
            body=issue.get("body") or "",
            status=issue["state"],
            author=(issue.get("user") or {}).get("login", "unknown"),
            url=issue["html_url"],
            labels=labels,
            created_at=issue["created_at"],
            embedding=embedding,
            repo_full_name=repo_full_name,
        )
        self.stats.nodes_upserted += 1
        self.stats.edges_created += 1
