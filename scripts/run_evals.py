"""Run the end-to-end evals against the real LLM provider and print a report.

Usage: uv run python scripts/run_evals.py [--llm-model openai/gpt-oss-20b] [--cases id1,id2]
                                          [--suite routing|adversarial|all] [--pace 20]
Needs GROQ_API_KEY. Each case runs in a fresh session with its own store DB copy; --pace
waits between turns (free tier: 8K tokens per minute per model). A JSON report is written to
data/runtime/evals/.
"""

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from emporio_agent.client.assistant import eval_agent_factory
from emporio_agent.config import PROJECT_ROOT, get_client_config, get_settings
from emporio_agent.core.evals import load_cases, run_case
from emporio_agent.core.retrieval.index import get_retrievers

SUITES = {
    "routing": PROJECT_ROOT / "evals" / "routing_cases.yaml",
    "adversarial": PROJECT_ROOT / "evals" / "adversarial_cases.yaml",
}
REPORTS = PROJECT_ROOT / "data" / "runtime" / "evals"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--llm-model", help="run on this model only, e.g. openai/gpt-oss-20b")
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    parser.add_argument("--pace", type=float, default=20.0, help="seconds between turns/cases")
    args = parser.parse_args()

    suites = SUITES if args.suite == "all" else {args.suite: SUITES[args.suite]}
    cases = [c for path in suites.values() for c in load_cases(path)]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c.id in wanted]

    settings = get_settings()
    with tempfile.TemporaryDirectory() as workdir:
        factory = eval_agent_factory(
            settings, get_client_config(), get_retrievers()[2], Path(workdir), args.llm_model
        )
        results = []
        for i, case in enumerate(cases):
            if i and args.pace:
                time.sleep(args.pace)
            result = run_case(case, factory, args.pace)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"{status}  {case.id:<42} tools={result.transcript.tools}")
            for failure in result.failures:
                print(f"      - {failure}")
            if not result.passed:
                print(f"      answer: {result.transcript.answers[-1][:300]!r}")

    passed = sum(r.passed for r in results)
    model = args.llm_model or settings.llm_model
    print(f"\n{passed}/{len(results)} passed on {model}")
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"report-{datetime.now():%Y%m%d-%H%M%S}.json"
    report.write_text(
        json.dumps(
            {
                "model": model,
                "passed": passed,
                "total": len(results),
                "cases": [
                    {
                        "id": r.case.id,
                        "passed": r.passed,
                        "failures": r.failures,
                        "tools": r.transcript.tools,
                        "answers": r.transcript.answers,
                        "notices": r.transcript.notices,
                        "input_tokens": r.transcript.input_tokens,
                    }
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"report: {report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
