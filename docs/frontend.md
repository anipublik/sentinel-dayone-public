# Frontend

The Sentinel Day One UI is a **Vite + React + TypeScript** single-page app in `frontend/`. It talks to the FastAPI backend over REST.

## Stack

| Piece | Choice |
|-------|--------|
| Build | Vite 6 |
| UI | React 19 |
| Language | TypeScript |
| Styling | CSS modules via global `src/index.css` (design tokens, no component library) |

## Local development

Run the API and the Vite dev server in parallel:

```bash
# Terminal 1 — from repo root
export CATALOG_SOURCE=mock
sentinel serve

# Terminal 2
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api/*` and `/health` to `http://localhost:8080` (see `frontend/vite.config.ts`).

Hot module replacement applies to React components; API changes require a backend restart.

## Production build

```bash
cd frontend
npm run build
```

Output lands in `frontend/dist/`. The FastAPI app mounts that directory at `/` when it exists:

```python
# src/sentinel/api/main.py
_static_dir = frontend/dist if present else frontend/
```

After building, a single `sentinel serve` on port 8080 serves both API and static UI.

## Docker

The root `Dockerfile` uses a multi-stage build:

1. `node:22-alpine` — `npm install` + `npm run build` in `frontend/`
2. `python:3.12-slim` — installs the Python package and copies `frontend/dist/`

No separate frontend container is required for default deployments.

## UI structure

```
frontend/src/
├── main.tsx              # React entry
├── App.tsx               # Layout, state, workflows (ask / provision / ingest)
├── api.ts                # Typed fetch wrappers
├── types.ts              # API response types
├── index.css             # Global styles
└── components/
    └── DecisionTrail.tsx # SVG graph + citation chips
```

### Workflows exposed in the UI

- **Ask** — chat-style Q&A with citations and decision-trail visualization
- **Provision** — trigger day-one provisioning for the selected hire
- **Ingest** — queue connector ingestion runs
- **Load demo data** — `POST /api/v1/seed/drill`
- **Buddy digest** — dry-run `POST /api/v1/buddy-digest`

Personas and sample questions come from `GET /api/v1/personas` and `GET /api/v1/drill/questions`.

## Environment

The frontend has no secrets. It uses same-origin requests in production (served by FastAPI) and Vite's dev proxy locally. No `VITE_*` env vars are required for the default setup.

To point the dev UI at a remote API, change the `proxy` target in `vite.config.ts` or add a `VITE_API_BASE` pattern in `api.ts` if you need cross-origin deployment later.

## CI

`.github/workflows/ci.yml` includes a **Frontend Build** job:

```bash
cd frontend && npm ci && npm run build
```

Docker build depends on that job passing.
