# PROGRESS.md — build log

Purpose: let any coding agent (Claude Code, Codex) or the owner resume work without context.
Update this file in the same commit as every plan step (see AGENTS.md §0 and §16).

## How to resume

1. Read `AGENTS.md` fully.
2. Read "Next step" and the latest log entry below.
3. Run `uv sync && uv run pytest -m "not llm"` to confirm the current state is green.
4. Continue with the next step; update this file before committing.

## Next step

Step 3 — `feat(data): SQLite models and CSV loader with cleaning rules` (AGENTS.md §5.1, §5.3).
Use `get_settings().database_url` and `PROJECT_ROOT / "data/raw"`; CSVs are CRLF (see step 1).

## Plan status

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Project scaffold | done | `chore: project scaffold` (de38bce) |
| 2 | Config, client.yaml, clock | done | `feat(config): client.yaml, settings and injectable clock` |
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
- **LLM_MODEL = `openai/gpt-oss-120b`** (checked 2026-11-01 on console.groq.com/docs/models and
  /docs/tool-use). Groq production models: `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`,
  `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (+ whisper). Everything else (qwen3.8-27b,
  minimax-m2.7, muse-spark-open, …) is *preview* ("evaluation only, may be discontinued") →
  excluded. Groq says all models support tool use and recommends the latest ones.
  gpt-oss-120b is the newest and largest production model, built for agentic tool use, and
  multilingual. `llama-3.3-70b-versatile` is the fallback (older; supports parallel tool calls,
  gpt-oss does not — not needed here). **Open risk:** the rate-limit page lists gpt-oss-120b at
  30 RPM / 8K TPM / 200K TPD, labelled as the Developer-plan base table, and does not list the
  Llama models; the real free-tier numbers must be read on the owner's account
  (console.groq.com/settings/limits). 8K TPM may be tight for multi-tool turns → measure in
  step 12, rely on §8 retries/backoff, switch model via `LLM_MODEL` if needed.
- **EMBEDDING_MODEL = `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`** (checked
  2026-11-01 with `TextEmbedding.list_supported_models()`, fastembed 0.9.1). Multilingual
  candidates: paraphrase-multilingual-MiniLM-L12-v2 (0.22 GB, but **truncates at 128 tokens** —
  too short for a full subsection chunk); paraphrase-multilingual-mpnet-base-v2 (1.0 GB,
  512 tokens, Apache-2.0, **no prefixes**) ← chosen; multilingual-e5-large (2.24 GB, needs
  prefixes); embeddinggemma-300m (1.24 GB, Gemma license, prefixes); Qwen3-Embedding-0.6B-Q
  (1.12 GB, instruction prefix for queries, needs onnxruntime>=1.23);
  potion-multilingual-128M (0.5 GB static embeddings, lower quality). **Prefix [VERIFY]:**
  the chosen model needs no `query:`/`passage:` prefixes (fastembed's model description says
  "not necessary"). Step 7's eval will show whether dense retrieval is good enough.
- pydantic-settings 2.16.2: `SettingsConfigDict(env_ignore_empty=True)` exists → empty values in
  `.env` (e.g. `AGENT_NOW=`) mean "use the default".
- Store timezone `America/Campo_Grande` = UTC-04:00 (no DST since 2019), checked with zoneinfo
  + the `tzdata` package (added so slim Docker images have the tz database).
- Manual §2 hours confirmed from the PDF: Mon–Fri 09:00–18:00, Sat 09:00–13:00, Sun and
  holidays closed.

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
- **Step 2** — `client.yaml`: AGENTS.md §4 lists `"venda final" em liquidação` unquoted inside a
  flow list, which is invalid YAML; it is quoted as a whole (same text).
- **Step 2** — `get_settings()` loads `.env` into `os.environ` (python-dotenv, real env vars win)
  and then reads settings from the environment. One mechanism, and SDKs that read their own
  variables (`GROQ_API_KEY`, `LANGSMITH_*`) see them, so Settings has no key fields.
- **Step 2** — Relative paths (`DATABASE_URL` sqlite path, `CHECKPOINT_DB`, persona prompt,
  retrieval documents) resolve from the project root, so commands work from any directory.
  Non-sqlite URLs are untouched (Postgres path).
- **Step 2** — `AGENT_NOW` **freezes** time (FixedClock) rather than starting a running clock:
  deterministic demos, recordings and tests. Naive values are read as store time; aware values
  are converted. `get_clock(now)` takes an explicit override for the CLI's `--now`.
- **Step 2** — The client config is validated with strict pydantic models (`extra="forbid"`):
  known timezone, all 7 weekdays present (null = closed), opening before closing, installment
  bands ordered and contiguous from 1, heading pattern compiled. YAML keys `from`/`to` map to
  `min_installments`/`max_installments`.
- **Convention** — The "Commit" column holds the commit subject; hashes are in `git log`
  (a commit cannot contain its own hash).

## Known issues / TODO

<!-- Open bugs, flaky tests, things to revisit. -->

- Groq free-tier TPM for `openai/gpt-oss-120b` unconfirmed (see Verified facts) — measure in
  step 12 with the owner's key.
- The persona prompt path in `client.yaml` points to a file created in step 12.

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

### 2026-11-01 18:40 — Step 2: Config, client.yaml, clock
- Done: `config/client.yaml` (full client layer from §4, hours filled from manual §2);
  `config.py` (Settings via env + `.env`, strict ClientConfig models, cached getters);
  `clock.py` (Clock protocol, SystemClock, FixedClock, `get_clock`); `.env.example` models.
- Files: `config/client.yaml`, `src/emporio_agent/config.py`, `src/emporio_agent/clock.py`,
  `tests/test_config.py`, `tests/test_clock.py`, `.env.example`, `pyproject.toml`, `uv.lock`.
- Deps: pydantic 2.14.2, pydantic-settings 2.16.2 (+ python-dotenv), pyyaml 6.0.3, tzdata.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 22 passed.
- Notes: [VERIFY] LLM_MODEL and EMBEDDING_MODEL resolved (see Verified facts).
