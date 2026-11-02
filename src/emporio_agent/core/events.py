"""One event stream per turn, shared by the CLI (--debug), the API (SSE) and the JSONL tracer.

Events: user_message, tool_start, tool_end, retrieval, notice (middleware decisions: 429
waits/fallbacks, invalid tool call retries, protocol guard), token, final, error.
Only the final answer's `content` is ever emitted as text, never reasoning. The final answer
is emitted only after the output guards validated it (it is the state's last message), so
`token` events replay the validated text in small chunks rather than live LLM tokens.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

EventType = Literal[
    "user_message", "tool_start", "tool_end", "retrieval", "notice", "token", "final", "error"
]
# What a customer may receive. tool_*, retrieval and notice are for the demo panel and traces.
CUSTOMER_EVENTS = frozenset({"user_message", "token", "final", "error"})

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


def stream_turn(
    agent,
    session_id: str,
    text: str,
    retrieval_tools: Sequence[str] = ("search_policies",),
) -> Iterator[AgentEvent]:
    """Run one customer message through the agent and yield its events, in order."""

    def event(type_: EventType, **data: Any) -> AgentEvent:
        return AgentEvent(type=type_, session_id=session_id, data=data)

    yield event("user_message", text=text)
    config = {"configurable": {"thread_id": session_id}}
    started = time.monotonic()
    pending: dict[str, tuple[str, float]] = {}  # tool_call_id → (name, start)
    usage, answer = TurnUsage(), ""
    try:
        stream = agent.stream(
            {"messages": [HumanMessage(text)]}, config, stream_mode=["updates", "custom"]
        )
        for mode, chunk in stream:
            if mode == "custom":
                notice = dict(chunk)
                if "discarded_usage" in notice:
                    usage.add_usage(notice["discarded_usage"], notice.get("model"))
                yield event("notice", **notice)
                continue
            for update in chunk.values():
                messages = update.get("messages", []) if isinstance(update, dict) else []
                for message in messages:
                    if isinstance(message, AIMessage):
                        usage.add(message)
                        for call in message.tool_calls:
                            pending[call["id"]] = (call["name"], time.monotonic())
                            yield event("tool_start", name=call["name"], args=call["args"])
                        if not message.tool_calls:
                            answer = message.text  # content only, never reasoning
                    elif isinstance(message, ToolMessage):
                        name, start = pending.pop(message.tool_call_id, (message.name, None))
                        yield event(
                            "tool_end",
                            name=name,
                            result_preview=message.text[:RESULT_PREVIEW_CHARS],
                            duration_ms=_ms_since(start),
                            status=message.status,
                        )
                        if name in retrieval_tools and message.artifact:
                            yield event("retrieval", chunks=message.artifact)
                        elif message.artifact:
                            yield event(
                                "notice", kind="tool_artifact", name=name, artifact=message.artifact
                            )
    except Exception as exc:  # the customer gets a friendly message; the trace gets details
        logger.exception("turn failed for session %s", session_id)
        yield event("error", message=ERROR_MESSAGE, detail=f"{type(exc).__name__}: {exc}")
        return

    for piece in TOKEN_CHUNK.findall(answer):
        yield event("token", text=piece)
    yield event("final", text=answer, usage=asdict(usage), duration_ms=_ms_since(started))


def _ms_since(start: float | None) -> int | None:
    return None if start is None else round((time.monotonic() - start) * 1000)
