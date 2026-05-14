"""Pytest fixtures shared across all tests."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel.catalog.base import AccessTopology, EmployeeProfile


@pytest.fixture
def sre_profile() -> EmployeeProfile:
    return EmployeeProfile(
        employee_id="sre@example.com",
        full_name="Alex Chen",
        team="Platform Engineering",
        role="Site Reliability Engineer",
        manager_id="manager@example.com",
        start_date=date(2024, 1, 15),
    )


@pytest.fixture
def backend_profile() -> EmployeeProfile:
    return EmployeeProfile(
        employee_id="backend@example.com",
        full_name="Jordan Rivera",
        team="Payments",
        role="Software Engineer",
        manager_id="manager@example.com",
        start_date=date(2024, 2, 1),
    )


@pytest.fixture
def sre_topology() -> AccessTopology:
    return AccessTopology(
        repos={"owned": ["infra-core", "k8s-platform"], "shared_write": [], "read": ["payments-api"]},
        test_repos={"owned": ["chaos-tests"], "read": []},
        shared_libs=["reliability-libs"],
        clusters=["prod-us-east", "staging"],
        vault_scope="sre/platform",
        observability_role="admin",
        ci_pipelines={"infra-*": "write"},
        alert_rules="owner",
        test_management="read",
    )


@pytest.fixture
def backend_topology() -> AccessTopology:
    return AccessTopology(
        repos={"owned": ["payments-svc"], "shared_write": [], "read": ["shared-libs"]},
        test_repos={"owned": [], "read": ["e2e-tests"]},
        shared_libs=[],
        clusters=["staging", "dev"],
        vault_scope="app/payments-svc",
        observability_role="viewer",
        ci_pipelines={"payments-svc-*": "write"},
        alert_rules="read",
        test_management="read",
    )


@pytest.fixture
def mock_graph() -> MagicMock:
    graph = MagicMock()
    graph.run = AsyncMock(return_value=[])
    graph.write = AsyncMock(return_value=None)
    graph.vector_search = AsyncMock(return_value=[])
    graph.close = AsyncMock(return_value=None)
    return graph


@pytest.fixture
def mock_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 3072)
    embedder.dims = 3072
    return embedder
