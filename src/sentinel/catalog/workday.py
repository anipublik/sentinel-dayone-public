"""Workday catalog adapter. Reads from Workday's REST API."""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from sentinel.catalog.base import (
    AccessTopology,  # noqa: F401
    CatalogAdapter,
    CatalogError,
    EmployeeNotFound,
    EmployeeProfile,
)

log = structlog.get_logger(__name__)


class WorkdayCatalog(CatalogAdapter):
    """
    Workday catalog adapter. OAuth2 client_credentials flow.

    Field mapping (Workday -> EmployeeProfile):
        descriptor              -> full_name
        workerID / primaryWorkEmail -> employee_id
        organization.descriptor -> team
        jobProfile.descriptor   -> role
        manager.primaryWorkEmail -> manager_id
        hireDate                -> start_date
    """

    name = "workday"

    def __init__(
        self,
        tenant_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self.tenant_url = (tenant_url or os.environ["WORKDAY_TENANT_URL"]).rstrip("/")
        self.client_id = client_id or os.environ["WORKDAY_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["WORKDAY_CLIENT_SECRET"]
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _auth(self) -> str:
        """Return a valid access token. Refreshes automatically before expiry."""
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token

        resp = await self._client.post(
            f"{self.tenant_url}/ccx/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        # expires_in is in seconds; default 3600 if Workday omits it
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + expires_in
        log.debug("workday token refreshed", expires_in=expires_in)
        return self._token  # type: ignore[return-value]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch(self, employee_id: str) -> EmployeeProfile:
        token = await self._auth()
        resp = await self._client.get(
            f"{self.tenant_url}/ccx/api/v2/workers",
            params={"primaryWorkEmail": employee_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            # Token may have been revoked — force refresh on next call
            self._token = None
            raise CatalogError("workday 401 — token revoked, will retry")
        if resp.status_code == 404:
            raise EmployeeNotFound(employee_id)
        if resp.status_code >= 400:
            raise CatalogError(f"workday returned {resp.status_code}: {resp.text}")

        data = resp.json().get("data", [])
        if not data:
            raise EmployeeNotFound(employee_id)
        return self._to_profile(data[0])

    async def list_recent_hires(self, since: date) -> list[EmployeeProfile]:
        token = await self._auth()
        resp = await self._client.get(
            f"{self.tenant_url}/ccx/api/v2/workers",
            params={"hireDateFrom": since.isoformat()},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return [self._to_profile(w) for w in resp.json().get("data", [])]

    def _to_profile(self, w: dict[str, Any]) -> EmployeeProfile:
        try:
            return EmployeeProfile(
                employee_id=w.get("primaryWorkEmail") or w["workerID"],
                full_name=w["descriptor"],
                team=w.get("organization", {}).get("descriptor", "unknown"),
                role=w.get("jobProfile", {}).get("descriptor", "unknown"),
                manager_id=w.get("manager", {}).get("primaryWorkEmail"),
                start_date=datetime.fromisoformat(w["hireDate"]).date(),
                provisioned_apps=[
                    app["descriptor"] for app in w.get("assignedApplications", [])
                ],
                business_functions=[
                    fn["descriptor"] for fn in w.get("businessFunctions", [])
                ],
                raw=w,
            )
        except KeyError as e:
            raise CatalogError(f"missing required workday field: {e}") from e

    async def health_check(self) -> bool:
        try:
            token = await self._auth()
            resp = await self._client.get(
                f"{self.tenant_url}/ccx/api/v2/workers",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning("workday health check failed", error=str(e))
            return False

    async def close(self) -> None:
        await self._client.aclose()
