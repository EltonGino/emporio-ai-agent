"""Hybrid retrieval: Reciprocal Rank Fusion of several retrievers' rankings."""

from __future__ import annotations

from collections.abc import Sequence

from emporio_agent.core.retrieval.base import RetrievedChunk, Retriever
from emporio_agent.core.retrieval.ingest import Chunk

RRF_K = 60


def _key(chunk: Chunk) -> tuple[str, str]:
    return (chunk.doc, chunk.section_id)


class HybridRetriever:
    """score(chunk) = Σ over retrievers of 1 / (k + rank). Rank-based, so BM25 and cosine
    scores never need to be put on the same scale."""

    name = "hybrid"

    def __init__(self, retrievers: Sequence[Retriever], k: int = RRF_K, depth: int = 10) -> None:
        self.retrievers = list(retrievers)
        self.k = k
        self.depth = depth  # how many candidates each retriever contributes

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        scores: dict[tuple[str, str], float] = {}
        chunks: dict[tuple[str, str], Chunk] = {}
        matched: dict[tuple[str, str], list[str]] = {}
        for retriever in self.retrievers:
            for rank, hit in enumerate(retriever.search(query, self.depth), start=1):
                key = _key(hit.chunk)
                scores[key] = scores.get(key, 0.0) + 1 / (self.k + rank)
                chunks[key] = hit.chunk
                matched.setdefault(key, []).append(retriever.name)
        ranked = sorted(scores, key=lambda key: -scores[key])  # stable: ties keep first-seen
        return [
            RetrievedChunk(chunks[key], scores[key], tuple(matched[key])) for key in ranked[:top_k]
        ]
