"""
Slack ingester. Pulls public channels the bot is in, threads, and replies.

Excluded channel patterns are driven by SLACK_EXCLUDED_CHANNELS env var
(comma-separated prefixes). Defaults exclude HR, finance, legal channels.

Bot must be invited to channels before ingestion. The ingester reads only
channels where is_member=true.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
import structlog

from sentinel.graph.ingestion.base import IngestStats, Ingester

log = structlog.get_logger(__name__)

# Channels whose names match any of these prefixes/substrings are excluded.
_DEFAULT_EXCLUSIONS = ["hr-", "compensation", "performance", "legal-", "finance-",
                       "exec-", "board-", "#random", "#general"]


def _is_excluded(channel_name: str, exclusions: list[str]) -> bool:
    name = channel_name.lower()
    return any(name.startswith(e.lower().lstrip("#")) or e.lower().lstrip("#") in name
               for e in exclusions)


class SlackIngester(Ingester):
    """
    Full Slack ingestion. Requires SLACK_BOT_TOKEN with:
        channels:history, channels:read, users:read
    """

    source_name = "slack"

    def __init__(self, graph, embedder) -> None:
        super().__init__(graph, embedder)
        self.token = os.environ["SLACK_BOT_TOKEN"]
        excluded_raw = os.environ.get("SLACK_EXCLUDED_CHANNELS", "")
        self.excluded = (
            [e.strip() for e in excluded_raw.split(",") if e.strip()]
            if excluded_raw
            else _DEFAULT_EXCLUSIONS
        )
        self.client = httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60.0,
        )

    async def run(self, since: str | None = None) -> IngestStats:
        log.info("slack ingestion starting", excluded_patterns=self.excluded)
        channels = await self._list_channels()
        log.info("slack channels to ingest", count=len(channels))
        for ch in channels:
            try:
                await self._ingest_channel(ch, since)
            except Exception as e:
                log.error("channel ingestion failed", channel=ch["name"], error=str(e))
                self.stats.errors += 1
        log.info("slack ingestion complete", **self.stats.__dict__)
        return self.stats

    async def _list_channels(self) -> list[dict[str, Any]]:
        channels: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 200, "types": "public_channel"}
            if cursor:
                params["cursor"] = cursor
            resp = await self.client.get("/conversations.list", params=params)
            data = resp.json()
            if not data.get("ok"):
                log.error("conversations.list failed", error=data.get("error"))
                break
            for ch in data.get("channels", []):
                if ch.get("is_member") and not _is_excluded(ch["name"], self.excluded):
                    channels.append(ch)
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return channels

    async def _ingest_channel(self, channel: dict[str, Any], since: str | None) -> None:
        cursor: str | None = None
        ch_id = channel["id"]
        ch_name = channel["name"]
        while True:
            params: dict[str, Any] = {"channel": ch_id, "limit": 200}
            if since:
                params["oldest"] = since
            if cursor:
                params["cursor"] = cursor
            resp = await self.client.get("/conversations.history", params=params)
            data = resp.json()
            if not data.get("ok"):
                log.warning("conversations.history failed",
                             channel=ch_name, error=data.get("error"))
                break
            for msg in data.get("messages", []):
                # Only ingest parent messages that have replies — those carry decisions
                if msg.get("reply_count", 0) > 0 or msg.get("subtype") is None:
                    await self._upsert_thread(ch_id, ch_name, msg, since)
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor or not data.get("has_more"):
                break

    async def _upsert_thread(
        self, ch_id: str, ch_name: str, msg: dict[str, Any], since: str | None
    ) -> None:
        thread_ts = msg["ts"]
        thread_id = f"{ch_id}:{thread_ts}"

        # Fetch replies if this is a thread parent
        thread_text = msg.get("text", "")
        if msg.get("reply_count", 0) > 0:
            replies = await self._fetch_replies(ch_id, thread_ts, since)
            thread_text += "\n\n" + "\n".join(r.get("text", "") for r in replies)

        # Content classifier — skip messages with no substantive text
        if len(thread_text.strip()) < 20:
            return

        try:
            embedding = await self.embedder.embed(thread_text)
            self.stats.embeddings_computed += 1
        except Exception as e:
            log.warning("embedding failed for slack thread", thread_id=thread_id, error=str(e))
            embedding = [0.0] * self.embedder.dims
            self.stats.errors += 1

        # Extract @mentions of services/repos (simple heuristic)
        mentions = list(set(re.findall(r"\b([a-z][a-z0-9_-]+-(?:svc|api|service|app))\b",
                                        thread_text.lower())))

        await self.graph.write(
            """
            MERGE (s:SlackThread {id: $id})
            SET s.channel = $channel,
                s.channel_id = $channel_id,
                s.text = $text,
                s.author = $author,
                s.ts = $ts,
                s.reply_count = $reply_count,
                s.url = $url,
                s.embedding = $embedding,
                s.mentions = $mentions
            """,
            id=thread_id,
            channel=ch_name,
            channel_id=ch_id,
            text=thread_text[:4000],
            author=msg.get("user", "unknown"),
            ts=thread_ts,
            reply_count=msg.get("reply_count", 0),
            url=f"https://slack.com/archives/{ch_id}/p{thread_ts.replace('.', '')}",
            embedding=embedding,
            mentions=mentions,
        )
        self.stats.nodes_upserted += 1

        # Link mentioned services to this thread
        for svc in mentions:
            await self.graph.write(
                """
                MERGE (svc:Service {id: $svc})
                ON CREATE SET svc.name = $svc
                WITH svc
                MATCH (s:SlackThread {id: $thread_id})
                MERGE (s)-[:MENTIONS]->(svc)
                """,
                svc=svc,
                thread_id=thread_id,
            )
            self.stats.edges_created += 1

    async def _fetch_replies(
        self, ch_id: str, thread_ts: str, since: str | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"channel": ch_id, "ts": thread_ts, "limit": 100}
        if since:
            params["oldest"] = since
        resp = await self.client.get("/conversations.replies", params=params)
        data = resp.json()
        if not data.get("ok"):
            return []
        # First message is the parent — skip it
        return data.get("messages", [])[1:]
