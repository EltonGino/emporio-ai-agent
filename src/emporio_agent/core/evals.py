"""End-to-end evals: scripted conversations run through the real agent, with checks on which
tools were called and what the final answer says.

Case format (evals/routing_cases.yaml, evals/adversarial_cases.yaml):

    - id: defect_policy
      now: "2026-03-25T10:30"      # optional simulated store time
      turns: ["Posso trocar um produto com defeito? Qual o prazo?"]
      expect:                      # tool checks: whole conversation; answer checks: last answer
        tools_any: [search_policies]
        ...

Text checks are accent/case-insensitive and ignore typographic hyphens/spaces ("e‑mail").
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from unidecode import unidecode

from emporio_agent.core.events import stream_turn

EXPECTATIONS = {
    "tools_any",  # at least one of these tools was called
    "tools_all",  # every one of these tools was called
    "forbidden_tools",  # none of these was called
    "first_tool_in",  # the first tool call is one of these
    "no_tools",  # true: no tool call at all
    "answer_contains",  # the last answer contains every string
    "answer_contains_any",  # the last answer contains at least one string
    "answer_not_contains",  # the last answer contains none (checked on every answer)
    "answer_not_matches",  # regexes that must not match any answer
    "no_emoji",  # true: the last answer has no emoji
    "no_guard_failures",  # true: the output guard never had to correct an answer
}
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")


def normalize(text: str) -> str:
    return " ".join(unidecode(text).casefold().split())


@dataclass(frozen=True)
class EvalCase:
    id: str
    turns: list[str]
    expect: dict[str, Any]
    now: str | None = None
    note: str = ""


def load_cases(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for item in raw["cases"]:
        unknown = set(item.get("expect", {})) - EXPECTATIONS
        if unknown:
            raise ValueError(f"{path.name}:{item['id']}: unknown expectations {unknown}")
        cases.append(
            EvalCase(
                id=item["id"],
                turns=list(item["turns"]),
                expect=item.get("expect", {}),
                now=item.get("now"),
                note=item.get("note", ""),
            )
        )
    return cases


@dataclass
class Transcript:
    answers: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0

    @property
    def tools(self) -> list[str]:
        return [c["name"] for c in self.tool_calls]


def run_conversation(
    agent, turns: Iterable[str], pace_seconds: float = 0, session_id: str | None = None
) -> Transcript:
    """Run the turns in one fresh session and collect what the evaluation needs."""
    session_id = session_id or f"eval-{uuid.uuid4().hex}"
    transcript = Transcript()
    for i, text in enumerate(turns):
        if i and pace_seconds:
            time.sleep(pace_seconds)  # free-tier TPM: let the minute window move
        for event in stream_turn(agent, session_id, text):
            data = event.data
            if event.type == "tool_start":
                transcript.tool_calls.append({"name": data["name"], "args": data["args"]})
            elif event.type == "notice":
                transcript.notices.append(data)
            elif event.type == "final":
                transcript.answers.append(data["text"])
                transcript.input_tokens += data["usage"]["input_tokens"]
            elif event.type == "error":
                transcript.answers.append(data["message"])
                transcript.errors.append(data["detail"])
    return transcript


def check(expect: dict[str, Any], transcript: Transcript) -> list[str]:
    """Failed expectations, as readable messages (empty = the case passed)."""
    failures: list[str] = []
    tools = transcript.tools
    last = normalize(transcript.answers[-1]) if transcript.answers else ""
    every = [normalize(a) for a in transcript.answers]

    if transcript.errors:
        failures.append(f"turn error: {transcript.errors}")
    if (wanted := expect.get("tools_any")) and not set(wanted) & set(tools):
        failures.append(f"expected one of {wanted}, called {tools}")
    if missing := [t for t in expect.get("tools_all", []) if t not in tools]:
        failures.append(f"expected all of {missing}, called {tools}")
    if called := [t for t in expect.get("forbidden_tools", []) if t in tools]:
        failures.append(f"forbidden tools called: {called}")
    if (first := expect.get("first_tool_in")) and (not tools or tools[0] not in first):
        failures.append(f"first tool should be one of {first}, got {tools[:1]}")
    if expect.get("no_tools") and tools:
        failures.append(f"expected no tool calls, called {tools}")
    for text in expect.get("answer_contains", []):
        if normalize(text) not in last:
            failures.append(f"answer lacks {text!r}")
    if (options := expect.get("answer_contains_any")) and not any(
        normalize(t) in last for t in options
    ):
        failures.append(f"answer lacks all of {options}")
    for text in expect.get("answer_not_contains", []):
        if any(normalize(text) in answer for answer in every):
            failures.append(f"an answer contains {text!r}")
    for pattern in expect.get("answer_not_matches", []):
        if any(re.search(pattern, a) for a in transcript.answers):
            failures.append(f"an answer matches {pattern!r}")
    if expect.get("no_emoji") and transcript.answers and EMOJI.search(transcript.answers[-1]):
        failures.append("last answer has an emoji")
    if expect.get("no_guard_failures"):
        guard = [n for n in transcript.notices if n.get("kind") == "output_guard"]
        if guard:
            failures.append(f"output guard had to act: {[n.get('failures') for n in guard]}")
    return failures


@dataclass
class CaseResult:
    case: EvalCase
    transcript: Transcript
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def run_case(
    case: EvalCase, agent_factory: Callable[[str | None], Any], pace_seconds: float = 0
) -> CaseResult:
    """`agent_factory(now)` builds a fresh agent (fresh memory) at the case's store time."""
    transcript = run_conversation(agent_factory(case.now), case.turns, pace_seconds)
    return CaseResult(case, transcript, check(case.expect, transcript))
