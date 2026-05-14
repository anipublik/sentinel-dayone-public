"""Tests for catalog adapters and compute_access_topology."""

from __future__ import annotations

from datetime import date

import pytest

from sentinel.catalog import compute_access_topology
from sentinel.catalog.base import EmployeeProfile
from sentinel.catalog.mock import MockCatalog


@pytest.mark.asyncio
async def test_mock_catalog_fetch_sre():
    cat = MockCatalog()
    profile = await cat.fetch("sre@example.com")
    assert profile.full_name == "Alex Chen"
    assert profile.role == "Site Reliability Engineer"


@pytest.mark.asyncio
async def test_mock_catalog_fetch_not_found():
    cat = MockCatalog()
    from sentinel.catalog.base import EmployeeNotFound
    with pytest.raises(EmployeeNotFound):
        await cat.fetch("nobody@example.com")


@pytest.mark.asyncio
async def test_mock_catalog_list_recent_hires():
    cat = MockCatalog()
    hires = await cat.list_recent_hires(since=date(2020, 1, 1))
    assert len(hires) >= 3


def test_compute_topology_sre(sre_profile, tmp_path):
    roles_yaml = tmp_path / "roles.yaml"
    roles_yaml.write_text("""
roles:
  sre:
    repos:
      owned: [infra-core]
      shared_write: []
      read: [payments-api]
    test_repos:
      owned: [chaos-tests]
      read: []
    shared_libs: [reliability-libs]
    clusters: [prod-us-east, staging]
    vault_scope: sre/platform
    observability_role: admin
    ci_pipelines: {}
    alert_rules: owner
    test_management: read
""")
    topology = compute_access_topology(sre_profile, roles_config_path=roles_yaml)
    assert "infra-core" in topology.repos["owned"]
    assert topology.vault_scope == "sre/platform"
    assert topology.observability_role == "admin"
    assert "prod-us-east" in topology.clusters


def test_compute_topology_backend_substitution(backend_profile, tmp_path):
    roles_yaml = tmp_path / "roles.yaml"
    roles_yaml.write_text("""
roles:
  backend:
    repos:
      owned: ["${team_repo}"]
      shared_write: []
      read: [shared-libs]
    test_repos:
      owned: []
      read: []
    shared_libs: []
    clusters: [staging, dev]
    vault_scope: "app/${team_repo}"
    observability_role: viewer
    ci_pipelines: {}
    alert_rules: read
    test_management: read
""")
    topology = compute_access_topology(backend_profile, roles_config_path=roles_yaml)
    # team = "Payments" -> team_slug = "payments" -> team_repo = "payments-svc"
    assert "payments-svc" in topology.repos["owned"]
    assert topology.vault_scope == "app/payments-svc"


def test_compute_topology_role_fuzzy_match(tmp_path):
    profile = EmployeeProfile(
        employee_id="x@x.com",
        full_name="X",
        team="SRE",
        role="Platform Engineer",  # Not exactly "sre"
        manager_id=None,
        start_date=date.today(),
    )
    roles_yaml = tmp_path / "roles.yaml"
    roles_yaml.write_text("""
roles:
  sre:
    repos:
      owned: [infra-core]
      shared_write: []
      read: []
    test_repos:
      owned: []
      read: []
    shared_libs: []
    clusters: [prod]
    vault_scope: sre/platform
    observability_role: admin
    ci_pipelines: {}
    alert_rules: owner
    test_management: read
""")
    topology = compute_access_topology(profile, roles_config_path=roles_yaml)
    assert topology.vault_scope == "sre/platform"


def test_compute_topology_missing_roles_yaml(sre_profile, tmp_path):
    """Missing roles.yaml returns minimal topology, not an exception."""
    topology = compute_access_topology(sre_profile, roles_config_path=tmp_path / "nonexistent.yaml")
    assert isinstance(topology.repos, dict)
    assert isinstance(topology.clusters, list)
