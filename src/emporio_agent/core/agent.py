"""Tool-calling agent (LangChain v1 `create_agent`) with memory and three middlewares:

- ProviderFallback: two recoverable provider errors. HTTP 429: if the provider asks to wait a
  few seconds (retry-after ≤ RATE_LIMIT_MAX_WAIT_SECONDS, default 15 s), wait and retry the
  same model once; otherwise retry the call on the next model in the chain (each Groq model
  has its own quota). HTTP 400 `tool_use_failed` (the provider rejected a tool call that
  didn't match its schema): retry the same model once, then the next one. Any other error
  propagates.
- DynamicContext: appends the current context (date, store open/closed) **after** the static
  system prompt, so the static prompt + tools stay a byte-identical, cacheable prefix.
- TrimHistory: sends the model only the recent history (by messages and tokens). The full
  history stays in the checkpointer.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from emporio_agent.core.events import TurnUsage, notify, stream_turn
from emporio_agent.core.tracing import JsonlTracer, traced

logger = logging.getLogger(__name__)

MAX_RETRY_WAIT_SECONDS = 15.0  # default; waiting longer is worse than a smaller model
RETRY_MARGIN_SECONDS = 0.5
# Groq's 429 message: "... Please try again in 7.66s" / "in 1m2.5s" / "in 450ms".
RETRY_IN = re.compile(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)(ms|s)")

RATE_LIMITED_MESSAGE = (
    "Desculpe, estou com muitas conversas agora e não consegui responder. "
    "Pode tentar de novo em alguns instantes? 🙏"
)
FAILED_MESSAGE = "Desculpe, não consegui processar sua mensagem. Pode escrever de outro jeito?"


def is_rate_limit(error: Exception) -> bool:
    """HTTP 429 from any provider SDK (groq.RateLimitError has status_code 429)."""
    return getattr(error, "status_code", None) == 429


def is_invalid_tool_call(error: Exception) -> bool:
    """HTTP 400 when the provider validates a generated tool call against its schema
    (Groq: code "tool_use_failed"). The generation is random: a retry usually works."""
    return getattr(error, "status_code", None) == 400 and "tool_use_failed" in str(error)


def retry_after(error: Exception) -> float | None:
    """Seconds the provider asks to wait: the retry-after header, else Groq's message."""
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        return float(headers["retry-after"])
    except (KeyError, TypeError, ValueError):
        pass
    if match := RETRY_IN.search(str(error)):
        minutes, amount, unit = match.groups()
        seconds = float(amount) / (1000 if unit == "ms" else 1)
        return seconds + 60 * int(minutes or 0)
    return None


def _model_name(request: ModelRequest) -> str:
    return getattr(request.model, "model_name", None) or type(request.model).__name__


class ProviderFallback(AgentMiddleware):
    def __init__(
        self,
        fallbacks: Sequence[BaseChatModel],
        max_wait: float = MAX_RETRY_WAIT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__()
        self.fallbacks = list(fallbacks)
        self.max_wait = max_wait
        self.sleep = sleep

    def _requests(self, request: ModelRequest) -> list[ModelRequest]:
        return [request, *(request.override(model=m) for m in self.fallbacks)]

    def _wait_before_retry(self, error: Exception, request: ModelRequest, retried: bool):
        """Seconds to wait before retrying the same model, or None to move on."""
        if is_invalid_tool_call(error):
            if retried:
                logger.warning("invalid tool call on %s: next model", _model_name(request))
                notify("invalid_tool_call", model=_model_name(request), action="next_model")
                return None
            logger.info("invalid tool call on %s: retrying", _model_name(request))
            notify("invalid_tool_call", model=_model_name(request), action="retry")
            return 0.0
        if not is_rate_limit(error):
            raise error
        wait = retry_after(error)
        if retried or wait is None or wait > self.max_wait:
            logger.warning("429 on %s (retry-after %s): next model", _model_name(request), wait)
            notify("rate_limit", model=_model_name(request), retry_after=wait, action="next_model")
            return None
        logger.info("429 on %s: waiting %.1fs and retrying", _model_name(request), wait)
        notify("rate_limit", model=_model_name(request), retry_after=wait, action="wait")
        return wait + RETRY_MARGIN_SECONDS

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse | AIMessage:
        last_error: Exception | None = None
        for attempt in self._requests(request):
            for retried in (False, True):
                try:
                    return handler(attempt)
                except Exception as error:
                    last_error = error
                    wait = self._wait_before_retry(error, attempt, retried)
                    if wait is None:
                        break
                    if wait:
                        self.sleep(wait)
        return _give_up(last_error)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | AIMessage:
        last_error: Exception | None = None
        for attempt in self._requests(request):
            for retried in (False, True):
                try:
                    return await handler(attempt)
                except Exception as error:
                    last_error = error
                    wait = self._wait_before_retry(error, attempt, retried)
                    if wait is None:
                        break
                    if wait:
                        await asyncio.sleep(wait)
        return _give_up(last_error)


def _give_up(error: Exception | None) -> AIMessage:
    """Every model failed with a recoverable error: answer politely instead of crashing."""
    if error is not None and is_rate_limit(error):
        return AIMessage(RATE_LIMITED_MESSAGE)
    return AIMessage(FAILED_MESSAGE)


class DynamicContext(AgentMiddleware):
    def __init__(self, context: Callable[[], str]) -> None:
        super().__init__()
        self.context = context

    def _with_context(self, request: ModelRequest) -> ModelRequest:
        static = request.system_message.text if request.system_message else ""
        return request.override(system_message=SystemMessage(f"{static}\n\n{self.context()}"))

    def wrap_model_call(self, request, handler):
        return handler(self._with_context(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._with_context(request))


def _turns(messages: Sequence[AnyMessage]) -> list[list[AnyMessage]]:
    """Split a history into turns, each starting with a human message."""
    turns: list[list[AnyMessage]] = []
    for message in messages:
        if isinstance(message, HumanMessage) or not turns:
            turns.append([])
        turns[-1].append(message)
    return turns


def _compact(turn: list[AnyMessage]) -> list[AnyMessage]:
    """A past turn without its tool calls and results: what was asked and answered. Facts must
    be looked up again anyway (prices, orders), and tool payloads are most of the tokens."""
    return [m for m in turn if isinstance(m, HumanMessage) or (_is_answer(m))]


def _is_answer(message: AnyMessage) -> bool:
    return isinstance(message, AIMessage) and not message.tool_calls


def trim_history(
    messages: Sequence[AnyMessage], max_messages: int, max_tokens: int
) -> list[AnyMessage]:
    """The current turn in full, plus as many whole past turns (compacted) as fit both limits.
    Whole turns are kept or dropped, so a tool call is never separated from its result."""
    turns = _turns(messages)
    if not turns:
        return []
    kept = turns[-1]
    budget = max_tokens - count_tokens_approximately(kept)
    for turn in reversed(turns[:-1]):
        past = _compact(turn)
        cost = count_tokens_approximately(past)
        if cost > budget or len(kept) + len(past) > max_messages:
            break
        kept = past + kept
        budget -= cost
    return kept


class TrimHistory(AgentMiddleware):
    def __init__(self, max_messages: int, max_tokens: int) -> None:
        super().__init__()
        self.max_messages = max_messages
        self.max_tokens = max_tokens

    def _trimmed(self, request: ModelRequest) -> ModelRequest:
        return request.override(
            messages=trim_history(request.messages, self.max_messages, self.max_tokens)
        )

    def wrap_model_call(self, request, handler):
        return handler(self._trimmed(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._trimmed(request))


CHECKPOINT_PRAGMAS = ("PRAGMA journal_mode = WAL", "PRAGMA busy_timeout = 5000")


def sqlite_checkpointer(path: Path) -> SqliteSaver:
    """Sync checkpointer (CLI). The API uses the async one (`api.main`), same pragmas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    for pragma in CHECKPOINT_PRAGMAS:
        conn.execute(pragma)
    return SqliteSaver(conn)


def build_agent(
    models: Sequence[BaseChatModel],
    tools: Sequence[BaseTool],
    system_prompt: str,
    context: Callable[[], str],
    checkpointer: BaseCheckpointSaver,
    history_max_messages: int,
    history_max_tokens: int,
    rate_limit_max_wait: float = MAX_RETRY_WAIT_SECONDS,
    rate_limit_sleep: Callable[[float], None] = time.sleep,
    guards: Sequence[AgentMiddleware] = (),
):
    """`models[0]` answers; the others are rate-limit fallbacks, in order. `guards` (e.g.
    ProtocolGuard) wrap everything else, so they check the answer whichever model wrote it."""
    return create_agent(
        models[0],
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=[
            *guards,
            ProviderFallback(models[1:], rate_limit_max_wait, rate_limit_sleep),
            DynamicContext(context),
            TrimHistory(history_max_messages, history_max_tokens),
        ],
        checkpointer=checkpointer,
    )


def run_turn(agent, session_id: str, text: str, tracer: JsonlTracer | None = None) -> TurnResult:
    """Send one customer message and return the answer (a consumer of `stream_turn`).
    `session_id` is the LangGraph thread id: memory and the tools' per-session checks
    (identity lockout, handoff idempotency) depend on it."""
    result = TurnResult("", TurnUsage(), [])
    for event in traced(stream_turn(agent, session_id, text), tracer):
        if event.type == "tool_start":
            result.tool_calls.append({"name": event.data["name"], "args": event.data["args"]})
        elif event.type == "final":
            result.text = event.data["text"]
            result.usage = TurnUsage(**event.data["usage"])
        elif event.type == "error":
            result.text = event.data["message"]
    return result


@dataclass
class TurnResult:
    text: str  # what the customer sees: the final message's text, never reasoning
    usage: TurnUsage
    tool_calls: list[dict[str, Any]]
