"""Catalog adapters and the role mapping layer."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from sentinel.catalog.base import (
    AccessTopology,
    CatalogAdapter,
    CatalogError,
    EmployeeNotFound,
    EmployeeProfile,
)
from sentinel.catalog.bamboohr import BambooHRCatalog
from sentinel.catalog.mock import MockCatalog
from sentinel.catalog.servicenow import ServiceNowCatalog
from sentinel.catalog.workday import WorkdayCatalog

__all__ = [
    "AccessTopology",
    "CatalogAdapter",
    "CatalogError",
    "EmployeeNotFound",
    "EmployeeProfile",
    "get_catalog",
    "compute_access_topology",
]

_REGISTRY: dict[str, type[CatalogAdapter]] = {
    "workday": WorkdayCatalog,
    "servicenow": ServiceNowCatalog,
    "bamboohr": BambooHRCatalog,
    "mock": MockCatalog,
}


def get_catalog(name: str | None = None) -> CatalogAdapter:
    """
    Return a catalog adapter. Reads CATALOG_SOURCE env var if name is not given.
    Defaults to 'mock' so the system works out of the box without a real HRIS.
    """
    name = name or os.environ.get("CATALOG_SOURCE", "mock").lower()
    if name not in _REGISTRY:
        raise CatalogError(
            f"unknown catalog '{name}'. registered: {list(_REGISTRY)}"
        )
    return _REGISTRY[name]()


def _substitute(value: str, ctx: dict[str, str]) -> str:
    for k, v in ctx.items():
        value = value.replace(f"${{{k}}}", v)
    return value


def _substitute_list(items: list[str], ctx: dict[str, str]) -> list[str]:
    return [_substitute(i, ctx) for i in items]


def compute_access_topology(
    profile: EmployeeProfile,
    roles_config_path: Path | str = "config/roles.yaml",
) -> AccessTopology:
    """
    Translate a normalized EmployeeProfile into a concrete AccessTopology
    using the role mapping YAML. Fuzzy-matches profile role strings to YAML keys.
    """
    path = Path(roles_config_path)
    if not path.exists():
        # Fallback: minimal topology so provisioning can still proceed
        import structlog
        structlog.get_logger(__name__).warning(
            "roles.yaml not found, using minimal topology",
            path=str(path),
        )
        return _minimal_topology(profile)

    with open(path) as f:
        config = yaml.safe_load(f)

    role_key = _resolve_role_key(profile.role, list(config["roles"]))
    role_cfg = config["roles"][role_key]

    team_slug = profile.team.lower().replace(" ", "-")
    ctx = {
        "team": profile.team,
        "team_repo": f"{team_slug}-svc",
        "employee_id": profile.employee_id,
    }

    repos_cfg = role_cfg.get("repos", {})
    test_cfg = role_cfg.get("test_repos", {})

    return AccessTopology(
        repos={
            "owned": _substitute_list(repos_cfg.get("owned", []), ctx),
            "shared_write": _substitute_list(repos_cfg.get("shared_write", []), ctx),
            "read": _substitute_list(repos_cfg.get("read", []), ctx),
        },
        test_repos={
            "owned": _substitute_list(test_cfg.get("owned", []), ctx),
            "read": _substitute_list(test_cfg.get("read", []), ctx),
        },
        shared_libs=_substitute_list(role_cfg.get("shared_libs", []), ctx),
        clusters=role_cfg.get("clusters", []),
        vault_scope=_substitute(role_cfg.get("vault_scope", ""), ctx),
        observability_role=role_cfg.get("observability_role", "viewer"),
        ci_pipelines=role_cfg.get("ci_pipelines", {}),
        alert_rules=role_cfg.get("alert_rules", "none"),
        test_management=role_cfg.get("test_management", "none"),
    )


def _minimal_topology(profile: EmployeeProfile) -> AccessTopology:
    team_slug = profile.team.lower().replace(" ", "-")
    return AccessTopology(
        repos={"owned": [f"{team_slug}-svc"], "shared_write": [], "read": []},
        test_repos={"owned": [], "read": []},
        shared_libs=[],
        clusters=["staging", "dev"],
        vault_scope=f"app/{team_slug}-svc",
        observability_role="viewer",
        ci_pipelines={},
        alert_rules="none",
        test_management="none",
    )


def _resolve_role_key(profile_role: str, available: list[str]) -> str:
    """Fuzzy-match the catalog role string to a config key."""
    normalized = profile_role.lower()
    if normalized in available:
        return normalized

    keywords: dict[str, tuple[str, ...]] = {
        "sre": ("site reliability", "platform engineer", "devops", "sre", "infrastructure"),
        "backend": ("backend", "software engineer", "swe", "developer", "full stack"),
        "qa": ("qa", "quality", "test engineer", "sdet", "quality assurance"),
        "frontend": ("frontend", "ui engineer", "web engineer", "react", "vue"),
        "data": ("data engineer", "data scientist", "ml engineer", "mlops"),
    }
    for key, terms in keywords.items():
        if key in available and any(t in normalized for t in terms):
            return key

    # Last resort: first available key
    if available:
        import structlog
        structlog.get_logger(__name__).warning(
            "role not matched, falling back to first key",
            profile_role=profile_role,
            available=available,
            fallback=available[0],
        )
        return available[0]

    raise CatalogError(
        f"could not map role '{profile_role}' to any of {available}. "
        f"Add it to config/roles.yaml or extend the keyword heuristics."
    )
