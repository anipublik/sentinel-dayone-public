"""
Confluence ingester. Pulls pages from configured spaces via Confluence REST API v1.

Requires: CONFLUENCE_URL, CONFLUENCE_USER, CONFLUENCE_API_TOKEN
Optionally: CONFLUENCE_SPACE_KEYS (comma-separated, defaults to all spaces)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from sentinel.graph.ingestion.base import IngestStats, Ingester

log = structlog.get_logger(__name__)


class ConfluenceIngester(Ingester):
    source_name = "confluence"

    def __init__(self, graph, embedder) -> None:
        super().__init__(graph, embedder)
        self.base_url = os.environ["CONFLUENCE_URL"].rstrip("/")
        user = os.environ["CONFLUENCE_USER"]
        token = os.environ["CONFLUENCE_API_TOKEN"]
        space_keys_raw = os.environ.get("CONFLUENCE_SPACE_KEYS", "")
        self.space_keys = [s.strip() for s in space_keys_raw.split(",") if s.strip()]
        self.client = httpx.AsyncClient(
            auth=(user, token),
            headers={"Accept": "application/json"},
            timeout=60.0,
        )

    async def run(self, since: str | None = None) -> IngestStats:
        log.info("confluence ingestion starting", spaces=self.space_keys or "all")
        spaces = self.space_keys or await self._list_space_keys()
        for key in spaces:
            try:
                await self._ingest_space(key, since)
            except Exception as e:
                log.error("space ingestion failed", space=key, error=str(e))
                self.stats.errors += 1
        log.info("confluence ingestion complete", **self.stats.__dict__)
        return self.stats

    async def _list_space_keys(self) -> list[str]:
        resp = await self.client.get(
            f"{self.base_url}/rest/api/space",
            params={"limit": 250, "type": "global"},
        )
        if resp.status_code >= 400:
            log.error("confluence space list failed", status=resp.status_code)
            return []
        data = resp.json()
        return [s["key"] for s in data.get("results", [])]

    async def _ingest_space(self, space_key: str, since: str | None) -> None:
        start = 0
        limit = 50
        while True:
            params: dict[str, Any] = {
                "spaceKey": space_key,
                "expand": "body.storage,version,metadata.labels",
                "limit": limit,
                "start": start,
            }
            if since:
                params["lastModified"] = since
            resp = await self.client.get(
                f"{self.base_url}/rest/api/content",
                params=params,
            )
            if resp.status_code >= 400:
                log.error("confluence content fetch failed",
                           space=space_key, status=resp.status_code)
                self.stats.errors += 1
                break
            data = resp.json()
            pages = data.get("results", [])
            for page in pages:
                try:
                    await self._upsert_page(page, space_key)
                except Exception as e:
                    log.warning("page upsert failed", page_id=page.get("id"), error=str(e))
                    self.stats.errors += 1

            size = data.get("size", 0)
            if size < limit:
                break
            start += limit

    async def _upsert_page(self, page: dict[str, Any], space_key: str) -> None:
        page_id = page["id"]
        title = page.get("title", "")
        # Strip HTML from body storage format
        body_html = (page.get("body") or {}).get("storage", {}).get("value", "")
        body_text = _strip_html(body_html)
        text = f"{title}\n\n{body_text}"

        try:
            embedding = await self.embedder.embed(text)
            self.stats.embeddings_computed += 1
        except Exception as e:
            log.warning("embedding failed for confluence page", page_id=page_id, error=str(e))
            embedding = [0.0] * self.embedder.dims
            self.stats.errors += 1

        version = (page.get("version") or {}).get("number", 1)
        url = f"{self.base_url}/wiki/spaces/{space_key}/pages/{page_id}"
        labels = [lbl["name"] for lbl in
                  (page.get("metadata") or {}).get("labels", {}).get("results", [])]

        await self.graph.write(
            """
            MERGE (c:ConfluencePage {id: $id})
            SET c.title = $title,
                c.body = $body,
                c.space_key = $space_key,
                c.version = $version,
                c.url = $url,
                c.labels = $labels,
                c.source = 'confluence',
                c.embedding = $embedding
            """,
            id=page_id,
            title=title,
            body=body_text[:4000],
            space_key=space_key,
            version=version,
            url=url,
            labels=labels,
            embedding=embedding,
        )
        self.stats.nodes_upserted += 1


def _strip_html(html: str) -> str:
    """Very fast HTML stripping without a full parser."""
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
