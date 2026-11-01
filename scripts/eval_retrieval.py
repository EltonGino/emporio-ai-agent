"""Evaluate BM25, dense and hybrid retrieval on evals/retrieval_golden.yaml.

Usage: uv run python scripts/eval_retrieval.py [--cases]
Prints hit@1, hit@3 and MRR per retriever and query style (customer / agent / all);
--cases also prints the rank of the first relevant section for every query.
"""

import sys

from emporio_agent.config import PROJECT_ROOT
from emporio_agent.core.retrieval.evaluation import (
    DEPTH,
    load_golden,
    metrics_by_style,
    rank_table,
    styles,
)
from emporio_agent.core.retrieval.index import get_retrievers

GOLDEN = PROJECT_ROOT / "evals" / "retrieval_golden.yaml"


def main() -> None:
    cases = load_golden(GOLDEN)
    retrievers = list(get_retrievers())
    ranks = rank_table(retrievers, cases)
    metrics = metrics_by_style(ranks)
    style_names = [*styles(cases), "all"]

    print(f"{len(cases)} cases × {len(style_names) - 1} styles; MRR over top {DEPTH}\n")
    print("| Retriever | Style    | hit@1 | hit@3 | MRR  | n  |")
    print("|-----------|----------|-------|-------|------|----|")
    for style in style_names:
        for retriever in retrievers:
            m = metrics[(retriever.name, style)]
            print(
                f"| {retriever.name:<9} | {style:<8} | {m.hit_at_1:.2f}  | {m.hit_at_3:.2f}  "
                f"| {m.mrr:.2f} | {m.n:<2} |"
            )

    if "--cases" in sys.argv:
        print("\nRank of first relevant section (- = not in top 10):")
        header = "  ".join(f"{r.name[:6]}/{s[:4]}" for s in style_names[:-1] for r in retrievers)
        print(f"{'case':<26}{header}")
        for case in cases:
            cells = [
                ranks[(r.name, s, case.id)] or "-" for s in style_names[:-1] for r in retrievers
            ]
            print(f"{case.id:<26}" + "  ".join(f"{str(c):>11}" for c in cells))


if __name__ == "__main__":
    main()
