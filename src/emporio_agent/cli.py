"""Interactive CLI: chat with the agent in the terminal (AGENTS.md §10).

    uv run emporio-chat [--session ID] [--debug] [--now ISO]

A thin layer over the core: it consumes the same event stream as the API and writes the same
JSONL trace. `--debug` shows tool calls, results, retrieved sections, middleware notices and
token usage (dimmed). Commands: /new (new conversation), /exit.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from emporio_agent.client.assistant import with_llm
from emporio_agent.clock import Clock, FixedClock, get_clock
from emporio_agent.config import TRACES_DIR, ClientConfig, Settings, get_client_config, get_settings
from emporio_agent.core.events import AgentEvent, stream_turn
from emporio_agent.core.tracing import JsonlTracer, trace_path, traced

app = typer.Typer(add_completion=False, help="Chat with Tom, Empório da Música's assistant.")
console = Console()


def new_session_id() -> str:
    return uuid.uuid4().hex


def build_chat_agent(settings: Settings, client: ClientConfig, clock: Clock):
    """The real agent: hybrid policy search, Groq models and a persistent SQLite memory."""
    from emporio_agent.client.assistant import build_store_agent
    from emporio_agent.core.agent import sqlite_checkpointer
    from emporio_agent.core.retrieval.index import get_retrievers

    with console.status("Preparando a busca nas políticas (a 1ª vez baixa ~1 GB)…"):
        retriever = get_retrievers()[2]
    checkpointer = sqlite_checkpointer(settings.checkpoint_db)
    return build_store_agent(settings, client, clock, retriever, checkpointer)


def debug_lines(event: AgentEvent) -> list[str]:
    data = event.data
    match event.type:
        case "tool_start":
            return [f"→ {data['name']}({json.dumps(data['args'], ensure_ascii=False)})"]
        case "tool_end":
            return [f"← {data['name']} [{data['duration_ms']} ms] {data['result_preview']}"]
        case "retrieval":
            return [
                f"  § {c['section_path']} (score {c['score']}, {'+'.join(c['matched_by'])})"
                for c in data["chunks"]
            ]
        case "notice":
            details = ", ".join(f"{k}={v}" for k, v in data.items() if k != "kind")
            return [f"! {data['kind']}: {details}"]
        case "final":
            u = data["usage"]
            return [
                f"tokens: entrada {u['input_tokens']} (cache {u['cached_tokens']}), "
                f"saída {u['output_tokens']} (raciocínio {u['reasoning_tokens']}) · "
                f"{u['model_calls']} chamada(s) · {', '.join(u['models']) or '-'} · "
                f"{data['duration_ms']} ms"
            ]
    return []


def print_dim(lines: list[str]) -> None:
    for line in lines:
        console.print(Text(line, style="dim"))  # Text: no rich markup parsing of tool output


def chat_turn(
    agent, session_id: str, text: str, tracer: JsonlTracer, debug: bool, assistant: str
) -> None:
    final = error = None
    with console.status(f"{assistant} está digitando…"):
        for event in traced(stream_turn(agent, session_id, text), tracer):
            if event.type == "final":
                final = event
            elif event.type == "error":
                error = event
            elif debug:
                print_dim(debug_lines(event))
    console.print(Text(f"{assistant}:", style="bold magenta"))
    if error is not None:
        console.print(Text(error.data["message"], style="red"))
        if debug:
            print_dim([f"erro: {error.data['detail']}"])
        return
    console.print(Markdown(final.data["text"] if final else ""))
    if debug and final is not None:
        print_dim(debug_lines(final))


def parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("use ISO format, e.g. 2026-03-25T10:30") from exc


def check_session_id(value: str | None) -> str | None:
    if value is not None:
        try:
            trace_path(TRACES_DIR, value)
        except ValueError as exc:
            raise typer.BadParameter("use letters, digits, '-' or '_' (max 100)") from exc
    return value


@app.command()
def chat(
    session: Annotated[
        str | None,
        typer.Option(help="Resume a conversation by id.", callback=check_session_id),
    ] = None,
    debug: Annotated[bool, typer.Option(help="Show tools, retrieval and token usage.")] = False,
    now: Annotated[
        str | None,
        typer.Option(
            help="Simulated store time, ISO (e.g. 2026-03-25T10:30). Overrides AGENT_NOW."
        ),
    ] = None,
    provider: Annotated[
        str | None, typer.Option(help="LLM provider (groq | ollama). Overrides LLM_PROVIDER.")
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            help="LLM model, e.g. openai/gpt-oss-20b or gpt-oss:20b. Overrides LLM_MODEL."
        ),
    ] = None,
) -> None:
    # Middleware decisions are shown by --debug and kept in the JSONL trace; don't let Python's
    # last-resort logging handler print them in the customer's terminal.
    package_logger = logging.getLogger("emporio_agent")
    package_logger.addHandler(logging.NullHandler())
    package_logger.propagate = False

    settings = with_llm(get_settings(), provider, model)
    client = get_client_config()
    clock = get_clock(parse_now(now))
    if settings.llm_provider == "groq" and not os.environ.get("GROQ_API_KEY"):
        console.print("[red]GROQ_API_KEY não definida.[/] Copie .env.example para .env e preencha.")
        raise typer.Exit(1)

    agent = build_chat_agent(settings, client, clock)
    tracer = JsonlTracer(TRACES_DIR)
    session_id = session or new_session_id()
    assistant = client.assistant.name

    console.print(f"[bold]{client.store.name}[/] — {client.store.slogan}")
    if isinstance(clock, FixedClock):
        console.print(f"[yellow]Data simulada: {clock.now():%d/%m/%Y %H:%M} (horário da loja)[/]")
    if session:
        console.print(f"[dim]Retomando a conversa {session_id}[/]")
    else:  # never resume implicitly: a new session id unless --session is given
        console.print(f"[dim]Nova conversa: {session_id} (para retomar: --session {session_id})[/]")
    console.print("[dim]/new para recomeçar · /exit para sair[/]\n")

    while True:
        try:
            text = console.input("[bold cyan]Você:[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/exit":
            break
        if text == "/new":
            session_id = new_session_id()
            console.print(f"[dim]Nova conversa: {session_id}[/]\n")
            continue
        chat_turn(agent, session_id, text, tracer, debug, assistant)
        console.print()
    console.print("Até mais! 🎶")


if __name__ == "__main__":
    app()
