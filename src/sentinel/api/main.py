"""
FastAPI application. Three use-case endpoints + health + metrics + webhooks.

POST /api/v1/provision   - day-one provisioning
POST /api/v1/ask         - context Q&A
POST /api/v1/recover     - partial provisioning recovery
GET  /api/v1/ingest      - trigger ingestion run (admin)
GET  /health             - health check
GET  /metrics            - Prometheus metrics (if enabled)
"""

from __future__ import annotations

import os as _os
import pathlib as _pathlib
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sentinel.agent import Agent
from sentinel.catalog import EmployeeNotFound, get_catalog
from sentinel.config import load_config, set_config
from sentinel.graph import GraphClient
from sentinel.graph.ingestion import REGISTRY, Embedder
from sentinel.graph.schema_init import apply_schema
from sentinel.retrieval import RetrievalEngine

log = structlog.get_logger(__name__)

_AGENT: Agent | None = None
_GRAPH: GraphClient | None = None
_START_TIME: float = 0.0

# ─── Prometheus metrics (optional) ───────────────────────────────────────────

_metrics_enabled = False
_ask_counter: Any = None
_provision_counter: Any = None
_ask_latency: Any = None

def _init_metrics() -> None:
    global _metrics_enabled, _ask_counter, _provision_counter, _ask_latency
    try:
        from prometheus_client import Counter, Histogram
        _ask_counter = Counter("sentinel_ask_total", "Total ask requests",
                               ["had_context", "intent"])
        _provision_counter = Counter("sentinel_provision_total", "Total provision requests",
                                     ["status"])
        _ask_latency = Histogram("sentinel_ask_latency_seconds", "Ask latency")
        _metrics_enabled = True
    except ImportError:
        log.warning("prometheus_client not installed — metrics disabled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _AGENT, _GRAPH, _START_TIME
    _START_TIME = time.monotonic()

    cfg = load_config()
    set_config(cfg)

    if cfg.server.metrics_enabled:
        _init_metrics()

    _GRAPH = GraphClient(
        uri=cfg.graph.neo4j_uri,
        user=cfg.graph.neo4j_user,
        password=cfg.graph.neo4j_password,
    )

    if cfg.graph.auto_apply_schema:
        log.info("applying neo4j schema")
        await apply_schema(_GRAPH)

    embedder = Embedder(model=cfg.graph.embedding_model)
    retrieval = RetrievalEngine(_GRAPH, embedder)
    catalog = get_catalog(cfg.catalog.source)
    _AGENT = Agent(catalog=catalog, retrieval=retrieval, model=cfg.agent.model)
    log.info("sentinel api ready", catalog=catalog.name, model=cfg.agent.model)
    yield
    if _GRAPH:
        await _GRAPH.close()


app = FastAPI(
    title="Sentinel Day One",
    description="Onboarding agent over your engineering knowledge graph.",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


_cors_origins = [o.strip() for o in _os.environ.get("CORS_ORIGINS", "*").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Webhooks router ─────────────────────────────────────────────────────────

from sentinel.api.webhooks import router as webhooks_router  # noqa: E402
app.include_router(webhooks_router)

# ─── Request / response models ────────────────────────────────────────────────


class AskRequest(BaseModel):
    employee_id: str = Field(..., description="Employee email or catalog ID")
    query: str = Field(..., min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]]
    retrieval_intent: str
    had_context: bool
    traversal_graph: dict[str, Any] = Field(default_factory=dict)


class BuddyDigestRequest(BaseModel):
    since_days: int = Field(7, ge=1, le=90)
    dry_run: bool = False


class SeedDrillRequest(BaseModel):
    clear_existing: bool = False


class ProvisionRequest(BaseModel):
    employee_id: str


class ProvisionResponse(BaseModel):
    report: dict[str, Any]
    narration: str
    topology: dict[str, Any]


class RecoverRequest(BaseModel):
    employee_id: str
    failed_steps: list[str]


class IngestRequest(BaseModel):
    source: str = Field(..., description="github | slack | linear | confluence")
    since: str | None = Field(None, description="ISO-8601 timestamp — only ingest newer data")


# ─── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": round(time.monotonic() - _START_TIME, 1),
        "catalog": _AGENT.catalog.name if _AGENT else None,
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    if not _metrics_enabled:
        raise HTTPException(status_code=404, detail="metrics not enabled")
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    assert _AGENT is not None
    t0 = time.monotonic()
    try:
        result = await _AGENT.ask(req.employee_id, req.query)
    except EmployeeNotFound:
        raise HTTPException(status_code=404, detail=f"employee {req.employee_id} not in catalog")

    if _metrics_enabled and _ask_counter and _ask_latency:
        _ask_counter.labels(
            had_context=str(result["had_context"]),
            intent=result["retrieval_intent"],
        ).inc()
        _ask_latency.observe(time.monotonic() - t0)

    return AskResponse(**result)


@app.post("/api/v1/provision", response_model=ProvisionResponse)
async def provision(req: ProvisionRequest) -> ProvisionResponse:
    assert _AGENT is not None
    try:
        result = await _AGENT.provision(req.employee_id)
    except EmployeeNotFound:
        raise HTTPException(status_code=404, detail=f"employee {req.employee_id} not in catalog")

    if _metrics_enabled and _provision_counter:
        _provision_counter.labels(
            status=result["report"]["summary"].get("failed", 0) and "partial" or "success"
        ).inc()

    return ProvisionResponse(**result)


@app.post("/api/v1/recover")
async def recover(req: RecoverRequest) -> dict[str, str]:
    assert _AGENT is not None
    try:
        narration = await _AGENT.recover(req.employee_id, req.failed_steps)
    except EmployeeNotFound:
        raise HTTPException(status_code=404, detail=f"employee {req.employee_id} not in catalog")
    return {"narration": narration}


@app.get("/api/v1/personas")
async def personas() -> list[dict[str, Any]]:
    """Mock hire personas for the guided tour and persona switcher."""
    from sentinel.catalog.mock import _MOCK_EMPLOYEES

    return [
        {
            "employee_id": p.employee_id,
            "full_name": p.full_name,
            "team": p.team,
            "role": p.role,
            "buddy_id": p.buddy_id,
            "sample_query": {
                "sre@example.com": "why does payments-svc have a 5% error budget?",
                "backend@example.com": "which PR added the circuit breaker after the outage?",
                "qa@example.com": "what ticket tracks the post-outage reliability work?",
            }.get(p.employee_id, "who owns payments-svc?"),
        }
        for p in _MOCK_EMPLOYEES.values()
    ]


@app.get("/api/v1/drill/questions")
async def drill_questions() -> list[dict[str, str]]:
    from sentinel.graph.drill_seed import DRILL_QUESTIONS

    return DRILL_QUESTIONS


@app.post("/api/v1/seed/drill")
async def seed_drill(req: SeedDrillRequest) -> dict[str, Any]:
    """Seed the chaos-drill storyline into Neo4j."""
    from sentinel.graph.drill_seed import seed_chaos_drill

    assert _GRAPH is not None
    if req.clear_existing:
        await _GRAPH.write(
            """
            MATCH (n)
            WHERE n.id IN [
              'payments-svc', 'ADR-007', 'slack:sre-general:1723500000',
              'INC-2024-089', 'PAY-892', 'conf:payments-architecture'
            ]
            OR n.global_id = 'acme/payments-api#445'
            OR n.full_name = 'acme/payments-api'
            DETACH DELETE n
            """
        )
    stats = await seed_chaos_drill(_GRAPH)
    return {"status": "ok", **stats.__dict__}


@app.post("/api/v1/buddy-digest")
async def buddy_digest(req: BuddyDigestRequest) -> dict[str, Any]:
    from sentinel.buddy_digest import run_buddy_digest

    result = await run_buddy_digest(since_days=req.since_days, dry_run=req.dry_run)
    return result.__dict__


@app.post("/api/v1/ingest")
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Trigger an ingestion run. Runs in background, returns immediately."""
    if req.source not in REGISTRY:
        raise HTTPException(status_code=400,
                             detail=f"unknown source '{req.source}'. valid: {list(REGISTRY)}")
    background_tasks.add_task(_run_ingestion, req.source, req.since)
    return {"status": "queued", "source": req.source}


async def _run_ingestion(source: str, since: str | None) -> None:
    from sentinel.graph.ingestion import REGISTRY
    assert _GRAPH is not None
    embedder = Embedder()
    ingester = REGISTRY[source](_GRAPH, embedder)
    try:
        stats = await ingester.run(since=since)
        log.info("ingestion complete", **stats.__dict__)
    except Exception as e:
        log.error("ingestion failed", source=source, error=str(e))


_frontend = _pathlib.Path(__file__).parent.parent.parent.parent / "frontend"
_frontend_dist = _frontend / "dist"
_static_dir = _frontend_dist if _frontend_dist.is_dir() else _frontend
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")
