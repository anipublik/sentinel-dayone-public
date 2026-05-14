"""
Agent orchestrator. Three modes:

  ask()        - context Q&A. Routes a user query through retrieval, then asks Claude
                 to synthesize the answer with citations.
  provision()  - day-one provisioning. Reads catalog, computes topology, runs the
                 provisioning runner, narrates the result.
  recover()    - partial provisioning recovery. Walks the user through gaps.

The agent is the only place that talks to Anthropic. Everything else (retrieval,
catalog, provisioning) is plain code with no LLM in the loop. That makes the
non-agent parts deterministic, testable, and replayable.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from anthropic import AsyncAnthropic

from sentinel.agent.prompts import (
    format_retrieval_result,
    provisioning_system,
    recovery_system,
    retrieval_system,
)
from sentinel.catalog import (
    CatalogAdapter,
    EmployeeProfile,
    compute_access_topology,
)
from sentinel.retrieval import RetrievalEngine, RetrievalResult
from sentinel.retrieval.traversal_graph import build_traversal_graph

log = structlog.get_logger(__name__)


class Agent:
    """The orchestrator. Stateless. One instance per process is fine."""

    def __init__(
        self,
        catalog: CatalogAdapter,
        retrieval: RetrievalEngine,
        anthropic_client: AsyncAnthropic | None = None,
        model: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.retrieval = retrieval
        self.client = anthropic_client or AsyncAnthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # ── context Q&A ─────────────────────────────────────────────────────────

    async def ask(self, employee_id: str, query: str) -> dict[str, Any]:
        """
        Answer a context question for an employee. Returns:
            {
              "answer": str,
              "citations": [Citation],
              "retrieval_intent": str,
              "had_context": bool,
            }
        """
        profile = await self.catalog.fetch(employee_id)
        topology = compute_access_topology(profile)
        result = await self.retrieval.retrieve(query, topology)
        log.info(
            "retrieval done",
            employee=employee_id,
            intent=result.intent.value,
            entry_count=len(result.entry_points),
            connected_count=len(result.connected),
        )

        # If retrieval is empty, short-circuit. Cheaper than calling the model
        # just to have it say "I don't know."
        if not result.entry_points and not result.connected:
            return {
                "answer": (
                    "I don't have context for this in the graph under your role scope. "
                    "Either the data hasn't been ingested yet, or it lives somewhere "
                    "I'm not allowed to read. Try asking your buddy or check #ask-platform."
                ),
                "citations": [],
                "retrieval_intent": result.intent.value,
                "had_context": False,
                "traversal_graph": build_traversal_graph(result),
            }

        answer = await self._synthesize(profile, query, result)

        if not _has_citations(answer):
            log.warning(
                "agent answer missing citations - regenerating with stricter prompt",
                employee=employee_id,
                query=query,
            )
            answer = await self._synthesize(profile, query, result, strict=True)

        return {
            "answer": answer,
            "citations": [c.__dict__ for c in (result.entry_points + result.connected)],
            "retrieval_intent": result.intent.value,
            "had_context": True,
            "traversal_graph": build_traversal_graph(result),
        }

    async def _synthesize(
        self,
        profile: EmployeeProfile,
        query: str,
        result: RetrievalResult,
        strict: bool = False,
    ) -> str:
        formatted = format_retrieval_result(result)
        user_msg = (
            f"User query: {query}\n\n{formatted}\n\n"
            f"Answer the query using only the retrieval result above. Cite every claim."
        )
        if strict:
            user_msg += (
                "\n\nIMPORTANT: Your previous answer had no citations. "
                "Every claim must reference a specific entity id from the retrieval result. "
                "If you cannot cite a claim, do not make it."
            )

        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=retrieval_system(profile),
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text  # type: ignore[union-attr]

    # ── day-one provisioning ────────────────────────────────────────────────

    async def provision(self, employee_id: str) -> dict[str, Any]:
        """
        Run day-one provisioning for a new hire. Returns the structured report
        plus an agent-narrated summary.
        """
        from sentinel.provisioning import ProvisioningRunner

        profile = await self.catalog.fetch(employee_id)
        topology = compute_access_topology(profile)
        runner = ProvisioningRunner(profile, topology)
        report = await runner.run()

        # Narrate the result through the agent so the report is human-readable
        narration = await self._narrate_provisioning(profile, report)

        return {
            "report": report,
            "narration": narration,
            "topology": topology.__dict__,
        }

    async def _narrate_provisioning(
        self, profile: EmployeeProfile, report: dict[str, Any]
    ) -> str:
        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=provisioning_system(profile),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Render this provisioning report for {profile.full_name}:\n\n"
                        f"{report}"
                    ),
                }
            ],
        )
        return resp.content[0].text  # type: ignore[union-attr]

    # ── recovery ────────────────────────────────────────────────────────────

    async def recover(self, employee_id: str, failed_steps: list[str]) -> str:
        profile = await self.catalog.fetch(employee_id)
        resp = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=recovery_system(profile, failed_steps),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Walk {profile.full_name} through resolving these failed "
                        f"provisioning steps: {failed_steps}"
                    ),
                }
            ],
        )
        return resp.content[0].text  # type: ignore[union-attr]


def _has_citations(answer: str) -> bool:
    """
    Citation classifier. Checks for recognizable entity references:
    ticket IDs, PR numbers, ADR refs, Slack channel mentions, confluence paths.

    Covers: GitHub (PR #123), Linear (ENG-123), Jira (PROJ-456),
            ADR variants, incidents (INC-2024-089), Slack (#channel),
            users (@handle), Confluence paths.

    Production hardening: replace with a small fine-tuned classifier.
    """
    import re

    patterns = [
        r"\b(?:PR|pr)\s*#?\d+",                         # PR #123, pr #44
        r"\b(?:ADR|adr)[-_\s]?\d+",                     # ADR-007, ADR 7
        r"\b(?:INC|inc)[-_\s]?\d{4,}[-_\s]?\d*",       # INC-2024-089
        r"\b[A-Z]{2,8}-\d+\b",                          # ENG-123, PAY-892, INFRA-44, SRE-5
        r"#[a-z][a-z0-9_-]{2,}",                        # #sre-general, #payments
        r"@[a-z][a-z0-9._-]{2,}",                       # @jsmith, @platform-team
        r"\bconfluence[/\\]",                           # confluence/page or confluence\path
        r"issue\s*#?\d+",                               # issue #42
        r"postmortem[-_\s]\d+",                         # postmortem-2024
    ]
    return any(re.search(p, answer, re.IGNORECASE) for p in patterns)
