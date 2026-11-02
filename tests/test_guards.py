import pytest
from langchain_core.messages import ToolMessage

from emporio_agent.core.guards import claimed_protocols, issued_protocols

KEYWORDS = ("protocolo", "protocol")


@pytest.mark.parametrize(
    ("text", "claimed"),
    [
        # Real case (run 6): no EMP- prefix, invented without calling the tool.
        ("**Protocolo:** 20231102-8745", {"20231102-8745"}),
        ("registrado sob o protocolo **EMP-20260325-0001**.", {"EMP-20260325-0001"}),
        # Real case (run 5): a mis-copied protocol (one digit short).
        ("O protocolo é EMP-20260220-001.", {"EMP-20260220-001"}),
        ("Seu número de protocolo é 8745123, guarde-o.", {"8745123"}),
        ("o código do caso é 20231102-8745", {"20231102-8745"}),  # dated code, no keyword
        ("Your protocol number is ABC-99812.", {"ABC-99812"}),
    ],
)
def test_claimed_protocols(text, claimed):
    assert claimed_protocols(text, KEYWORDS) == claimed


@pytest.mark.parametrize(
    "text",
    [
        "Quer que eu registre o caso para gerar um protocolo?",
        "Protocolo registrado! Retorno até 24/02/2026, às 10:30.",
        "Fale com a gente no (67) 3341-4444 ou no CEP 79202-333.",
        "O pedido 8 custa R$ 2.199,00 e o rastreio é BRJL5544332BR.",
    ],
)
def test_no_false_positives(text):
    assert claimed_protocols(text, KEYWORDS) == set()


def test_issued_protocols_only_from_the_handoff_tool():
    messages = [
        ToolMessage('{"protocol":"EMP-20260325-0001"}', tool_call_id="1", name="handoff_to_human"),
        ToolMessage('{"protocol":"FAKE-1234"}', tool_call_id="2", name="search_products"),
        ToolMessage("not json", tool_call_id="3", name="handoff_to_human"),
    ]
    assert issued_protocols(messages, "handoff_to_human") == {"EMP-20260325-0001"}


# --- money amounts ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from emporio_agent.core.guards import PriceCheck, cited_amounts, parse_amount  # noqa: E402


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2.199,00", "2199.00"),
        ("2.199", "2199.00"),
        ("1.000", "1000.00"),
        ("2199.0", "2199.00"),
        ("183,25", "183.25"),
        ("12.000,50", "12000.50"),
        ("50", "50.00"),
    ],
)
def test_parse_amount(text, value):
    assert parse_amount(text) == Decimal(value)


def test_cited_amounts():
    answer = "Sai por **R$ 2.199,00**, ou R$2.089,05 no PIX; 12x de R$ 183,25. Uns 500 reais."
    assert set(cited_amounts(answer)) == {
        Decimal("2199.00"),
        Decimal("2089.05"),
        Decimal("183.25"),
        Decimal("500.00"),
    }


SEARCH_RESULT = ToolMessage(
    '{"products":[{"name":"Takamine GD20","price":2199.0,"price_text":"R$ 2.199,00"}]}',
    tool_call_id="1",
    name="search_products",
)
CHECK = PriceCheck(correction_note=lambda p: "", safe_answer="safe")


def test_amounts_from_this_turn_tools_pass():
    history = [HumanMessage("quanto custa o GD20?"), SEARCH_RESULT]
    assert CHECK.problems("O GD20 sai por R$ 2.199,00 (ou R$ 2.199).", history) == set()


def test_invented_prices_are_caught():
    """Real case: after an empty search the model listed basses 'por R$ 2.399,00'."""
    history = [
        HumanMessage("e o Ibanez Bass 2X?"),
        ToolMessage('{"total_matches":0,"products":[]}', tool_call_id="1"),
    ]
    answer = "Temos o **Ibanez SR500E** – R$ 2.399,00 e o **GSR200** – R$ 1.299,00."
    assert CHECK.problems(answer, history) == {"R$ 2.399,00", "R$ 1.299,00"}


def test_computed_amounts_are_caught():
    history = [HumanMessage("e no PIX?"), SEARCH_RESULT]
    assert CHECK.problems("Você economiza R$ 109,95.", history) == {"R$ 109,95"}


def test_customer_amounts_may_be_repeated():
    history = [HumanMessage("violões até R$ 1.000?"), SEARCH_RESULT]
    assert CHECK.problems("Até **R$ 1.000** temos estes:", history) == set()


def test_previous_turn_tool_results_do_not_count():
    history = [
        HumanMessage("quanto custa o GD20?"),
        SEARCH_RESULT,
        AIMessage("R$ 2.199,00"),
        HumanMessage("e a cor?"),
    ]
    assert CHECK.problems("Natural, e sai por R$ 2.199,00.", history) == {"R$ 2.199,00"}


# --- robotic phrases -----------------------------------------------------------------------

from emporio_agent.client.assistant import ROBOTIC_PHRASES  # noqa: E402
from emporio_agent.core.guards import PhraseCheck  # noqa: E402

ROBOTIC = PhraseCheck(phrases=ROBOTIC_PHRASES, correction_note=lambda p: "", safe_answer="s")


@pytest.mark.parametrize(
    ("answer", "found"),
    [
        # Real answer from the eval run (prompt_reveal, role_override)
        ("Desculpe, não posso atender a essa solicitação. Posso ajudar com...", 1),
        ("Desculpe, mas NÃO TENHO ACESSO A ESSES DADOS.", 1),
        ("Essa parte fica só com a nossa equipe 😊 Posso te ajudar com um instrumento?", 0),
    ],
)
def test_robotic_phrases(answer, found):
    assert len(ROBOTIC.problems(answer, [])) == found
