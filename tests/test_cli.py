import json

import pytest
from fakes import ProviderError, ScriptedModel, call
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from typer.testing import CliRunner

from emporio_agent import cli
from emporio_agent.core.agent import build_agent

runner = CliRunner()


@tool(response_format="content_and_artifact")
def search_policies(query: str):
    """Fake policy search."""
    return '{"results":[]}', [
        {
            "section_path": "4. Trocas > 4.2 Trocas por Defeito",
            "score": 0.0325,
            "matched_by": ["bm25", "dense"],
            "section_id": "4.2",
        }
    ]


@pytest.fixture
def fake_agent(monkeypatch, tmp_path):
    """The CLI with a scripted model instead of Groq + embeddings; traces in tmp_path."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(cli, "TRACES_DIR", tmp_path)
    seen_clocks = []

    def install(*script):
        model = ScriptedModel(script=list(script))

        def build(settings, client, clock):
            seen_clocks.append(clock)
            return build_agent(
                [model], [search_policies], "S", lambda messages: "C", InMemorySaver(), 20, 2500
            )

        monkeypatch.setattr(cli, "build_chat_agent", build)
        return model, seen_clocks

    return install


def test_chat_shows_the_answer_and_writes_a_trace(fake_agent, tmp_path):
    fake_agent(AIMessage("Oi! Eu sou o **Tom** 🎸"))
    result = runner.invoke(cli.app, ["--session", "abc"], input="oi\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Tom:" in result.output and "Eu sou o Tom" in result.output
    assert "Até mais!" in result.output
    lines = (tmp_path / "abc.jsonl").read_text().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["user_message", "final"]


def test_debug_shows_tools_retrieval_and_usage(fake_agent):
    final = AIMessage("Defeito: até 30 dias.", response_metadata={"model_name": "m1"})
    final.usage_metadata = {"input_tokens": 2200, "output_tokens": 40, "total_tokens": 2240}
    fake_agent(call("search_policies", {"query": "prazo troca defeito"}), final)
    result = runner.invoke(cli.app, ["--debug"], input="posso trocar com defeito?\n/exit\n")
    assert result.exit_code == 0, result.output
    assert '→ search_policies({"query": "prazo troca defeito"})' in result.output
    assert "← search_policies" in result.output
    assert "§ 4. Trocas > 4.2 Trocas por Defeito (score 0.0325, bm25+dense)" in result.output
    assert "tokens: entrada 2200 (cache 0), saída 40" in result.output


def test_without_debug_tools_are_hidden(fake_agent):
    fake_agent(call("search_policies", {"query": "x"}), AIMessage("Resposta."))
    result = runner.invoke(cli.app, [], input="pergunta\n/exit\n")
    assert "search_policies" not in result.output and "Resposta." in result.output


def test_new_starts_another_session(fake_agent, tmp_path):
    model, _ = fake_agent(AIMessage("um"), AIMessage("dois"))
    result = runner.invoke(cli.app, ["--session", "first"], input="oi\n/new\noi de novo\n/exit\n")
    assert "Nova conversa:" in result.output
    assert (tmp_path / "first.jsonl").exists()
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    second_call_humans = [m for m in model.seen[-1] if m.type == "human"]
    assert [m.content for m in second_call_humans] == ["oi de novo"]  # no memory of "first"


def test_now_sets_a_simulated_clock(fake_agent):
    _, clocks = fake_agent(AIMessage("ok"))
    result = runner.invoke(cli.app, ["--now", "2026-03-25T10:30"], input="/exit\n")
    assert "Data simulada: 25/03/2026 10:30" in result.output
    assert clocks[0].now().isoformat() == "2026-03-25T10:30:00-04:00"  # store time (UTC-4)


def test_invalid_now_and_session_are_rejected(fake_agent):
    fake_agent()
    assert runner.invoke(cli.app, ["--now", "ontem"]).exit_code == 2
    assert runner.invoke(cli.app, ["--session", "../x"]).exit_code == 2


def test_missing_groq_key_exits_with_a_hint(fake_agent, monkeypatch):
    fake_agent()
    monkeypatch.delenv("GROQ_API_KEY")
    result = runner.invoke(cli.app, [], input="/exit\n")
    assert result.exit_code == 1 and "GROQ_API_KEY" in result.output


def test_error_event_is_shown_politely(fake_agent):
    fake_agent(RuntimeError("boom"))
    result = runner.invoke(cli.app, ["--debug"], input="oi\n/exit\n")
    assert "problema técnico" in result.output
    assert "RuntimeError: boom" in result.output  # details only in debug


def test_without_session_every_run_starts_a_new_conversation(fake_agent, tmp_path):
    fake_agent(AIMessage("um"), AIMessage("dois"))
    first = runner.invoke(cli.app, [], input="oi\n/exit\n")
    second = runner.invoke(cli.app, [], input="oi\n/exit\n")
    assert "Nova conversa:" in first.output and "Nova conversa:" in second.output
    assert len(list(tmp_path.glob("*.jsonl"))) == 2  # two different session ids


def test_explicit_session_resumes(fake_agent):
    fake_agent(AIMessage("ok"))
    result = runner.invoke(cli.app, ["--session", "abc"], input="/exit\n")
    assert "Retomando a conversa abc" in result.output


def test_middleware_logs_stay_out_of_the_terminal(fake_agent):
    fake_agent(ProviderError(429, retry_after="99"), AIMessage("ok"))  # 1 model: gives up
    result = runner.invoke(cli.app, [], input="oi\n/exit\n")
    assert "429 on" not in result.output
    assert "muitas conversas" in result.output  # the friendly rate-limit answer
