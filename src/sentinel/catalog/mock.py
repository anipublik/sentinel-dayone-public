"""
Mock catalog adapter for local development and testing.

Activate with CATALOG_SOURCE=mock. Pre-populates three employees
(one per persona) so you can drive the full flow without a real HRIS.
"""

from __future__ import annotations

from datetime import date

from sentinel.catalog.base import CatalogAdapter, EmployeeNotFound, EmployeeProfile

_MOCK_EMPLOYEES: dict[str, EmployeeProfile] = {
    "sre@example.com": EmployeeProfile(
        employee_id="sre@example.com",
        full_name="Alex Chen",
        team="Platform Engineering",
        role="Site Reliability Engineer",
        manager_id="manager@example.com",
        buddy_id="buddy@example.com",
        start_date=date.today(),
        provisioned_apps=["github", "vault", "grafana"],
        business_functions=["SRE", "Platform"],
    ),
    "backend@example.com": EmployeeProfile(
        employee_id="backend@example.com",
        full_name="Jordan Rivera",
        team="Payments",
        role="Software Engineer",
        manager_id="manager@example.com",
        buddy_id="buddy@example.com",
        start_date=date.today(),
        provisioned_apps=["github", "jira"],
        business_functions=["Backend"],
    ),
    "qa@example.com": EmployeeProfile(
        employee_id="qa@example.com",
        full_name="Sam Patel",
        team="Quality Engineering",
        role="QA Engineer",
        manager_id="manager@example.com",
        buddy_id="buddy@example.com",
        start_date=date.today(),
        provisioned_apps=["github", "jira", "testRail"],
        business_functions=["QA"],
    ),
}


class MockCatalog(CatalogAdapter):
    """
    Mock catalog for dev/CI. Accepts any email from _MOCK_EMPLOYEES.
    You can extend SENTINEL_MOCK_EMPLOYEES env var as JSON to add more.
    """

    name = "mock"

    def __init__(self) -> None:
        import json
        import os

        extra_raw = os.environ.get("SENTINEL_MOCK_EMPLOYEES", "")
        self._db: dict[str, EmployeeProfile] = dict(_MOCK_EMPLOYEES)
        if extra_raw:
            try:
                extra: dict[str, dict] = json.loads(extra_raw)
                for eid, data in extra.items():
                    self._db[eid] = EmployeeProfile(
                        employee_id=eid,
                        full_name=data.get("full_name", eid),
                        team=data.get("team", "unknown"),
                        role=data.get("role", "Software Engineer"),
                        manager_id=data.get("manager_id"),
                        start_date=date.fromisoformat(
                            data.get("start_date", date.today().isoformat())
                        ),
                        provisioned_apps=data.get("provisioned_apps", []),
                    )
            except Exception:
                pass  # bad JSON — ignore, keep defaults

    async def fetch(self, employee_id: str) -> EmployeeProfile:
        profile = self._db.get(employee_id.lower())
        if not profile:
            raise EmployeeNotFound(employee_id)
        return profile

    async def list_recent_hires(self, since: date) -> list[EmployeeProfile]:
        return [p for p in self._db.values() if p.start_date >= since]
