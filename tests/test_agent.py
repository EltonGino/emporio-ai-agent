from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from emporio_agent.client.assistant import model_chain, render_system_prompt
from emporio_agent.config import Settings, get_client_config
from emporio_agent.core.agent import (
    RATE_LIMITED_MESSAGE,
    build_agent,
    retry_after,
    run_turn,
    trim_history,
)

STATIC = "Você é o Tom."
CONTEXT = "## Agora\nquarta-feira, 25/03/2026 às 10:30."


class ScriptedModel(BaseChatModel):
    """Answers with the next scripted message (or raises it) and records what it was sent."""

    script: list[Any]
    seen: list[list[Any]] = []
    name_: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return ChatResult(generations=[ChatGeneration(message=step)])


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str = "", retry_after: str | None = None):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = SimpleNamespace(headers=headers)


def agent_with(*models, tools=(), sleeps=None):
    sleep = sleeps.append if sleeps is not None else lambda seconds: None
    return build_agent(
        list(models), list(tools), STATIC, lambda: CONTEXT, InMemorySaver(), 20, 2500, sleep
    )


def call(name: str, args: dict, call_id: str = "c1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])


# --- session id, memory, output --------------------------------------------------------------


def test_tools_receive_the_session_id_from_the_run_config():
    @tool
    def whoami(config: RunnableConfig) -> str:
        """Echo the session."""
        return config["configurable"]["thread_id"]

    model = ScriptedModel(script=[call("whoami", {}), AIMessage("ok")])
    agent = agent_with(model, tools=[whoami])
    run_turn(agent, "session-42", "oi")
    tool_message = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    assert tool_message.content == "session-42"


def test_only_content_reaches_the_customer_never_reasoning():
    answer = AIMessage(
        "Oi! Sou o Tom 🎸", additional_kwargs={"reasoning_content": "SEGREDO: pensar..."}
    )
    result = run_turn(agent_with(ScriptedModel(script=[answer])), "s1", "oi")
    assert result.text == "Oi! Sou o Tom 🎸"
    assert "SEGREDO" not in result.text


def test_memory_per_session():
    model = ScriptedModel(script=[AIMessage("um"), AIMessage("dois"), AIMessage("três")])
    agent = agent_with(model)
    run_turn(agent, "a", "primeira")
    run_turn(agent, "a", "segunda")
    assert [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)] == [
        "primeira",
        "segunda",
    ]
    run_turn(agent, "b", "outra conversa")
    assert [m.content for m in model.seen[-1] if isinstance(m, HumanMessage)] == ["outra conversa"]


def test_usage_is_summed_over_the_turn():
    first = call("whoami", {})
    first.usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    second = AIMessage("ok", response_metadata={"model_name": "m1"})
    second.usage_metadata = {
        "input_tokens": 150,
        "output_tokens": 20,
        "total_tokens": 170,
        "input_token_details": {"cache_read": 90},
        "output_token_details": {"reasoning": 5},
    }

    @tool
    def whoami() -> str:
        """x"""
        return "x"

    result = run_turn(agent_with(ScriptedModel(script=[first, second]), tools=[whoami]), "s", "oi")
    usage = result.usage
    assert (usage.input_tokens, usage.cached_tokens, usage.output_tokens) == (250, 90, 30)
    assert (usage.reasoning_tokens, usage.model_calls, usage.models) == (5, 2, ["m1"])
    assert [c["name"] for c in result.tool_calls] == ["whoami"]


# --- dynamic context -------------------------------------------------------------------------


def test_dynamic_context_is_appended_after_the_static_prompt():
    model = ScriptedModel(script=[AIMessage("oi")])
    run_turn(agent_with(model), "s1", "oi")
    system = model.seen[-1][0]
    assert isinstance(system, SystemMessage)
    assert system.content == f"{STATIC}\n\n{CONTEXT}"  # static prefix first (cacheable)


# --- rate-limit fallback ---------------------------------------------------------------------


def test_falls_back_to_the_next_model_on_429():
    first = ScriptedModel(script=[ProviderError(429)])
    second = ScriptedModel(script=[AIMessage("resposta do segundo")])
    assert run_turn(agent_with(first, second), "s1", "oi").text == "resposta do segundo"


def test_short_retry_after_waits_and_retries_the_same_model():
    sleeps: list[float] = []
    first = ScriptedModel(script=[ProviderError(429, retry_after="3"), AIMessage("do primeiro")])
    second = ScriptedModel(script=[AIMessage("não deveria responder")])
    result = run_turn(agent_with(first, second, sleeps=sleeps), "s1", "oi")
    assert result.text == "do primeiro" and second.seen == []
    assert sleeps == [3.5]  # retry-after + margin


def test_long_retry_after_moves_to_the_next_model_without_waiting():
    sleeps: list[float] = []
    first = ScriptedModel(script=[ProviderError(429, retry_after="42")])
    second = ScriptedModel(script=[AIMessage("do segundo")])
    assert run_turn(agent_with(first, second, sleeps=sleeps), "s1", "oi").text == "do segundo"
    assert sleeps == []


def test_same_model_is_retried_only_once():
    sleeps: list[float] = []
    first = ScriptedModel(script=[ProviderError(429, retry_after="2")] * 2)
    second = ScriptedModel(script=[AIMessage("do segundo")])
    assert run_turn(agent_with(first, second, sleeps=sleeps), "s1", "oi").text == "do segundo"
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    ("message", "seconds"),
    [
        ("Rate limit reached ... Please try again in 7.66s. Need more tokens?", 7.66),
        ("Please try again in 450ms.", 0.45),
        ("Please try again in 1m2.5s.", 62.5),
        ("no hint", None),
    ],
)
def test_retry_after_from_groq_message(message, seconds):
    assert retry_after(ProviderError(429, message)) == seconds


def test_retry_after_header_wins():
    assert retry_after(ProviderError(429, "try again in 9s", retry_after="2")) == 2.0


def test_other_errors_are_not_hidden_by_the_fallback():
    first = ScriptedModel(script=[ProviderError(500)])
    second = ScriptedModel(script=[AIMessage("não deveria responder")])
    with pytest.raises(ProviderError):
        run_turn(agent_with(first, second), "s1", "oi")
    assert second.seen == []


def test_friendly_message_when_every_model_is_rate_limited():
    models = [ScriptedModel(script=[ProviderError(429)]) for _ in range(3)]
    assert run_turn(agent_with(*models), "s1", "oi").text == RATE_LIMITED_MESSAGE


# --- history trimming ------------------------------------------------------------------------


def past_turn(i: int, payload: str = "x") -> list:
    return [
        HumanMessage(f"pergunta {i}"),
        call("search_products", {}, f"t{i}"),
        ToolMessage(payload, tool_call_id=f"t{i}"),
        AIMessage(f"resposta {i}"),
    ]


def test_trim_keeps_current_turn_whole_and_compacts_past_turns():
    big_result = ToolMessage("y" * 20000, tool_call_id="now")
    current = [HumanMessage("agora"), call("x", {}, "now"), big_result]
    history = past_turn(1) + past_turn(2) + current
    kept = trim_history(history, max_messages=20, max_tokens=100)
    assert kept[-3:] == current  # over budget alone, but never cut
    assert all(not isinstance(m, ToolMessage) or m.tool_call_id == "now" for m in kept)


def test_trim_drops_oldest_turns_first():
    history = past_turn(1) + past_turn(2) + past_turn(3) + [HumanMessage("agora")]
    kept = trim_history(history, max_messages=5, max_tokens=10_000)
    assert [m.content for m in kept] == [
        "pergunta 2",
        "resposta 2",
        "pergunta 3",
        "resposta 3",
        "agora",
    ]


def test_trim_by_tokens():
    history = past_turn(1) + [HumanMessage("p2"), AIMessage("r" * 4000), HumanMessage("agora")]
    kept = trim_history(history, max_messages=20, max_tokens=500)
    assert [m.content for m in kept] == ["agora"]  # turn 2 too big, and turn 1 is older


# --- client wiring ---------------------------------------------------------------------------


def test_system_prompt_has_official_contacts_only():
    prompt = render_system_prompt(get_client_config())
    assert "(67) 3341-4444" in prompt and "contato@emporiodamusica.com.br" in prompt
    assert "3321-4500" not in prompt and "empóriodamusica" not in prompt
    assert "$" not in prompt.replace("R$", "")  # every placeholder filled
    assert "Não indique outras lojas." in prompt  # P4: no partner stores


def test_partner_stores_rule_comes_from_config():
    client = get_client_config()
    catalog = client.catalog.model_copy(update={"partner_stores": ["Loja A", "Loja B"]})
    prompt = render_system_prompt(client.model_copy(update={"catalog": catalog}))
    assert "sugira as lojas parceiras: Loja A, Loja B." in prompt
    assert "Não indique outras lojas." not in prompt


def test_model_chain_settings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    settings = Settings(
        llm_model="openai/gpt-oss-120b",
        llm_fallback_models=["openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
    )
    first, second, qwen = model_chain(settings)
    assert [m.model_name for m in (first, second, qwen)] == [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]
    assert first.reasoning_effort == "low" and first.reasoning_format is None
    assert qwen.reasoning_format == "parsed"
    assert (first.max_retries, second.max_retries, qwen.max_retries) == (0, 0, 2)
