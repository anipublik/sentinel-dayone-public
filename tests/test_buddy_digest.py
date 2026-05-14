"""Tests for buddy digest."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from sentinel.buddy_digest import run_buddy_digest


@pytest.mark.asyncio
async def test_buddy_digest_dry_run(sre_profile):
    sre_profile.start_date = date.today()
    sre_profile.buddy_id = "buddy@example.com"

    mock_catalog = AsyncMock()
    mock_catalog.list_recent_hires = AsyncMock(return_value=[sre_profile])

    mock_report = {
        "steps": [
            {"name": "github", "status": "skipped"},
            {"name": "backlog", "status": "success", "details": {"items": []}},
            {"name": "reading_list", "status": "success", "details": {"items": [{"id": "ADR-007", "title": "Error budget"}]}},
        ],
        "summary": {"success": 1, "skipped": 2, "failed": 0},
    }

    with patch("sentinel.buddy_digest.get_catalog", return_value=mock_catalog), patch(
        "sentinel.buddy_digest.ProvisioningRunner"
    ) as mock_runner:
        mock_runner.return_value.run = AsyncMock(return_value=mock_report)
        result = await run_buddy_digest(since_days=7, dry_run=True)

    assert result.hires_processed == 1
    assert result.messages_sent == 0
    assert result.digests[0]["employee_id"] == sre_profile.employee_id
    assert "Day-one digest" in result.digests[0]["message"]
