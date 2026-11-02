"""One event stream per turn, shared by the CLI (--debug), the API (SSE) and the JSONL tracer.

Events: user_message, tool_start, tool_end, retrieval, notice (middleware decisions: 429
waits/fallbacks, invalid tool call retries, protocol guard), token, final, error,
products (public catalog cards).
Only the final answer's `content` is ever emitted as text, never reasoning. The final answer
is emitted only after the output guards validated it (it is the state's last message), so
`token` events replay the validated text in small chunks rather than live LLM tokens.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

EventType = Literal[
    "user_message",
    "tool_start",
    "tool_end",
    "retrieval",
    "notice",
    "token",
    "final",
    "error",
    "products",
]
# What a customer may receive. tool_*, retrieval and notice are for the demo panel and traces.
CUSTOMER_EVENTS = frozenset({"user_message", "token", "final", "error", "products"})

ERROR_MESSAGE = "Desculpe, tive um problema técnico agora. Pode tentar de novo em instantes?"
RESULT_PREVIEW_CHARS = 300
TOKEN_CHUNK = re.compile(r"\S+\s*|\s+")


class AgentEvent(BaseModel):
    type: EventType
    session_id: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = {}


def notify(kind: str, **data: Any) -> None:
    """Report a middleware decision as a `notice` event (no-op outside an agent run)."""
    with contextlib.suppress(RuntimeError):  # not inside a graph run (e.g. a unit test)
        get_stream_writer()({"kind": kind, **data})


@dataclass
class TurnUsage:
    """Token usage summed over the model calls of one turn (cached ⊂ input), including calls
    whose answer was discarded (e.g. by the protocol guard)."""

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    models: list[str] = field(default_factory=list)

    def add_usage(self, usage: dict[str, Any] | None, model: str | None) -> None:
        usage = usage or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cached_tokens += (usage.get("input_token_details") or {}).get("cache_read", 0)
        self.reasoning_tokens += (usage.get("output_token_details") or {}).get("reasoning", 0)
        self.model_calls += 1
        if model:
            self.models.append(model)

    def add(self, message: AIMessage) -> None:
        self.add_usage(message.usage_metadata, message.response_metadata.get("model_name"))


class _Turn:
    """Turns raw `agent.stream` chunks into AgentEvents (shared by the sync and async
    streams)."""

    def __init__(
        self,
        session_id: str,
        text: str,
        retrieval_tools: Sequence[str],
        context: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        self.context = context or {}
        self.text = text
        self.retrieval_tools = retrieval_tools
        self.started = time.monotonic()
        self.pending: dict[str, tuple[str, float]] = {}  # tool_call_id → (name, start)
        self.usage = TurnUsage()
        self.answer = ""
        self.tool_results: list[ToolMessage] = []

    def event(self, type_: EventType, **data: Any) -> AgentEvent:
        return AgentEvent(type=type_, session_id=self.session_id, data=data)

    @property
    def input(self) -> dict[str, Any]:
        return {"messages": [HumanMessage(self.text)]}

    @property
    def config(self) -> dict[str, Any]:
        return {"configurable": {**self.context, "thread_id": self.session_id}}

    def chunk(self, mode: str, chunk: Any) -> list[AgentEvent]:
        if mode == "custom":
            notice = dict(chunk)
            if "discarded_usage" in notice:
                self.usage.add_usage(notice["discarded_usage"], notice.get("model"))
            return [self.event("notice", **notice)]
        events = []
        for update in chunk.values():
            messages = update.get("messages", []) if isinstance(update, dict) else []
            for message in messages:
                if isinstance(message, AIMessage):
                    self.usage.add(message)
                    for call in message.tool_calls:
                        self.pending[call["id"]] = (call["name"], time.monotonic())
                        events.append(
                            self.event("tool_start", name=call["name"], args=call["args"])
                        )
                    if not message.tool_calls:
                        self.answer = message.text  # content only, never reasoning
                elif isinstance(message, ToolMessage):
                    events.extend(self._tool_end(message))
        return events

    def _tool_end(self, message: ToolMessage) -> list[AgentEvent]:
        self.tool_results.append(message)
        name, start = self.pending.pop(message.tool_call_id, (message.name, None))
        events = [
            self.event(
                "tool_end",
                name=name,
                result_preview=message.text[:RESULT_PREVIEW_CHARS],
                duration_ms=_ms_since(start),
                status=message.status,
            )
        ]
        if name in self.retrieval_tools and message.artifact:
            events.append(self.event("retrieval", chunks=message.artifact))
        elif message.artifact:
            events.append(
                self.event("notice", kind="tool_artifact", name=name, artifact=message.artifact)
            )
        return events

    def error(self, exc: Exception) -> AgentEvent:
        # The customer gets a friendly message; the trace gets the details.
        logger.exception("turn failed for session %s", self.session_id)
        return self.event("error", message=ERROR_MESSAGE, detail=f"{type(exc).__name__}: {exc}")

    def finish(self) -> list[AgentEvent]:
        tokens = [self.event("token", text=piece) for piece in TOKEN_CHUNK.findall(self.answer)]
        final = self.event(
            "final",
            text=self.answer,
            usage=asdict(self.usage),
            duration_ms=_ms_since(self.started),
        )
        return [*tokens, final]


STREAM_MODES = ["updates", "custom"]


def stream_turn(
    agent,
    session_id: str,
    text: str,
    retrieval_tools: Sequence[str] = ("search_policies",),
) -> Iterator[AgentEvent]:
    """Run one customer message through the agent and yield its events, in order."""
    turn = _Turn(session_id, text, retrieval_tools)
    yield turn.event("user_message", text=text)
    try:
        for mode, chunk in agent.stream(turn.input, turn.config, stream_mode=STREAM_MODES):
            yield from turn.chunk(mode, chunk)
    except Exception as exc:
        yield turn.error(exc)
        return
    yield from turn.finish()


async def astream_turn(
    agent,
    session_id: str,
    text: str,
    retrieval_tools: Sequence[str] = ("search_policies",),
    context: dict[str, Any] | None = None,
    product_projector: Callable[[str, list[ToolMessage]], list[dict[str, Any]]] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Async `stream_turn` (the API): async model calls and checkpointer; sync tools run in
    LangChain's worker threads, so the event loop is never blocked. `context` adds entries to
    the run config the tools read (e.g. the client key for the per-IP lockout)."""
    # The optional client-layer projector runs only after the graph/guards finish, using
    # complete tool messages from this invocation, never previews or checkpoint history.
    turn = _Turn(session_id, text, retrieval_tools, context)
    yield turn.event("user_message", text=text)
    try:
        async for mode, chunk in agent.astream(turn.input, turn.config, stream_mode=STREAM_MODES):
            for event in turn.chunk(mode, chunk):
                yield event
    except Exception as exc:
        yield turn.error(exc)
        return
    if product_projector and (products := product_projector(turn.answer, turn.tool_results)):
        yield turn.event("products", products=products)
    for event in turn.finish():
        yield event


def _ms_since(start: float | None) -> int | None:
    return None if start is None else round((time.monotonic() - start) * 1000)
