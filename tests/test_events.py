import json

import pytest
from fakes import ProviderError, ScriptedModel, call
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from emporio_agent.client.assistant import protocol_guard
from emporio_agent.core.agent import build_agent, run_turn
from emporio_agent.core.events import CUSTOMER_EVENTS, ERROR_MESSAGE, stream_turn
from emporio_agent.core.tracing import JsonlTracer, trace_path, traced


@tool(response_format="content_and_artifact")
def search_policies(query: str):
    """Fake policy search."""
    content = '{"results":[{"section":"4.1 Direito de Arrependimento","text":"7 dias"}]}'
    return content, [{"section_id": "4.1", "score": 0.0325, "matched_by": ["bm25", "dense"]}]


@tool(response_format="content_and_artifact")
def check_return_eligibility(order_id: int):
    """Fake return check."""
    return '{"eligible":true,"days_left":4}', {"days_elapsed": 3}


def agent_with(*models, tools=(), sleeps=None, guards=()):
    sleep = sleeps.append if sleeps is not None else lambda seconds: None
    return build_agent(
        list(models),
        list(tools),
        "S",
        lambda: "C",
        InMemorySaver(),
        20,
        2500,
        15,
        sleep,
        guards=list(guards),
    )


def answer(text: str, input_tokens: int = 100, **kwargs) -> AIMessage:
    message = AIMessage(text, response_metadata={"model_name": "m1"}, **kwargs)
    message.usage_metadata = {"input_tokens": input_tokens, "output_tokens": 10, "total_tokens": 0}
    return message


def events_of(agent, text="oi", session="s1"):
    return list(stream_turn(agent, session, text))


def test_event_sequence_for_a_policy_question():
    model = ScriptedModel(
        script=[call("search_policies", {"query": "arrependimento"}), answer("Você tem 7 dias.")]
    )
    events = events_of(agent_with(model, tools=[search_policies]))
    types = [e.type for e in events]
    assert types[:4] == ["user_message", "tool_start", "tool_end", "retrieval"]
    assert types[-1] == "final" and set(types[4:-1]) == {"token"}

    start, end, retrieval, final = events[1], events[2], events[3], events[-1]
    assert start.data == {"name": "search_policies", "args": {"query": "arrependimento"}}
    assert end.data["name"] == "search_policies" and end.data["duration_ms"] >= 0
    assert "Direito de Arrependimento" in end.data["result_preview"]
    assert retrieval.data["chunks"][0]["section_id"] == "4.1"
    assert "".join(e.data["text"] for e in events if e.type == "token") == final.data["text"]
    assert final.data["usage"]["model_calls"] == 2  # tool call + answer


def test_tool_artifacts_reach_the_trace_not_the_model():
    model = ScriptedModel(
        script=[call("check_return_eligibility", {"order_id": 7}), answer("Faltam 4 dias.")]
    )
    events = events_of(agent_with(model, tools=[check_return_eligibility]))
    notice = next(e for e in events if e.type == "notice")
    assert notice.data == {
        "kind": "tool_artifact",
        "name": "check_return_eligibility",
        "artifact": {"days_elapsed": 3},
    }
    assert "days_elapsed" not in model.seen[-1][-1].content


def test_reasoning_never_appears_in_any_event():
    secret = answer("Oi! 🎸", additional_kwargs={"reasoning_content": "SEGREDO interno"})
    events = events_of(agent_with(ScriptedModel(script=[secret])))
    assert all("SEGREDO" not in e.model_dump_json() for e in events)
    assert events[-1].data["text"] == "Oi! 🎸"


def test_rate_limit_decisions_are_notices():
    first = ScriptedModel(script=[ProviderError(429, retry_after="3"), answer("ok")])
    events = events_of(agent_with(first, sleeps=[]))
    notice = next(e for e in events if e.type == "notice")
    assert notice.data == {
        "kind": "rate_limit",
        "model": "ScriptedModel",
        "retry_after": 3.0,
        "action": "wait",
    }


def test_protocol_guard_notice_and_discarded_usage():
    invented = answer("Protocolo: 20231102-8745", input_tokens=500)
    honest = answer("Quer que eu registre o caso?", input_tokens=600)
    events = events_of(
        agent_with(ScriptedModel(script=[invented, honest]), guards=[protocol_guard()])
    )
    notice = next(e for e in events if e.type == "notice")
    assert notice.data["kind"] == "protocol_guard" and notice.data["invalid"] == ["20231102-8745"]
    usage = events[-1].data["usage"]
    assert (usage["model_calls"], usage["input_tokens"]) == (2, 1100)  # discarded call counted
    customer_text = "".join(e.model_dump_json() for e in events if e.type in CUSTOMER_EVENTS)
    assert "20231102-8745" not in customer_text  # only in the notice (demo panel / trace)


def test_unexpected_error_becomes_a_friendly_error_event():
    events = events_of(agent_with(ScriptedModel(script=[ProviderError(500, "boom")])))
    error = events[-1]
    assert error.type == "error" and error.data["message"] == ERROR_MESSAGE
    assert "ProviderError" in error.data["detail"]
    assert not any(e.type == "final" for e in events)


# --- JSONL tracing -------------------------------------------------------------------------


def test_trace_file_per_session_without_token_events(tmp_path):
    tracer = JsonlTracer(tmp_path)
    model = ScriptedModel(script=[call("search_policies", {"query": "x"}), answer("Resposta.")])
    run_turn(agent_with(model, tools=[search_policies]), "sess-1", "oi", tracer)
    lines = [json.loads(line) for line in (tmp_path / "sess-1.jsonl").read_text().splitlines()]
    assert [r["type"] for r in lines] == [
        "user_message",
        "tool_start",
        "tool_end",
        "retrieval",
        "final",
    ]
    assert lines[-1]["data"]["usage"]["input_tokens"] == 100  # the tool-call step has none
    assert all(r["session_id"] == "sess-1" and r["at"] for r in lines)


def test_traced_passes_events_through(tmp_path):
    events = events_of(agent_with(ScriptedModel(script=[answer("oi")])), session="abc")
    assert list(traced(events, JsonlTracer(tmp_path))) == events


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "x" * 101, "sessão"])
def test_session_ids_cannot_escape_the_traces_dir(tmp_path, bad):
    with pytest.raises(ValueError):
        trace_path(tmp_path, bad)


def test_delete_trace(tmp_path):
    tracer = JsonlTracer(tmp_path)
    run_turn(agent_with(ScriptedModel(script=[answer("oi")])), "s9", "oi", tracer)
    assert tracer.delete("s9") is True
    assert not (tmp_path / "s9.jsonl").exists()
    assert tracer.delete("s9") is False
