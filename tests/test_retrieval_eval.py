import pytest

from emporio_agent.config import PROJECT_ROOT, get_client_config
from emporio_agent.core.retrieval.base import RetrievedChunk
from emporio_agent.core.retrieval.evaluation import (
    GoldenCase,
    load_golden,
    metrics_by_style,
    rank_table,
    summarize,
)
from emporio_agent.core.retrieval.ingest import Chunk, chunk_document

GOLDEN = PROJECT_ROOT / "evals" / "retrieval_golden.yaml"


def _chunk(section_id: str) -> Chunk:
    return Chunk("doc.pdf", section_id, section_id, section_id, 1, section_id)


class FixedRetriever:
    name = "fixed"

    def search(self, query: str, top_k: int) -> list[RetrievedChunk]:
        ids = query.split()  # the "query" is the ranking itself
        return [RetrievedChunk(_chunk(i), 1.0, (self.name,)) for i in ids[:top_k]]


def test_summarize_metrics():
    m = summarize([1, 2, 3, None])
    assert (m.hit_at_1, m.hit_at_3, m.n) == (0.25, 0.75, 4)
    assert m.mrr == pytest.approx((1 + 1 / 2 + 1 / 3) / 4)


def test_metrics_per_style_and_overall():
    cases = [
        GoldenCase("a", frozenset({"x"}), {"customer": "y x", "agent": "x"}),
        GoldenCase("b", frozenset({"z"}), {"customer": "y", "agent": "z y"}),
    ]
    metrics = metrics_by_style(rank_table([FixedRetriever()], cases))
    assert metrics[("fixed", "agent")].hit_at_1 == 1.0
    assert metrics[("fixed", "customer")].hit_at_3 == 0.5
    assert metrics[("fixed", "customer")].mrr == 0.25  # ranks 2 and not found
    assert metrics[("fixed", "all")].n == 4


def test_golden_set_is_well_formed():
    cases = load_golden(GOLDEN)
    assert len(cases) >= 15
    assert len({c.id for c in cases}) == len(cases)
    assert all(set(c.queries) == {"customer", "agent"} for c in cases)


def test_golden_expected_sections_exist():
    retrieval = get_client_config().retrieval
    section_ids = {
        c.section_id for c in chunk_document(retrieval.documents[0], retrieval.heading_pattern)
    }
    for case in load_golden(GOLDEN):
        assert case.expected <= section_ids, case.id


@pytest.mark.embeddings
def test_hybrid_hit_at_3_per_style():
    """AGENTS.md §6: hybrid hit@3 ≥ 0.85 — required for both query styles."""
    from emporio_agent.core.retrieval.index import get_retrievers

    hybrid = get_retrievers()[2]
    metrics = metrics_by_style(rank_table([hybrid], load_golden(GOLDEN)))
    for style in ("customer", "agent", "all"):
        assert metrics[("hybrid", style)].hit_at_3 >= 0.85, style
