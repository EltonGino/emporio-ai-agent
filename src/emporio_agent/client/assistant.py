"""Wires the Empório client layer into the core agent: models, persona prompt, tools, context."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from string import Template

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.identity import FailedLookupStore, VerifiedOrderStore
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import HolidayCalendar, store_status
from emporio_agent.client.tools import br_datetime, build_tools
from emporio_agent.clock import Clock, get_clock
from emporio_agent.config import ClientConfig, Settings
from emporio_agent.core.agent import build_agent
from emporio_agent.core.guards import OutputGuard, PhraseCheck, PriceCheck, ProtocolCheck
from emporio_agent.core.retrieval.base import Retriever

GROQ_FALLBACK_RETRIES = 0  # a 429 moves on to the next model at once
GROQ_LAST_MODEL_RETRIES = 2  # the last model waits through the SDK's backoff

SAFE_PROTOCOL_ANSWER = (
    "Desculpe, me confundi aqui: ainda não registrei nenhum chamado. "
    "Quer que eu registre seu caso com a equipe agora para gerar o protocolo?"
)
SAFE_PRICE_ANSWER = (
    "Desculpe, não consegui confirmar esses valores agora. "
    "Quer que eu consulte o catálogo de novo pra você?"
)


# Robotic refusals seen in evals; they break Tom's persona (manual §7.1).
ROBOTIC_PHRASES = (
    "não posso atender a essa solicitação",
    "não posso atender a sua solicitação",
    "não tenho acesso a esses dados",
)
SAFE_REFUSAL_ANSWER = (
    "Isso eu não consigo fazer por aqui 😊 Mas posso te ajudar com instrumentos, preços, "
    "formas de pagamento, pedidos ou trocas. O que você precisa?"
)


def phrase_correction_note(found: set[str]) -> str:
    phrases = "; ".join(f'"{p}"' for p in sorted(found))
    return (
        "## Correção obrigatória (tom)\n"
        f"Sua resposta anterior usou {phrases}, que soa robótico. Responda de novo como o Tom: "
        "gentil, com o motivo em palavras simples e oferecendo o que você pode fazer."
    )


def protocol_correction_note(invalid: set[str]) -> str:
    codes = ", ".join(sorted(invalid))
    return (
        "## Correção obrigatória (protocolo)\n"
        f"Sua resposta anterior citou como protocolo {codes}, que não foi gerado por "
        "handoff_to_human nesta conversa. Responda de novo sem esse número. Só informe "
        "protocolos devolvidos por handoff_to_human; se o caso ainda não foi registrado, "
        "pergunte se o cliente quer registrar."
    )


def price_correction_note(invalid: set[str]) -> str:
    amounts = ", ".join(sorted(invalid))
    return (
        "## Correção obrigatória (valores)\n"
        f"Sua resposta anterior citou {amounts}, que não aparece nos resultados das "
        "ferramentas desta mensagem. Responda de novo citando só valores devolvidos pelas "
        "ferramentas neste turno (consulte de novo se precisar), sem calcular nem inventar "
        "produtos ou preços."
    )


def output_guard() -> OutputGuard:
    return OutputGuard(
        [
            ProtocolCheck(
                tool_name="handoff_to_human",
                keywords=("protocolo", "protocol"),  # also English/Spanish answers
                correction_note=protocol_correction_note,
                safe_answer=SAFE_PROTOCOL_ANSWER,
            ),
            PriceCheck(correction_note=price_correction_note, safe_answer=SAFE_PRICE_ANSWER),
            PhraseCheck(
                phrases=ROBOTIC_PHRASES,
                correction_note=phrase_correction_note,
                safe_answer=SAFE_REFUSAL_ANSWER,
                name="robotic_phrase",
            ),
        ]
    )


def make_chat_model(name: str, settings: Settings, last: bool) -> BaseChatModel:
    if settings.llm_provider != "groq":
        return init_chat_model(
            name, model_provider=settings.llm_provider, temperature=settings.llm_temperature
        )
    from langchain_groq import ChatGroq

    params = {"reasoning_effort": settings.llm_reasoning_effort}
    if name.startswith("qwen/"):
        params["reasoning_format"] = "parsed"  # raw (the default) + tools → HTTP 400
    return ChatGroq(
        model=name,
        temperature=settings.llm_temperature,
        max_retries=GROQ_LAST_MODEL_RETRIES if last else GROQ_FALLBACK_RETRIES,
        **params,
    )


def model_chain(settings: Settings) -> list[BaseChatModel]:
    names = [settings.llm_model, *settings.llm_fallback_models]
    return [make_chat_model(n, settings, last=i == len(names) - 1) for i, n in enumerate(names)]


def render_system_prompt(client: ClientConfig) -> str:
    """The static prompt: persona file + official contacts from client.yaml (P1/P2)."""
    partners = client.catalog.partner_stores
    partners_rule = (
        f"Se fizer sentido, sugira as lojas parceiras: {', '.join(partners)}."
        if partners
        else "Não indique outras lojas."  # P4: the manual names no partners
    )
    template = Template(client.assistant.persona_prompt.read_text(encoding="utf-8"))
    return template.substitute(
        assistant_name=client.assistant.name,
        store_name=client.store.name,
        slogan=client.store.slogan,
        phone=client.store.phone_whatsapp,
        email=client.store.email,
        address=client.store.address,
        partners_rule=partners_rule,
    ).strip()


def context_provider(client: ClientConfig, clock: Clock):
    """Per-call context appended after the static prompt (date/time and open/closed)."""
    calendar = HolidayCalendar(client.store.holidays)

    def context() -> str:
        status = store_status(clock.now(), client.store, calendar)
        if status.is_open:
            state = f"Loja aberta agora (fecha às {status.closes_at:%H:%M})."
        else:
            reason = f" ({status.holiday})" if status.holiday else ""
            back = f" Abre {br_datetime(status.next_opening)}." if status.next_opening else ""
            state = f"Loja fechada agora{reason}.{back}"
        return f"## Agora\n{br_datetime(status.now)} (horário de Campo Grande). {state}"

    return context


def build_store_agent(
    settings: Settings,
    client: ClientConfig,
    clock: Clock,
    retriever: Retriever,
    checkpointer: BaseCheckpointSaver,
    failed_lookups: FailedLookupStore | None = None,
    verified_orders: VerifiedOrderStore | None = None,
):
    """The store agent. Identity state defaults to in-memory (CLI, tests); the API passes the
    database-backed versions (`SqlFailedLookups`, `SqlVerifiedOrders`)."""
    engine = make_engine(settings.database_url)
    ensure_store_data(engine)
    tools = build_tools(
        SqlStoreRepository(engine),
        client,
        clock,
        retriever,
        failed_lookups=failed_lookups,
        verified_orders=verified_orders,
    )
    return build_agent(
        model_chain(settings),
        tools,
        render_system_prompt(client),
        context_provider(client, clock),
        checkpointer,
        settings.history_max_messages,
        settings.history_max_tokens,
        settings.rate_limit_max_wait_seconds,
        guards=[output_guard()],
    )


DEFAULT_EVAL_NOW = "2026-03-25T10:30"  # demo date: store open, after the last order


def eval_agent_factory(
    settings: Settings,
    client: ClientConfig,
    retriever: Retriever,
    workdir: Path,
    llm_model: str | None = None,
) -> Callable[[str | None], object]:
    """Builds a fresh agent per eval case: its own store DB copy (handoffs don't leak between
    cases), in-memory conversation memory and a fixed clock. `llm_model` runs the evals on that
    model only (no fallbacks), e.g. openai/gpt-oss-20b to spare the 120b quota."""
    from langgraph.checkpoint.memory import InMemorySaver

    if llm_model:
        settings = settings.model_copy(update={"llm_model": llm_model, "llm_fallback_models": []})

    def factory(now: str | None) -> object:
        db = workdir / f"store-{uuid.uuid4().hex}.db"
        case_settings = settings.model_copy(update={"database_url": f"sqlite:///{db}"})
        clock = get_clock(datetime.fromisoformat(now or DEFAULT_EVAL_NOW))
        return build_store_agent(case_settings, client, clock, retriever, InMemorySaver())

    return factory
