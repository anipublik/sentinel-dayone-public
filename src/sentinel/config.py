"""
Centralized configuration loader.

Priority order: environment variables > catalog.yaml > hardcoded defaults.
All downstream code reads from a Config instance, never from os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_list(key: str, default: list[str] | None = None) -> list[str]:
    val = os.environ.get(key, "")
    if val:
        return [v.strip() for v in val.split(",") if v.strip()]
    return default or []


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, "").lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


@dataclass
class CatalogConfig:
    source: str = "mock"  # workday | servicenow | bamboohr | mock
    # Workday
    workday_tenant_url: str = ""
    workday_client_id: str = ""
    workday_client_secret: str = ""
    # ServiceNow
    snow_instance_url: str = ""
    snow_username: str = ""
    snow_password: str = ""
    # BambooHR
    bamboohr_subdomain: str = ""
    bamboohr_api_key: str = ""
    # Field mapping (from catalog.yaml)
    field_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class GraphConfig:
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "sentinel-dev"
    embedding_model: str = "text-embedding-3-large"
    openai_api_key: str = ""
    auto_apply_schema: bool = True


@dataclass
class AgentConfig:
    anthropic_api_key: str = ""
    model: str = "claude-sonnet-4-5"
    max_tokens_ask: int = 1024
    max_tokens_provision: int = 1500


@dataclass
class ConnectorConfig:
    # GitHub
    github_org: str = ""
    github_token: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""
    # Slack
    slack_bot_token: str = ""
    slack_excluded_channels: list[str] = field(
        default_factory=lambda: ["hr-", "compensation", "performance", "legal-", "finance-"]
    )
    slack_webhook_secret: str = ""
    # Linear
    linear_api_key: str = ""
    linear_team_ids: list[str] = field(default_factory=list)
    linear_webhook_secret: str = ""
    # Confluence
    confluence_url: str = ""
    confluence_user: str = ""
    confluence_api_token: str = ""
    confluence_space_keys: list[str] = field(default_factory=list)
    # Vault
    vault_addr: str = ""
    vault_token: str = ""
    vault_role_id: str = ""
    vault_secret_id: str = ""
    # Kubernetes
    kubeconfig_bucket: str = ""
    kube_clusters: dict[str, str] = field(default_factory=dict)  # {name: api_server_url}
    # Observability (Grafana/Datadog/Dynatrace)
    grafana_url: str = ""
    grafana_admin_token: str = ""
    datadog_api_key: str = ""
    datadog_app_key: str = ""


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    webhook_secret: str = ""
    frontend_enabled: bool = True
    metrics_enabled: bool = True
    log_level: str = "info"


@dataclass
class Config:
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    connectors: ConnectorConfig = field(default_factory=ConnectorConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def load_config(config_dir: Path | str = "config") -> Config:
    """
    Load config. Reads catalog.yaml for catalog-specific field mappings.
    Env vars always override YAML.
    """
    config_dir = Path(config_dir)
    catalog_yaml: dict[str, Any] = {}
    catalog_path = config_dir / "catalog.yaml"
    if catalog_path.exists():
        with open(catalog_path) as f:
            catalog_yaml = yaml.safe_load(f) or {}

    auth_cfg = catalog_yaml.get("auth", {})

    catalog_source = _env(
        "CATALOG_SOURCE", catalog_yaml.get("catalog", "mock")
    )

    return Config(
        catalog=CatalogConfig(
            source=catalog_source,
            workday_tenant_url=_env(
                "WORKDAY_TENANT_URL",
                catalog_yaml.get("endpoint", ""),
            ),
            workday_client_id=_env(
                "WORKDAY_CLIENT_ID",
                os.environ.get(auth_cfg.get("client_id_env", ""), ""),
            ),
            workday_client_secret=_env(
                "WORKDAY_CLIENT_SECRET",
                os.environ.get(auth_cfg.get("client_secret_env", ""), ""),
            ),
            snow_instance_url=_env("SNOW_INSTANCE_URL"),
            snow_username=_env("SNOW_USERNAME"),
            snow_password=_env("SNOW_PASSWORD"),
            bamboohr_subdomain=_env("BAMBOOHR_SUBDOMAIN"),
            bamboohr_api_key=_env("BAMBOOHR_API_KEY"),
            field_mapping=catalog_yaml.get("field_mapping", {}),
        ),
        graph=GraphConfig(
            neo4j_uri=_env("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=_env("NEO4J_USER", "neo4j"),
            neo4j_password=_env("NEO4J_PASSWORD", "sentinel-dev"),
            embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-large"),
            openai_api_key=_env("OPENAI_API_KEY"),
            auto_apply_schema=_env_bool("AUTO_APPLY_SCHEMA", True),
        ),
        agent=AgentConfig(
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            model=_env("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        ),
        connectors=ConnectorConfig(
            github_org=_env("GITHUB_ORG"),
            github_token=_env("GITHUB_TOKEN"),
            github_app_id=_env("GITHUB_APP_ID"),
            github_app_private_key=_env("GITHUB_APP_PRIVATE_KEY"),
            github_webhook_secret=_env("GITHUB_WEBHOOK_SECRET"),
            slack_bot_token=_env("SLACK_BOT_TOKEN"),
            slack_excluded_channels=_env_list(
                "SLACK_EXCLUDED_CHANNELS",
                ["hr-", "compensation", "performance", "legal-", "finance-"],
            ),
            slack_webhook_secret=_env("SLACK_WEBHOOK_SECRET"),
            linear_api_key=_env("LINEAR_API_KEY"),
            linear_team_ids=_env_list("LINEAR_TEAM_IDS"),
            linear_webhook_secret=_env("LINEAR_WEBHOOK_SECRET"),
            confluence_url=_env("CONFLUENCE_URL"),
            confluence_user=_env("CONFLUENCE_USER"),
            confluence_api_token=_env("CONFLUENCE_API_TOKEN"),
            confluence_space_keys=_env_list("CONFLUENCE_SPACE_KEYS"),
            vault_addr=_env("VAULT_ADDR"),
            vault_token=_env("VAULT_TOKEN"),
            vault_role_id=_env("VAULT_ROLE_ID"),
            vault_secret_id=_env("VAULT_SECRET_ID"),
            kubeconfig_bucket=_env("KUBECONFIG_BUCKET"),
            grafana_url=_env("GRAFANA_URL"),
            grafana_admin_token=_env("GRAFANA_ADMIN_TOKEN"),
            datadog_api_key=_env("DATADOG_API_KEY"),
            datadog_app_key=_env("DATADOG_APP_KEY"),
        ),
        server=ServerConfig(
            host=_env("HOST", "0.0.0.0"),
            port=int(_env("PORT", "8080")),
            cors_origins=_env_list("CORS_ORIGINS", ["*"]),
            webhook_secret=_env("WEBHOOK_SECRET"),
            frontend_enabled=_env_bool("FRONTEND_ENABLED", True),
            metrics_enabled=_env_bool("METRICS_ENABLED", True),
            log_level=_env("LOG_LEVEL", "info"),
        ),
    )


# Module-level singleton — populated by api/main.py lifespan
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg
