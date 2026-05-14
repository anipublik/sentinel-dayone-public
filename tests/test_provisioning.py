"""Tests for provisioning runner."""

from __future__ import annotations

import pytest

from sentinel.provisioning.runner import (
    ProvisioningRunner,
    StepResult,
    StepStatus,
)


@pytest.mark.asyncio
async def test_runner_collects_all_steps(sre_profile, sre_topology):
    """All default steps are called and results are collected."""

    async def always_success(profile, topology):
        return StepResult(name="test_step", status=StepStatus.SUCCESS)

    runner = ProvisioningRunner(sre_profile, sre_topology, steps=[always_success])
    report = await runner.run()
    assert report["employee_id"] == sre_profile.employee_id
    assert len(report["steps"]) == 1
    assert report["steps"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_runner_captures_exceptions(sre_profile, sre_topology):
    """A step that raises an exception is reported as FAILED, not silently skipped."""

    async def always_fails(profile, topology):
        raise RuntimeError("intentional failure")

    runner = ProvisioningRunner(sre_profile, sre_topology, steps=[always_fails])
    report = await runner.run()
    assert report["steps"][0]["status"] == "failed"
    assert "intentional failure" in report["steps"][0]["error"]


@pytest.mark.asyncio
async def test_runner_summary_counts(sre_profile, sre_topology):
    """Summary correctly counts per-status results."""

    async def success_step(profile, topology):
        return StepResult(name="s", status=StepStatus.SUCCESS)

    async def skipped_step(profile, topology):
        return StepResult(name="sk", status=StepStatus.SKIPPED)

    async def failed_step(profile, topology):
        return StepResult(name="f", status=StepStatus.FAILED, error="err")

    runner = ProvisioningRunner(
        sre_profile, sre_topology,
        steps=[success_step, skipped_step, failed_step],
    )
    report = await runner.run()
    assert report["summary"]["success"] == 1
    assert report["summary"]["skipped"] == 1
    assert report["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_runner_partial_status(sre_profile, sre_topology):
    """NEEDS_APPROVAL step does not count as failure."""

    async def approval_step(profile, topology):
        return StepResult(name="github", status=StepStatus.NEEDS_APPROVAL,
                          details={"reason": "user not found"})

    runner = ProvisioningRunner(sre_profile, sre_topology, steps=[approval_step])
    report = await runner.run()
    assert report["summary"]["needs_approval"] == 1
    assert report["summary"]["failed"] == 0


@pytest.mark.asyncio
async def test_github_step_skipped_without_env(sre_profile, sre_topology, monkeypatch):
    """GitHub step returns SKIPPED when env vars are not set."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_ORG", raising=False)

    from sentinel.provisioning.steps.github import run
    result = await run(sre_profile, sre_topology)
    assert result.status == StepStatus.SKIPPED


@pytest.mark.asyncio
async def test_vault_step_skipped_without_env(sre_profile, sre_topology, monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    from sentinel.provisioning.steps.vault import run
    result = await run(sre_profile, sre_topology)
    assert result.status == StepStatus.SKIPPED


@pytest.mark.asyncio
async def test_kubernetes_step_skipped_without_env(sre_profile, sre_topology, monkeypatch):
    monkeypatch.delenv("KUBE_CLUSTERS", raising=False)

    from sentinel.provisioning.steps.kubernetes import run
    result = await run(sre_profile, sre_topology)
    assert result.status == StepStatus.SKIPPED
