"""Output guards: deterministic checks on the final answer **before** it reaches the customer.

OutputGuard holds the final answer (an AI message without tool calls) and runs its checks.
If a check fails, the model is asked once more with a correction note; if a check still fails,
the answer is replaced with that check's safe answer. Invalid answers are never stored or
emitted (see PROGRESS.md: streaming).

- ProtocolCheck: protocol numbers must have been issued by the handoff tool in this session
  (models invented "Protocolo: 20231102-8745" without opening a case).
- PriceCheck: every money amount must appear in this turn's tool results or in what the
  customer wrote (a model invented basses "por R$ 2.399,00" after an empty search).
- PhraseCheck: forbidden phrases (e.g. robotic refusals that break the persona).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from unidecode import unidecode

from emporio_agent.core.events import notify

logger = logging.getLogger(__name__)


class Check(Protocol):
    name: str
    safe_answer: str

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        """What is wrong with the answer (empty = fine), given the whole conversation."""
        ...

    def correction(self, problems: set[str]) -> str: ...


def current_turn(history: Sequence[AnyMessage]) -> Sequence[AnyMessage]:
    """Messages from the last customer message on."""
    starts = [i for i, m in enumerate(history) if isinstance(m, HumanMessage)]
    return history[starts[-1] :] if starts else history


# --- protocols -----------------------------------------------------------------------------

# Date + number anywhere in the text, with or without a prefix: "EMP-20260325-0001",
# "20231102-8745". Phones ("3341-4444") and CEPs ("79202-333") don't have 8 leading digits.
DATED_CODE = re.compile(r"\b(?:[A-Za-z]{2,6}-)?\d{8}-\d{3,}\b")
# After the keyword, the first token with 4+ digits within a few words is the claimed code.
WORDS_AFTER_KEYWORD = 8
MIN_CODE_DIGITS = 4
TOKEN_STRIP = "*_`\"'“”‘’()[]{}:;,.!?#"


def issued_protocols(messages: Iterable[AnyMessage], tool_name: str) -> set[str]:
    """Protocols returned by the handoff tool in these messages."""
    issued = set()
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == tool_name:
            try:
                protocol = json.loads(message.text).get("protocol")
            except (ValueError, AttributeError):
                continue
            if protocol:
                issued.add(protocol.upper())
    return issued


def _looks_like_code(token: str) -> bool:
    """4+ digits and not a date/time/money ("24/02/2026", "10:30", "2.199,00")."""
    if any(sep in token for sep in "/:,") or token.lower().startswith("r$"):
        return False
    return sum(c.isdigit() for c in token) >= MIN_CODE_DIGITS


def claimed_protocols(text: str, keywords: Sequence[str]) -> set[str]:
    """Codes the text presents as protocols: a dated code anywhere, or the first code-like
    token right after a keyword ("protocolo ... 8745-XYZ")."""
    claimed = {m.group(0).upper() for m in DATED_CODE.finditer(text)}
    words = text.split()
    normalized = [unidecode(w).lower().strip(TOKEN_STRIP) for w in words]
    for i, word in enumerate(normalized):
        if not any(word.startswith(k) for k in keywords):
            continue
        for token in words[i + 1 : i + 1 + WORDS_AFTER_KEYWORD]:
            token = token.strip(TOKEN_STRIP)
            if _looks_like_code(token):
                claimed.add(token.upper())
                break
    return claimed


@dataclass
class ProtocolCheck:
    tool_name: str
    keywords: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: str
    name: str = "protocol"

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        keywords = [unidecode(k).lower() for k in self.keywords]
        return claimed_protocols(answer, keywords) - issued_protocols(history, self.tool_name)

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- money amounts -------------------------------------------------------------------------

# "R$ 2.199,00", "R$2199", "R$ 1.000", "R$ 183,25", "2.089,05 reais"
MONEY_IN_ANSWER = re.compile(r"R\$\s*(\d[\d.,]*\d|\d)|(\d[\d.,]*\d|\d)\s*reais\b", re.IGNORECASE)
NUMBER = re.compile(r"\d[\d.,]*\d|\d")
CENT = Decimal("0.01")


def parse_amount(text: str) -> Decimal | None:
    """pt-BR or plain number → Decimal: "2.199,00", "1.000", "2199.0", "183,25"."""
    text = text.strip(".,")
    if "," in text:  # pt-BR: dots are thousands, comma is the decimal separator
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):  # "1.000", "12.000"
        text = text.replace(".", "")
    try:
        return Decimal(text).quantize(CENT)
    except InvalidOperation:
        return None


def amounts_in(text: str) -> set[Decimal]:
    """Every number in a text (tool JSON, customer message): what the answer may cite."""
    return {a for n in NUMBER.findall(text) if (a := parse_amount(n)) is not None}


def cited_amounts(answer: str) -> dict[Decimal, str]:
    """Money amounts the answer cites, with the text as written."""
    cited = {}
    for match in MONEY_IN_ANSWER.finditer(answer):
        raw = match.group(1) or match.group(2)
        if (amount := parse_amount(raw)) is not None:
            cited[amount] = match.group(0)
    return cited


@dataclass
class PriceCheck:
    correction_note: Callable[[set[str]], str]
    safe_answer: str
    name: str = "price"

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        allowed: set[Decimal] = set()
        for message in current_turn(history):
            if isinstance(message, ToolMessage):
                allowed |= amounts_in(message.text)
        for message in history:  # amounts the customer wrote ("até R$ 1.000") may be repeated
            if isinstance(message, HumanMessage):
                allowed |= amounts_in(message.text)
        return {text for amount, text in cited_amounts(answer).items() if amount not in allowed}

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- forbidden phrases ---------------------------------------------------------------------


@dataclass
class PhraseCheck:
    """The answer must not contain any of these phrases (accent/case-insensitive)."""

    phrases: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: str
    name: str = "phrase"

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        text = " ".join(unidecode(answer).casefold().split())
        return {p for p in self.phrases if " ".join(unidecode(p).casefold().split()) in text}

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- middleware ----------------------------------------------------------------------------


def _final_answer(response: ModelResponse | AIMessage) -> AIMessage | None:
    """The AI message of a model response if it is a final answer (no tool calls)."""
    message = response if isinstance(response, AIMessage) else response.result[-1]
    if isinstance(message, AIMessage) and not message.tool_calls:
        return message
    return None


class OutputGuard(AgentMiddleware):
    def __init__(self, checks: Sequence[Check]) -> None:
        super().__init__()
        self.checks = list(checks)

    def failures(self, request: ModelRequest, answer: AIMessage) -> dict[str, set[str]]:
        # The full state, not the trimmed request: old turns lose their tool messages.
        history: list[Any] = [*request.state.get("messages", []), *request.messages]
        found = {c.name: c.problems(answer.text, history) for c in self.checks}
        return {name: problems for name, problems in found.items() if problems}

    def _corrected(
        self, request: ModelRequest, failures: dict[str, set[str]], discarded: AIMessage
    ) -> ModelRequest:
        logger.warning("answer failed output checks %s: asking again", failures)
        notify(  # the discarded answer's tokens were spent too: count them in the turn
            "output_guard",
            action="retry",
            failures={name: sorted(p) for name, p in failures.items()},
            discarded_usage=discarded.usage_metadata,
            model=discarded.response_metadata.get("model_name"),
        )
        notes = [c.correction(failures[c.name]) for c in self.checks if c.name in failures]
        system = request.system_message.text if request.system_message else ""
        return request.override(system_message=SystemMessage("\n\n".join([system, *notes])))

    def _final(self, request: ModelRequest, response: ModelResponse | AIMessage):
        answer = _final_answer(response)
        if answer is None or not (failures := self.failures(request, answer)):
            return response
        check = next(c for c in self.checks if c.name in failures)
        logger.warning("answer still failed %s after correction: safe answer", failures)
        notify(
            "output_guard",
            action="safe_answer",
            failures={name: sorted(p) for name, p in failures.items()},
            discarded_usage=answer.usage_metadata,
            model=answer.response_metadata.get("model_name"),
        )
        return AIMessage(check.safe_answer)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | AIMessage:
        response = handler(request)
        answer = _final_answer(response)
        if answer is None or not (failures := self.failures(request, answer)):
            return response
        return self._final(request, handler(self._corrected(request, failures, answer)))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | AIMessage:
        response = await handler(request)
        answer = _final_answer(response)
        if answer is None or not (failures := self.failures(request, answer)):
            return response
        return self._final(request, await handler(self._corrected(request, failures, answer)))
