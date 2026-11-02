"""Wires the Empório client layer into the core agent: models, persona prompt, tools, context."""

from __future__ import annotations

from string import Template

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import HolidayCalendar, store_status
from emporio_agent.client.tools import br_datetime, build_tools
from emporio_agent.clock import Clock
from emporio_agent.config import ClientConfig, Settings
from emporio_agent.core.agent import build_agent
from emporio_agent.core.retrieval.base import Retriever

GROQ_FALLBACK_RETRIES = 0  # a 429 moves on to the next model at once
GROQ_LAST_MODEL_RETRIES = 2  # the last model waits through the SDK's backoff


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
):
    engine = make_engine(settings.database_url)
    ensure_store_data(engine)
    tools = build_tools(SqlStoreRepository(engine), client, clock, retriever)
    return build_agent(
        model_chain(settings),
        tools,
        render_system_prompt(client),
        context_provider(client, clock),
        checkpointer,
        settings.history_max_messages,
        settings.history_max_tokens,
    )
