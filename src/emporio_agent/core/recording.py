"""Recorded example conversations: scripted turns through the real agent, written as Markdown.

Each scenario runs in a fresh session (and, through the agent factory, a clean store database
and its own simulated clock). A recording is only returned when every turn got a real answer:
a turn that ends in the rate-limit message or in an error event stops it, so no partial
example is ever written.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from emporio_agent.core.agent import RATE_LIMITED_MESSAGE
from emporio_agent.core.events import stream_turn

HELD_BACK_CHARS = 160


@dataclass(frozen=True)
class Scenario:
    number: int
    slug: str
    title: str
    description: str
    now: str  # ISO store time, e.g. "2026-03-25T10:30"
    turns: tuple[str, ...]

    @property
    def filename(self) -> str:
        return f"{self.number:02d}_{self.slug}.md"


@dataclass
class Turn:
    customer: str
    answer: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # name, args, outcome
    guard: list[dict[str, Any]] = field(default_factory=list)  # output_guard notices
    models: list[str] = field(default_factory=list)
    input_tokens: int = 0


@dataclass
class Recording:
    scenario: Scenario
    turns: list[Turn]

    @property
    def models(self) -> list[str]:
        return sorted({m for t in self.turns for m in t.models})

    @property
    def input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)


class RecordingStopped(Exception):
    """A turn ended without a real answer (rate limit or error): nothing is written."""


def record(agent, scenario: Scenario, pace_seconds: float = 0) -> Recording:
    """Run the scenario's turns in one fresh session. Raises RecordingStopped."""
    session_id = f"example-{uuid.uuid4().hex}"
    turns: list[Turn] = []
    for i, text in enumerate(scenario.turns):
        if i and pace_seconds:
            time.sleep(pace_seconds)  # free-tier tokens per minute
        turn = Turn(customer=text)
        for event in stream_turn(agent, session_id, text):
            data = event.data
            if event.type == "tool_start":
                turn.tool_calls.append({"name": data["name"], "args": data["args"]})
            elif event.type == "tool_end":
                _record_outcome(turn, data)
            elif event.type == "notice" and data.get("kind") == "output_guard":
                turn.guard.append(data)
            elif event.type == "final":
                turn.answer = data["text"]
                turn.models = list(data["usage"].get("models") or [])
                turn.input_tokens = data["usage"]["input_tokens"]
            elif event.type == "error":
                raise RecordingStopped(f"turn {i + 1}: error event ({data.get('detail')})")
        if turn.answer == RATE_LIMITED_MESSAGE:
            raise RecordingStopped(f"turn {i + 1}: rate limited")
        turns.append(turn)
    return Recording(scenario, turns)


def _outcome(data: dict[str, Any]) -> str:
    """ "ok", "rejected" (invalid arguments) or "error: <code>" (the tool's own error JSON)."""
    if data.get("status") == "error":
        return "rejected"
    try:
        result = json.loads(data["result_preview"])
    except ValueError:
        return "ok"  # a long result is cut in the preview: not an error object
    return f"error: {result['error']}" if isinstance(result, dict) and "error" in result else "ok"


def _record_outcome(turn: Turn, data: dict[str, Any]) -> None:
    """Pair a result with the earliest unfinished call of that tool (calls run in order)."""
    for call in turn.tool_calls:
        if call["name"] == data["name"] and "outcome" not in call:
            call["outcome"] = _outcome(data)
            return


def _call_line(call: dict[str, Any]) -> str:
    line = f"- `{call['name']}` `{_args(call['args'])}`"
    outcome = call.get("outcome", "ok")
    if outcome == "rejected":
        return f"{line} → rejected (invalid arguments; the model called again)"
    return f"{line} → {outcome}" if outcome != "ok" else line


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())


def _args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, separators=(", ", ": "))


def render_markdown(
    recording: Recording, provider: str, simulated_date: str, recorded_on: str
) -> str:
    """The example file: scenario, simulated date, model, then each turn with the tools it
    called and any output-guard intervention."""
    s = recording.scenario
    lines = [
        f"# {s.number:02d} · {s.title}",
        "",
        s.description,
        "",
        f"- **Simulated date:** {simulated_date}",
        f"- **Model:** {provider} · {', '.join(recording.models) or 'unknown'}",
        f"- **Recorded:** {recorded_on} with `scripts/record_conversations.py` (real agent output,"
        " not edited)",
        f"- **Input tokens:** {recording.input_tokens:,} over {len(recording.turns)} turns",
        "",
    ]
    for i, turn in enumerate(recording.turns, 1):
        lines += [f"## Turn {i}", "", "**Cliente:**", "", _quote(turn.customer), "", "**Tom:**"]
        lines += ["", _quote(turn.answer), ""]
        if turn.tool_calls:
            lines.append("Tools called:")
            lines += [_call_line(c) for c in turn.tool_calls]
        else:
            lines.append("Tools called: none.")
        for notice in turn.guard:
            failures = "; ".join(f"{k}: {', '.join(v)}" for k, v in notice["failures"].items())
            lines.append(f"\nOutput guard ({notice['action']}): {failures}")
            if held := " ".join(notice.get("discarded_text", "").split()):
                cut = held if len(held) <= HELD_BACK_CHARS else f"{held[:HELD_BACK_CHARS]}…"
                lines.append(f'(held back, never shown to the customer: "{cut}")')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def record_all(
    scenarios: list[Scenario],
    agent_for: Callable[[str], Any],
    write: Callable[[Recording], None],
    pace_seconds: float = 0,
) -> list[Recording]:
    """Record each scenario with its own agent (clock, database) and write it when complete.
    Stops at the first RecordingStopped: finished scenarios stay written, the current one
    is not."""
    done = []
    for i, scenario in enumerate(scenarios):
        if i and pace_seconds:
            time.sleep(pace_seconds)
        recording = record(agent_for(scenario.now), scenario, pace_seconds)
        write(recording)
        done.append(recording)
    return done
