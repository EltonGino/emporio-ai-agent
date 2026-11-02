import pytest
from fakes import ProviderError, ScriptedModel, call
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from emporio_agent.client.assistant import output_guard
from emporio_agent.core.agent import build_agent
from emporio_agent.core.recording import (
    RecordingStopped,
    Scenario,
    record,
    record_all,
    render_markdown,
)

SCENARIO = Scenario(
    2,
    "payment",
    "Pagamento",
    "Price, then PIX.",
    "2026-03-25T10:30",
    ("Quanto custa o Takamine GD20?", "E no PIX?"),
)


@tool
def search_products(query: str) -> str:
    """Fake product search."""
    return '{"products":[{"id":95,"name":"Takamine GD20","price_text":"R$ 2.199,00"}]}'


def agent(*script):
    return build_agent(
        [ScriptedModel(script=list(script))],
        [search_products],
        "S",
        lambda messages: "C",
        InMemorySaver(),
        20,
        2500,
        rate_limit_sleep=lambda seconds: None,
        guards=[output_guard()],
    )


def test_recording_keeps_each_turns_tools_and_guard_interventions():
    recording = record(
        agent(
            call("search_products", {"query": "Takamine GD20"}),
            AIMessage("O Takamine GD20 sai por R$ 2.199,00."),
            AIMessage("No PIX sai por R$ 1.999,00."),  # invented: the guard holds it
            AIMessage("Quer que eu calcule o PIX pra você?"),
        ),
        SCENARIO,
    )
    first, second = recording.turns
    assert first.tool_calls == [
        {"name": "search_products", "args": {"query": "Takamine GD20"}, "outcome": "ok"}
    ]
    assert first.answer == "O Takamine GD20 sai por R$ 2.199,00." and not first.guard
    assert second.tool_calls == [] and second.guard[0]["action"] == "retry"

    text = render_markdown(recording, "ollama", "quarta-feira, 25/03/2026 às 10:30", "2026-11-02")
    assert text.startswith("# 02 · Pagamento\n\nPrice, then PIX.\n")
    assert "- **Simulated date:** quarta-feira, 25/03/2026 às 10:30" in text
    assert "## Turn 1\n\n**Cliente:**\n\n> Quanto custa o Takamine GD20?" in text
    assert '- `search_products` `{"query": "Takamine GD20"}`' in text
    assert "Tools called: none." in text
    assert "Output guard (retry): " in text and "R$ 1.999,00" in text
    assert '(held back, never shown to the customer: "No PIX sai por R$ 1.999,00.")' in text


@tool
def handoff_to_human(reason: str) -> str:
    """Fake handoff: rejects order reasons without a verified order."""
    return '{"error":"order_not_verified","message":"..."}'


def test_failed_tool_calls_are_marked():
    """Seen on the 20b: a call with invalid arguments, then the corrected call."""
    bad = call("search_products", {"query": 5}, "a")  # wrong type → rejected by the tool
    model = ScriptedModel(
        script=[bad, call("handoff_to_human", {"reason": "delayed_order"}, "b"), AIMessage("ok")]
    )
    agent = build_agent(
        [model], [search_products, handoff_to_human], "S", lambda m: "C", InMemorySaver(), 20, 2500
    )
    one_turn = Scenario(9, "x", "X", "x", "2026-03-25T10:30", ("pedido atrasado",))
    recording = record(agent, one_turn)
    assert [c["outcome"] for c in recording.turns[0].tool_calls] == [
        "rejected",
        "error: order_not_verified",
    ]
    text = render_markdown(recording, "ollama", "d", "r")
    assert "→ rejected (invalid arguments; the model called again)" in text
    assert '`{"reason": "delayed_order"}` → error: order_not_verified' in text


def test_rate_limited_turn_stops_the_recording():
    long_wait = ProviderError(429, retry_after="120")  # above the wait limit, no fallback model
    with pytest.raises(RecordingStopped, match="turn 2: rate limited"):
        record(agent(AIMessage("Oi!"), long_wait), SCENARIO)


def test_error_event_stops_the_recording():
    with pytest.raises(RecordingStopped, match="turn 1: error event"):
        record(agent(ValueError("boom")), SCENARIO)


def test_finished_scenarios_are_written_and_the_stopped_one_is_not():
    other = Scenario(3, "other", "Outro", "x", "2026-03-25T10:30", ("oi",))
    agents = iter([agent(AIMessage("a"), AIMessage("b")), agent(ProviderError(429, "x", "120"))])
    written = []
    with pytest.raises(RecordingStopped):
        record_all([SCENARIO, other], lambda now: next(agents), written.append)
    assert [r.scenario.filename for r in written] == ["02_payment.md"]
