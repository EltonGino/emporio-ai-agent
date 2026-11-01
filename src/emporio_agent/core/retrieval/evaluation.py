"""Retrieval evaluation: hit@1, hit@3 and MRR per retriever and per query style."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from emporio_agent.core.retrieval.base import Retriever

DEPTH = 10  # MRR looks this deep; a relevant chunk below it counts as not found


@dataclass(frozen=True)
class GoldenCase:
    id: str
    expected: frozenset[str]  # section ids; any of them answers the question
    queries: dict[str, str]  # style → query


@dataclass(frozen=True)
class Metrics:
    hit_at_1: float
    hit_at_3: float
    mrr: float
    n: int


def load_golden(path: Path) -> list[GoldenCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        GoldenCase(
            id=case["id"],
            expected=frozenset(case["expected"]),
            queries={k: v for k, v in case.items() if k not in ("id", "expected")},
        )
        for case in raw["cases"]
    ]


def styles(cases: list[GoldenCase]) -> list[str]:
    return list(dict.fromkeys(style for case in cases for style in case.queries))


def first_relevant_rank(retriever: Retriever, query: str, expected: frozenset[str]) -> int | None:
    hits = retriever.search(query, DEPTH)
    return next(
        (rank for rank, hit in enumerate(hits, 1) if hit.chunk.section_id in expected), None
    )


def summarize(ranks: list[int | None]) -> Metrics:
    n = len(ranks)
    return Metrics(
        hit_at_1=sum(r == 1 for r in ranks if r) / n,
        hit_at_3=sum(r <= 3 for r in ranks if r) / n,
        mrr=sum(1 / r for r in ranks if r) / n,
        n=n,
    )


def rank_table(
    retrievers: list[Retriever], cases: list[GoldenCase]
) -> dict[tuple[str, str, str], int | None]:
    """(retriever, style, case id) → rank of the first relevant chunk (None = not in top DEPTH)."""
    return {
        (retriever.name, style, case.id): first_relevant_rank(retriever, query, case.expected)
        for retriever in retrievers
        for case in cases
        for style, query in case.queries.items()
    }


def metrics_by_style(
    ranks: dict[tuple[str, str, str], int | None],
) -> dict[tuple[str, str], Metrics]:
    """(retriever, style or "all") → metrics."""
    grouped: dict[tuple[str, str], list[int | None]] = {}
    for (name, style, _case), rank in ranks.items():
        grouped.setdefault((name, style), []).append(rank)
        grouped.setdefault((name, "all"), []).append(rank)
    return {key: summarize(values) for key, values in grouped.items()}
