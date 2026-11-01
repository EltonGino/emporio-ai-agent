"""Lexical retrieval with BM25 (good at exact terms: "PIX", "boquilha", "R$ 2.000")."""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi
from unidecode import unidecode

from emporio_agent.core.retrieval.base import RetrievedChunk
from emporio_agent.core.retrieval.ingest import Chunk

# Small inline list of Portuguese function words (already accent-free, as tokens are).
STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de da do das dos e em no na nos nas num numa para pra pro por
    pelo pela pelos pelas com que se ao aos ou mas mais como qual quais quanto quanta quando
    onde eu voce voces ele ela eles elas meu minha meus minhas seu sua seus suas nosso nossa
    me te lhe nos tem ter tenho posso pode podem ser sao foi era esta estao este isso
    essa esse isto ja nao sim ate sobre entre tambem muito so ha vai vou
    """.split()  # noqa: SIM905 — a word list reads better than a literal
)
TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, strip accents, split on non-alphanumerics, drop stopwords."""
    return [t for t in TOKEN.findall(unidecode(text).lower()) if t not in STOPWORDS]


class BM25Retriever:
    name = "bm25"

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self._index = BM25Okapi([tokenize(c.index_text) for c in chunks])

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._index.get_scores(tokens)
        ranked = sorted(range(len(self.chunks)), key=lambda i: -scores[i])
        return [
            RetrievedChunk(self.chunks[i], float(scores[i]), (self.name,))
            for i in ranked[:top_k]
            if scores[i] > 0  # no shared term → not a match
        ]
