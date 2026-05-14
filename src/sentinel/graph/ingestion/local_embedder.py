"""Deterministic local embedder for offline demos and chaos-drill seeding."""

from __future__ import annotations

import hashlib
import math
import re


class LocalEmbedder:
    """Bag-of-words hash embedder. Overlapping text gets high cosine similarity."""

    def __init__(self, dims: int = 3072) -> None:
        self.dims = dims
        self.model = "local-hash"

    async def embed(self, text: str) -> list[float]:
        return self._vector(text)

    async def embed_batch(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        if not text or not text.strip():
            return [0.0] * self.dims
        vec = [0.0] * self.dims
        tokens = re.findall(r"[a-z0-9][a-z0-9_-]{1,}", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode()).hexdigest()
            idx = int(digest[:8], 16) % self.dims
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
