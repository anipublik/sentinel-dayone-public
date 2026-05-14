"""OpenAI embedding wrapper. Swap model via EMBEDDING_MODEL env var."""

from __future__ import annotations

import os

import structlog
from openai import AsyncOpenAI

log = structlog.get_logger(__name__)

# text-embedding-3-large = 3072 dims  |  text-embedding-3-small = 1536 dims
_MODEL_DIMS = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
}


class Embedder:
    """
    Async embedding wrapper. Used by both ingesters (write path) and
    the retrieval engine (query path) so model + dims are always consistent.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
        self.dims = _MODEL_DIMS.get(self.model, 3072)
        if not resolved_key or os.environ.get("EMBEDDING_PROVIDER") == "local":
            from sentinel.graph.ingestion.local_embedder import LocalEmbedder

            self._local = LocalEmbedder(dims=self.dims)
            self.client = None
            log.debug("embedder ready", model="local-hash", dims=self.dims)
            return
        self._local = None
        self.client = AsyncOpenAI(api_key=resolved_key)
        log.debug("embedder ready", model=self.model, dims=self.dims)

    async def embed(self, text: str) -> list[float]:
        """Embed text. Truncates to ~30k chars (conservative 8k token budget)."""
        if self._local is not None:
            return await self._local.embed(text)
        if not text or not text.strip():
            return [0.0] * self.dims
        if len(text) > 30000:
            text = text[:30000]
        resp = await self.client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    async def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Embed multiple texts, batched to stay under rate limits."""
        if self._local is not None:
            return await self._local.embed_batch(texts, batch_size=batch_size)
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = [t[:30000] if len(t) > 30000 else t for t in texts[i : i + batch_size]]
            resp = await self.client.embeddings.create(model=self.model, input=batch)
            results.extend(item.embedding for item in resp.data)
        return results
