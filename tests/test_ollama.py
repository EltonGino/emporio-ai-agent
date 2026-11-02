"""Local Ollama provider (development): model construction, error policy, history, overrides.
No server needed: models are built but never called, the agent runs on a scripted model."""

import pytest
from fakes import ProviderError, ScriptedModel
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from typer.testing import CliRunner

from emporio_agent import cli
from emporio_agent.client.assistant import (
    eval_agent_factory,
    model_chain,
    provider_error_policy,
    with_llm,
)
from emporio_agent.config import Settings, get_client_config
from emporio_agent.core.agent import FAILED_MESSAGE, build_agent, run_turn, trim_history
from emporio_agent.core.events import ERROR_MESSAGE

OLLAMA = Settings(
    llm_provider="ollama",
    llm_model="gpt-oss:20b",
    ollama_base_url="http://elton:11434",
    ollama_num_ctx=16384,
)


def test_ollama_model_chain_is_one_model_with_explicit_context_and_reasoning():
    models = model_chain(OLLAMA)  # LLM_FALLBACK_MODELS (Groq) are ignored
    assert len(models) == 1
    model = models[0]
    assert type(model).__name__ == "ChatOllama"
    assert (model.model, model.base_url, model.num_ctx) == (
        "gpt-oss:20b",
        "http://elton:11434",
        16384,
    )
    assert model.reasoning == "low"  # LLM_REASONING_EFFORT → Ollama `think`


def agent_with(model, policy):
    return build_agent([model], [], "S", lambda messages: "C", InMemorySaver(), 20, 2500, **policy)


def test_ollama_has_no_rate_limit_handling():
    model = ScriptedModel(script=[ProviderError(429, retry_after="2")])
    result = run_turn(agent_with(model, provider_error_policy(OLLAMA)), "s", "oi")
    assert result.text == ERROR_MESSAGE  # not waited/retried: a local server has no quotas


def test_ollama_unparsable_tool_call_gets_one_plain_retry():
    model = ScriptedModel(script=[OutputParserException("bad tool args"), AIMessage("ok")])
    assert run_turn(agent_with(model, provider_error_policy(OLLAMA)), "s", "oi").text == "ok"
    twice = ScriptedModel(script=[OutputParserException("bad")] * 2)
    assert (
        run_turn(agent_with(twice, provider_error_policy(OLLAMA)), "s", "oi").text == FAILED_MESSAGE
    )


def test_groq_specific_400_is_not_retried_on_ollama():
    groq_400 = ProviderError(400, "Error code: 400 - {'code': 'tool_use_failed'}")
    model = ScriptedModel(script=[groq_400])
    assert (
        run_turn(agent_with(model, provider_error_policy(OLLAMA)), "s", "oi").text == ERROR_MESSAGE
    )


def test_groq_policy_unchanged():
    policy = provider_error_policy(Settings(llm_provider="groq"))
    assert policy["rate_limits"] is True


def test_past_reasoning_is_not_sent_back_to_the_model():
    """ChatOllama re-sends `reasoning_content`; past turns drop it, the current turn keeps it."""
    past = AIMessage("Resposta 1", additional_kwargs={"reasoning_content": "pensamento antigo"})
    current = AIMessage(
        "",
        additional_kwargs={"reasoning_content": "pensamento atual"},
        tool_calls=[{"name": "x", "args": {}, "id": "t1"}],
    )
    kept = trim_history([HumanMessage("p1"), past, HumanMessage("p2"), current], 20, 10_000)
    assert "reasoning_content" not in kept[1].additional_kwargs
    assert kept[-1].additional_kwargs["reasoning_content"] == "pensamento atual"


def test_with_llm_overrides():
    base = Settings(llm_provider="groq", llm_model="openai/gpt-oss-120b")
    assert with_llm(base, None, None) is base
    local = with_llm(base, "ollama", "gpt-oss:20b")
    assert (local.llm_provider, local.llm_model) == ("ollama", "gpt-oss:20b")
    assert with_llm(base, None, "openai/gpt-oss-20b", fallbacks=False).llm_fallback_models == []


def test_cli_provider_and_model_options(monkeypatch, tmp_path):
    seen = {}

    def builder(settings, client, clock):
        seen["settings"] = settings
        return build_agent(
            [ScriptedModel(script=[])], [], "S", lambda messages: "C", InMemorySaver(), 20, 2500
        )

    monkeypatch.setattr(cli, "build_chat_agent", builder)
    monkeypatch.setattr(cli, "TRACES_DIR", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)  # not needed for ollama
    result = CliRunner().invoke(
        cli.app, ["--provider", "ollama", "--model", "gpt-oss:20b"], input="/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert (seen["settings"].llm_provider, seen["settings"].llm_model) == ("ollama", "gpt-oss:20b")


def test_eval_factory_provider_override(monkeypatch, tmp_path, policy_retriever):
    from emporio_agent.client import assistant

    seen = {}
    monkeypatch.setattr(
        assistant, "build_store_agent", lambda settings, *a, **k: seen.setdefault("s", settings)
    )
    factory = eval_agent_factory(
        Settings(), get_client_config(), policy_retriever, tmp_path, "gpt-oss:20b", "ollama"
    )
    factory(None)
    assert (seen["s"].llm_provider, seen["s"].llm_model) == ("ollama", "gpt-oss:20b")
    assert seen["s"].llm_fallback_models == []


@pytest.mark.parametrize("bad", [0, 1000])
def test_num_ctx_has_a_floor(bad):
    with pytest.raises(ValueError):
        Settings(ollama_num_ctx=bad)
