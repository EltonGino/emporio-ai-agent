"""Semantic retrieval with local embeddings (good at paraphrases: "me arrependi da compra")."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from emporio_agent.core.retrieval.base import RetrievedChunk
from emporio_agent.core.retrieval.ingest import Chunk


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class FastEmbedder:
    """fastembed (ONNX on CPU, no PyTorch): text never leaves the server.

    `passage_embed`/`query_embed` add whatever prefixes the model needs (none for the
    paraphrase-multilingual models). Vectors are L2-normalized, so dot product = cosine.
    """

    def __init__(self, model_name: str, cache_dir: Path | None = None) -> None:
        from fastembed import TextEmbedding  # heavy import, only when embeddings are needed

        self.model_name = model_name
        self._model = TextEmbedding(model_name, cache_dir=str(cache_dir) if cache_dir else None)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.array(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.array(next(iter(self._model.query_embed(text))), dtype=np.float32))


class DenseRetriever:
    name = "dense"

    def __init__(
        self, chunks: list[Chunk], embedder: Embedder, vectors: np.ndarray | None = None
    ) -> None:
        self.chunks = chunks
        self.embedder = embedder
        if vectors is None:
            vectors = embedder.embed_documents([c.index_text for c in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(f"{len(vectors)} vectors for {len(chunks)} chunks")
        self.vectors = vectors

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        scores = self.vectors @ self.embedder.embed_query(query)
        ranked = np.argsort(-scores, kind="stable")[:top_k]
        return [RetrievedChunk(self.chunks[i], float(scores[i]), (self.name,)) for i in ranked]
