"""The eval runner and case files, validated with a scripted model (no LLM calls)."""

from pathlib import Path

import pytest
from fakes import ScriptedModel, call
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from emporio_agent.client import assistant
from emporio_agent.client.assistant import eval_agent_factory, output_guard
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import get_clock
from emporio_agent.config import PROJECT_ROOT, get_client_config, get_settings
from emporio_agent.core.agent import build_agent
from emporio_agent.core.evals import (
    EvalCase,
    Transcript,
    check,
    load_cases,
    normalize,
    run_case,
)

EVALS = PROJECT_ROOT / "evals"
ROUTING = load_cases(EVALS / "routing_cases.yaml")
ADVERSARIAL = load_cases(EVALS / "adversarial_cases.yaml")


# --- case files ----------------------------------------------------------------------------


def test_case_ids_are_unique_across_suites():
    ids = [c.id for c in ROUTING + ADVERSARIAL]
    assert len(ids) == len(set(ids))
    assert len(ROUTING) >= 10 and 5 <= len(ADVERSARIAL) <= 10


def test_cases_only_name_real_tools(tmp_path, policy_retriever):
    repo = SqlStoreRepository(make_engine(f"sqlite:///{tmp_path}/s.db"))
    names = {t.name for t in build_tools(repo, get_client_config(), get_clock(), policy_retriever)}
    for case in ROUTING + ADVERSARIAL:
        for key in ("tools_any", "tools_all", "forbidden_tools", "first_tool_in"):
            assert set(case.expect.get(key, [])) <= names, (case.id, key)
        assert case.turns and all(t.strip() for t in case.turns)


@pytest.mark.parametrize(
    "case_id",
    [
        # Owner decisions and findings recorded in PROGRESS.md
        "official_whatsapp",
        "official_email",
        "strings_accessory",
        "seven_string_guitar",
        "defect_policy_general",
        "discontinued_with_alternatives",
        "no_name_in_a_new_session",
        "delayed_order_asks_for_data",
        "ukulele_promotion",
        "complaint_no_emoji",
        # AGENTS.md §14 adversarial list
        "prompt_reveal",
        "role_override",
        "other_customer_email",
        "order_lookup_with_another_customers_email",
        "injected_instruction",
        # Owner review of the refusals
        "lookup_by_customer_name",
        "lookup_by_another_name",
    ],
)
def test_required_cases_exist(case_id):
    assert case_id in {c.id for c in ROUTING + ADVERSARIAL}


def test_unknown_expectation_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("cases:\n  - id: x\n    turns: [oi]\n    expect: {tools_maybe: [a]}\n")
    with pytest.raises(ValueError, match="tools_maybe"):
        load_cases(bad)


# --- checks --------------------------------------------------------------------------------


def transcript(answers, tools=(), notices=()):
    return Transcript(
        answers=list(answers),
        tool_calls=[{"name": t, "args": {}} for t in tools],
        notices=list(notices),
    )


def test_normalize_ignores_accents_case_and_typographic_hyphens():
    assert normalize("Seu E‑mail e o número") == "seu e-mail e o numero"


@pytest.mark.parametrize("hyphen", ["‑", "–", "—"])
def test_eval_protocol_checks_normalize_hyphens_and_spaces(hyphen):
    answer = f"Protocolo:\u00a0EMP{hyphen}20260325{hyphen}0001"
    assert normalize(answer) == "protocolo: emp-20260325-0001"


def test_sunday_hours_accepts_fecha():
    case = next(c for c in ROUTING if c.id == "sunday_hours")
    assert (
        check(case.expect, transcript(["Não, a loja fecha no domingo."], ["get_store_status"]))
        == []
    )


@pytest.mark.parametrize(
    ("expect", "t", "ok"),
    [
        ({"tools_any": ["a", "b"]}, transcript(["x"], ["b"]), True),
        ({"tools_any": ["a"]}, transcript(["x"], ["b"]), False),
        ({"tools_all": ["a", "b"]}, transcript(["x"], ["a"]), False),
        ({"forbidden_tools": ["h"]}, transcript(["x"], ["a", "h"]), False),
        ({"first_tool_in": ["p"]}, transcript(["x"], ["p", "o"]), True),
        ({"first_tool_in": ["p"]}, transcript(["x"], ["o", "p"]), False),
        ({"first_tool_in": ["p"]}, transcript(["x"]), False),
        ({"no_tools": True}, transcript(["x"], ["a"]), False),
        ({"answer_contains": ["Dias Úteis"]}, transcript(["5 a 12 dias uteis"]), True),
        ({"answer_contains": ["30 dias"]}, transcript(["30 dias", "outro"]), False),  # last only
        ({"answer_contains_any": ["a1", "b2"]}, transcript(["tem b2"]), True),
        ({"answer_not_contains": ["Ana"]}, transcript(["Oi, Ana!", "ok"]), False),  # any answer
        ({"answer_not_matches": [r"\d{8}-\d{3,}"]}, transcript(["20231102-8745"]), False),
        ({"no_emoji": True}, transcript(["Sinto muito. 🎸"]), False),
        ({"no_emoji": True}, transcript(["🎸 oi", "Sinto muito."]), True),
        ({"persona_refusal": True}, transcript(["Confere os dados e me envie de novo?"]), True),
        (
            {"answer_contains_any": ["28/02/2026", "25 dias"]},
            transcript(["atraso de 25 dias"]),
            True,
        ),
    ],
)
def test_check(expect, t, ok):
    assert (check(expect, t) == []) is ok


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Desculpe, não posso atender a essa solicitação.", False),  # robotic, no offer
        ("Não posso atender a esse pedido. Posso te ajudar com instrumentos?", False),
        ("Não posso atender a sua solicitação. Posso te ajudar com instrumentos?", False),
        ("Desculpe, mas não tenho acesso a esses dados. Posso ajudar em outra coisa?", False),
        ("Essa parte fica só com a equipe 😊 Posso te ajudar a escolher um instrumento?", True),
        (
            "Por segurança, só consulto pedidos com o número do pedido e o e-mail da compra. "
            "Se quiser, me passa esses dois dados que eu vejo pra você.",
            True,
        ),
        ("Prefiro não compartilhar isso.", False),  # friendly but no alternative
    ],
)
def test_persona_refusal(answer, ok):
    assert (check({"persona_refusal": True}, transcript([answer])) == []) is ok


def test_tool_results_contain():
    t = transcript(["Não encontrei esse pedido."], ["get_order_status"])
    t.tool_results.append({"name": "get_order_status", "preview": '{"error":"order_not_found"}'})
    assert check({"tool_results_contain": {"get_order_status": "order_not_found"}}, t) == []
    t.tool_results.append({"name": "get_order_status", "preview": '{"order_id":1,"total":11499}'})
    assert check({"tool_results_contain": {"get_order_status": "order_not_found"}}, t)


def test_turn_errors_fail_the_case():
    t = transcript(["Desculpe..."])
    t.errors.append("RuntimeError: boom")
    assert check({}, t)


# --- runner with a scripted model ----------------------------------------------------------


@tool
def search_policies(query: str) -> str:
    """Fake policy search."""
    return '{"results":[{"section":"4.2 Trocas por Defeito","text":"até 30 dias"}]}'


@tool
def get_order_status(order_id: int, email: str) -> str:
    """Fake order lookup."""
    return '{"order_id":8}'


def factory_for(*script):
    def factory(now):
        return build_agent(
            [ScriptedModel(script=list(script))],
            [search_policies, get_order_status],
            "S",
            lambda: "C",
            InMemorySaver(),
            20,
            2500,
            guards=[output_guard()],
        )

    return factory


DEFECT = next(c for c in ROUTING if c.id == "defect_policy_general")


def test_runner_passes_a_good_conversation():
    good = factory_for(
        call("search_policies", {"query": "prazo troca defeito"}),
        AIMessage("Defeito de fabricação: até 30 dias da compra. Quer que eu veja seu pedido?"),
    )
    result = run_case(DEFECT, good)
    assert result.passed, result.failures
    assert result.transcript.tools == ["search_policies"]


def test_runner_fails_the_real_routing_gap():
    """The behaviour seen live: asking for order data instead of searching the policy."""
    bad = factory_for(AIMessage("Me passa o número do pedido e o e-mail da compra?"))
    result = run_case(DEFECT, bad)
    assert not result.passed
    assert any("first tool" in f for f in result.failures)


def test_guard_interventions_are_a_metric_not_a_failure():
    """Real case (price_martin): the guard fixed an invented price; the customer saw the right
    answer, so the case passes and the intervention is reported separately."""
    case = EvalCase("p", ["quanto custa?"], {"answer_contains": ["consultar"]})
    invented = AIMessage("Sai por R$ 2.399,00.")
    result = run_case(case, factory_for(invented, AIMessage("Vou consultar o catálogo.")))
    assert result.passed
    assert result.guard_interventions == {"price": 1}


def test_no_guard_failures_is_no_longer_an_expectation(tmp_path):
    old = tmp_path / "old.yaml"
    old.write_text("cases:\n  - id: x\n    turns: [oi]\n    expect: {no_guard_failures: true}\n")
    with pytest.raises(ValueError, match="no_guard_failures"):
        load_cases(old)


def test_multi_turn_cases_share_one_session():
    case = EvalCase("m", ["oi", "e agora?"], {"answer_contains": ["segunda"]})
    result = run_case(case, factory_for(AIMessage("primeira"), AIMessage("segunda")))
    assert result.passed and result.transcript.answers == ["primeira", "segunda"]


# --- --llm-model ---------------------------------------------------------------------------


def test_eval_factory_uses_only_the_chosen_model(monkeypatch, tmp_path, policy_retriever):
    seen = {}

    def spy(settings, client, clock, retriever, checkpointer):
        seen.update(settings=settings, now=clock.now())
        return "agent"

    monkeypatch.setattr(assistant, "build_store_agent", spy)
    factory = eval_agent_factory(
        get_settings(), get_client_config(), policy_retriever, Path(tmp_path), "openai/gpt-oss-20b"
    )
    assert factory("2026-02-20T10:00") == "agent"
    assert seen["settings"].llm_model == "openai/gpt-oss-20b"
    assert seen["settings"].llm_fallback_models == []
    assert seen["settings"].database_url.startswith(f"sqlite:///{tmp_path}")
    assert seen["now"].isoformat() == "2026-02-20T10:00:00-04:00"
