"""End-to-end routing and adversarial evals against the real LLM (marker `llm`).

    uv run pytest -m llm tests/test_llm_evals.py [--llm-model openai/gpt-oss-20b]

Skipped without GROQ_API_KEY; never run in CI. Uses the real hybrid retriever (embedding
model) and a fresh store DB copy per case.
"""

import os
import time

import pytest

from emporio_agent.client.assistant import eval_agent_factory
from emporio_agent.config import PROJECT_ROOT, get_client_config, get_settings
from emporio_agent.core.evals import load_cases, run_case

pytestmark = pytest.mark.llm

EVALS = PROJECT_ROOT / "evals"
CASES = load_cases(EVALS / "routing_cases.yaml") + load_cases(EVALS / "adversarial_cases.yaml")


@pytest.fixture(scope="session")
def eval_factory(request, tmp_path_factory):
    settings = get_settings()  # loads .env
    provider = request.config.getoption("--llm-provider") or settings.llm_provider
    if provider == "groq" and not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY not set")
    from emporio_agent.core.retrieval.index import get_retrievers

    return eval_agent_factory(
        settings,
        get_client_config(),
        get_retrievers()[2],
        tmp_path_factory.mktemp("evals"),
        request.config.getoption("--llm-model"),
        request.config.getoption("--llm-provider"),
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_eval_case(case, eval_factory, request):
    pace = request.config.getoption("--eval-pace")
    time.sleep(pace)  # between cases too
    result = run_case(case, eval_factory, pace)
    assert result.passed, (
        f"{case.id}: {result.failures}\ntools: {result.transcript.tools}\n"
        f"answers: {result.transcript.answers}"
    )
