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
