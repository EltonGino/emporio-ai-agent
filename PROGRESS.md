# PROGRESS.md — build log

Purpose: let any coding agent (Claude Code, Codex) or the owner resume work without context.
Update this file in the same commit as every plan step (see AGENTS.md §0 and §16).

## How to resume

1. Read `AGENTS.md` fully.
2. Read "Next step" and the latest log entry below.
3. Run `uv sync && uv run pytest -m "not llm"` to confirm the current state is green.
4. Continue with the next step; update this file before committing.

## Next step

Step 2 — `feat(config): client.yaml, settings and injectable clock` (AGENTS.md §4, §16).
Resolve the `LLM_MODEL` and `EMBEDDING_MODEL` [VERIFY] items there and fill them in `.env.example`.

## Plan status

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Project scaffold | done | `chore: project scaffold` |
| 2 | Config, client.yaml, clock | pending | |
| 3 | SQLite models + CSV loader with cleaning | pending | |
| 4 | Data profiling script | pending | |
| 5 | PDF ingestion + structure-aware chunking | pending | |
| 6 | BM25, dense and hybrid retrievers | pending | |
| 7 | Retrieval golden set + eval script | pending | |
| 8 | Catalog and promotion tools | pending | |
| 9 | Payment rules | pending | |
| 10 | Order status + return eligibility | pending | |
| 11 | Store status, policy search, handoff | pending | |
| 12 | Persona prompt + agent with memory | pending | |
| 13 | Event stream + JSONL tracing | pending | |
| 14 | CLI with debug mode | pending | |
| 15 | Routing + adversarial evals | pending | |
| 16 | CI workflow | pending | |
| 17 | FastAPI (SSE, sessions, rate limit, retention, deletion) | pending | |
| 18 | Frontend scaffold | pending | |
| 19 | Chat UI with streaming | pending | |
| 20 | Bastidores panel + simulated date banner | pending | |
| 21 | Serve frontend from API, Docker | pending | |
| 22 | Recorded example conversations | pending | |
| 23 | README | pending | |

## Verified facts ([VERIFY] items resolved)

<!-- e.g. "LLM_MODEL = <id> — chosen because ..., checked on <date>" -->

- Tooling (2026-11-01): uv 0.13.6 (Homebrew, installed with owner approval), Python 3.12
  (system framework install, found by uv), ruff 0.17.3, pytest 9.2.0. Build backend
  `uv_build>=0.13.6,<0.14.0` (what `uv init --package` generates for this uv version).

## Decisions and deviations during implementation

<!-- Anything not in AGENTS.md, or changed from it, with the reason. -->

- **Step 1** — The PDF arrived as `políticas_da_loja.pdf` (accented). Renamed to
  `politicas_da_loja.pdf` to match AGENTS.md §1 and `retrieval.documents`; content untouched.
  The CSVs were renamed from `desafio_tecnico_ai_eng - <name>.csv` to `<name>.csv`.
- **Step 1** — Project name `emporio-ai-agent`, import package `emporio_agent`
  (`[tool.uv.build-backend] module-name`). `requires-python = ">=3.12,<3.13"` + `.python-version`.
- **Step 1** — The CSVs use CRLF and the global git config has `core.autocrlf=input`, which
  would rewrite them to LF on commit. `.gitattributes` marks `data/raw/** -text` so the inputs
  are stored byte-for-byte. Loaders must therefore tolerate CRLF (the `csv` module/pandas do).
- **Step 1** — Runtime dependencies are added in the step that first needs them (none yet).
- **Step 1** — `.env.example` keeps comments on their own lines instead of inline after values,
  so python-dotenv and Docker Compose `env_file` parse it the same way.
- **Step 1** — Ruff: line length 100, rules `E,F,I,UP,B,SIM`; `frontend/` and `data/` excluded.
- **Convention** — The "Commit" column holds the commit subject; hashes are in `git log`
  (a commit cannot contain its own hash).

## Known issues / TODO

<!-- Open bugs, flaky tests, things to revisit. -->

- `LLM_MODEL` and `EMBEDDING_MODEL` are still empty in `.env.example` ([VERIFY], step 2).

## Log

<!--
### YYYY-MM-DD HH:MM — Step N: <title>
- Done: ...
- Files: ...
- Tests: ...
- Notes: ...
-->

### 2026-11-01 18:10 — Step 1: Project scaffold
- Done: uv project (src layout), ruff + pytest config with `llm` marker, `.gitignore`,
  `.env.example`, `.python-version`, `.gitattributes`, `uv.lock`; raw inputs renamed in `data/raw/`.
- Files: `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.gitattributes`, `.env.example`,
  `src/emporio_agent/__init__.py`, `tests/test_scaffold.py`, `data/raw/*`, `AGENTS.md`,
  `CLAUDE.md`, `PROGRESS.md`.
- Tests: `ruff check` ok, `ruff format --check` ok, `pytest -m "not llm"` 2 passed
  (package imports; all 7 raw inputs present).
- Notes: raw input sha256 — categories `7a7901e1…`, customers `1cd724dc…`, order_items
  `7656eb68…`, orders `deb9c248…`, products `78d911d9…`, promotions `cbe08c7e…`,
  politicas_da_loja.pdf `b7527f96…`.
