"""Test doubles for the agent: a scripted chat model and provider-style errors."""

from types import SimpleNamespace
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    """Answers with the next scripted message (or raises it) and records what it was sent."""

    script: list[Any]
    seen: list[list[Any]] = []
    bound: list[tuple[list[str], Any]] = []  # (tool names, tool_choice) per bind_tools call
    name_: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound.append(([t.name for t in tools], kwargs.get("tool_choice")))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return ChatResult(generations=[ChatGeneration(message=step)])


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str = "", retry_after: str | None = None):
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = SimpleNamespace(headers=headers)


def call(name: str, args: dict, call_id: str = "c1") -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])
