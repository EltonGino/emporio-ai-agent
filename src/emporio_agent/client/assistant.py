"""Wires the Empório client layer into the core agent: models, persona prompt, tools, context."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from string import Template

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from unidecode import unidecode

from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.identity import FailedLookupStore, VerifiedOrderStore
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import HolidayCalendar, store_status
from emporio_agent.client.tools import br_datetime, build_tools
from emporio_agent.clock import Clock, get_clock
from emporio_agent.config import ClientConfig, Settings
from emporio_agent.core.agent import build_agent, is_invalid_tool_call, is_local_invalid_tool_call
from emporio_agent.core.guards import (
    FieldLabelCheck,
    OutputGuard,
    PhraseCheck,
    PriceCheck,
    ProtocolCheck,
    RequiredToolCheck,
    TopicToolCheck,
    issued_protocols,
)
from emporio_agent.core.retrieval.base import Retriever

GROQ_FALLBACK_RETRIES = 0  # a 429 moves on to the next model at once
GROQ_LAST_MODEL_RETRIES = 2  # the last model waits through the SDK's backoff

# Safe answers (what the guard shows when a corrected answer still fails) exist in the three
# languages Tom speaks; `localized` picks one with the language hint's detection (owner).
SAFE_PROTOCOL_ANSWER = (
    "Desculpe, me confundi aqui: ainda não registrei nenhum chamado. "
    "Quer que eu registre seu caso com a equipe agora para gerar o protocolo?"
)
SAFE_PROTOCOL_ANSWERS = {
    "pt": SAFE_PROTOCOL_ANSWER,
    "en": "Sorry, I got mixed up: I haven't registered any case yet. Would you like me to "
    "register it with the team now to get a protocol number?",
    "es": "Disculpa, me confundí: todavía no registré ningún caso. ¿Quieres que lo registre "
    "ahora con el equipo para generar el protocolo?",
}
REGISTERED_PROTOCOL = {
    "pt": (
        "Desculpe, me confundi ao informar o protocolo.",
        "Seu chamado já está registrado com o protocolo **{}**.",
        "Os protocolos registrados nesta conversa são: {}.",
    ),
    "en": (
        "Sorry, I got the protocol number wrong.",
        "Your case is already registered under protocol **{}**.",
        "The protocols registered in this conversation are: {}.",
    ),
    "es": (
        "Disculpa, me equivoqué con el número de protocolo.",
        "Tu caso ya está registrado con el protocolo **{}**.",
        "Los protocolos registrados en esta conversación son: {}.",
    ),
}
SAFE_PRICE_ANSWER = (
    "Desculpe, não consegui confirmar esses valores agora. "
    "Quer que eu consulte o catálogo de novo pra você?"
)
SAFE_PRICE_ANSWERS = {
    "pt": SAFE_PRICE_ANSWER,
    "en": "Sorry, I couldn't confirm those prices right now. Would you like me to check the "
    "catalog again?",
    "es": "Disculpa, no pude confirmar esos valores ahora. ¿Quieres que consulte el catálogo "
    "de nuevo?",
}


# Robotic refusals seen in evals; they break Tom's persona (manual §7.1).
ROBOTIC_PATTERNS = (
    r"\bnao posso (?:atender|realizar|fornecer)\b",
    r"\bnao tenho acesso a esses dados\b",
)
SAFE_REFUSAL_ANSWER = (
    "Isso eu não consigo fazer por aqui 😊 Mas posso te ajudar com instrumentos, preços, "
    "formas de pagamento, pedidos ou trocas. O que você precisa?"
)
SAFE_REFUSAL_ANSWERS = {
    "pt": SAFE_REFUSAL_ANSWER,
    "en": "That's something I can't do here 😊 But I can help you with instruments, prices, "
    "payment options, orders or returns. What do you need?",
    "es": "Eso no lo puedo hacer por aquí 😊 Pero puedo ayudarte con instrumentos, precios, "
    "formas de pago, pedidos o cambios. ¿Qué necesitas?",
}


def customer_language(messages: Sequence[AnyMessage]) -> str:
    """ "en"/"es" when the last customer message is clearly in that language, else "pt"."""
    last = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    return (detect_language(last.text) if last else None) or "pt"


def localized(answers: dict[str, str]) -> Callable[[Sequence[AnyMessage]], str]:
    """A safe answer in the language of the customer's last message."""
    return lambda history: answers[customer_language(history)]


def safe_protocol_answer(history: Sequence[AnyMessage]) -> str:
    language = customer_language(history)
    protocols = sorted(issued_protocols(history, "handoff_to_human"))
    if not protocols:
        return SAFE_PROTOCOL_ANSWERS[language]
    sorry, one, many = REGISTERED_PROTOCOL[language]
    registered = (
        one.format(protocols[0]) if len(protocols) == 1 else many.format(", ".join(protocols))
    )
    return f"{sorry} {registered}"


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
        f"Sua resposta anterior citou {amounts}, que não veio de nenhuma ferramenta neste "
        "turno. Consulte agora a ferramenta que fornece esse dado: calculate_payment_options "
        "para PIX, parcelas e totais; search_products ou get_product_details para o preço de "
        "um produto. Depois responda só com os valores que ela devolver, sem calcular."
    )


# Payment answers need a payment tool in the turn (smoke test: "no PIX o valor permanece o
# mesmo" after only search_products). search_policies covers general rules without a product.
# Installments/boleto/card: only claims about conditions count ("12x", "sem juros"), not prices
# elsewhere in the answer (an offer "quer saber do parcelamento?" isn't a claim).
PAYMENT_TOPIC_WORDS = (
    "parcel",
    "boleto",
    "cartão de crédito",
    "cartão de débito",
    "forma de pagamento",
    "formas de pagamento",
    "pagamento combinado",
)
PAYMENT_DETAIL_WORDS = ("juros", "vezes", "à vista", "compensa", "mínim", "dias úteis", "%")
PAYMENT_DETAIL_PATTERNS = (r"\b\d{1,2} ?x\b",)  # "12x", "3 x"
PAYMENT_TOOLS = ("calculate_payment_options", "get_product_details", "search_policies")
# PIX: a product search returns pix_price, so citing that exact amount is accepted too.
PIX_DETAIL_WORDS = ("desconto", "%")
PIX_EVIDENCE_FIELDS = ("pix_price", "pix_total")
# When an amount/PIX/payment answer had none of these in the turn, the retry must call one.
CATALOG_PAYMENT_TOOLS = (
    "search_products",
    "get_product_details",
    "list_active_promotions",
    "calculate_payment_options",
)
FORCED_TOOL_NOTE = (
    "## Chamada obrigatória\n"
    "Nesta tentativa, não responda em texto: chame agora a ferramenta que traz o dado "
    "(search_products para produtos e preços, com termos em português; "
    "calculate_payment_options para PIX e parcelas)."
)
SAFE_PAYMENT_ANSWER = (
    "Pra te passar as condições de pagamento certinhas, me diz qual instrumento você quer "
    "que eu calculo o PIX e as parcelas pra você 😊"
)
SAFE_PAYMENT_ANSWERS = {
    "pt": SAFE_PAYMENT_ANSWER,
    "en": "To give you the exact payment conditions, tell me which instrument you want and "
    "I'll work out the PIX price and the installments for you 😊",
    "es": "Para darte las condiciones de pago exactas, dime qué instrumento quieres y calculo "
    "el precio por PIX y las cuotas 😊",
}


def payment_correction_note(_: set[str]) -> str:
    return (
        "## Correção obrigatória (pagamento)\n"
        "Sua resposta anterior fala de parcelas ou formas de pagamento sem ter consultado a "
        "ferramenta certa neste turno. Chame agora calculate_payment_options com o produto (ou "
        "o carrinho) para parcelas e totais — ou search_policies para uma regra geral — e "
        "responda com o que ela devolver."
    )


def pix_correction_note(_: set[str]) -> str:
    return (
        "## Correção obrigatória (PIX)\n"
        "Sua resposta anterior fala do PIX sem o valor de PIX devolvido pelas ferramentas. Use "
        "o pix_price_text do resultado de search_products (já segue a regra: sem desconto de "
        "PIX sobre preço promocional) ou chame calculate_payment_options, e responda com esse "
        "valor."
    )


def output_guard() -> OutputGuard:
    return OutputGuard(
        [
            ProtocolCheck(
                tool_name="handoff_to_human",
                keywords=("protocolo", "protocol"),  # also English/Spanish answers
                correction_note=protocol_correction_note,
                safe_answer=safe_protocol_answer,
            ),
            PriceCheck(
                correction_note=price_correction_note,
                safe_answer=localized(SAFE_PRICE_ANSWERS),
                forced_tools=CATALOG_PAYMENT_TOOLS,
            ),
            TopicToolCheck(
                topic_words=PAYMENT_TOPIC_WORDS,
                detail_words=PAYMENT_DETAIL_WORDS,
                detail_patterns=PAYMENT_DETAIL_PATTERNS,
                amounts_are_details=False,
                tools=PAYMENT_TOOLS,
                correction_note=payment_correction_note,
                safe_answer=localized(SAFE_PAYMENT_ANSWERS),
                name="payment_tool",
                forced_tools=CATALOG_PAYMENT_TOOLS,
            ),
            TopicToolCheck(
                topic_words=("pix",),
                detail_words=PIX_DETAIL_WORDS,
                tools=PAYMENT_TOOLS,
                evidence_fields=PIX_EVIDENCE_FIELDS,
                correction_note=pix_correction_note,
                safe_answer=localized(SAFE_PAYMENT_ANSWERS),
                name="pix_value",
                forced_tools=CATALOG_PAYMENT_TOOLS,
            ),
            PhraseCheck(
                patterns=ROBOTIC_PATTERNS,
                correction_note=phrase_correction_note,
                safe_answer=localized(SAFE_REFUSAL_ANSWERS),
                name="robotic_phrase",
            ),
            FieldLabelCheck(
                tool_name="get_order_status",
                fields=("order_date",),
                wrong_labels=WRONG_DATE_LABELS,
                right_labels=RIGHT_DATE_LABELS,
                correction_note=order_date_correction_note,
                safe_answer=safe_order_date_answer,
                name="order_date_label",
            ),
            RequiredToolCheck(
                message_patterns=(ORDER_NUMBER, EMAIL),
                tools=ORDER_TOOLS,
                correction_note=order_lookup_correction_note,
                safe_answer=localized(ORDER_LOOKUP_ANSWERS),
                name="order_lookup",
                forced_tools=ORDER_TOOLS,
            ),
        ],
        forced_tool_note=FORCED_TOOL_NOTE,
    )


# Order dates: order_date is the purchase date; the data has no shipping, delivery or
# cancellation date (owner's test: "📅 Enviado em 15/02/2026", "entregue em 15/10/2025").
WRONG_DATE_LABELS = (
    r"envi",  # envio, enviado (pt/es)
    r"entreg",  # entregue, entrega (pt), entregado (es)
    r"despach",
    r"expedi",
    r"postad",
    r"cancel",
    r"chegou",
    r"\bship",
    r"\bdeliver",
    r"\bdispatch",
    r"\bsent\b",
    r"\barriv",
)
RIGHT_DATE_LABELS = (
    r"feito",
    r"\bdo pedido",
    r"\bpedido (?:de|em|no dia)\b",
    r"\bcompra",
    r"realizad",
    r"\bplaced",
    r"\bordered",
    r"\border date",
    r"\bpurchase",
    r"\bmade\b",
    r"\bhecho",
    r"\bdel pedido",
)
ORDER_STATUS = {
    "en": {
        "pending": "awaiting payment confirmation",
        "confirmed": "confirmed, being prepared",
        "shipped": "shipped",
        "delivered": "delivered",
        "cancelled": "cancelled",
    },
    "es": {
        "pending": "esperando la confirmación del pago",
        "confirmed": "confirmado, en preparación",
        "shipped": "enviado",
        "delivered": "entregado",
        "cancelled": "cancelado",
    },
}
# A message with an order number and an e-mail must be looked up in the same turn (video
# rehearsal on the 20b: "E o pedido 9, <e-mail>?" got "me passa o número e o e-mail", twice).
EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
# The number after pedido/order/compra ("E o pedido 9", "pedido nº 9") or right before the
# e-mail ("9, ana@…"); not part of an amount ("R$ 1.000") — a bare number elsewhere doesn't count.
NUMBER = r"(?<![\d.,$])\b\d{1,6}\b(?![.,]\d)"
ORDER_NUMBER = (
    rf"(?i)\b(?:pedido|order|compra)\b\D{{0,12}}?{NUMBER}|{NUMBER}[\s,;:/-]{{1,4}}{EMAIL}"
)
ORDER_TOOLS = ("get_order_status", "check_return_eligibility")
ORDER_LOOKUP_ANSWERS = {
    "pt": "Desculpe, não consegui consultar o pedido agora. Pode me mandar de novo o número do "
    "pedido e o e-mail da compra?",
    "en": "Sorry, I couldn't look up the order just now. Could you send me the order number "
    "and the purchase e-mail again?",
    "es": "Disculpa, no pude consultar el pedido ahora. ¿Me envías de nuevo el número del "
    "pedido y el e-mail de la compra?",
}


def order_lookup_correction_note(_: set[str]) -> str:
    return (
        "## Correção obrigatória (consulta do pedido)\n"
        "A última mensagem do cliente já traz o número do pedido e o e-mail da compra. Não peça "
        "esses dados de novo: chame agora get_order_status com eles (ou "
        "check_return_eligibility, se for troca ou devolução) e responda com o resultado."
    )


# (opening, estimated delivery, tracking code, delayed → offer, closing, no order found)
ORDER_DATE_ANSWER = {
    "pt": (
        "Desculpe, me confundi com as datas. Seu pedido {id} foi feito em {date}; "
        "status: {status}.",
        " Previsão de entrega: {eta}.",
        " Código de rastreio: {code}.",
        " A entrega está atrasada: quer que eu registre o caso com a equipe?",
        " Posso ajudar em algo mais?",
        "Desculpe, me confundi com as datas. Quer que eu consulte o pedido de novo?",
    ),
    "en": (
        "Sorry, I mixed up the dates. Your order {id} was placed on {date}; status: {status}.",
        " Estimated delivery: {eta}.",
        " Tracking code: {code}.",
        " The delivery is late: would you like me to register the case with the team?",
        " Can I help with anything else?",
        "Sorry, I mixed up the dates. Would you like me to check the order again?",
    ),
    "es": (
        "Disculpa, me confundí con las fechas. Tu pedido {id} se hizo el {date}; estado: {status}.",
        " Entrega prevista: {eta}.",
        " Código de rastreo: {code}.",
        " La entrega está atrasada: ¿quieres que registre el caso con el equipo?",
        " ¿Te ayudo con algo más?",
        "Disculpa, me confundí con las fechas. ¿Quieres que consulte el pedido de nuevo?",
    ),
}


def order_date_correction_note(dates: set[str]) -> str:
    return (
        "## Correção obrigatória (data do pedido)\n"
        f"Sua resposta anterior apresentou {', '.join(sorted(dates))} como data de envio, "
        "entrega ou cancelamento. Essa é a data em que o pedido foi feito (order_date_text); os "
        "dados não têm data de envio, de entrega nem de cancelamento (estimated_delivery é só "
        "uma previsão). Responda de novo chamando-a de data do pedido."
    )


def safe_order_date_answer(history: Sequence[AnyMessage]) -> str:
    """The last verified order's facts, worded by code, in the customer's language."""
    language = customer_language(history)
    opening, eta_text, code_text, late_text, closing, fallback = ORDER_DATE_ANSWER[language]
    order = None
    for message in reversed(history):
        if isinstance(message, ToolMessage) and message.name == "get_order_status":
            try:
                data = json.loads(message.text)
            except ValueError:
                continue
            if isinstance(data, dict) and "order_id" in data:
                order = data
                break
    if order is None:
        return fallback
    status = ORDER_STATUS.get(language, {}).get(order["status"], order["status_text"])
    answer = opening.format(id=order["order_id"], date=order["order_date"], status=status)
    if order.get("estimated_delivery") and order["status"] in ("confirmed", "shipped"):
        answer += eta_text.format(eta=order["estimated_delivery"])
    if order.get("tracking_code"):
        answer += code_text.format(code=order["tracking_code"])
    return answer + (late_text if order.get("delayed") else closing)


def make_chat_model(name: str, settings: Settings, last: bool) -> BaseChatModel:
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=name,
            base_url=settings.ollama_base_url,
            num_ctx=settings.ollama_num_ctx,  # explicit: the default would cut the prompt
            temperature=settings.llm_temperature,
            # gpt-oss: "low"/"medium"/"high" → Ollama's `think`; the reasoning comes back apart
            # from the answer, in additional_kwargs["reasoning_content"] (never emitted).
            reasoning=settings.llm_reasoning_effort,
        )
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
    """Groq: the model + its rate-limit fallbacks (each has its own quota). Other providers
    (a local Ollama): the model only — fallbacks exist for Groq's free-tier quotas."""
    fallbacks = settings.llm_fallback_models if settings.llm_provider == "groq" else []
    names = [settings.llm_model, *fallbacks]
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


# Function words that tell the three supported languages apart (accents stripped). Owner's
# frontend test: with only the prompt rule, the 20b answered Spanish and English customers in
# Portuguese (the prompt, the tool outputs and the context are all Portuguese).
LANGUAGE_WORDS = {
    "pt": {"voce", "voces", "tem", "quero", "qual", "quais", "quanto", "um", "uma", "nao",
           "e", "meu", "minha", "pra", "com", "obrigado", "obrigada", "oi", "ola", "vendem",
           "onde", "quando", "preco", "sim", "isso", "esse", "essa", "tambem", "gostaria",
           "o", "do", "da", "dos", "das", "quem", "ontem"},
    "en": {"the", "and", "you", "your", "does", "have", "has", "about", "what", "which",
           "how", "is", "are", "can", "my", "i", "want", "any", "for", "with", "please", "hi",
           "hello", "thanks", "price", "much", "sell", "where", "when", "there", "show",
           "tell", "need", "would", "could", "like", "buy", "it", "of", "this", "that"},
    "es": {"tienen", "tiene", "tienes", "hola", "quiero", "cuanto", "cuesta", "cuales",
           "usted", "ustedes", "gracias", "puedo", "hay", "los", "las", "el", "y", "es", "son",
           "mi", "venden", "donde", "cuando", "precio", "necesito", "busco", "electricas",
           "electrica", "electrico", "tambien", "algun", "alguna", "puedes", "puede",
           "pueden", "tus", "sus", "mis", "quieres", "necesitas", "hacer", "ayuda", "una",
           "un", "del", "pero", "muy", "ahora", "soy", "eres"},
}  # fmt: skip
# Written in the target language: closer to what the model must produce than a Portuguese rule.
LANGUAGE_HINTS = {
    "en": "## Language\nThe customer's last message is in English. Write your whole answer in "
    "English, from the first word to the closing, lists included. Product names and prices "
    "stay as the tools return them.",
    "es": "## Idioma\nEl último mensaje del cliente está en español. Escribe toda la respuesta "
    "en español, de la primera palabra al cierre, listas incluidas. Los nombres de productos y "
    "los precios quedan como los devuelven las herramientas.",
}


def detect_language(text: str) -> str | None:
    """ "pt", "en" or "es" when one language clearly has more function words; else None
    (e.g. "Pedido 8, x@y.com"), which keeps the default rule (Portuguese)."""
    words = re.findall(r"[a-z]+", unidecode(text).lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in LANGUAGE_WORDS.items()}
    best, *rest = sorted(scores, key=scores.get, reverse=True)
    return best if scores[best] > 0 and all(scores[best] > scores[r] for r in rest) else None


def language_hint(messages: Sequence[AnyMessage]) -> str:
    """Hint for an English/Spanish last customer message ("" for Portuguese or unclear)."""
    return LANGUAGE_HINTS.get(customer_language(messages), "")


def context_provider(client: ClientConfig, clock: Clock):
    """Per-call context appended after the static prompt (date/time, open/closed and, for an
    English/Spanish customer message, a language hint)."""
    calendar = HolidayCalendar(client.store.holidays)

    def context(messages: Sequence[AnyMessage]) -> str:
        status = store_status(clock.now(), client.store, calendar)
        if status.is_open:
            state = f"Loja aberta agora (fecha às {status.closes_at:%H:%M})."
        else:
            reason = f" ({status.holiday})" if status.holiday else ""
            back = f" Abre {br_datetime(status.next_opening)}." if status.next_opening else ""
            state = f"Loja fechada agora{reason}.{back}"
        now = f"## Agora\n{br_datetime(status.now)} (horário de Campo Grande). {state}"
        return "\n\n".join(filter(None, [now, language_hint(messages)]))

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
        SqlStoreRepository(engine, client.catalog.category_synonyms),
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
        **provider_error_policy(settings),
    )


def provider_error_policy(settings: Settings) -> dict:
    """Groq-specific handling (429 + retry-after, 400 tool_use_failed, fallbacks) only for
    Groq. Ollama: no rate limits; an unparsable tool call gets one plain retry."""
    if settings.llm_provider == "ollama":
        return {"rate_limits": False, "invalid_tool_call": is_local_invalid_tool_call}
    return {"rate_limits": True, "invalid_tool_call": is_invalid_tool_call}


def with_llm(
    settings: Settings, provider: str | None, model: str | None, fallbacks: bool = True
) -> Settings:
    """Settings with a provider/model chosen by argument (CLI, evals); the .env is the default.
    `fallbacks=False` with an explicit model: run on that model only (evals)."""
    update: dict = {}
    if provider:
        update["llm_provider"] = provider
    if model:
        update["llm_model"] = model
        if not fallbacks:
            update["llm_fallback_models"] = []
    return settings.model_copy(update=update) if update else settings


DEFAULT_EVAL_NOW = "2026-03-25T10:30"  # demo date: store open, after the last order


def eval_agent_factory(
    settings: Settings,
    client: ClientConfig,
    retriever: Retriever,
    workdir: Path,
    llm_model: str | None = None,
    llm_provider: str | None = None,
) -> Callable[[str | None], object]:
    """Builds a fresh agent per eval case: its own store DB copy (handoffs don't leak between
    cases), in-memory conversation memory and a fixed clock. `llm_model` runs the evals on that
    model only (no fallbacks), e.g. openai/gpt-oss-20b to spare the 120b quota."""
    from langgraph.checkpoint.memory import InMemorySaver

    settings = with_llm(settings, llm_provider, llm_model, fallbacks=False)

    def factory(now: str | None) -> object:
        db = workdir / f"store-{uuid.uuid4().hex}.db"
        case_settings = settings.model_copy(update={"database_url": f"sqlite:///{db}"})
        clock = get_clock(datetime.fromisoformat(now or DEFAULT_EVAL_NOW))
        return build_store_agent(case_settings, client, clock, retriever, InMemorySaver())

    return factory
