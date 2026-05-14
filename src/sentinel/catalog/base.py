"""
Catalog adapter base class.

The catalog is the identity source. Sentinel reads the new hire's profile
from whatever HRIS or ITSM the customer runs (ServiceNow, Workday,
SharePoint / Azure AD, BambooHR, etc.) and translates the raw catalog
data into a normalized EmployeeProfile.

Concrete adapters live in this package. They implement fetch() against
their respective APIs and map source fields to the normalized model.

The mapping layer (config/roles.yaml) turns the EmployeeProfile into an
AccessTopology. That separation is intentional: catalogs change slowly,
access topology mappings change with team structure, and we don't want
to bake one into the other.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class EmployeeProfile:
    """Normalized employee record. Every catalog adapter returns one of these."""

    employee_id: str          # canonical id (email, employee number, AD guid)
    full_name: str
    team: str
    role: str                 # job profile / title as the catalog knows it
    manager_id: str | None
    start_date: date
    buddy_id: str | None = None
    provisioned_apps: list[str] = field(default_factory=list)
    business_functions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # source-system record, kept for debugging


@dataclass
class AccessTopology:
    """
    What the new hire actually gets. Computed by the role mapping layer
    from an EmployeeProfile. The provisioning runner reads this directly.
    """

    repos: dict[str, list[str]]       # {"owned": [...], "shared_write": [...], "read": [...]}
    test_repos: dict[str, list[str]]  # {"owned": [...], "read": [...]}
    shared_libs: list[str]            # libs the role co-owns or maintains
    clusters: list[str]               # kubeconfig contexts
    vault_scope: str                  # vault path the role is granted
    observability_role: str           # admin / editor / viewer
    ci_pipelines: dict[str, str]      # {pipeline_name: access_tier}
    alert_rules: str                  # owner / read / none
    test_management: str              # owner / read / none


class CatalogAdapter(ABC):
    """
    Abstract catalog adapter. Implement fetch() per source system.

    Adapters are stateless and idempotent. Sentinel may call fetch()
    repeatedly for the same employee (e.g., during role drift reconciliation),
    so they must always return current truth from the source system, not
    cached data.
    """

    name: str = "abstract"

    @abstractmethod
    async def fetch(self, employee_id: str) -> EmployeeProfile:
        """Fetch the current employee profile from the source system."""
        ...

    @abstractmethod
    async def list_recent_hires(self, since: date) -> list[EmployeeProfile]:
        """Return all employees with a start_date >= since. Used for backfill."""
        ...

    async def health_check(self) -> bool:
        """Optional: verify the adapter can reach its source system."""
        return True


class CatalogError(Exception):
    """Raised when a catalog adapter can't fetch what it was asked for."""


class EmployeeNotFound(CatalogError):
    """Raised when the requested employee_id doesn't exist in the catalog."""
