"""Build, persist and load the policy index (chunks + dense vectors) in one directory.

`index_meta.json` records the embedding model and a hash of the chunks, so changing the model
or the documents makes the stored vectors stale and triggers a rebuild.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from emporio_agent.config import INDEX_DIR, MODEL_CACHE_DIR, get_client_config, get_settings
from emporio_agent.core.retrieval.bm25 import BM25Retriever
from emporio_agent.core.retrieval.dense import DenseRetriever, Embedder, FastEmbedder
from emporio_agent.core.retrieval.hybrid import HybridRetriever
from emporio_agent.core.retrieval.ingest import Chunk, chunk_document, read_chunks, write_chunks

CHUNKS_FILE = "chunks.json"
VECTORS_FILE = "vectors.npy"
META_FILE = "index_meta.json"


def _fingerprint(index_dir: Path) -> str:
    return hashlib.sha256((index_dir / CHUNKS_FILE).read_bytes()).hexdigest()


def build_index(
    documents: list[Path],
    heading_pattern: re.Pattern[str] | str,
    embedder: Embedder,
    index_dir: Path = INDEX_DIR,
) -> list[Chunk]:
    chunks = [chunk for doc in documents for chunk in chunk_document(doc, heading_pattern)]
    write_chunks(chunks, index_dir / CHUNKS_FILE)
    np.save(index_dir / VECTORS_FILE, embedder.embed_documents([c.index_text for c in chunks]))
    meta = {"embedding_model": embedder.model_name, "chunks_sha256": _fingerprint(index_dir)}
    (index_dir / META_FILE).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return chunks


def index_is_current(embedding_model: str, index_dir: Path = INDEX_DIR) -> bool:
    try:
        meta = json.loads((index_dir / META_FILE).read_text(encoding="utf-8"))
        return (
            meta["embedding_model"] == embedding_model
            and meta["chunks_sha256"] == _fingerprint(index_dir)
            and (index_dir / VECTORS_FILE).is_file()
        )
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return False


def load_retrievers(
    embedder: Embedder, index_dir: Path = INDEX_DIR
) -> tuple[BM25Retriever, DenseRetriever, HybridRetriever]:
    chunks = read_chunks(index_dir / CHUNKS_FILE)
    vectors = np.load(index_dir / VECTORS_FILE)
    bm25 = BM25Retriever(chunks)
    dense = DenseRetriever(chunks, embedder, vectors)
    return bm25, dense, HybridRetriever([bm25, dense])


def default_embedder() -> FastEmbedder:
    return FastEmbedder(get_settings().embedding_model, cache_dir=MODEL_CACHE_DIR)


@lru_cache
def get_retrievers() -> tuple[BM25Retriever, DenseRetriever, HybridRetriever]:
    """Retrievers over the configured documents; builds the index if missing or stale."""
    embedder = default_embedder()
    if not index_is_current(embedder.model_name):
        retrieval = get_client_config().retrieval
        build_index(retrieval.documents, retrieval.heading_pattern, embedder)
    return load_retrievers(embedder)
