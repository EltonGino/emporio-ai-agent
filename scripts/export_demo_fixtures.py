"""Export recorded conversations (JSONL traces) as anonymized fixtures for the frontend's
mock mode (VITE_MOCK=true), which replays them without a backend.

Usage: uv run python scripts/export_demo_fixtures.py
Reads data/runtime/traces/*.jsonl (gitignored), writes frontend/src/mocks/conversations.json.
Only turns listed in CONVERSATIONS are exported (answers recorded before later fixes, e.g.
invented products, are left out). Real names/e-mails from the test data are replaced by
fictional ones (REPLACEMENTS); token events are not in traces and are synthesized by the mock.
"""

import json

from emporio_agent.config import PROJECT_ROOT, TRACES_DIR

OUTPUT = PROJECT_ROOT / "frontend" / "src" / "mocks" / "conversations.json"
OWNER = "09e5bb60a2fa4fe1bb084f54da9ace64"  # owner's manual CLI test, 2026-03-25 10:30
SMOKE = "trace-smoke"  # step 13 live smoke test, 2026-02-20 10:00

# (id, title, simulated now, [(trace, turn number starting at 1), ...])
CONVERSATIONS = [
    ("contato-frete", "Contato e prazo de entrega", "2026-03-25T10:30", [(OWNER, 1), (OWNER, 2)]),
    (
        "violao-esgotado",
        "Violão esgotado e alternativas",
        "2026-03-25T10:30",
        [(OWNER, 3), (OWNER, 5)],
    ),
    ("devolucao", "Devolução por arrependimento", "2026-02-20T10:00", [(SMOKE, 2)]),
    (
        "seguranca",
        "Tentativas de acesso indevido",
        "2026-03-25T10:30",
        [(OWNER, 8), (OWNER, 9), (OWNER, 10)],
    ),
]
# Longest first, so full names are replaced before first names.
REPLACEMENTS = [
    ("Letícia Gonçalves Rocha", "Marina Teixeira"),
    ("Letícia Rocha", "Marina Teixeira"),
    ("leticia.rocha@jmail.com", "marina.teixeira@jmail.com"),
    ("Letícia", "Marina"),
    ("Leticia", "Marina"),
    ("Bruno Martins", "Carlos Nogueira"),
]
KEPT_EVENTS = {"tool_start", "tool_end", "retrieval", "notice", "final", "error"}


def turns_of(trace: str) -> list[dict]:
    """The trace split into turns: the user's text and the events that followed it."""
    turns: list[dict] = []
    for line in (TRACES_DIR / f"{trace}.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "user_message":
            turns.append({"user": event["data"]["text"], "events": []})
        elif turns and event["type"] in KEPT_EVENTS:
            data = dict(event["data"])
            data.pop("detail", None)  # error details never go to the browser
            turns[-1]["events"].append({"type": event["type"], "data": data})
    return turns


def anonymize(text: str) -> str:
    for real, fictional in REPLACEMENTS:
        text = text.replace(real, fictional)
    return text


def main() -> None:
    cache: dict[str, list[dict]] = {}
    conversations = []
    for conv_id, title, now, picks in CONVERSATIONS:
        turns = []
        for trace, number in picks:
            cache.setdefault(trace, turns_of(trace))
            turns.append(cache[trace][number - 1])
        conversations.append({"id": conv_id, "title": title, "now": now, "turns": turns})
    payload = {
        "note": "Real agent outputs recorded in JSONL traces, anonymized "
        "(scripts/export_demo_fixtures.py). Do not edit by hand.",
        "conversations": conversations,
    }
    text = anonymize(json.dumps(payload, ensure_ascii=False, indent=2))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    print(f"{len(conversations)} conversations → {OUTPUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
