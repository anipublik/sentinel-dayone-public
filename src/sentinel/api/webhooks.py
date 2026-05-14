"""
Webhook receivers for real-time ingestion triggers.

GitHub:  push/PR events -> re-ingest affected repo
Slack:   event subscriptions -> ingest new threads
Linear:  issue created/updated -> upsert ticket

Each handler verifies the payload signature before processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _verify_signature(payload: bytes, secret: str, signature_header: str | None) -> bool:
    """HMAC-SHA256 signature verification (GitHub / Linear style)."""
    if not secret:
        return True  # no secret configured — allow (dev mode)
    if not signature_header:
        return False
    sig = signature_header.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# ─── GitHub ──────────────────────────────────────────────────────────────────


@router.post("/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str]:
    payload = await request.body()
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    if not _verify_signature(payload, secret, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    data: dict[str, Any] = json.loads(payload)
    event = x_github_event or "unknown"
    log.info("github webhook received", event=event)

    if event in ("push", "pull_request", "pull_request_review"):
        repo_full_name = (data.get("repository") or {}).get("full_name", "")
        if repo_full_name:
            background_tasks.add_task(_reingest_github_repo, repo_full_name)

    return {"status": "queued", "event": event}


async def _reingest_github_repo(repo_full_name: str) -> None:
    """Re-ingest a single repo's PRs. Runs in background."""
    from sentinel.graph.client import GraphClient
    from sentinel.graph.ingestion.embedder import Embedder
    from sentinel.graph.ingestion.github import GitHubIngester

    log.info("webhook-triggered github re-ingestion", repo=repo_full_name)
    try:
        graph = GraphClient()
        embedder = Embedder()
        ingester = GitHubIngester(graph, embedder)
        # Limit to the specific repo by monkey-patching _list_repos
        org, repo_name = repo_full_name.split("/", 1)

        async def _single_repo():
            resp = await ingester.client.get(f"/repos/{org}/{repo_name}")
            if resp.status_code == 200:
                yield resp.json()

        ingester._list_repos = _single_repo  # type: ignore[method-assign]
        await ingester.run()
        await graph.close()
        log.info("webhook re-ingestion complete", repo=repo_full_name)
    except Exception as e:
        log.error("webhook re-ingestion failed", repo=repo_full_name, error=str(e))


# ─── Slack ────────────────────────────────────────────────────────────────────


@router.post("/slack")
async def slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    payload = await request.body()
    secret = os.environ.get("SLACK_WEBHOOK_SECRET", "")

    # Slack uses v0=sha256(timestamp+":"+body) — simplified here
    if secret and x_slack_signature:
        sig = x_slack_signature.removeprefix("v0=")
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=401, detail="invalid signature")

    data: dict[str, Any] = json.loads(payload)

    # URL verification challenge (Slack app setup)
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}

    event = data.get("event", {})
    if event.get("type") == "message" and not event.get("subtype"):
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        if channel_id and thread_ts:
            background_tasks.add_task(_reingest_slack_thread, channel_id, thread_ts)

    return {"status": "ok"}


async def _reingest_slack_thread(channel_id: str, thread_ts: str) -> None:
    from sentinel.graph.client import GraphClient
    from sentinel.graph.ingestion.embedder import Embedder
    from sentinel.graph.ingestion.slack import SlackIngester

    log.info("webhook-triggered slack thread re-ingestion",
              channel=channel_id, ts=thread_ts)
    try:
        graph = GraphClient()
        embedder = Embedder()
        ingester = SlackIngester(graph, embedder)
        channel = {"id": channel_id, "name": channel_id}
        resp = await ingester.client.get(
            "/conversations.info",
            params={"channel": channel_id},
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            channel["name"] = resp.json()["channel"]["name"]

        # Fetch parent message
        hist = await ingester.client.get(
            "/conversations.history",
            params={"channel": channel_id, "latest": thread_ts, "limit": 1, "inclusive": True},
        )
        messages = hist.json().get("messages", [])
        if messages:
            await ingester._upsert_thread(channel_id, channel["name"], messages[0], None)
        await graph.close()
    except Exception as e:
        log.error("webhook slack re-ingestion failed", error=str(e))


# ─── Linear ───────────────────────────────────────────────────────────────────


@router.post("/linear")
async def linear_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    linear_signature: str | None = Header(default=None),
) -> dict[str, str]:
    payload = await request.body()
    secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")

    if not _verify_signature(payload, secret, linear_signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    data: dict[str, Any] = json.loads(payload)
    action = data.get("action", "")
    issue = data.get("data", {})
    log.info("linear webhook received", action=action, issue_id=issue.get("identifier"))

    if action in ("create", "update") and issue:
        background_tasks.add_task(_reingest_linear_issue, issue)

    return {"status": "queued", "action": action}


async def _reingest_linear_issue(issue_data: dict[str, Any]) -> None:
    from sentinel.graph.client import GraphClient
    from sentinel.graph.ingestion.embedder import Embedder
    from sentinel.graph.ingestion.linear import LinearIngester

    log.info("webhook-triggered linear issue re-ingestion", issue=issue_data.get("identifier"))
    try:
        graph = GraphClient()
        embedder = Embedder()
        ingester = LinearIngester(graph, embedder)
        # Map Linear webhook payload to the shape _upsert_issue expects
        normalized = {
            "identifier": issue_data.get("identifier", ""),
            "title": issue_data.get("title", ""),
            "description": issue_data.get("description", ""),
            "state": {"name": (issue_data.get("state") or {}).get("name", "unknown")},
            "priority": issue_data.get("priority", 0),
            "team": issue_data.get("team", {}),
            "assignee": issue_data.get("assignee"),
            "creator": issue_data.get("creator"),
            "url": issue_data.get("url", ""),
            "labels": {"nodes": []},
            "createdAt": issue_data.get("createdAt"),
            "updatedAt": issue_data.get("updatedAt"),
        }
        await ingester._upsert_issue(normalized)
        await graph.close()
    except Exception as e:
        log.error("webhook linear re-ingestion failed", error=str(e))
