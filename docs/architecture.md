# Architecture

Technical overview for contributors. For setup and usage, see the [README](../README.md). For the web UI, see [frontend.md](./frontend.md). For product background, see [this write-up](https://anisri.dev/insights/sentinel-day-one-onboarding-buddy).

## Layers

```
Catalog adapter  →  Neo4j graph  →  Retrieval engine  →  Agent (Claude)
                                                      ↘  FastAPI + React UI
```

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Identity | `sentinel.catalog` | Fetch hire profile; map role → `AccessTopology` via `config/roles.yaml` |
| Graph | `sentinel.graph` | Neo4j client, schema, ingestion pipelines, embeddings |
| Retrieval | `sentinel.retrieval` | Vector search, graph traversal, role scoping, `traversal_graph` |
| Agent | `sentinel.agent` | Only layer that calls Anthropic; ask / provision / recover |
| Provisioning | `sentinel.provisioning` | Parallel day-one steps (GitHub, Vault, K8s, …) |
| API | `sentinel.api` | FastAPI routes, webhooks, static UI from `frontend/dist/` |

The agent does not call connectors directly for retrieval or provisioning logic — those paths are deterministic Python.

## Graph model

Schema and indexes: `src/sentinel/graph/schema.cypher`.

**Node labels:** Person, Team, Service, Repository, PullRequest, Ticket, ADR, ConfluencePage, SlackThread, Incident, Runbook, AlertRule.

**Relationships** (examples): `OWNS`, `BELONGS_TO`, `CLOSES`, `REFERENCES`, `MENTIONS`, `TRIGGERED_BY`, `DEPENDS_ON`, `AUTHORED`, `MODIFIES`, `CO_OWNS`.

Ingestion sources: GitHub, Slack, Linear, Confluence (`sentinel.graph.ingestion`).

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Secrets, Neo4j, model keys, connector tokens |
| `config/roles.yaml` | Role → repos, clusters, vault scope, CI tiers |
| `config/catalog.yaml` | Catalog source and field mapping |

## API

OpenAPI: `/api/docs` when the server is running.

Key routes: `/api/v1/ask`, `/api/v1/provision`, `/api/v1/ingest`, `/api/v1/seed/drill`, `/api/v1/buddy-digest`, `/health`.

## Tests

```bash
pytest tests/ -q
ruff check src tests
cd frontend && npm run build
```
