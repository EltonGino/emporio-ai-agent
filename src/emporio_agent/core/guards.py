"""Output guards: deterministic checks on the final answer **before** it reaches the customer.

OutputGuard holds the final answer (an AI message without tool calls) and runs its checks.
If a check fails, the model is asked once more with a correction note; if a check still fails,
the answer is replaced with that check's safe answer. Invalid answers are never stored or
emitted (see PROGRESS.md: streaming).

- ProtocolCheck: protocol numbers must have been issued by the handoff tool in this session
  (models invented "Protocolo: 20231102-8745" without opening a case).
- PriceCheck: every money amount must appear in this turn's tool results or in what the
  customer wrote (a model invented basses "por R$ 2.399,00" after an empty search).
- TopicToolCheck: an answer about a topic, with figures or rules, needs one of that topic's
  tools in this turn (a model said "no PIX o valor permanece o mesmo" without the payment tool).
- PhraseCheck: forbidden phrase patterns (e.g. robotic refusals that break the persona).
- FieldLabelCheck: a value a tool returned must not get a wrong label (an order date called
  "Enviado em" / "entregue em": the data has no shipping or delivery date).
- RequiredToolCheck: when the customer's message carries what a tool needs, the turn must call
  it ("E o pedido 9, <e-mail>?" got a request for the data already given).

Corrections must point to the right action (which tool to call), not only forbid: a check
that only forbids can push the model to an evasive or wrong answer. A check with
`forced_tools` goes further when none of them ran in the turn: the retry must call one of them
(`tool_choice="any"`, tools narrowed to that set), since a model that answered from memory
tends to do it again (live: "And about drums?" got invented prices twice, then the safe answer).
Providers that ignore `tool_choice` (Ollama) still get the narrowed tools and the note.
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
from emporio_agent.core.text import normalize_typography

logger = logging.getLogger(__name__)
DISCARDED_PREVIEW_CHARS = 300
SafeAnswer = str | Callable[[Sequence[AnyMessage]], str]


class Check(Protocol):
    name: str
    safe_answer: SafeAnswer

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


def _protocol_text(text: str) -> str:
    return re.sub(r"\s*-\s*", "-", normalize_typography(text))


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
                issued.add(_protocol_text(protocol).upper())
    return issued


def _looks_like_code(token: str) -> bool:
    """4+ digits and not a date/time/money ("24/02/2026", "10:30", "2.199,00")."""
    if any(sep in token for sep in "/:,") or token.lower().startswith("r$"):
        return False
    return sum(c.isdigit() for c in token) >= MIN_CODE_DIGITS


def claimed_protocols(text: str, keywords: Sequence[str]) -> set[str]:
    """Codes the text presents as protocols: a dated code anywhere, or the first code-like
    token right after a keyword ("protocolo ... 8745-XYZ")."""
    text = _protocol_text(text)
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
    safe_answer: SafeAnswer
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
    text = normalize_typography(text).strip(".,")
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
    return {
        a for n in NUMBER.findall(normalize_typography(text)) if (a := parse_amount(n)) is not None
    }


def cited_amounts(answer: str) -> dict[Decimal, str]:
    """Money amounts the answer cites, with typography normalized."""
    cited = {}
    for match in MONEY_IN_ANSWER.finditer(normalize_typography(answer)):
        raw = match.group(1) or match.group(2)
        if (amount := parse_amount(raw)) is not None:
            cited[amount] = match.group(0)
    return cited


@dataclass
class PriceCheck:
    correction_note: Callable[[set[str]], str]
    safe_answer: SafeAnswer
    name: str = "price"
    forced_tools: Sequence[str] = ()

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


# --- topics that need a tool ---------------------------------------------------------------


def _normalized(text: str) -> str:
    return unidecode(normalize_typography(text)).casefold()


def field_values(messages: Iterable[AnyMessage], fields: Sequence[str]) -> list[Any]:
    """Values of the given JSON keys (at any depth) in these tool results."""
    found: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in fields:
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for message in messages:
        if isinstance(message, ToolMessage):
            try:
                walk(json.loads(message.text))
            except ValueError:
                continue
    return found


def field_amounts(messages: Iterable[AnyMessage], fields: Sequence[str]) -> set[Decimal]:
    """Values of the given JSON keys (at any depth) in these tool results, as amounts."""
    amounts = (parse_amount(str(v)) for v in field_values(messages, fields))
    return {a for a in amounts if a is not None}


@dataclass
class TopicToolCheck:
    """If the answer talks about `topic_words` with details — money amounts (when
    `amounts_are_details`), `detail_words` or `detail_patterns` — one of `tools` must have run
    in this turn, or the answer must cite an amount a tool returned in one of
    `evidence_fields` (e.g. `pix_price` in a product search result)."""

    topic_words: Sequence[str]
    detail_words: Sequence[str]
    tools: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: SafeAnswer
    name: str = "topic"
    evidence_fields: Sequence[str] = ()
    detail_patterns: Sequence[str] = ()  # regexes over the normalized text
    amounts_are_details: bool = True  # False: a price elsewhere in the answer isn't a claim
    forced_tools: Sequence[str] = ()

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        text = _normalized(answer)
        if not any(_normalized(w) in text for w in self.topic_words):
            return set()
        cited = set(cited_amounts(answer))
        details = (
            (self.amounts_are_details and bool(cited))
            or any(_normalized(w) in text for w in self.detail_words)
            or any(re.search(p, text) for p in self.detail_patterns)
        )
        if not details:
            return set()  # mentions the topic without figures or rules (e.g. an offer)
        turn = current_turn(history)
        called = {m.name for m in turn if isinstance(m, ToolMessage)}
        if called & set(self.tools):
            return set()
        if self.evidence_fields and cited & field_amounts(turn, self.evidence_fields):
            return set()
        return {self.name}

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- forbidden phrases ---------------------------------------------------------------------


@dataclass
class PhraseCheck:
    """Forbidden regexes over accent/case/whitespace-normalized text."""

    patterns: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: SafeAnswer
    name: str = "phrase"

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        text = _normalized(answer)
        return {m.group(0) for pattern in self.patterns for m in re.finditer(pattern, text)}

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- messages that require a tool ----------------------------------------------------------


@dataclass
class RequiredToolCheck:
    """If the customer's last message matches every one of `message_patterns`, one of `tools`
    must have run in this turn; with `forced_tools`, the retry has to call one of them."""

    message_patterns: Sequence[str]
    tools: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: SafeAnswer
    name: str = "required_tool"
    forced_tools: Sequence[str] = ()

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        turn = current_turn(history)
        message = next((m for m in turn if isinstance(m, HumanMessage)), None)
        if message is None or not all(re.search(p, message.text) for p in self.message_patterns):
            return set()
        called = {m.name for m in turn if isinstance(m, ToolMessage)}
        return set() if called & set(self.tools) else {self.name}

    def correction(self, problems: set[str]) -> str:
        return self.correction_note(problems)


# --- labels of tool values ------------------------------------------------------------------

BR_DATE = re.compile(r"(\d{2}/\d{2})/(\d{4})")


def _value_pattern(value: str) -> str:
    """A dd/mm/yyyy date also matches its short dd/mm form ("Enviado em 15/02")."""
    if match := BR_DATE.fullmatch(value):
        return rf"(?<![\d/]){re.escape(match[1])}(?:/{match[2]})?(?![\d/])"
    return re.escape(_normalized(value))


def _last(patterns: Sequence[str], text: str) -> int:
    return max((m.end() for p in patterns for m in re.finditer(p, text)), default=-1)


@dataclass
class FieldLabelCheck:
    """Values of `fields` returned by `tool_name` in this conversation must not be presented
    with a wrong label: on the answer's line, the closest label within `window` characters
    before the value must not be one of `wrong_labels` (regexes over accent/case-normalized
    text) unless one of `right_labels` is closer."""

    tool_name: str
    fields: Sequence[str]
    wrong_labels: Sequence[str]
    right_labels: Sequence[str]
    correction_note: Callable[[set[str]], str]
    safe_answer: SafeAnswer
    name: str = "field_label"
    window: int = 30

    def problems(self, answer: str, history: Sequence[AnyMessage]) -> set[str]:
        results = [m for m in history if isinstance(m, ToolMessage) and m.name == self.tool_name]
        values = {str(v) for v in field_values(results, self.fields) if v}
        found = set()
        for line in answer.splitlines():
            text = _normalized(line)
            for value in values:
                for match in re.finditer(_value_pattern(value), text):
                    before = text[max(0, match.start() - self.window) : match.start()]
                    if _last(self.wrong_labels, before) > _last(self.right_labels, before):
                        found.add(value)
        return found

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
    def __init__(self, checks: Sequence[Check], forced_tool_note: str = "") -> None:
        super().__init__()
        self.checks = list(checks)
        self.forced_tool_note = forced_tool_note

    def failures(self, request: ModelRequest, answer: AIMessage) -> dict[str, set[str]]:
        # The full state, not the trimmed request: old turns lose their tool messages.
        history: list[Any] = [*request.state.get("messages", []), *request.messages]
        found = {c.name: c.problems(answer.text, history) for c in self.checks}
        return {name: problems for name, problems in found.items() if problems}

    def forced_tools(self, request: ModelRequest, failures: dict[str, set[str]]) -> list[Any]:
        """The tools the retry must choose from: those of the failed checks, if none of them
        ran in this turn (empty = an ordinary retry)."""
        names = {
            t for c in self.checks if c.name in failures for t in getattr(c, "forced_tools", ())
        }
        history = [*request.state.get("messages", []), *request.messages]
        called = {m.name for m in current_turn(history) if isinstance(m, ToolMessage)}
        if not names or called & names:
            return []
        return [t for t in request.tools if getattr(t, "name", None) in names]

    def _corrected(
        self, request: ModelRequest, failures: dict[str, set[str]], discarded: AIMessage
    ) -> ModelRequest:
        logger.warning("answer failed output checks %s: asking again", failures)
        forced = self.forced_tools(request, failures)
        notify(  # the discarded answer's tokens were spent too: count them in the turn
            "output_guard",
            action="retry",
            failures={name: sorted(p) for name, p in failures.items()},
            # Internal (demo panel / trace only): what the customer was kept from seeing.
            discarded_text=discarded.text[:DISCARDED_PREVIEW_CHARS],
            discarded_usage=discarded.usage_metadata,
            model=discarded.response_metadata.get("model_name"),
            forced_tools=[t.name for t in forced],
        )
        notes = [c.correction(failures[c.name]) for c in self.checks if c.name in failures]
        system = request.system_message.text if request.system_message else ""
        if not forced:
            return request.override(system_message=SystemMessage("\n\n".join([system, *notes])))
        notes.append(self.forced_tool_note)
        return request.override(
            system_message=SystemMessage("\n\n".join(filter(None, [system, *notes]))),
            tools=forced,
            tool_choice="any",  # ChatGroq sends "required"; ChatOllama ignores it
        )

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
            discarded_text=answer.text[:DISCARDED_PREVIEW_CHARS],
            discarded_usage=answer.usage_metadata,
            model=answer.response_metadata.get("model_name"),
        )
        history = [*request.state.get("messages", []), *request.messages]
        safe = check.safe_answer(history) if callable(check.safe_answer) else check.safe_answer
        return AIMessage(safe)

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
