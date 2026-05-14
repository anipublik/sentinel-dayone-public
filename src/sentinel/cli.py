"""
Sentinel Day One CLI.

Commands:
    sentinel serve          - start the API server
    sentinel ingest         - run ingestion pipelines
    sentinel provision      - provision a new hire
    sentinel ask            - ask a question
    sentinel schema init    - apply Neo4j schema
    sentinel seed drill     - seed chaos-drill demo data
    sentinel buddy-digest   - provision recent hires and notify buddies
    sentinel health         - check service health
"""

from __future__ import annotations

import asyncio

import click
import structlog

log = structlog.get_logger(__name__)


@click.group()
@click.version_option(version="0.2.0", prog_name="sentinel")
def main() -> None:
    """Sentinel Day One — onboarding agent for engineering hires."""


# ─── serve ────────────────────────────────────────────────────────────────────


@main.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8080, show_default=True)
@click.option("--reload", is_flag=True, default=False, help="Enable hot reload (dev only)")
@click.option("--log-level", default="info", show_default=True,
              type=click.Choice(["debug", "info", "warning", "error"]))
def serve(host: str, port: int, reload: bool, log_level: str) -> None:
    """Start the Sentinel API server."""
    import uvicorn
    uvicorn.run(
        "sentinel.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


# ─── ingest ───────────────────────────────────────────────────────────────────


@main.command()
@click.option("--source", required=True,
              type=click.Choice(["github", "slack", "linear", "confluence", "all"]),
              help="Which source to ingest")
@click.option("--since", default=None,
              help="ISO-8601 timestamp — only ingest data newer than this")
def ingest(source: str, since: str | None) -> None:
    """Run ingestion pipelines against configured connectors."""

    async def _run() -> None:
        from sentinel.graph.client import GraphClient
        from sentinel.graph.ingestion import REGISTRY, Embedder

        graph = GraphClient()
        embedder = Embedder()
        sources = list(REGISTRY.keys()) if source == "all" else [source]

        for src in sources:
            click.echo(f"► Ingesting {src}...")
            ingester = REGISTRY[src](graph, embedder)
            try:
                stats = await ingester.run(since=since)
                click.echo(
                    f"  ✓ {src}: {stats.nodes_upserted} nodes, "
                    f"{stats.edges_created} edges, "
                    f"{stats.embeddings_computed} embeddings, "
                    f"{stats.errors} errors"
                )
            except Exception as e:
                click.echo(f"  ✗ {src} failed: {e}", err=True)

        await graph.close()

    asyncio.run(_run())


# ─── provision ────────────────────────────────────────────────────────────────


@main.command()
@click.argument("employee_id")
@click.option("--catalog", default=None, help="Override CATALOG_SOURCE")
def provision(employee_id: str, catalog: str | None) -> None:
    """Run day-one provisioning for an employee."""

    async def _run() -> None:
        import os
        if catalog:
            os.environ["CATALOG_SOURCE"] = catalog

        from sentinel.catalog import compute_access_topology, get_catalog
        from sentinel.provisioning.runner import ProvisioningRunner

        cat = get_catalog()
        click.echo(f"► Fetching {employee_id} from {cat.name}...")
        profile = await cat.fetch(employee_id)
        click.echo(f"  Name:  {profile.full_name}")
        click.echo(f"  Role:  {profile.role}")
        click.echo(f"  Team:  {profile.team}")

        topology = compute_access_topology(profile)
        click.echo("\n► Access topology computed")
        click.echo(f"  Repos owned:  {topology.repos.get('owned', [])}")
        click.echo(f"  Clusters:     {topology.clusters}")
        click.echo(f"  Vault scope:  {topology.vault_scope}")

        click.echo("\n► Running provisioning steps...")
        runner = ProvisioningRunner(profile, topology)
        report = await runner.run()

        for step in report["steps"]:
            icon = {"success": "✓", "partial": "~", "failed": "✗",
                    "skipped": "-", "needs_approval": "!"}.get(step["status"], "?")
            click.echo(f"  {icon} {step['name']:20s} [{step['status']}]")
            if step.get("error"):
                click.echo(f"    error: {step['error']}", err=True)

        summary = report["summary"]
        click.echo(f"\n  Summary: {summary}")

    asyncio.run(_run())


# ─── ask ──────────────────────────────────────────────────────────────────────


@main.command()
@click.argument("employee_id")
@click.argument("query")
@click.option("--catalog", default=None, help="Override CATALOG_SOURCE")
def ask(employee_id: str, query: str, catalog: str | None) -> None:
    """Ask the agent a question on behalf of an employee."""

    async def _run() -> None:
        import os
        if catalog:
            os.environ["CATALOG_SOURCE"] = catalog

        from sentinel.agent import Agent
        from sentinel.catalog import get_catalog
        from sentinel.graph.client import GraphClient
        from sentinel.graph.ingestion.embedder import Embedder
        from sentinel.retrieval import RetrievalEngine

        graph = GraphClient()
        embedder = Embedder()
        retrieval = RetrievalEngine(graph, embedder)
        cat = get_catalog()
        agent = Agent(catalog=cat, retrieval=retrieval)

        click.echo(f"► Asking: {query}\n")
        result = await agent.ask(employee_id, query)
        click.echo(result["answer"])
        click.echo(f"\n— intent: {result['retrieval_intent']}, "
                    f"had_context: {result['had_context']}, "
                    f"citations: {len(result['citations'])}")
        await graph.close()

    asyncio.run(_run())


# ─── schema ───────────────────────────────────────────────────────────────────


@main.group()
def schema() -> None:
    """Neo4j schema management."""


@schema.command("init")
def schema_init() -> None:
    """Apply schema.cypher to the configured Neo4j instance."""

    async def _run() -> None:
        from sentinel.graph.client import GraphClient
        from sentinel.graph.schema_init import apply_schema

        graph = GraphClient()
        click.echo("► Applying schema...")
        await apply_schema(graph)
        await graph.close()
        click.echo("  ✓ Schema applied")

    asyncio.run(_run())


# ─── seed ─────────────────────────────────────────────────────────────────────


@main.group()
def seed() -> None:
    """Seed demo data into Neo4j."""


@seed.command("drill")
@click.option("--clear", is_flag=True, help="Remove existing drill nodes before seeding")
def seed_drill(clear: bool) -> None:
    """Seed the payments-outage chaos-drill storyline."""

    async def _run() -> None:
        from sentinel.graph.client import GraphClient
        from sentinel.graph.drill_seed import seed_chaos_drill

        graph = GraphClient()
        if clear:
            click.echo("► Clearing existing drill nodes…")
            await graph.write(
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
        click.echo("► Seeding chaos drill…")
        stats = await seed_chaos_drill(graph)
        click.echo(
            f"  ✓ {stats.nodes_upserted} nodes, "
            f"{stats.edges_created} edges, "
            f"{stats.embeddings_computed} embeddings"
        )
        await graph.close()

    asyncio.run(_run())


# ─── buddy digest ─────────────────────────────────────────────────────────────


@main.command("buddy-digest")
@click.option("--since-days", default=7, show_default=True, help="Include hires from the last N days")
@click.option("--dry-run", is_flag=True, help="Build digests without sending Slack messages")
@click.option("--catalog", default=None, help="Override CATALOG_SOURCE")
def buddy_digest(since_days: int, dry_run: bool, catalog: str | None) -> None:
    """Provision recent hires and send onboarding digests to buddies."""

    async def _run() -> None:
        import os
        if catalog:
            os.environ["CATALOG_SOURCE"] = catalog

        from sentinel.buddy_digest import run_buddy_digest

        click.echo(f"► Running buddy digest (since {since_days} days, dry_run={dry_run})…")
        result = await run_buddy_digest(since_days=since_days, dry_run=dry_run, catalog_source=catalog)
        click.echo(f"  Hires processed: {result.hires_processed}")
        click.echo(f"  Messages sent:   {result.messages_sent}")
        for digest in result.digests:
            click.echo(f"\n  — {digest.get('full_name')} ({digest.get('employee_id')})")
            if digest.get("message"):
                click.echo(digest["message"])
            if digest.get("error"):
                click.echo(f"    error: {digest['error']}", err=True)
        if result.errors:
            click.echo(f"\n  Errors: {len(result.errors)}", err=True)

    asyncio.run(_run())


# ─── health ───────────────────────────────────────────────────────────────────


@main.command()
@click.option("--url", default="http://localhost:8080", show_default=True)
def health(url: str) -> None:
    """Check the health of a running Sentinel instance."""
    import httpx
    try:
        resp = httpx.get(f"{url}/health", timeout=5.0)
        data = resp.json()
        click.echo(f"✓ status:  {data['status']}")
        click.echo(f"  catalog: {data.get('catalog', 'unknown')}")
        click.echo(f"  uptime:  {data.get('uptime_seconds', '?')}s")
    except Exception as e:
        click.echo(f"✗ health check failed: {e}", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
