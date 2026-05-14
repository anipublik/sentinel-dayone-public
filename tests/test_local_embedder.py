"""Tests for local embedder used in chaos drill."""

import pytest

from sentinel.graph.ingestion.local_embedder import LocalEmbedder


@pytest.mark.asyncio
async def test_local_embedder_similar_texts():
    emb = LocalEmbedder(dims=128)
    a = await emb.embed("payments-svc error budget outage")
    b = await emb.embed("payments error budget for payments-svc")
    c = await emb.embed("completely unrelated kubernetes helm chart")

    def cosine(x, y):
        dot = sum(i * j for i, j in zip(x, y))
        return dot

    assert cosine(a, b) > cosine(a, c)
