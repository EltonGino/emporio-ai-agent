"""JSONL trace per session: `<traces_dir>/<session_id>.jsonl`, one event per line.

Token events are not written (the final event has the whole text). Optional LangSmith tracing
needs no code: set LANGSMITH_TRACING / LANGSMITH_API_KEY in the environment.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path

from emporio_agent.core.events import AgentEvent

SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")  # also prevents path traversal
SKIPPED_EVENTS = frozenset({"token"})


def trace_path(traces_dir: Path, session_id: str) -> Path:
    if not SAFE_SESSION_ID.fullmatch(session_id):
        raise ValueError(f"invalid session id {session_id!r}")
    return traces_dir / f"{session_id}.jsonl"


class JsonlTracer:
    def __init__(self, traces_dir: Path) -> None:
        self.traces_dir = traces_dir
        self._lock = threading.Lock()

    def write(self, event: AgentEvent) -> None:
        if event.type in SKIPPED_EVENTS:
            return
        path = trace_path(self.traces_dir, event.session_id)
        line = event.model_dump_json() + "\n"
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line)

    def delete(self, session_id: str) -> bool:
        """LGPD deletion (step 17). Returns True if a trace existed."""
        path = trace_path(self.traces_dir, session_id)
        with self._lock:
            existed = path.exists()
            path.unlink(missing_ok=True)
        return existed


def traced(events: Iterable[AgentEvent], tracer: JsonlTracer | None) -> Iterator[AgentEvent]:
    """Pass events through, writing each one to the trace first."""
    for event in events:
        if tracer is not None:
            tracer.write(event)
        yield event
