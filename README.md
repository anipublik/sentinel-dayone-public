# Sentinel Day One

Onboarding agent for engineering hires. Reads your employee catalog, provisions the new hire's environment on day one, and acts as the context retrieval layer over your engineering knowledge graph after that.

Works with ServiceNow, Workday, SharePoint / Azure AD, BambooHR, or any HRIS with an API.

**Background:** [Sentinel Day One — onboarding buddy](https://anisri.dev/insights/sentinel-day-one-onboarding-buddy)

## What it does

- Reads the new hire's profile from the employee catalog on day one
- Computes the full access topology (not just role-default) from a YAML mapping
- Provisions GitHub, Vault, Kubernetes, observability, and CI/CD access in parallel
- Surfaces the day-one backlog and reading list scoped to the role
- Stays available as the context retrieval agent for ongoing questions
- Cites source entities (Slack threads, PRs, ADRs, tickets) on every answer

## What it doesn't do

- Write code
- Pair-program
- Replace buddies or senior engineers
- Invent answers when the graph doesn't have them

## Architecture

Four layers. Each does one job.

```
Identity (catalog adapter)
    │
    ▼
Knowledge graph (Neo4j + embeddings)
    │
    ▼
Retrieval (vector entry + graph traversal + role scoping)
    │
    ▼
Agent (Claude Sonnet, retrieval-and-provisioning only)
    │
    ▼
Web UI (Vite + React)  ←  REST API (FastAPI)
```

See `docs/architecture.md` for a contributor-oriented overview. Frontend: `docs/frontend.md`.

## Project layout

```
sentinel-day-one/
├── src/sentinel/          # Python package (API, agent, retrieval, provisioning)
├── frontend/              # Vite + React + TypeScript UI
│   ├── src/               # App source
│   └── dist/              # Production build (generated; gitignored)
├── config/                # roles.yaml, catalog.yaml
├── deploy/helm/           # Kubernetes chart
├── tests/
└── docs/                  # architecture.md, frontend.md
```

## Quick start

### Backend

```bash
# Local Neo4j + the agent service
docker-compose up -d

# Install Python package
pip install -e ".[dev]" --break-system-packages

# Copy and edit env (use CATALOG_SOURCE=mock for local demo)
cp .env.example .env

# Apply Neo4j schema
sentinel schema init

# Seed the chaos-drill demo storyline (optional — works offline without OpenAI)
sentinel seed drill --clear

# Run the API
sentinel serve
# or: uvicorn sentinel.api.main:app --reload --port 8080
```

### Frontend (development)

The UI is a **Vite + React + TypeScript** app in `frontend/`. In dev it runs on port **5173** and proxies API calls to the backend.

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Keep the API running on **http://localhost:8080** in another terminal.

### Frontend (production build)

```bash
cd frontend
npm run build
```

FastAPI serves `frontend/dist/` when that directory exists. With no build present it falls back to the Vite source tree (not recommended for production).

```bash
# API only, after npm run build
sentinel serve
# → http://localhost:8080
```

The Docker image builds the frontend in a multi-stage Dockerfile and ships `frontend/dist/` with the Python app.

### Demo without real connectors

```bash
export CATALOG_SOURCE=mock
docker-compose up -d
sentinel schema init
sentinel seed drill --clear
sentinel serve
```

In another terminal: `cd frontend && npm run dev`. In the UI:

1. Pick a hire persona (SRE, Backend, QA)
2. Click **Load demo data** (or run `sentinel seed drill` from CLI)
3. Ask e.g. *why does payments-svc have a 5% error budget?*
4. Inspect the **Decision trail** panel for retrieval graph + citations

Mock employees: `sre@example.com`, `backend@example.com`, `qa@example.com`.

## CLI

| Command | Description |
|---------|-------------|
| `sentinel serve` | Start FastAPI on port 8080 |
| `sentinel ask <id> <query>` | Ask from the terminal |
| `sentinel provision <id>` | Run day-one provisioning |
| `sentinel ingest --source <name>` | Run ingestion (`github`, `slack`, `linear`, `confluence`, `all`) |
| `sentinel seed drill [--clear]` | Seed chaos-drill demo graph data |
| `sentinel buddy-digest [--dry-run]` | Provision recent hires and notify buddies via Slack |
| `sentinel schema init` | Apply Neo4j schema |
| `sentinel health` | Ping a running instance |

## API

Interactive docs: **http://localhost:8080/api/docs**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health |
| `POST` | `/api/v1/ask` | Context Q&A (returns `traversal_graph`) |
| `POST` | `/api/v1/provision` | Day-one provisioning |
| `POST` | `/api/v1/recover` | Partial provisioning recovery |
| `POST` | `/api/v1/ingest` | Queue background ingestion |
| `GET` | `/api/v1/personas` | Mock hire personas for the UI |
| `GET` | `/api/v1/drill/questions` | Chaos-drill sample questions |
| `POST` | `/api/v1/seed/drill` | Seed demo storyline into Neo4j |
| `POST` | `/api/v1/buddy-digest` | Run buddy digest job |

Example:

```bash
curl -X POST http://localhost:8080/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"employee_id": "sre@example.com", "query": "why does payments-svc have a 5% error budget?"}'
```

## Configuration

Three files drive most behavior:

1. `.env` — connector credentials, model selection, Neo4j connection (see `.env.example`)
2. `config/roles.yaml` — access topology mapping per role
3. `config/catalog.yaml` — which employee catalog to use and field mappings

For local development set `CATALOG_SOURCE=mock`. Without `OPENAI_API_KEY`, embeddings fall back to a deterministic local hash embedder (sufficient for the chaos-drill demo).

Buddy digest Slack delivery: set `SLACK_BUDDY_WEBHOOK_URL` or `SLACK_WEBHOOK_URL` (optional `SLACK_BUDDY_CHANNEL`).

Example `config/catalog.yaml`:

```yaml
catalog: workday
endpoint: https://example.workday.com/ccx/api/v2
auth:
  type: oauth2
  client_id_env: WORKDAY_CLIENT_ID
  client_secret_env: WORKDAY_CLIENT_SECRET
field_mapping:
  employee_id: workerID
  team: organization.name
  role: jobProfile.name
  manager: manager.email
  start_date: hireDate
```

## Development

```bash
# Python tests + lint
pytest tests/ -q
ruff check src tests

# Frontend typecheck + production build
cd frontend && npm run build
```

CI runs Python tests, Ruff, a frontend build job, and a Docker build.

## Deploy

Helm chart: `deploy/helm/`. Enable optional cron jobs in `values.yaml`:

- `ingestion.*` — connector sync schedules
- `buddyDigest.enabled` — weekly buddy digest (`sentinel buddy-digest`)

## License

MPL-2.0. See `LICENSE`.
