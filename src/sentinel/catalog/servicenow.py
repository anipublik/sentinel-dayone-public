"""ServiceNow catalog adapter. Reads from the sys_user table and related tables."""

from __future__ import annotations

import os
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


class ServiceNowCatalog(CatalogAdapter):
    """
    ServiceNow catalog adapter.

    ServiceNow exposes sys_user as the canonical employee record. Related
    tables (sys_user_group, sys_user_role) provide team membership and
    application access. Auth is basic auth or OAuth2 depending on how
    the instance is configured.

    Field mapping:
        email                -> employee_id
        name                 -> full_name
        department.name      -> team
        title                -> role
        manager.email        -> manager_id
        start_date           -> start_date
        u_provisioned_apps   -> provisioned_apps   (custom field)
    """

    name = "servicenow"

    def __init__(
        self,
        instance_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.instance_url = (instance_url or os.environ["SNOW_INSTANCE_URL"]).rstrip("/")
        self.username = username or os.environ["SNOW_USERNAME"]
        self.password = password or os.environ["SNOW_PASSWORD"]
        self._client = httpx.AsyncClient(
            timeout=30.0,
            auth=(self.username, self.password),
            headers={"Accept": "application/json"},
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch(self, employee_id: str) -> EmployeeProfile:
        resp = await self._client.get(
            f"{self.instance_url}/api/now/table/sys_user",
            params={
                "sysparm_query": f"email={employee_id}",
                "sysparm_display_value": "true",
                "sysparm_limit": "1",
            },
        )
        if resp.status_code >= 400:
            raise CatalogError(f"servicenow returned {resp.status_code}: {resp.text}")

        result = resp.json().get("result", [])
        if not result:
            raise EmployeeNotFound(employee_id)

        user = result[0]
        # Hydrate group memberships (these encode team / function assignments)
        groups = await self._fetch_groups(user["sys_id"])
        return self._to_profile(user, groups)

    async def list_recent_hires(self, since: date) -> list[EmployeeProfile]:
        resp = await self._client.get(
            f"{self.instance_url}/api/now/table/sys_user",
            params={
                "sysparm_query": f"start_date>={since.isoformat()}",
                "sysparm_display_value": "true",
            },
        )
        resp.raise_for_status()
        users = resp.json().get("result", [])
        profiles: list[EmployeeProfile] = []
        for u in users:
            groups = await self._fetch_groups(u["sys_id"])
            profiles.append(self._to_profile(u, groups))
        return profiles

    async def _fetch_groups(self, user_sys_id: str) -> list[str]:
        resp = await self._client.get(
            f"{self.instance_url}/api/now/table/sys_user_grmember",
            params={
                "sysparm_query": f"user={user_sys_id}",
                "sysparm_display_value": "true",
                "sysparm_fields": "group",
            },
        )
        if resp.status_code >= 400:
            return []
        return [g["group"] for g in resp.json().get("result", []) if g.get("group")]

    def _to_profile(self, u: dict[str, Any], groups: list[str]) -> EmployeeProfile:
        try:
            start_raw = u.get("start_date") or u.get("u_hire_date") or ""
            start_date = (
                datetime.strptime(start_raw, "%Y-%m-%d").date()
                if start_raw
                else date.today()
            )
            manager = u.get("manager", {})
            manager_id = manager.get("email") if isinstance(manager, dict) else None

            return EmployeeProfile(
                employee_id=u["email"],
                full_name=u["name"],
                team=u.get("department", {}).get("name", "unknown")
                    if isinstance(u.get("department"), dict)
                    else (u.get("department") or "unknown"),
                role=u.get("title", "unknown"),
                manager_id=manager_id,
                start_date=start_date,
                provisioned_apps=(u.get("u_provisioned_apps") or "").split(",")
                    if u.get("u_provisioned_apps")
                    else [],
                business_functions=groups,
                raw=u,
            )
        except KeyError as e:
            raise CatalogError(f"missing required servicenow field: {e}") from e

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self.instance_url}/api/now/table/sys_user",
                params={"sysparm_limit": "1"},
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning("servicenow health check failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._client.aclose()
