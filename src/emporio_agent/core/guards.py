"""Output guards: deterministic checks on the final answer **before** it reaches the customer.

ProtocolGuard: an answer may only present protocol numbers that the handoff tool really issued
in this session. Models were seen inventing one ("Protocolo: 20231102-8745") without opening a
case. The guard holds the final answer, validates it and, if it cites an unknown protocol,
asks the model once more with a correction note; if that still fails, it replaces the answer
with a safe one. Answers are emitted only after this check (see PROGRESS.md: streaming).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from unidecode import unidecode

logger = logging.getLogger(__name__)

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


def _final_answer(response: ModelResponse | AIMessage) -> AIMessage | None:
    """The AI message of a model response if it is a final answer (no tool calls)."""
    message = response if isinstance(response, AIMessage) else response.result[-1]
    if isinstance(message, AIMessage) and not message.tool_calls:
        return message
    return None


class ProtocolGuard(AgentMiddleware):
    def __init__(
        self,
        tool_name: str,
        keywords: Sequence[str],
        correction_note: Callable[[set[str]], str],
        safe_answer: str,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.keywords = [unidecode(k).lower() for k in keywords]
        self.correction_note = correction_note
        self.safe_answer = safe_answer

    def invalid_protocols(self, request: ModelRequest, answer: AIMessage) -> set[str]:
        # The full state, not the trimmed request: old turns lose their tool messages.
        history: list[Any] = [*request.state.get("messages", []), *request.messages]
        issued = issued_protocols(history, self.tool_name)
        return claimed_protocols(answer.text, self.keywords) - issued

    def _corrected(self, request: ModelRequest, invalid: set[str]) -> ModelRequest:
        logger.warning("answer cited protocol(s) never issued: %s; asking again", invalid)
        system = request.system_message.text if request.system_message else ""
        note = self.correction_note(invalid)
        return request.override(system_message=SystemMessage(f"{system}\n\n{note}"))

    def _safe(self, request: ModelRequest, response: ModelResponse | AIMessage):
        answer = _final_answer(response)
        if answer is None or not self.invalid_protocols(request, answer):
            return response
        logger.warning("protocol still invalid after correction: safe answer")
        return AIMessage(self.safe_answer)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | AIMessage:
        response = handler(request)
        answer = _final_answer(response)
        if answer is None or not (invalid := self.invalid_protocols(request, answer)):
            return response
        return self._safe(request, handler(self._corrected(request, invalid)))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | AIMessage:
        response = await handler(request)
        answer = _final_answer(response)
        if answer is None or not (invalid := self.invalid_protocols(request, answer)):
            return response
        return self._safe(request, await handler(self._corrected(request, invalid)))
