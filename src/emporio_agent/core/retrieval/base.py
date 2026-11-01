"""Retriever interface shared by BM25, dense and hybrid retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from emporio_agent.core.retrieval.ingest import Chunk


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    matched_by: tuple[str, ...]  # which retrievers ranked it (e.g. ("bm25", "dense"))


class Retriever(Protocol):
    name: str

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        """Best chunks first; at most top_k."""
        ...
