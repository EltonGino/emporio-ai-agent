"""Retriever mechanics. A hashing bag-of-words embedder stands in for the real model, so these
tests need no model download; real retrieval quality is measured by the eval (step 7)."""

import json
import zlib

import numpy as np
import pytest

from emporio_agent.config import get_client_config
from emporio_agent.core.retrieval.base import RetrievedChunk
from emporio_agent.core.retrieval.bm25 import BM25Retriever, tokenize
from emporio_agent.core.retrieval.dense import DenseRetriever
from emporio_agent.core.retrieval.hybrid import HybridRetriever
from emporio_agent.core.retrieval.index import (
    CHUNKS_FILE,
    META_FILE,
    build_index,
    index_is_current,
    load_retrievers,
)
from emporio_agent.core.retrieval.ingest import Chunk, chunk_document


class FakeEmbedder:
    model_name = "fake-hashing-bow"

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(64, dtype=np.float32)
        for token in tokenize(text):
            vector[zlib.crc32(token.encode()) % 64] += 1
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.array([self._vector(t) for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


def _chunk(section_id: str, text: str) -> Chunk:
    return Chunk("doc.pdf", section_id, f"Seção {section_id}", f"{section_id} Seção", 1, text)


@pytest.fixture(scope="module")
def policy_chunks():
    retrieval = get_client_config().retrieval
    return chunk_document(retrieval.documents[0], retrieval.heading_pattern)


class StubRetriever:
    def __init__(self, name: str, ranking: list[Chunk]) -> None:
        self.name = name
        self.ranking = ranking

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        return [RetrievedChunk(c, 1.0, (self.name,)) for c in self.ranking[:top_k]]


def test_tokenize_strips_accents_case_and_stopwords():
    assert tokenize("Vocês têm desconto no PIX à vista?") == ["desconto", "pix", "vista"]
    assert tokenize("parcela mínima de R$ 2.000,00") == ["parcela", "minima", "r", "2", "000", "00"]


@pytest.mark.parametrize(
    ("query", "section_id"),
    [("boquilha troca", "4.4"), ("desconto PIX em preço promocional", "6.2"), ("LGPD", "9")],
)
def test_bm25_finds_exact_terms(policy_chunks, query, section_id):
    assert BM25Retriever(policy_chunks).search(query, 3)[0].chunk.section_id == section_id


def test_bm25_returns_nothing_without_shared_terms(policy_chunks):
    assert BM25Retriever(policy_chunks).search("xyzzy", 3) == []
    assert BM25Retriever(policy_chunks).search("de para com", 3) == []  # only stopwords


def test_dense_ranks_by_cosine():
    chunks = [_chunk("1", "frete grátis entrega"), _chunk("2", "garantia legal defeito")]
    hits = DenseRetriever(chunks, FakeEmbedder()).search("defeito garantia", 2)
    assert [h.chunk.section_id for h in hits] == ["2", "1"]
    assert hits[0].score > hits[1].score
    assert hits[0].matched_by == ("dense",)


def test_dense_rejects_mismatched_vectors():
    with pytest.raises(ValueError, match="vectors"):
        DenseRetriever([_chunk("1", "a")], FakeEmbedder(), np.zeros((2, 64)))


def test_hybrid_reciprocal_rank_fusion():
    a, b, c = _chunk("a", "a"), _chunk("b", "b"), _chunk("c", "c")
    hybrid = HybridRetriever([StubRetriever("bm25", [a, b]), StubRetriever("dense", [c, b])], k=60)
    hits = hybrid.search("q", 3)
    # b is 2nd in both lists: 2/62 beats a or c, each 1st in only one list (1/61).
    assert [h.chunk.section_id for h in hits] == ["b", "a", "c"]
    assert hits[0].score == pytest.approx(2 / 62)
    assert hits[0].matched_by == ("bm25", "dense")
    assert hits[1].matched_by == ("bm25",)


def test_hybrid_respects_top_k_and_depth():
    chunks = [_chunk(str(n), str(n)) for n in range(20)]
    hybrid = HybridRetriever([StubRetriever("bm25", chunks)], depth=5)
    assert [h.chunk.section_id for h in hybrid.search("q", 10)] == ["0", "1", "2", "3", "4"]
    assert len(hybrid.search("q", 2)) == 2


def test_index_build_load_and_staleness(tmp_path):
    retrieval = get_client_config().retrieval
    embedder = FakeEmbedder()
    chunks = build_index(retrieval.documents, retrieval.heading_pattern, embedder, tmp_path)
    assert index_is_current(embedder.model_name, tmp_path)
    assert not index_is_current("another-model", tmp_path)

    bm25, dense, hybrid = load_retrievers(embedder, tmp_path)
    assert bm25.chunks == chunks and len(dense.vectors) == len(chunks)
    assert hybrid.search("boquilha troca", 3)[0].chunk.section_id == "4.4"

    edited = json.loads((tmp_path / CHUNKS_FILE).read_text(encoding="utf-8"))[:-1]
    (tmp_path / CHUNKS_FILE).write_text(json.dumps(edited), encoding="utf-8")
    assert not index_is_current(embedder.model_name, tmp_path)  # documents changed


def test_missing_index_is_not_current(tmp_path):
    assert not index_is_current("any", tmp_path)
    (tmp_path / META_FILE).write_text("not json", encoding="utf-8")
    assert not index_is_current("any", tmp_path)
