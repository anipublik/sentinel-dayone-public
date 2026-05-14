"""Buddy digest: provision recent hires and notify their onboarding buddy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import structlog

from sentinel.catalog import compute_access_topology, get_catalog
from sentinel.provisioning.runner import ProvisioningRunner

log = structlog.get_logger(__name__)


@dataclass
class BuddyDigestResult:
    since: str
    hires_processed: int = 0
    messages_sent: int = 0
    digests: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _format_slack_message(profile: Any, report: dict[str, Any], topology: Any) -> str:
    summary = report.get("summary", {})
    failed = [s for s in report.get("steps", []) if s.get("status") in ("failed", "partial")]
    skipped = [s for s in report.get("steps", []) if s.get("status") == "skipped"]
    backlog = next((s for s in report.get("steps", []) if s.get("name") == "backlog"), {})
    reading = next((s for s in report.get("steps", []) if s.get("name") == "reading_list"), {})

    lines = [
        f"*Day-one digest for {profile.full_name}* (`{profile.employee_id}`)",
        f"Role: {profile.role} · Team: {profile.team} · Start: {profile.start_date}",
        "",
        f"Provisioning: {summary.get('success', 0)} ok, "
        f"{summary.get('failed', 0)} failed, {summary.get('skipped', 0)} skipped",
    ]
    if failed:
        lines.append("Gaps: " + ", ".join(f"{s['name']} ({s['status']})" for s in failed))
    if skipped:
        lines.append("Skipped: " + ", ".join(s["name"] for s in skipped[:5]))

    backlog_items = (backlog.get("details") or {}).get("items") or []
    if backlog_items:
        lines.extend(["", "*Backlog*"])
        for item in backlog_items[:3]:
            lines.append(f"• {item.get('id', '?')}: {item.get('title', '')}")

    reading_items = (reading.get("details") or {}).get("items") or []
    if reading_items:
        lines.extend(["", "*Reading list*"])
        for item in reading_items[:3]:
            lines.append(f"• {item.get('id', '?')}: {item.get('title', '')}")

    repos = topology.repos.get("owned", [])[:4]
    if repos:
        lines.extend(["", f"Owned repos: {', '.join(repos)}"])

    lines.append("")
    lines.append("_Sent by Sentinel Day One buddy digest_")
    return "\n".join(lines)


async def _send_slack(webhook_url: str, text: str, channel: str | None = None) -> None:
    payload: dict[str, Any] = {"text": text}
    if channel:
        payload["channel"] = channel
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()


async def run_buddy_digest(
    since_days: int = 7,
    dry_run: bool = False,
    catalog_source: str | None = None,
) -> BuddyDigestResult:
    """Find recent hires, provision them, and DM summaries to their buddy."""
    since = date.today() - timedelta(days=since_days)
    catalog = get_catalog(catalog_source)
    webhook = os.environ.get("SLACK_BUDDY_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL")
    channel = os.environ.get("SLACK_BUDDY_CHANNEL")

    result = BuddyDigestResult(since=since.isoformat())
    hires = await catalog.list_recent_hires(since)
    log.info("buddy digest starting", hires=len(hires), since=since.isoformat(), dry_run=dry_run)

    for profile in hires:
        result.hires_processed += 1
        buddy_id = getattr(profile, "buddy_id", None)
        digest_entry: dict[str, Any] = {
            "employee_id": profile.employee_id,
            "full_name": profile.full_name,
            "buddy_id": buddy_id,
        }
        try:
            topology = compute_access_topology(profile)
            report = await ProvisioningRunner(profile, topology).run()
            message = _format_slack_message(profile, report, topology)
            digest_entry["message"] = message
            digest_entry["summary"] = report.get("summary")

            if dry_run or not webhook:
                if not webhook:
                    digest_entry["delivery"] = "skipped_no_webhook"
                else:
                    digest_entry["delivery"] = "dry_run"
            else:
                header = f"Onboarding buddy update for <mailto:{profile.employee_id}|{profile.full_name}>"
                if buddy_id:
                    header += f" (buddy: {buddy_id})"
                await _send_slack(webhook, f"{header}\n\n{message}", channel=channel)
                digest_entry["delivery"] = "sent"
                result.messages_sent += 1
        except Exception as e:
            msg = f"{profile.employee_id}: {e}"
            log.warning("buddy digest hire failed", error=msg)
            result.errors.append(msg)
            digest_entry["error"] = str(e)

        result.digests.append(digest_entry)

    log.info(
        "buddy digest complete",
        hires=result.hires_processed,
        sent=result.messages_sent,
        errors=len(result.errors),
    )
    return result
