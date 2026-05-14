"""BambooHR catalog adapter. Reads from the BambooHR REST API v1."""

from __future__ import annotations

import os
from base64 import b64encode
from datetime import date, datetime
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from sentinel.catalog.base import (
    CatalogAdapter,
    CatalogError,
    EmployeeNotFound,
    EmployeeProfile,
)

log = structlog.get_logger(__name__)


class BambooHRCatalog(CatalogAdapter):
    """
    BambooHR catalog adapter.

    Auth: API key passed as HTTP Basic username (password = 'x').
    Endpoint: https://api.bamboohr.com/api/gateway.php/{subdomain}/v1/

    Field mapping:
        workEmail       -> employee_id
        displayName     -> full_name
        department      -> team
        jobTitle        -> role
        supervisorEmail -> manager_id
        hireDate        -> start_date
    """

    name = "bamboohr"

    def __init__(
        self,
        subdomain: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.subdomain = subdomain or os.environ["BAMBOOHR_SUBDOMAIN"]
        api_key = api_key or os.environ["BAMBOOHR_API_KEY"]
        token = b64encode(f"{api_key}:x".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=f"https://api.bamboohr.com/api/gateway.php/{self.subdomain}/v1",
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch(self, employee_id: str) -> EmployeeProfile:
        """
        BambooHR doesn't support lookup-by-email directly. We search the
        employee directory and match. For large orgs, cache the directory
        response (not implemented here — add Redis if needed).
        """
        resp = await self._client.get(
            "/employees/directory",
            params={"fields": "workEmail,displayName,department,jobTitle,supervisorEmail,hireDate"},
        )
        if resp.status_code >= 400:
            raise CatalogError(f"bamboohr returned {resp.status_code}: {resp.text}")

        employees = resp.json().get("employees", [])
        for emp in employees:
            if (emp.get("workEmail") or "").lower() == employee_id.lower():
                return self._to_profile(emp)

        raise EmployeeNotFound(employee_id)

    async def list_recent_hires(self, since: date) -> list[EmployeeProfile]:
        resp = await self._client.get(
            "/employees/directory",
            params={"fields": "workEmail,displayName,department,jobTitle,supervisorEmail,hireDate"},
        )
        resp.raise_for_status()
        employees = resp.json().get("employees", [])
        profiles = []
        for emp in employees:
            try:
                p = self._to_profile(emp)
                if p.start_date >= since:
                    profiles.append(p)
            except (CatalogError, KeyError):
                continue
        return profiles

    def _to_profile(self, emp: dict[str, Any]) -> EmployeeProfile:
        try:
            hire_raw = emp.get("hireDate") or ""
            start_date = (
                datetime.strptime(hire_raw, "%Y-%m-%d").date()
                if hire_raw
                else date.today()
            )
            return EmployeeProfile(
                employee_id=emp["workEmail"],
                full_name=emp.get("displayName") or emp["workEmail"],
                team=emp.get("department") or "unknown",
                role=emp.get("jobTitle") or "unknown",
                manager_id=emp.get("supervisorEmail"),
                start_date=start_date,
                raw=emp,
            )
        except KeyError as e:
            raise CatalogError(f"missing required bamboohr field: {e}") from e

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/employees/directory")
            return resp.status_code == 200
        except Exception as e:
            log.warning("bamboohr health check failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._client.aclose()
