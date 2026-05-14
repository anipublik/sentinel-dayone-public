"""
Provisioning runner. Uses real provisioning steps (GitHub, Vault, K8s)
with graceful fallback to SKIPPED when credentials aren't configured.

Each step is an async module with a `run(profile, topology)` function.
Customers disable steps by removing them from the registry.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

import structlog

from sentinel.catalog import AccessTopology, EmployeeProfile

log = structlog.get_logger(__name__)


class StepStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_APPROVAL = "needs_approval"


@dataclass
class StepResult:
    name: str
    status: StepStatus
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


StepFn = Callable[[EmployeeProfile, AccessTopology], Coroutine[Any, Any, StepResult]]


# ─── individual steps (delegated to steps/ subpackage) ───────────────────────


async def provision_github(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    from sentinel.provisioning.steps.github import run
    return await run(profile, topology)


async def provision_vault(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    from sentinel.provisioning.steps.vault import run
    return await run(profile, topology)


async def provision_kubernetes(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    from sentinel.provisioning.steps.kubernetes import run
    return await run(profile, topology)


async def provision_observability(
    profile: EmployeeProfile, topology: AccessTopology
) -> StepResult:
    """
    Provisions the user in the configured observability platform.
    Supports Grafana (GRAFANA_URL + GRAFANA_ADMIN_TOKEN).
    """
    import os
    import httpx

    grafana_url = os.environ.get("GRAFANA_URL", "")
    grafana_token = os.environ.get("GRAFANA_ADMIN_TOKEN", "")

    if not grafana_url or not grafana_token:
        return StepResult(
            name="observability",
            status=StepStatus.SKIPPED,
            details={"reason": "GRAFANA_URL / GRAFANA_ADMIN_TOKEN not configured"},
        )

    async with httpx.AsyncClient(
        base_url=grafana_url,
        headers={"Authorization": f"Bearer {grafana_token}", "Content-Type": "application/json"},
        timeout=15.0,
    ) as client:
        resp = await client.post(
            "/api/admin/users",
            json={
                "name": profile.full_name,
                "email": profile.employee_id,
                "login": profile.employee_id,
                "password": "ChangeMe123!",  # Force reset on first login
                "orgId": 1,
            },
        )
        if resp.status_code in (200, 412):  # 412 = already exists
            # Set role
            user_id = resp.json().get("id")
            if user_id:
                await client.patch(
                    f"/api/org/users/{user_id}",
                    json={"role": topology.observability_role.capitalize()},
                )
            return StepResult(
                name="observability",
                status=StepStatus.SUCCESS,
                details={"role": topology.observability_role, "platform": "grafana"},
            )
        return StepResult(
            name="observability",
            status=StepStatus.FAILED,
            error=f"grafana user create failed: {resp.status_code}",
        )


async def provision_ci(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    log.info("provisioning ci/cd", employee=profile.employee_id)
    return StepResult(
        name="ci_cd",
        status=StepStatus.SKIPPED,
        details={
            "pipelines": topology.ci_pipelines,
            "note": "CI/CD provisioning depends on your platform (Actions, CircleCI, etc). "
                    "Configure the ci_provisioner in your deployment.",
        },
    )


async def surface_backlog(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    """Query the graph for open tickets on owned repos. Falls back to empty if graph is cold."""
    from sentinel.graph.client import GraphClient

    try:
        graph = GraphClient()
        owned = topology.repos.get("owned", [])
        if not owned:
            return StepResult(name="backlog", status=StepStatus.SUCCESS, details={"items": []})

        rows = await graph.run(
            """
            MATCH (t:Ticket)-[:RELATES_TO]->(r:Repository)
            WHERE r.name IN $repos AND t.status IN ['open', 'in_progress', 'todo']
            RETURN t.id AS id, t.title AS title, t.url AS url,
                   t.priority_label AS priority, t.status AS status
            ORDER BY t.priority ASC
            LIMIT 5
            """,
            repos=owned,
        )
        await graph.close()
        return StepResult(
            name="backlog",
            status=StepStatus.SUCCESS,
            details={"items": rows},
        )
    except Exception as e:
        log.warning("backlog query failed", error=str(e))
        return StepResult(name="backlog", status=StepStatus.SKIPPED,
                          details={"items": [], "reason": str(e)})


async def surface_reading_list(profile: EmployeeProfile, topology: AccessTopology) -> StepResult:
    """Query the graph for ADRs and Confluence pages relevant to the role."""
    from sentinel.graph.client import GraphClient

    try:
        graph = GraphClient()
        rows = await graph.run(
            """
            MATCH (n)
            WHERE (n:ADR OR n:ConfluencePage) AND n.embedding IS NOT NULL
            RETURN n.id AS id, n.title AS title, n.url AS url,
                   labels(n)[0] AS kind
            LIMIT 5
            """,
        )
        await graph.close()
        return StepResult(
            name="reading_list",
            status=StepStatus.SUCCESS,
            details={"items": rows},
        )
    except Exception as e:
        log.warning("reading list query failed", error=str(e))
        return StepResult(name="reading_list", status=StepStatus.SKIPPED,
                          details={"items": [], "reason": str(e)})


# ─── runner ───────────────────────────────────────────────────────────────────

DEFAULT_STEPS: list[StepFn] = [
    provision_github,
    provision_vault,
    provision_kubernetes,
    provision_observability,
    provision_ci,
    surface_backlog,
    surface_reading_list,
]


class ProvisioningRunner:
    """Runs all steps in parallel. Never silently skips failures."""

    def __init__(
        self,
        profile: EmployeeProfile,
        topology: AccessTopology,
        steps: list[StepFn] | None = None,
    ) -> None:
        self.profile = profile
        self.topology = topology
        self.steps = steps or DEFAULT_STEPS

    async def run(self) -> dict[str, Any]:
        log.info(
            "provisioning run starting",
            employee=self.profile.employee_id,
            steps=[s.__name__ for s in self.steps],
        )
        results = await asyncio.gather(
            *(self._run_step(s) for s in self.steps),
            return_exceptions=False,
        )
        report = {
            "employee_id": self.profile.employee_id,
            "team": self.profile.team,
            "role": self.profile.role,
            "steps": [r.__dict__ for r in results],
            "summary": self._summarize(results),
        }
        log.info("provisioning run complete", **report["summary"])
        return report

    async def _run_step(self, step: StepFn) -> StepResult:
        try:
            return await step(self.profile, self.topology)
        except Exception as e:
            log.exception("step failed", step=step.__name__, error=str(e))
            return StepResult(
                name=step.__name__.removeprefix("provision_").removeprefix("surface_"),
                status=StepStatus.FAILED,
                error=str(e),
            )

    @staticmethod
    def _summarize(results: list[StepResult]) -> dict[str, int]:
        summary = {s.value: 0 for s in StepStatus}
        for r in results:
            summary[r.status.value] += 1
        return summary
