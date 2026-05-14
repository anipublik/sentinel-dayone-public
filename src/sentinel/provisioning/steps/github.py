"""Real GitHub provisioning step using GitHub REST API."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"


async def _gh(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=_GH_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    ) as client:
        return await getattr(client, method)(path, **kwargs)


async def _get_github_username(email: str, token: str) -> str | None:
    """Look up GitHub login from email using user search."""
    resp = await _gh("get", "/search/users", token, params={"q": f"{email} in:email"})
    if resp.status_code == 200:
        items = resp.json().get("items", [])
        if items:
            return items[0]["login"]
    return None


async def run(profile: Any, topology: Any) -> "StepResult":  # type: ignore[name-defined]  # noqa: F821
    from sentinel.provisioning.runner import StepResult, StepStatus

    token = os.environ.get("GITHUB_TOKEN", "")
    org = os.environ.get("GITHUB_ORG", "")

    if not token or not org:
        return StepResult(
            name="github",
            status=StepStatus.SKIPPED,
            details={"reason": "GITHUB_TOKEN or GITHUB_ORG not configured"},
        )

    username = await _get_github_username(profile.employee_id, token)
    if not username:
        return StepResult(
            name="github",
            status=StepStatus.NEEDS_APPROVAL,
            details={
                "reason": f"GitHub user not found for {profile.employee_id}. "
                          "Add the GitHub username to the employee catalog or invite manually.",
            },
        )

    granted: dict[str, list[str]] = {"owned": [], "shared_write": [], "read": [], "failed": []}

    # Owned repos — write access
    for repo in topology.repos.get("owned", []):
        resp = await _gh(
            "put", f"/repos/{org}/{repo}/collaborators/{username}",
            token, json={"permission": "push"},
        )
        if resp.status_code in (201, 204):
            granted["owned"].append(repo)
        elif resp.status_code == 404:
            log.warning("github repo not found", repo=repo, org=org)
            granted["failed"].append(repo)
        else:
            log.warning("github collaborator add failed",
                         repo=repo, status=resp.status_code)
            granted["failed"].append(repo)

    # Read repos — read access
    for repo in topology.repos.get("read", []):
        resp = await _gh(
            "put", f"/repos/{org}/{repo}/collaborators/{username}",
            token, json={"permission": "pull"},
        )
        if resp.status_code in (201, 204):
            granted["read"].append(repo)
        else:
            granted["failed"].append(repo)

    status = StepStatus.SUCCESS if not granted["failed"] else StepStatus.PARTIAL
    return StepResult(
        name="github",
        status=status,
        details={
            "github_username": username,
            "granted": granted,
            "note": "shared_write requires CODEOWNERS update — flagged for buddy review"
            if topology.repos.get("shared_write")
            else None,
        },
    )
