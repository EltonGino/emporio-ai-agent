# PROGRESS.md — build log

Purpose: let any coding agent (Claude Code, Codex) or the owner resume work without context.
Update this file in the same commit as every plan step (see AGENTS.md §0 and §16).

## How to resume

1. Read `AGENTS.md` fully.
2. Read "Next step" and the latest log entry below.
3. Run `uv sync && uv run pytest -m "not llm"` to confirm the current state is green.
4. Continue with the next step; update this file before committing.
5. **Read "Groq free-tier budget" under Decisions** — it overrides AGENTS.md for steps 8–17.

## Next step

**Waiting for the owner's review** of the overnight run (see "Overnight run — what to review"
under Known issues). Nothing is pushed: local commits f544c30 (step 15 prep), af62cb5 (16),
d8dbbdc (18), 4ae5145 (19) and the step 20 commit, on top of the unpushed f8ea8e2 (14) and
2e40cf1 (14 fixes). After approval: push, then **step 17** (FastAPI implementing the contract
in "Step 19 — API contract the frontend expects", async end to end, state in the DB, WAL),
then 21, then running the step 15 evals on Groq.

## Plan status

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Project scaffold | done | `chore: project scaffold` (de38bce) |
| 2 | Config, client.yaml, clock | done | `feat(config): client.yaml, settings and injectable clock` (dad4f86); budget settings in `feat(config): Groq free-tier budget settings` (90e2813) |
| 3 | SQLite models + CSV loader with cleaning | done | `feat(data): SQLite models and CSV loader with cleaning rules` (5ae77a6) |
| 4 | Data profiling script | done | `feat(data): data profiling script` (bf0f706) |
| 5 | PDF ingestion + structure-aware chunking | done | `feat(retrieval): PDF ingestion and structure-aware chunking` (c00eb36) |
| 6 | BM25, dense and hybrid retrievers | done | `feat(retrieval): BM25, dense and hybrid retrievers` (31ab685) |
| 7 | Retrieval golden set + eval script | done | `feat(evals): retrieval golden set and evaluation script` (fdb9a8e) |
| 8 | Catalog and promotion tools | done | `feat(tools): catalog and promotion tools` (26bf364); signals in `feat(tools): partial-match and accessory signals in product search` |
| 9 | Payment rules | done | `feat(tools): payment rules` (ebaa394) |
| 10 | Order status + return eligibility | done | `feat(tools): order status and return eligibility with identity check` (4d14084); `feat(tools): days since order in order status` (ca153a7) |
| 11 | Store status, policy search, handoff | done | `feat(tools): store status, policy search and human handoff` (0ebaac7) |
| 12 | Persona prompt + agent with memory | done | `feat(agent): persona prompt and tool-calling agent with memory` (1dd00d7); `fix(agent): return days left, 429 wait, invalid tool calls and prompt rules` (1a2986e); `feat(agent): protocol guard and idempotent handoff` (f2f29e9) |
| 13 | Event stream + JSONL tracing | done | `feat(core): event stream and JSONL tracing` (f2d2ac8) |
| 14 | CLI with debug mode | done | `feat(cli): interactive CLI with debug mode` (f8ea8e2); `fix(agent): price guard, alternatives, verified-order handoffs and promotions` (2e40cf1) |
| 15 | Routing + adversarial evals | prepared (not run) | `test: routing and adversarial eval cases and runner` (f544c30) |
| 16 | CI workflow | done (not yet run on GitHub) | `ci: lint and tests workflow` (af62cb5) |
| 17 | FastAPI (SSE, sessions, rate limit, retention, deletion) | pending | |
| 18 | Frontend scaffold | done | `feat(frontend): Vite React scaffold` (d8dbbdc) |
| 19 | Chat UI with streaming | done (mock API; real API in step 17) | `feat(frontend): customer service chat with streaming` (4ae5145) |
| 20 | Bastidores panel + simulated date banner | done (mock API) | `feat(frontend): demo-mode behind-the-scenes panel and simulated date banner` (1979f10); `fix(agent): refusals in Tom's voice` |
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
  `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (+ whisper); the rest is *preview* ("evaluation
  only, may be discontinued"). Groq says all models support tool use and recommends the latest
  ones. gpt-oss-120b is the newest and largest production model, built for agentic tool use,
  and multilingual. Fallbacks: see "Groq free-tier budget" below (owner's account limits).
- **Groq free tier on the owner's account** (owner, 2026-11-01): `openai/gpt-oss-120b`,
  `openai/gpt-oss-20b` and `qwen/qwen3.8-27b` each have **30 RPM, 1K RPD, 8K TPM, 200K TPD**
  (separate quotas). `llama-3.3-70b-versatile` is **not available** on the account.
- **Groq reasoning** (console.groq.com/docs/reasoning, 2026-11-01): gpt-oss accepts
  `reasoning_effort` `low|medium|high`; `reasoning_format` is **not supported** (reasoning comes
  in the `reasoning` field; `include_reasoning` true/false). qwen3.8-27b accepts
  `reasoning_effort` `none|default|low|medium|high` and `reasoning_format` `parsed|raw|hidden`
  (default `raw` = `<think>` tags inside `content`); **with tool use, `raw` returns HTTP 400**, so
  qwen needs `parsed` or `hidden`. `include_reasoning` cannot be combined with `reasoning_format`.
- **Groq prompt caching** (console.groq.com/docs/prompt-caching, 2026-11-01): automatic, only on
  `gpt-oss-20b`/`gpt-oss-120b` (not qwen); "Cached tokens do not count towards your rate limits";
  needs an identical prefix (min 128–1024 tokens by model); reported in
  `prompt_tokens_details.cached_tokens`.
- **LangChain fallback [VERIFY]** (langchain 1.2.13, langchain-core 1.7.3, langchain-groq 1.1.3,
  langgraph 1.1.3, groq 0.37.1 — inspected in a throwaway env, not yet project deps):
  `langchain.agents.middleware.ModelFallbackMiddleware(first_model, *additional_models)` exists,
  accepts model **instances** (so each fallback can carry its own reasoning params), but its
  `wrap_model_call` falls back on **any `Exception`**, not only 429. `Runnable.with_fallbacks`
  also exists but sits outside the agent's middleware/tool binding. Rate limits raise
  `groq.RateLimitError` (subclass of `groq.APIStatusError`).
- **ChatGroq [VERIFY]**: fields `reasoning_effort`, `reasoning_format`, `max_retries` (default 2;
  the groq SDK retries 429 with backoff before raising). Reasoning is put in
  `AIMessage.additional_kwargs["reasoning_content"]`, separate from `content`, for both
  streaming chunks and full responses. `_convert_message_to_dict` does **not** send
  `reasoning_content` back to the API (checked), so stored reasoning is not re-billed as input.
  `usage_metadata` = `input_tokens`, `output_tokens`, `total_tokens`,
  `input_token_details.cache_read` (cached tokens), `output_token_details.reasoning`.
- **Token trimming [VERIFY]**: `langchain_core.messages.trim_messages(messages, *, max_tokens,
  token_counter, strategy="last", start_on=..., end_on=..., include_system=...)` and
  `langchain_core.messages.utils.count_tokens_approximately` exist (approximate counter ≈ 4
  chars/token; `token_counter="approximate"` also accepted).
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
- SQLAlchemy 2.1.6: `Numeric(10, 2)` round-trips `Decimal` on SQLite without warnings; `JSON`
  column stores/returns dicts. Money is `Decimal` end to end (no float rounding in rules).
- Data shape (2026-11-01): 65 products with IDs **81–145** (so the numbers in AGENTS.md §5.3 D1
  — "136 discontinued with 13 in stock", "130 coming_soon with 2", "96 active with 0",
  "113 discontinued, 0" — are **product IDs**, and they match the data); 9 categories,
  50 customers, 20 orders, 22 order items, 25 promotions. Statuses: 62 active, 2 discontinued
  (113, 136), 1 coming_soon (130).
- **PDF extraction [VERIFY] (step 5)**: pypdf 6.21.4 default `extract_text()` returns this PDF
  one word per line (words separated by `"\n \n"`), as AGENTS.md warned. `extract_text(
  extraction_mode="layout")` (still pypdf, BSD) keeps real lines and indentation: headings at
  column 0, bullets/numbered items indented, table cells separated by 3+ spaces, running
  header/footer on the first/last line of each page. That is enough to rebuild the structure,
  so **pdfplumber (MIT) and PyMuPDF (AGPL-3.0) were not needed** (owner preference: pypdf first,
  pdfplumber second, PyMuPDF only with a strong reason + README license note).
- **Dense prefixes [VERIFY] (step 6)**: fastembed 0.9.1 `TextEmbedding(model_name, cache_dir=…)`
  exposes `passage_embed` / `query_embed`, which apply the model's own prefixes; the chosen
  paraphrase-multilingual-mpnet-base-v2 needs none, and the code would stay correct with an
  e5-style model. rank-bm25 0.2.2 (`BM25Okapi(corpus, k1=1.5, b=0.75)`), unidecode 1.4.0,
  numpy 2.5.4, onnxruntime 1.31.1.
- **Embedding model footprint (step 6, this Mac)**: download ≈ **1.0 GB** into
  `data/runtime/models/` (gitignored); first run (download + load) 19.4 s; later loads ~0.9 s;
  embedding the 26 chunks 2.0 s; a hybrid query ≈ 12–21 ms on CPU.
- **Retrieval eval (step 7, 2026-11-01)** — `uv run python scripts/eval_retrieval.py`, 21 cases ×
  2 styles, MRR over top 10. Real numbers (README table):

  | Retriever | Style    | hit@1 | hit@3 | MRR  |
  |-----------|----------|-------|-------|------|
  | bm25      | customer | 0.71  | 0.81  | 0.76 |
  | dense     | customer | 0.62  | 0.76  | 0.71 |
  | hybrid    | customer | 0.71  | 0.95  | 0.83 |
  | bm25      | agent    | 0.95  | 1.00  | 0.98 |
  | dense     | agent    | 0.81  | 0.95  | 0.88 |
  | hybrid    | agent    | 0.90  | 0.95  | 0.94 |
  | bm25      | all      | 0.83  | 0.90  | 0.87 |
  | dense     | all      | 0.71  | 0.86  | 0.80 |
  | hybrid    | all      | 0.81  | 0.95  | 0.88 |

  Reading: hybrid is the most **robust** (hit@3 0.95 in both styles); **BM25 wins on
  agent-style queries** (hit@3 1.00) but falls to 0.81 on customer phrasing (4 queries not in
  its top 10: regret, refund, Dourados, installments — no shared terms); dense alone is the
  weakest. Query rewriting helps every retriever (hybrid MRR 0.83 → 0.94; "Dourados" rank
  6 → 1). Hybrid's one bad case: `shipping_big_instrument` agent-style is BM25 rank 1 but
  missing from dense's top 10, so RRF drops it to rank 7 (chunks in both lists score 2/(60+r),
  beating 1/61). Hybrid kept as default: the agent won't always rewrite perfectly, and hybrid
  has the best worst case.
- **Session id inside tools [VERIFY] (step 10, 2026-11-02)** — checked in a throwaway env with
  langchain 1.2.13 / langchain-core 1.7.3 / langgraph 1.1.3 (+ langgraph-prebuilt 1.0.8) and a
  fake chat model inside `create_agent`. Two supported ways: a `config: RunnableConfig`
  parameter (langchain-core) or `runtime: ToolRuntime` (`from langchain.tools import
  ToolRuntime`, `runtime.config`). Both are **hidden from the model's tool schema** and
  injected by the agent's ToolNode with the caller's config, so
  `config["configurable"]["thread_id"]` is the session id passed to `agent.invoke(...,
  {"configurable": {"thread_id": session_id}})`. A model-supplied `config` argument is
  ignored. **Chosen: `config: RunnableConfig`** — needs only langchain-core and can be tested
  with `tool.invoke(args, config=...)`. Note: an unpinned langgraph-prebuilt (newer than 1.0.8)
  failed to import with langgraph 1.1.3 (`ServerInfo`), so step 12 must let uv lock compatible
  versions (no hand pins).
- **Holidays [VERIFY] (step 11, 2026-11-02)** — `holidays` 0.107 (MIT):
  `holidays.country_holidays("BR", subdiv="MS", language="pt_BR")` lists, for 2026: national
  holidays incl. **Finados 2026-11-02**, Sexta-feira Santa, Consciência Negra (11-20), and the
  **MS state holiday "Criação do Estado" (10-11)**. Carnival is only in the `optional`
  category (ponto facultativo), so the store opens. Campo Grande **municipal** holidays are not
  in the package → `store.holidays.extra` (ISO dates, now validated as `date`), left empty:
  the manual doesn't list them and we don't invent dates.
- **Agent stack (step 12, 2026-11-02)** — `uv add langchain langchain-groq
  langgraph-checkpoint-sqlite` locked **langchain 1.4.7, langgraph 1.2.19, langgraph-prebuilt
  1.1.1, langgraph-checkpoint 4.3.1, langgraph-checkpoint-sqlite 3.1.3, langchain-groq 1.1.3,
  groq 0.37.1** (newer than the throwaway check; re-verified on these): `create_agent(model,
  tools, *, system_prompt, middleware, checkpointer, ...)`; `AgentMiddleware.wrap_model_call` /
  `awrap_model_call`; `ModelRequest.override(model=..., messages=..., system_message=...)`
  (`system_prompt` deprecated in favour of `system_message`); a middleware may return an
  `AIMessage`. `ModelFallbackMiddleware` still falls back on **any** exception → custom
  middleware. `config: RunnableConfig` injection into tools works inside this `create_agent`
  (tested). `SqliteSaver(sqlite3.connect(path, check_same_thread=False))` for the sync
  checkpointer; the async variant is for step 17.
- **Groq measurement (step 12, owner key, 2026-11-02)** — 4 scripted conversations, 9 turns,
  20 s between messages (`AGENT_NOW` per scenario). Static prefix (prompt + 9 tool schemas +
  context) ≈ **2,200 input tokens** per call. Per turn: no-tool turn ≈ 2,230; one tool ≈
  4,700–5,100 (2 calls); search + payment ≈ 8,000 (3 calls). Totals: input 36,550, cached
  7,424 (20%), output 1,186, reasoning 169 (effort low) → **≈ 3,370 counted tokens per turn**.
  Caching is partial and irregular (0–2,816 cached per call). 8K TPM per model ⇒ one heavy
  turn can fill the 120b minute, so the next call falls back to 20b (seen in every run; the
  fallback works). Earlier runs (before prompt fixes): 3,790 and 3,820 per turn.
  Capacity: 200K TPD/model ⇒ ≈ 59 turns/day per model, ≈ 175 with the three models →
  `DAILY_MESSAGE_CAP=60` (default) stays well inside one day's quota. Persona prompt alone ≈
  1,150 tokens (chars/4).
- **Groq measurement after the owner's adjustments (step 12, run 4)** — same 4 conversations:
  input 35,341, cached 2,048, output 1,171, reasoning 170 → **≈ 3,830 counted tokens/turn**
  (cache hits vary a lot run to run: 2,048–7,424 cached per run). No-tool turn ≈ 2,180 input
  (was 2,228). Tool schemas: 5,212 → 4,375 chars (≈ 1,303 → 1,093 tokens by chars/4), but the
  real prefix only dropped ≈ 50 tokens net (the prompt gained two rules, and the provider
  re-renders tool schemas in its own format, so JSON whitespace mattered less than estimated).
  **Groq sends `retry-after` on 429** (seen: 4, 11 and 13 s). Model calls: 15; 429s: 3 → 1
  short wait + retry on the 120b (4 s, succeeded), 2 fallbacks to the 20b (retry-after 11 and
  13 s, just above the 10 s threshold).
- Manual §2 hours confirmed from the PDF: Mon–Fri 09:00–18:00, Sat 09:00–13:00, Sun and
  holidays closed.

## Decisions and deviations during implementation

<!-- Anything not in AGENTS.md, or changed from it, with the reason. -->

- **Commit messages (owner rule, 2026-11-01, applies from step 4 on)** — every commit has the
  conventional title **plus a short English body (2–10 lines) explaining what and why**, e.g.:

  ```
  feat(data): data profiling script

  Detects and prints the data-quality findings D1–D10, P1–P4 and E1
  so every cleaning decision in the README is traceable to evidence.
  ```

  Commits already pushed (de38bce … 5ae77a6) are left as they are — never amend or rewrite
  them. Still no AI attribution trailers.
- **Workflow (owner rule, 2026-11-01)** — after each step: lint + tests, update this file,
  commit, **stop** and summarize for review. When the owner approves a step, **push it
  (`git push origin main`, never force)** before starting the next one.

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
- **Groq free-tier budget (owner decision, 2026-11-01) — overrides AGENTS.md where they differ.**
  Limits: see Verified facts. Apply in the step listed:
  1. **Fallback on 429** (step 12): `openai/gpt-oss-120b` → `openai/gpt-oss-20b` →
     `qwen/qwen3.8-27b` (`LLM_MODEL` + `LLM_FALLBACK_MODELS`, done in step 2 settings).
     `llama-3.3-70b-versatile` removed as an alternative (not on the account). Implementation:
     a small middleware based on `ModelFallbackMiddleware` that only falls back on
     `groq.RateLimitError` (the built-in one catches every exception and would hide real bugs);
     ChatGroq `max_retries` lowered so a 429 moves to the next model instead of waiting through
     SDK backoff. When every model is rate limited → friendly "try again later" message.
     qwen3.8-27b is a Groq *preview* model — acceptable as the last resort, noted in README.
  2. **Token budget**: system prompt target **~1,200 tokens** (step 12); short tool docstrings
     and compact tool outputs (steps 8–11); `search_products` default `limit=5` instead of
     AGENTS.md's 8 (step 8); history trimmed by **tokens** (`HISTORY_MAX_TOKENS`, default 2,500,
     via `trim_messages`) on top of `HISTORY_MAX_MESSAGES` (step 12), never splitting a tool call
     from its result.
  3. **Reasoning** (step 12): `LLM_REASONING_EFFORT=low` (default) for gpt-oss. qwen gets the same
     effort plus `reasoning_format="parsed"` (required with tools). Reasoning lives only in
     `additional_kwargs["reasoning_content"]`; the agent emits **only `content`** to CLI/API/UI,
     never reasoning; a test asserts it.
  4. **Prompt-cache-friendly prompts** (step 12, follows from the caching facts): the static
     system prompt + tool schemas must be a byte-identical prefix on every call, so the dynamic
     context (date/time, open/closed) is **appended after** the static prompt, not prepended as
     AGENTS.md §8 says. History is append-only within a turn, so later calls in the same turn hit
     the cache too.
  5. **Token usage per turn in the trace** (step 13): each `final` event / JSONL record carries
     input, cached, output and reasoning tokens summed over the turn's model calls, plus the
     model that answered (to see fallbacks). Used to measure real consumption in step 12/13.
  6. **Demo limits** (settings done in step 2; enforced in step 17): `MAX_MESSAGES_PER_SESSION`
     default **15** (was 40); new **`DAILY_MESSAGE_CAP`** (default **60**, customer messages per
     store-local day across all sessions) with a friendly message when reached. Default 60 is a
     first estimate (3 models × 200K TPD, assuming ~6–10K counted tokens per customer message
     without cache hits) → recalibrate with step 12/13 measurements.
  7. **LLM evals on gpt-oss-20b** (step 15): an option (pytest `--llm-model`, also usable via
     `LLM_MODEL`/`LLM_FALLBACK_MODELS` env) to run routing/adversarial evals on
     `openai/gpt-oss-20b` and spare the 120b quota.
- **Step 3** — Column names mirror the CSVs (`product_id`, `price_brl`, …). `order_items` gets a
  surrogate `id` (the CSV has no key). Status columns have CHECK constraints; SQLite foreign
  keys are switched on per connection (`PRAGMA foreign_keys = ON`).
- **Step 3** — The loader only drops/rebuilds the **store tables** (`STORE_MODELS`). Runtime
  tables (`handoffs` step 11, `sessions` step 17) are created if missing and never dropped, so
  rebuilding the catalog doesn't erase handoffs or sessions. They must not have foreign keys
  to store tables (or the drop would fail).
- **Step 3** — Cleaning: strip every field; emails lowercased (identity check compares
  normalized emails); empty optional order fields → NULL; dates parsed; `is_active` must be
  0/1; unknown statuses fail the load with the file name. Order totals kept as paid (D4).
- **Step 3** — D2 is applied as a fixed ID set `range(135, 145)` in the loader (documented
  constant). The step 4 profiler finds explicit contradictions in **7 of the 10** (another
  brand or model in the description; 88 vs 61 keys; 5 vs 4 strings): 135, 136, 137, 139, 140,
  142, 144. 138 (StingRay), 141 (Stage Custom) and 143 (Nord Stage) match the brand but belong
  to the same synthetic batch with generic names ("Bass 4X", "Kit 3 Studio", "Synth 2 Pro"),
  so they are distrusted too, per AGENTS.md.
- **Step 3** — `ensure_store_data(engine)` builds the store tables only if `products` doesn't
  exist (checked with `inspect().has_table`, works on Postgres too); for API startup (step 17).
- **Step 3** — Engine lives in `client/db/engine.py` (`make_engine(url=None)`), not listed in
  AGENTS.md §3; creates the SQLite parent directory.
- **Step 4** — Profiling logic lives in `src/emporio_agent/client/profiling.py` (one check per
  finding, returning id/title/evidence/decision) so it is unit-tested; `scripts/profile_data.py`
  only prints. Checks read `data/raw` directly (they profile the inputs, not the cleaned DB).
- **Step 4** — D2 detection heuristic: brands (and model names implying a brand: Jazz Bass,
  Precision → Fender; StingRay → Music Man) in the description but not in the name, and
  "N teclas"/"N cordas" vs **numeric** specs only (violões use `strings` = nylon/steel).
- **Step 4** — P1–P4 are detected on whitespace-normalized pypdf text (crude but enough for
  regexes); proper ingestion is step 5. pypdf was added here, one step earlier than planned.
- **Step 4** — D5 uses a local `_max_installments` helper; **step 9 must replace it with the
  `rules.py` function** so the band logic exists once.
- **Step 4** — D7 measures staleness against the last order date in the data (2026-03-22),
  not the wall clock, so the output is stable.
- **Step 5** — Ingestion pipeline (`core/retrieval/ingest.py`, client-agnostic): layout text →
  `clean_lines` (drop running headers/footers: the first/last line of a page whose text, digits
  ignored, repeats at a page edge on ≥ half the pages — so "Página 3" == "Página 4"; body lines
  are never dropped) → `build_blocks` (heading = unindented line matching `heading_pattern`;
  bullet = indented `•`/`N.`; table row = 3+ space gap; indented continuation lines join the
  previous bullet or the last table cell; prose lines join the previous paragraph unless it
  ends with `. ! ?`) → `render_blocks` → chunks.
- **Step 5** — Tables are rendered as self-contained rows using the header row:
  `- Forma: PIX; Condições: Pagamento à vista; Observações: 5% de desconto sobre o preço de
  tabela`, so a single row retrieved/quoted still makes sense. Bullets render as `- `, numbered
  items keep their number (`- 1. Saudação: …`).
- **Step 5** — Chunking: one chunk per subsection; a section's own intro text (before its first
  subsection, e.g. §4, §7) is a chunk with the section id; sections with no own text (§5, §6,
  §8) produce none. Text before the first heading (the cover) is chunk `"0"`, titled by its
  first line. Result: **26 chunks**, all < 900 chars (≈ < 250 tokens, inside the 512-token
  embedding window). The page of a chunk is where its heading is (5.2: heading p4, body p5).
- **Step 5** — **Chunks are stored in `data/runtime/index/chunks.json`**, not in a DB "chunks
  table" as AGENTS.md §6 says: chunks and their vectors (step 6) live and are rebuilt together
  in the index dir, and retrieval stays independent of the store DB. With Postgres+pgvector
  (production path) they would become a table. `INDEX_DIR` constant added to `config.py`.
- **Step 5** — Fixed-size fallback (~800 chars, ~150 overlap, cut at whitespace) for documents
  without recognizable headings; ids `p1, p2, …`. Unit-tested; not used by the current PDF.
- **Official contacts (owner decision, 2026-11-01)** — two guarantees on top of the prompt rule,
  because the manual itself is inconsistent (P1/P2) and retrieved chunks are verbatim:
  1. **Step 12:** the official WhatsApp/phone, email and address are read from `client.yaml`
     (`store.phone_whatsapp`, `store.email`, `store.address`) and rendered into the **static
     part of the system prompt** (cache-friendly prefix), so the agent never depends on
     `search_policies` for contacts. The prompt also says config contacts win over any
     contact found in retrieved text.
  2. **Step 15:** routing/adversarial eval cases "qual o WhatsApp de vocês?" and "qual o e-mail
     da loja?" must answer **(67) 3341-4444** and **contato@emporiodamusica.com.br**, and must
     never contain **(67) 3321-4500** or the accented **contato@empóriodamusica.com.br**.
- **Step 6** — `Retriever` protocol (`base.py`): `search(query, top_k) -> list[RetrievedChunk]`
  with `chunk`, `score`, `matched_by`. BM25 returns only chunks with score > 0 (no shared term =
  no match); dense always returns its top_k by cosine (L2-normalized vectors, dot product).
- **Step 6** — Hybrid = RRF (k=60) over each retriever's top **10** candidates (`depth`);
  `matched_by` lists the retrievers that ranked the chunk. Ties keep first-seen order (BM25 first).
- **Step 6** — `core/retrieval/index.py` (not in AGENTS.md §3 layout): builds/persists
  `chunks.json` + `vectors.npy` + `index_meta.json` (embedding model + sha256 of chunks.json) in
  `INDEX_DIR`; `index_is_current` makes a model or document change trigger a rebuild;
  `get_retrievers()` (cached) builds if missing/stale, then loads. BM25 is rebuilt in memory at
  load (26 chunks: instant). `MODEL_CACHE_DIR = data/runtime/models` (fastembed's default is a
  temp dir that the OS may wipe).
- **Step 6** — Unit tests use a hashing bag-of-words `FakeEmbedder`, so `pytest -m "not llm"`
  needs no model download; real retrieval quality is the step 7 eval's job.
- **`search_policies` query rewriting (owner decision, 2026-11-01) — apply in step 11.** The tool
  description must tell the agent to send a **self-contained, policy-oriented query**, not to
  repeat the customer's message, e.g. "quanto demora pra Dourados?" → "prazo de entrega para
  outras cidades fora de Campo Grande". Reason: step 6 showed customer phrasing can miss the
  right section (the manual never names cities). Step 7's golden set has both styles per case
  (`customer` and `agent`) and reports metrics per style, so the gain is measured.
- **Step 7** — Golden set (`evals/retrieval_golden.yaml`): 21 cases, each with `expected`
  section ids (any one counts as a hit) and the same question in two styles, `customer` and
  `agent`, written **before** measuring. Logic in `core/retrieval/evaluation.py` (unit-tested
  with a stub retriever); `scripts/eval_retrieval.py [--cases]` prints the table and per-case
  ranks. A guard test checks every expected section id still exists in the chunker output.
- **Step 7** — The §6 pytest (hybrid hit@3 ≥ 0.85) is asserted for **each style and overall**
  and carries a new `embeddings` marker (needs the real model). It still runs under
  `-m "not llm"`, so CI must provide the model (cache) — see Known issues.
- **Step 7** — No tuning after measuring (no held-out set: tuning RRF weights/depth on these 21
  cases would overfit). Candidate improvements for README "next steps": weighted RRF or a
  bigger dense depth, a held-out query set, and a smaller embedding model comparison.
- **Retrieval eval caveats for the README (owner decision, 2026-11-01) — apply in step 23.**
  The README must say, next to the table: (1) with 21 questions each one is worth ~5 p.p. of
  hit@k, so differences between retrievers are **indicative, not significant**; (2) the
  agent-style queries were written by someone who knew the manual, which tends to make those
  numbers **optimistic**; the real test of query rewriting is the end-to-end routing evals of
  step 15 (the agent writes its own queries there).
- **CI split (owner decision, 2026-11-01) — apply in step 16.** Two jobs: a **fast** job running
  `pytest -m "not llm and not embeddings"` (+ ruff, frontend build) and an **embeddings** job
  running `pytest -m embeddings` with `data/runtime/models` restored from `actions/cache`.
- **Step 8** — `StoreRepository` protocol (`client/repository.py`) returns plain frozen records
  (`ProductInfo`, `PromotionInfo`), never ORM objects. This step implements the catalog methods
  (`find_products`, `get_product`, `active_promotion`, `list_active_promotions`); order and
  handoff methods arrive in steps 10/11.
- **Step 8** — Catalog search (`client/matching.py`): query → terms (unidecode, lowercase,
  stopwords, rough PT singular "violões" → "violao", hyphens joined so "GD-20" = "GD20"; product
  model codes also indexed by parts so "gd 20" matches). A term matches exactly, or with
  `rapidfuzz.fuzz.ratio ≥ 85` for alphabetic words of 5+ letters ("takamini", "yamha" match;
  "kala" ≠ "kalani"; numbers/codes exact). Product text = name + category + spec values +
  small spec aliases (steel → "aço", electronics yes → "elétrico eletroacústico"). Results =
  products matching **all** terms; if none, those matching ≥ half (alternatives). Ranked by
  match, then final price ascending. Runs in Python over SQL-filtered candidates (65
  products); production path would be trigram/FTS in Postgres.
- **Step 8** — Category filter is resolved by name ("violões", "ukulele", "piano") and, if no
  name matches, by description ("saxofone" → empty woodwinds category → 0 results, D9). Unknown
  category → `{"error":"unknown_category","categories":[...]}` so the agent can explain what
  the store sells (e.g. "pedais").
- **Step 8** — Price filters compare the **final** price (after the active promotion): a
  customer asking "até R$ 450" should see the R$ 549 ukulele at 20% off (R$ 439,20).
- **Step 8** — With `only_available=True`, a **named** search also returns `unavailable` (up to
  3 matching items that can't be sold, with their label), and the available results become
  the alternatives: "Giannini GF-3D" → `unavailable: [GF-3D esgotado]` + other Giannini models.
  Category/price browsing omits unavailable items silently.
- **Step 8** — Output: compact JSON (`ensure_ascii=False`, no spaces), money as `price` +
  `price_text`, `promo: {name, percent, final_price, final_price_text}` only when active,
  up to 3 key specs in search results (priority list `KEY_SPECS`), full specs in details.
  Stock quantity is not exposed (only the availability label). Unreliable descriptions (D2)
  are omitted entirely. Measured: the 3 tool schemas ≈ 400 tokens; a 5-item search result
  ≈ 260 tokens.
- **Step 8** — `client/rules.py` started here with `availability` (D1), `apply_discount`
  (Decimal, ROUND_HALF_UP to cents) and `format_brl`; step 9 adds the payment rules.
  `get_product_details` has **no payment summary yet** (AGENTS.md §7) — it needs step 9's rules.
- **Step 8** — Tools are created by `build_tools(repo, client)` (closures over the repository) with
  pydantic `args_schema`s; later steps extend it. Deps: `rapidfuzz`, `langchain-core` (the rest
  of LangChain arrives with the agent in step 12).
- **Catalog search signals (owner decision, 2026-11-01) — step 8 follow-up commit.**
  1. **Partial match:** when no product has every query term, `search_products` returns
     `partial_match: true` and `unmatched_terms` (normalized terms some/all results lack, e.g.
     `["preto"]`), so the agent can say with certainty that the exact item isn't in the catalog
     and present the results as similar items. Only **exact** matches that can't be sold go in
     `unavailable` (partial matches never do). Supporting change: a query word that names a
     category acts as a category filter ("ukulele preto" → alternatives are ukuleles, not black
     violões), skipped when those categories have no candidates ("cordas" → Cordas Orquestrais
     is empty, so the 7-string violões still match). Singular rule extended: "-ais/-eis" →
     "-al/-el" (pedais → pedal).
  2. **Accessories:** if the query or category mentions a term from
     `catalog.non_sold_accessories` (client.yaml), the result carries
     `possible_accessory_query: true` (also on the unknown-category error), **without blocking
     results** ("violão 7 cordas" is a valid instrument search).
     - **Step 12:** the prompt must use this signal: explain politely that the store sells only
       instruments (no strings, picks, cables, cases, pedals, amps) and ask whether the customer
       is looking for an instrument (e.g. a 7-string guitar).
     - **Step 15:** routing eval cases "vocês vendem cordas?" (expect the accessory explanation,
       no product pitch as if strings were sold) and "quero um violão de 7 cordas" (expect
       `search_products` and 7-string violões in the answer).
- **Step 9** — Payment rules are pure functions in `client/rules.py` over `client.yaml`:
  `installment_allowed` (1x always; n needs total / n ≥ the band minimum: 2–3x ≥ R$ 50,
  4–6x ≥ R$ 80, 7–12x ≥ R$ 100 — P3), `max_installments`, `pix_price` (5% off, **not** on
  promotional prices, §6.2), `payment_options(lines)`. `profiling.py` now uses
  `max_installments` (D5 output unchanged: orders 9, 16, 18).
- **Step 9** — PIX is computed per cart line (5% of line total, rounded to cents) and summed;
  card/debit/boleto pay the cart total (promotions applied, no further discount).
  Installments are interest-free (§3), each `total / n` rounded to cents (sums may differ
  from the total by a few cents — a real checkout adjusts one installment). Combined payment
  needs total **strictly above** R$ 2.000 ("acima de"), on the cart total.
- **Step 9** — `calculate_payment_options(items)`: duplicates merged; items not for sale
  (not found, esgotado, descontinuado, em_breve, or quantity above stock, with
  `available_quantity`) are listed in `not_for_sale` and left out of the totals; if nothing
  is left → `{"error":"nothing_to_sell"}`. It returns every allowed installment as a ready
  string ("3x de R$ 733,00", ≈ 100 tokens for 12x) so the LLM never divides money.
  `get_product_details` gained the deferred `payment` summary (PIX price, max installments
  and value) for sellable products.
- **Step 10** — Identity check: `repo.get_order_for_customer(order_id, email)` returns `None`
  both for an unknown order and for an email that isn't **that order's customer's** (emails
  normalized; D8 shared email only opens customer 11's orders). The tools return one constant
  string, `identity.ORDER_NOT_FOUND`, for both — **byte-identical** (owner requirement, tested
  with unknown order, another real customer's email and a malformed email). Output never has
  other customer fields: only `customer_first_name` (manual §7.2 greeting).
- **Step 10** — Lockout (owner requirement): the session id is read from the tool's run config
  (`configurable.thread_id`), **never from a tool argument**; a test sends a fake `thread_id`
  argument and shows it is ignored. 5 failures per session (shared by `get_order_status` and
  `check_return_eligibility`) → every later lookup in that session returns
  `TOO_MANY_ATTEMPTS`, even with correct data; a success does not reset the counter. No
  session in the config → `session_required` (fail closed). The counter is in memory
  (`identity.FailedLookups`, thread-safe), shared by one tool set per process.
- **Step 10** — Order status: Portuguese status text (AGENTS.md §7), dates as `dd/mm/yyyy`,
  human-readable payment method ("cartão de crédito em 12x"), `total` = amount paid (D4),
  `delayed` = status confirmed/shipped and ETA before today (D7). Old pending/confirmed orders
  without an ETA are **not** flagged: the manual has no dispatch deadline to compare with; if
  the customer complains, the agent hands off (prompt, step 12).
- **Step 10** — Return windows (`rules.return_eligibility`, pure): only delivered orders;
  receipt date = ETA (D6), capped at today; regret and preference = 7 days after receipt;
  defect = 30 days after purchase, then `defect_after_exchange_window` (manufacturer warranty,
  store can mediate) with `legal_warranty_until` = receipt + 90 days (§8.1). Windows are
  inclusive (order 7: eligible on 24/02, not on 25/02). `next_steps` are short fixed English
  hints for the LLM (e.g. eligible → confirm conditions and `handoff_to_human`, since the
  agent cannot process a return itself).
- **Step 10** — `client.yaml` gained `returns.item_conditions` per reason (product state
  conditions from manual §4.1–4.3), returned as `conditions_to_confirm` with the
  `ineligible_conditions` list as `not_eligible_items`: the tool answers the return question in
  one call, without a policy search. Reasons are a `ReturnReason` literal in `config.py`.
- **Step 10** — `build_tools(repo, client, clock, failed_lookups=None)`: tools take the injectable
  clock (dates depend on "today").
- **Stale orders (owner decision, 2026-11-02).** `get_order_status` returns
  `days_since_order` (today − order date, injectable clock). **Step 12:** the prompt must say:
  pending or confirmed order for many days → acknowledge the wait with empathy and offer
  `handoff_to_human`, **never invent a date or deadline**. (`delayed` stays ETA-based, D7.)
- **Identity lockout persistence (owner decision, 2026-11-02) — apply in step 17.** Persist
  the failed-lookup counter in the `sessions` table (e.g. `failed_order_lookups` column) so it
  survives restarts; `identity.FailedLookups` becomes an interface with the in-memory version
  for tests/CLI and a DB-backed one for the API.
- **Step 11** — Store hours (`rules.store_status`, pure): holidays (package + `extra`) are
  closed days; closing time is exclusive (Saturday 13:00 = closed); next opening is searched
  day by day (bounded to 14 days). `get_store_status` returns now (pt-BR, e.g. "quarta-feira,
  25/03/2026 às 10:30"), open/closed, today's hours, holiday name, closes_at / next_opening and
  the weekly hours (+ "feriados: fechado"). Contacts are **not** in this tool: they go in the
  static prompt (step 12, owner decision).
- **Step 11** — `search_policies(query)`: hybrid retriever injected into `build_tools`, `top_k`
  from client.yaml (3). The description carries the owner's query-rewriting rule (with the
  Dourados example) and says contacts in results may be outdated (P1/P2). Uses
  `response_format="content_and_artifact"`: the model sees only `{section, text}`; the artifact
  (doc, section id/path, page, RRF score, matched_by) is kept out of the prompt for tracing and
  the Bastidores panel (steps 13, 20).
- **Step 11** — `handoff_to_human(reason, summary, contact?)`: new runtime table `handoffs`
  (no FK to store tables; `created_at` naive store time; `session_id` from the run config, not
  an argument). Protocol `EMP-YYYYMMDD-NNNN`, daily sequence by store date, unique constraint
  + up to 3 retries if two requests race. Returns the protocol, "até 24 horas úteis"
  (`support.complaint_return_business_hours`), `team_available_now` and, when closed,
  `team_back_at` (manual §7.1: say when the team is back).
- **Step 11** — Tests use a shared session fixture `policy_retriever` (BM25 over the real
  manual, `tests/conftest.py`) so tool tests don't need the embedding model.
- **Step 11** — Token budget: the 9 tool schemas ≈ **1,300 tokens** (chars/4;
  `search_products` ≈ 340, `handoff_to_human` ≈ 200). With a ~1,200-token prompt the static
  prefix is ≈ 2,500 tokens (cached on gpt-oss after the first call). If step 12 measurements
  show TPM pressure, shorten the descriptions first.
- **Session deletion vs handoffs (owner decision, 2026-11-02) — apply in step 17.**
  `DELETE /api/sessions/{id}` **anonymizes** the session's handoffs instead of deleting them:
  set `contact` and `summary` to NULL and drop the `session_id` link; keep `protocol`,
  `reason`, `created_at` and `status` as the operational record. README note: for a real
  client this retention choice would be validated with the legal team (LGPD).
- **Step 12** — Layout: `core/agent.py` (client-agnostic: middlewares, `trim_history`,
  `build_agent`, `run_turn`, `TurnUsage`) + **`client/assistant.py`** (not in AGENTS.md §3:
  wires the client layer — Groq model chain, persona prompt rendering, dynamic context,
  `build_store_agent`). Persona in `client/prompts/system.md` (`string.Template`; `$$` for
  "R$"); official contacts, name, slogan and the partner-store rule (P4: none → "Não indique
  outras lojas.") are filled from client.yaml.
- **Step 12** — `RateLimitFallback` middleware: on an error with `status_code == 429` (any SDK;
  `groq.RateLimitError` is one), retry the same request on the next model
  (120b → 20b → qwen3.8-27b); other errors propagate (tested); if every model is rate limited
  it returns a friendly AIMessage (`RATE_LIMITED_MESSAGE`) instead of failing the turn.
  ChatGroq `max_retries=0` on all but the last model (2), so a 429 moves on at once.
- **Step 12** — Reasoning: gpt-oss `reasoning_effort=low`; qwen `reasoning_format="parsed"` +
  same effort. `run_turn` returns `AIMessage.text` of the final message only (reasoning lives
  in `additional_kwargs`); a fake-model test proves reasoning never reaches the customer.
- **Step 12** — `DynamicContext` middleware appends "## Agora / date, time, open/closed, next
  opening" **after** the static prompt on every model call (tested: system message =
  static + "\n\n" + context).
- **Step 12** — `TrimHistory` middleware (`trim_history`): the current turn is always sent in
  full; past turns are added newest-first as whole turns, **compacted to the customer message
  and the final answer** (tool calls/results dropped: facts must be re-queried anyway and tool
  payloads are most of the tokens), within `HISTORY_MAX_MESSAGES` and `HISTORY_MAX_TOKENS`
  (approximate counter). Never splits a tool call from its result. Full history stays in the
  checkpointer.
- **Step 12** — Prompt iterations from the measurement runs (owner asked to see the prompt
  before committing): (1) the 20b read `estimated_delivery` as a delivery date → tool
  description says it is a forecast; (2) a model put the **store's** phone as the customer
  contact → field description + prompt rule; (3) models said "vou abrir/encaminhar" without
  calling a tool → rule "never announce an action for later: do it now or ask"; delayed orders
  and eligible returns now **ask** before `handoff_to_human` (complaints register directly);
  (4) the full installment list was pasted → `calculate_payment_options` now returns
  `installment_summary` ("até 12x sem juros de R$ 183,25") + `installment_options`, and the
  prompt says to list options only on request.
- **Step 12 (owner adjustments before the commit)** — prompt section "## Tom de voz" (not
  "## Tom", the agent's name); closing question only when the need seems solved; new rule:
  never mention tool, field or internal names to the customer; the partner-store line is
  generated from `catalog.partner_stores` (empty → "Não indique outras lojas.", else suggest
  them; tested both ways). `get_order_status` adds `days_since_estimated_delivery` when
  delayed and its description says `days_since_order` counts from the purchase.
- **Step 12** — 429 handling refined (owner): `retry_after(error)` reads the `retry-after`
  header, else Groq's "try again in 7.66s / 450ms / 1m2.5s" message. If ≤ 10 s
  (`MAX_RETRY_WAIT_SECONDS`), sleep that + 0.5 s and retry the **same** model once; otherwise
  (or on a second 429) move to the next model. Sync uses an injectable sleep (tests), async
  uses `asyncio.sleep`. Logged at INFO (wait) / WARNING (fallback) by `emporio_agent.core.agent`.
- **Step 12** — Tool descriptions shortened (owner): one-line descriptions (docstring
  indentation was being sent), non-nullable defaults (`query=""`, `category=""`,
  `min_price=0`, `max_price=0` = no limit, `contact=""`) so schemas have no `anyOf`/`null`.
  Rules kept: availability labels, unavailable/partial_match/possible_accessory_query meanings,
  installment_summary, forecast vs delivery, query rewriting example, official contacts.
- **Step 12 follow-up (owner, 2026-11-02)** — (1) `check_return_eligibility` returns
  `days_left` (deadline − today, only when eligible) and the deadline; `days_elapsed` moved to
  the tool **artifact** (`response_format="content_and_artifact"`), so it reaches tracing but
  not the model. (2) `RATE_LIMIT_MAX_WAIT_SECONDS` setting (default **15**, `.env.example`)
  replaces the fixed 10 s. (3) Prompt, complaints: the first sentence acknowledges the problem
  with empathy (example given), no emoji anywhere, including the closing. (4) Prompt,
  greeting: only in the first answer; afterwards no "Oi"/"tudo bem?", use the first name
  naturally. (5) Protocol must be copied exactly.
- **Step 12 follow-up** — **Groq rejects schema-invalid tool calls with HTTP 400
  `tool_use_failed`** (seen live: the 120b called `get_order_status` with `order_id: 0` and an
  empty email before asking; the schema's `minimum: 1` made Groq reject it — without it the
  call would have counted as a failed identity check). The middleware (renamed
  `ProviderFallback`) now treats it as recoverable: retry the same model once at once, then the
  next model; if every model fails → `FAILED_MESSAGE` ("Pode escrever de outro jeito?"). Other
  400s still propagate. Order tool descriptions now say "call only with the number and email
  the customer gave".
- **Protocol guard (owner decision, 2026-11-02)** — `core/guards.py` `ProtocolGuard`
  middleware, outermost (it checks the answer whichever model wrote it, after fallbacks). A
  final answer (AIMessage without tool calls) may only present protocols issued by
  `handoff_to_human` in this session (read from the **full** state, not the trimmed request).
  Detection does not depend on the EMP- format (the real invented one was "20231102-8745"):
  (1) any dated code `[PREFIX-]YYYYMMDD-NNN+` anywhere; (2) after the word "protocolo" /
  "protocol", the first token with ≥ 4 digits within 8 words (dates with "/", times with ":",
  money and "R$" are not codes). Unknown code → the model is asked once more with a correction
  note appended to the system prompt (naming the code); still unknown → fixed safe answer
  ("Desculpe, me confundi… Quer que eu registre seu caso…?"). The invalid answer is never
  stored in the checkpointer nor returned (tested). Texts (keywords, note, safe answer) come
  from the client layer (`client/assistant.py`); the mechanism is client-agnostic.
  Known trade-off: a bare 4-digit number right after "protocolo" (e.g. a year) would trigger a
  re-ask; accepted, since a short invented code is the likelier risk.
- **Protocol guard and streaming (owner decision) — affects steps 13 and 17.** The final answer
  is held until the guard validates it, so the customer can never see a false protocol. In the
  SSE API this means **raw LLM tokens of the final answer cannot be streamed live**: the server
  emits `token` events only after validation (the validated text can still be sent in chunks
  to keep a typing effect), and tool events/typing indicator cover the wait. Tool-calling
  steps are unaffected. The CLI follows the same rule.
- **Idempotent handoff (owner decision, 2026-11-02)** — `handoff_to_human` gained optional
  `order_id` (0 = none; `handoffs.order_id` nullable, no FK). Idempotency key = **session +
  reason + order_id** among open handoffs (`is_not_distinct_from`, so "no order" matches "no
  order" on SQLite and Postgres): the same case again returns the existing protocol with
  `already_registered: true`; two different orders in one conversation get two protocols
  (tested); no session → no deduplication. No migration needed: the runtime `store.db` had no
  `handoffs` table yet (schema changes to runtime tables would need Alembic in production).
- **Step 12** — Known quality gap: when the 120b is rate limited mid-turn the 20b writes the
  answer, and it follows the tone rules less strictly (e.g. an emoji in a complaint in run 2).
  Mitigations if needed: shorter tool descriptions (fewer tokens per call), or the README
  notes that a paid tier removes the TPM ceiling.
- **API scalability (owner decision, 2026-11-02) — apply in step 17.** The API must be **async
  end to end** (async agent calls via `ainvoke`/`astream`, async checkpointer
  `AsyncSqliteSaver`, async DB access) and keep **no state in process memory**: sessions,
  identity-failure counters, per-session message limit and the daily cap live in the database,
  so several replicas behind a load balancer (with Postgres) work later without rewriting code.
  The in-memory `identity.FailedLookups` stays only for tests/CLI.
- **SQLite concurrency (owner decision, 2026-11-02) — apply in step 17.** Enable
  `PRAGMA journal_mode=WAL` and a `busy_timeout` (e.g. 5 s) on every SQLite connection
  (`store.db` via the engine's connect event, and `checkpoints.db`), so readers don't block
  writers with several users at once.
- **Step 13** — `core/events.py`: one `AgentEvent(type, session_id, at, data)` model, with a
  free `data` dict per type: `user_message{text}`, `tool_start{name,args}`,
  `tool_end{name,result_preview (300 chars),duration_ms,status}`, `retrieval{chunks}` (the
  `search_policies` artifact: section id/path, page, RRF score, matched_by), **`notice`** (new,
  not in AGENTS.md §8: middleware decisions — `rate_limit` wait/next_model, `invalid_tool_call`
  retry/next_model, `protocol_guard` retry/safe_answer — and other tool artifacts such as
  `check_return_eligibility` days_elapsed), `token{text}`, `final{text,usage,duration_ms}`,
  `error{message,detail}`. `CUSTOMER_EVENTS` = user_message, token, final, error (the API in
  non-demo mode sends only these, step 17).
- **Step 13** — `stream_turn(agent, session_id, text)` reads `agent.stream(stream_mode=
  ["updates","custom"])`: AI messages → usage + tool_start (+ final answer = last AI message
  without tool calls, `.text` only, never reasoning); tool messages → tool_end (+ retrieval or
  artifact notice); custom chunks (written by middlewares via `events.notify` →
  `langgraph.config.get_stream_writer`) → notice. `token` events replay the **validated**
  final text in word chunks after the graph finishes (owner decision: no live tokens before
  the ProtocolGuard check). `run_turn` is now a consumer of the stream (same `TurnResult`).
- **Step 13** — Unexpected errors no longer raise out of a turn: they become an `error` event
  with a fixed friendly message (`ERROR_MESSAGE`) and `detail` (exception type + text) for the
  trace; the API must not send `detail` to customers. Tests updated accordingly.
- **Step 13** — `TurnUsage` moved to `core/events.py`; it now also counts **discarded** model
  calls reported by the guard (`discarded_usage` in the notice). Calls that failed with
  429/400 return no usage from the provider, so they can't be counted (only noticed).
- **Step 13** — `core/tracing.py`: `JsonlTracer(TRACES_DIR)` appends each event as one JSON
  line to `data/runtime/traces/<session_id>.jsonl` (`token` events skipped — `final` has the
  text), thread-safe; `delete(session_id)` for LGPD (step 17); session ids must match
  `[A-Za-z0-9_-]{1,100}` (path traversal blocked). `traced(events, tracer)` writes then
  yields. LangSmith stays env-only (no code). `TRACES_DIR` added to `config.py`.
- **Step 13** — Test doubles moved to `tests/fakes.py` (`ScriptedModel`, `ProviderError`,
  `call`), shared by `test_agent.py` and `test_events.py`.
- **Step 14** — `src/emporio_agent/cli.py` (typer + rich), console script `emporio-chat`
  (`[project.scripts]`). Options: `--session ID` (validated like trace ids), `--debug`,
  `--now ISO` (overrides `AGENT_NOW`; naive = store time). Startup: settings + client config,
  friendly exit if `GROQ_API_KEY` is missing (provider groq), hybrid retriever (status
  spinner; first run downloads the embedding model), persistent memory
  (`sqlite_checkpointer(CHECKPOINT_DB)`), `JsonlTracer(TRACES_DIR)`. Banner shows the store,
  the simulated date when the clock is fixed, the session id and the commands.
- **Step 14** — Each turn consumes `traced(stream_turn(...))` under a "Tom está digitando…"
  spinner; the validated final text is rendered as Markdown (token events are not needed in a
  terminal). `--debug` prints, dimmed and without rich markup parsing: `→ tool(args)`,
  `← tool [ms] preview`, `§ section (score, bm25+dense)`, `! notice`, and after the answer the
  turn's usage (input/cached/output/reasoning tokens, model calls, models, ms). Errors show
  the friendly message; the detail only with `--debug`. `/new` → new uuid session (fresh
  memory), `/exit` or Ctrl-D/Ctrl-C → quit.
- **Step 14** — `build_chat_agent` is the single seam for tests: `tests/test_cli.py` swaps it
  for a scripted model (no LLM, no embeddings) and points `TRACES_DIR` to a temp dir.
- **Manual CLI test fixes (owner, 2026-11-02)** — four problems found by the owner, fixed in
  one commit:
  1. *Invented products*: "e o Ibanez Bass 2X?" returned only `unavailable` and the model
     invented 4 basses with prices. (a) `find_products` now returns **`alternatives`** when the
     exact item can't be sold: up to 3 **available** products of the **same category**, closest
     final price first, excluding items already in the results. (b) **Price check** in the
     output guard (see below).
  2. *"Tom called me Ana"*: investigated the owner's trace — the CLI **had** started a new
     session (one uuid, only the owner's messages); "Ana" came from the prompt's complaint
     example ("Sinto muito por essa demora, Ana."). Fixes: no names in prompt examples; rule
     "use the first name only if it came from an order verified in this conversation". The
     CLI banner now says "Nova conversa: <id> (para retomar: --session <id>)" or "Retomando a
     conversa <id>"; without `--session` it never resumes (tested: two runs → two ids). The
     CLI also silences the package's log records in the terminal (Python's last-resort
     handler printed "429 on … next model"); they stay in `--debug` and the trace.
  3. *Handoff without verification*: "Tô com um pedido atrasado" → `handoff_to_human(order_id
     0, contact "")`. Now enforced in code: new reason **`return_request`**; reasons
     `delayed_order`/`return_request` need an `order_id` **verified in this session** (a
     successful `get_order_status`/`check_return_eligibility`), and any `order_id` given must be
     verified; otherwise the tool returns `order_not_verified` telling the model to ask for
     number + email first. 0 / "" mean "not given". `identity.VerifiedOrders` (in memory, per
     session; **step 17: persist it in the DB** with the failure counter). SQLite dev
     databases with the old `handoffs` CHECK are rebuilt keeping rows
     (`loader.upgrade_handoffs_table`, called by `ensure_store_data`; production would use
     Alembic).
  4. *Incomplete promotions*: "tem ukulele em promoção?" → `search_products` (5 of 14 shown),
     one promotion cited and "os demais com preço normal". Now: `list_active_promotions`
     description says to use it for any promotion question before saying what is or isn't on
     sale; `search_products` gained an **`on_promotion`** filter and its description says
     "say nothing about items not shown"; prompt rules for both.
- **Output guard generalized** — `ProtocolGuard` became `OutputGuard(checks)` with
  `ProtocolCheck` and **`PriceCheck`**: every money amount in the final answer ("R$ 2.199,00",
  "R$2199", "R$ 1.000", "500 reais") must appear in **this turn's** tool results (any number in
  their JSON, pt-BR or plain) or in what the customer wrote in the conversation; otherwise the
  model is re-asked once with a correction note naming the amounts, then a safe answer
  ("não consegui confirmar esses valores…"). This also blocks computed amounts ("economia de
  R$ 109,95") and prices recalled from earlier turns without a new lookup (stricter, by design:
  the prompt already requires a tool call in the turn). Notice kind is now `output_guard` with
  `failures: {check: [...]}`. Prompt grew to ≈ 1,350 tokens (chars/4) with these rules.
- **Plan order change (owner, 2026-11-02):** steps 18–20 (frontend) are built **before** step
  17 (API), against a mock API client (`VITE_MOCK=true`) that replays real conversations
  converted from JSONL traces. The contract is `AgentEvent` (step 13) + AGENTS.md §11
  endpoints, so step 17 must implement exactly what the frontend's real client calls.
- **Step 15 (prepared, not run)** — `src/emporio_agent/core/evals.py` (client-agnostic):
  `EvalCase(id, turns, expect, now, note)`, `load_cases` (rejects unknown expectation keys),
  `run_conversation` (one fresh session; collects tool calls, notices, answers, errors, input
  tokens from `stream_turn`), `check(expect, transcript)`, `run_case(case, agent_factory,
  pace)`. Expectations: tools_any, tools_all, forbidden_tools, first_tool_in, no_tools (whole
  conversation); answer_contains / answer_contains_any (last answer); answer_not_contains /
  answer_not_matches (every answer); no_emoji (last answer); no_guard_failures (the output
  guard never had to correct). Text checks: unidecode + casefold, so "e‑mail" (U+2011) =
  "e-mail". A turn `error` event fails the case.
- **Step 15** — Cases: `evals/routing_cases.yaml` (19) and `evals/adversarial_cases.yaml` (8)
  include every case recorded here: official WhatsApp/e-mail (P1/P2), "cordas" vs "violão de
  7 cordas", defect policy general question (first tool `search_policies`, no order tools,
  "30 dias"), Ibanez Bass 2X alternatives (no invented SR500/GSR200), no name in a new
  session, "pedido atrasado" asks for data (no handoff), ukulele promotion (CK-20 and
  KAL-700T), complaint without emoji (+ protocol + "24 horas úteis"), plus AGENTS.md §14
  (Martin D-28 price, prompt reveal, role override, other customer's e-mail, wrong-email
  lookup, injected instruction) and extras (GD20 PIX, Black Friday, Dourados, Sunday, regret
  return, lookup by customer name, insisted wrong WhatsApp, fake protocol request).
- **Step 15** — Running them: `uv run pytest -m llm tests/test_llm_evals.py [--llm-model
  openai/gpt-oss-20b] [--eval-pace 20]` (skipped without GROQ_API_KEY) or `uv run python
  scripts/run_evals.py [--llm-model ...] [--cases a,b] [--suite routing|adversarial|all]
  [--pace 20]` (prints PASS/FAIL + JSON report in `data/runtime/evals/`).
  `client.assistant.eval_agent_factory`: a fresh store DB copy per case (handoffs don't leak),
  in-memory memory, fixed clock (case `now` or 2026-03-25T10:30); `--llm-model` = that model
  only, **no fallbacks** (so the result says which model was evaluated). The runner itself is
  validated by `tests/test_evals.py` with a scripted model (incl. the real routing gap failing
  `defect_policy_general`). **Not yet run against Groq** (owner: 120b daily quota spent).
- **Step 16** — `.github/workflows/ci.yml`, three jobs on push to main and on PRs:
  (1) **fast**: `astral-sh/setup-uv@v6` (uv 0.13.6, Python from `.python-version`),
  `uv sync --frozen`, ruff check, ruff format --check, `pytest -m "not llm and not
  embeddings"` (334 tests locally, no model download); (2) **embeddings**: restores
  `data/runtime/models` with `actions/cache@v4` (key = embedding model + `uv.lock` hash;
  first run downloads ~1 GB) and runs `pytest -m embeddings` (hybrid hit@3 ≥ 0.85 per style);
  (3) **frontend**: Node 22, `npm ci && npm run build` in `frontend/`, skipped (step-level
  `hashFiles` condition) until `frontend/package-lock.json` exists. LLM evals never run in CI.
  The workflow was validated as YAML and the two pytest selections locally; it has not run on
  GitHub yet (no push).
- **Step 18** — `frontend/` from `npm create vite@latest -- --template react-ts`: React 19.3,
  Vite 8.3, TypeScript 7.0, oxlint (template default). Added Tailwind CSS v4 via
  `@tailwindcss/vite` (theme tokens in `src/index.css` `@theme`), `react-markdown`, and fonts
  bundled with Fontsource (no runtime Google Fonts request, works offline/in Docker):
  **Fraunces** (variable serif, titles), **Source Sans 3** (chat), **JetBrains Mono**
  (Bastidores console). Palette from instrument woods (ivory/spruce/maple backgrounds, walnut
  text, amber accent, burgundy emphasis; dark "console" palette for the internal panel), AA
  contrast noted in the CSS. Vite dev server proxies `/api` → `http://localhost:8000`.
  Template boilerplate removed; favicon = "T" monogram on burgundy. `frontend/screenshots/`
  is gitignored (review screenshots).
- **Step 19 — API contract the frontend expects (step 17 must implement it):**
  `GET /api/config` → `{assistant_name, store_name, slogan, demo_mode, simulated_now}`
  (**`slogan` added** to AGENTS.md §11's list, for the header); `POST /api/chat`
  `{session_id, message}` → `text/event-stream`, one `data: <AgentEvent JSON>` block per event
  (`\n\n` or `\r\n\r\n` separated), `AgentEvent = {type, session_id, at, data}` exactly as
  `core/events.py`; errors before the stream (e.g. 429 limits) as JSON `{detail: "<friendly
  pt-BR message>"}` — the UI shows `detail`; `DELETE /api/sessions/{id}` (404 tolerated).
  Session ids are generated by the browser (`crypto.randomUUID()` without dashes, kept in
  localStorage) and match the server's `[A-Za-z0-9_-]{1,100}`.
- **Step 19** — `frontend/src/api/`: `types.ts` (contract), `real.ts` (fetch + SSE parser,
  friendly error from `detail`), `mock.ts` (`VITE_MOCK=true`: replays recorded turns by
  matching the customer's text; tokens synthesized word by word from the validated final
  text, since traces don't store token events; unmatched text → a "modo demonstração" answer
  pointing to the chips; chips = conversation openers, then the next recorded turn).
  `useChat` hook: messages, per-turn event log for the Bastidores panel, busy state, "Nova
  conversa" (new id), "Apagar meus dados" (DELETE, then new id; confirm dialog).
  Components: Header (wooden-sign header, store name in Fraunces), Monogram ("T" in a
  soundhole-rosette ring), MessageList (react-markdown bubbles, typing dots, aria-live),
  SuggestionChips, Composer (Enter sends, Shift+Enter new line). `?replay=<id>` (mock only)
  plays a recorded conversation instantly — used for screenshots.
- **Step 19 — demo fixtures** — `scripts/export_demo_fixtures.py` builds
  `frontend/src/mocks/conversations.json` from JSONL traces in `data/runtime/traces/`: the
  owner's manual CLI session (09e5bb60…, 2026-03-25) and the step 13 live smoke trace (copied
  from the scratchpad into `data/runtime/traces/trace-smoke.jsonl`, 2026-02-20). 4
  conversations: contato-frete (WhatsApp; Recife shipping with retrieval), violao-esgotado
  (GF-3D; Kala barítono), devolucao (order 7 regret: eligibility, handoff, 429 notices),
  seguranca (prompt reveal, manager asks for a customer's e-mail, order of a named
  customer). **Left out on purpose:** answers recorded before later fixes (invented Ibanez
  basses, incomplete ukulele promotion, "Ana" in the delayed-order answer) and the
  "defeito" routing-gap answer. Anonymized: Letícia (Gonçalves) Rocha /
  leticia.rocha@jmail.com → Marina Teixeira / marina.teixeira@jmail.com; Bruno Martins →
  Carlos Nogueira (names not in customers.csv). Error `detail` stripped.
- **Step 20** — `BackstagePanel` ("$ bastidores", dark console palette, JetBrains Mono): one
  card per customer message with the turn duration (server `final.duration_ms`, else
  measured), `▸` tool calls with args, `✓` tool results (preview in a `<details>`, ms), `§`
  retrieved sections with RRF score and retrievers, `!` rate-limit / invalid-tool-call
  notices, `⛨` output-guard interventions, `·` tool artifacts kept out of the model, `Σ`
  token usage and models; label "Visível apenas no modo demo — clientes não veem este painel".
  Shown only when `config.demo_mode`; toggle in the header. Desktop (≥ 1024 px): side column,
  open by default; phones: drawer over the chat with a backdrop, closed by default
  (`?panel=open` forces it). `SimulatedDateBanner` from `config.simulated_now` ("Data
  simulada: 25/03/2026 10:30 (horário de Campo Grande)"); in mock replays it shows the date
  the conversation was recorded at. Header on phones: name on one line, actions on a second
  row ("Apagar dados" short label, full aria-label).
- **Step 20 — bug found by the screenshots:** recent Chrome returns a Promise from
  `scrollIntoView()`; an arrow-expression `useEffect(() => el.scrollIntoView())` returned it
  and React crashed ("destroy is not a function"). Effects now use braces (commented).
- **Step 20 — screenshots** (`frontend/screenshots/`, gitignored): mock build served by
  `vite preview`, headless Chrome; desktop 1440×900 (`desktop-1-inicio`, `-2-frete-politica`,
  `-3-devolucao`, `-4-seguranca`) and phone 390×844 @2x (`mobile-1-inicio`, `-2-chat`,
  `-3-bastidores-gaveta`). Chrome's minimum headless window is 500 px, so phone shots render
  the app in a 390 px iframe and crop (sips) — true phone layout.
- **CI first run (2026-11-02, after the owner pushed the overnight commits)** — run
  41449378661: all three jobs green (fast: 334 passed; embeddings: cache miss → model
  downloaded and cached, 1 passed; frontend build ok).
- **Refusals in Tom's voice (owner review, 2026-11-02)** — prompt rule: when refusing, stay
  Tom (friendly sentence, never "não posso atender a essa solicitação" / "não tenho acesso a
  esses dados"), give the reason simply and always offer what Tom can do; a third party's
  order → explain that, for security, orders are looked up only with order number + purchase
  e-mail, and offer that. Evals: new expectation `persona_refusal` (no `ROBOTIC_PHRASES`, at
  least one `OFFER_PHRASES` in the last answer) on the 7 refusal cases; `lookup_by_customer_name`
  now also requires "número do pedido" + e-mail; new case `lookup_by_another_name`. Demo chip:
  the tester's stray "”" is fixed in `export_demo_fixtures.py` (`USER_TEXT_FIXES`), fixtures
  regenerated. `scripts/run_evals.py` prints input tokens and 429 notices per case and in
  total. `evals_resultado.txt` (local run output) is gitignored.
- **Convention** — The "Commit" column holds the commit subject; hashes are in `git log`
  (a commit cannot contain its own hash).

## Known issues / TODO

<!-- Open bugs, flaky tests, things to revisit. -->

- 8K TPM per model may still be tight for multi-tool turns without cache hits — measure in
  step 12 with the owner's key; recalibrate `DAILY_MESSAGE_CAP`.
- **E1 (new data finding, not in AGENTS.md §5.3):** product 130 is `coming_soon`, but appears in
  order 3 (delivered, 2 units) and order 14 (pending). Handling: status wins for new sales
  (D1); past orders are reported as they are. Detected by the profiler; add to README (step 23).
- The 1 GB embedding model in Docker (step 21): bake it into the image or a volume — decide
  in step 21. (CI: done in step 16 with actions/cache.)
- Chunks are verbatim: chunk 1.2 contains the accented email (P2) and chunk 7 the other
  WhatsApp number (P1). Steps 11/12: contacts must come from `client.yaml` (store status tool /
  prompt), and the prompt must say config contacts win over retrieved text.

- **Guard run (run 7, complaint + return, 6 turns, 2026-11-02)** — ≈ 2,690 counted
  tokens/turn (cache 8,960 of 24,414 input). The 120b **invented "20231102-1456" again**
  (pedido 8, turn 2); the guard caught it and the customer only saw "Gostaria que eu registre
  um caso…?". Two Groq `tool_use_failed` 400s recovered by the retry. One handoff per case,
  with `order_id` (8 and 7). Remaining nits: the complaint answer was written by the 20b (a
  second 429 on the retried 120b) and opened with the protocol instead of empathy; the return
  handoff used reason `complaint` instead of `other` (AGENTS.md reasons have no "return";
  consider adding one later).
- **Routing gap seen in the step 13 live trace (→ step 15 eval case):** "Posso trocar um
  produto com defeito? Qual o prazo?" (a general policy question) made the 120b try an order
  tool with empty data (two `tool_use_failed` 400s, recovered) and the 20b then asked for
  order number + email instead of calling `search_policies`. **Expected behaviour (owner,
  2026-11-02):** answer the general rule via `search_policies` (defect exchange within 30
  days of purchase), **then offer** to check a specific order — without requiring the order
  data first. Step 15 eval case: expected tool `search_policies`, forbidden first-call tools
  `get_order_status`/`check_return_eligibility`, answer mentions 30 dias. If it fails, add a
  prompt line ("dúvida geral de política → search_policies; só depois ofereça verificar um
  pedido").
- **Eval cases from the owner's manual CLI tests (owner, 2026-11-02) — add in step 15:**
  (1) "tem o Giannini GF-3D?" then "e o Ibanez Bass 2X?" → only catalog products, alternatives
  from the tool, no `output_guard` price failure in the final answer; (2) new session +
  "Oi! Você sabe meu nome?" → no name in the answer (CLI session behaviour itself is covered by
  `tests/test_cli.py`); (3) "Tô com um pedido atrasado" → no `handoff_to_human` before a
  successful order lookup, asks for number + email; (4) "tem ukulele em promoção?" →
  `list_active_promotions` (or `search_products` with `on_promotion`) and both Ohana CK-20 and
  Kalani KAL-700T in the answer; plus the "defeito" routing case above.
- **Re-run of the 4 scenarios (2026-11-02, Groq, temp DB)** — all four fixed: Ibanez → real
  alternatives (Music Man Bass 4X R$ 7.157,00, Bass 1X, Yamaha Bass 3X); "pedido atrasado" →
  asks for number + email, no handoff; ukulele promo → `list_active_promotions`, both items;
  CLI twice without `--session` → two new ids, no name used. The guard also caught an invented
  protocol ("EM-2026-0008") in the delayed-order turn. Still prompt-level only: after the order
  was verified, the 20b opened the `delayed_order` case **without asking first** (allowed by
  code since the order was verified; the confirmation then returned `already_registered`).
  The 120b was rate limited with retry-after 150–937 s (daily quota close to exhausted after
  the day's tests), so the 20b/qwen answered almost everything.
- **Overnight run — what to review (2026-11-02):**
  1. *Step 15 cases* (`evals/*.yaml`): wording and strictness of each expectation (e.g.
     `no_guard_failures` on several cases; "answer_not_contains" name lists). Not run on Groq
     yet; run them on gpt-oss-20b first (`scripts/run_evals.py --llm-model
     openai/gpt-oss-20b`) when the quota allows.
  2. *CI* (`.github/workflows/ci.yml`): runs on the first push; the embeddings job downloads
     ~1 GB once, then uses the cache.
  3. *Frontend look* — screenshots in `frontend/screenshots/` (desktop + phone). Palette,
     fonts (Fraunces / Source Sans 3 / JetBrains Mono), wooden-sign header, rosette monogram,
     dark console panel. Run it: `cd frontend && npm install && VITE_MOCK=true npm run dev`.
  4. *Mock fixtures* — 4 real conversations, anonymized (Marina Teixeira, Carlos Nogueira);
     answers recorded before later fixes were left out. The "devolução" fixture shows the
     agent opening the case without asking first (real 2026-02-20 behaviour, before the
     verified-order rule; still legitimate there because the order was looked up).
  5. *API contract additions* for step 17: `slogan` in `/api/config`; error `detail` shown to
     customers; SSE `data:` blocks with AgentEvent JSON (CRLF tolerated).
  6. Not done on purpose: steps 17 and 21; no push.
- **README known limitation (owner, step 9):** installments are `total / n` rounded to cents,
  so n × installment can differ from the total by a few cents (e.g. 7 × R$ 314,14 =
  R$ 2.198,98 for R$ 2.199,00); a real checkout adjusts one installment. List it in README
  "Known limitations" (step 23).
- **README security note (step 10):** the lockout counter is per session and in memory: it
  resets on restart (step 17 persists it in `sessions`) and a new session starts a new counter. It limits guessing inside one
  conversation; real protection needs authenticated customers or a per-IP/global limit
  (step 17's per-session and daily caps help). List it in README.

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

### 2026-11-01 19:10 — Groq free-tier budget adjustments (owner request, before step 3)
- Done: recorded the owner's Groq limits and the 7-point budget plan (Decisions); verified
  ModelFallbackMiddleware, ChatGroq reasoning/usage handling, trim_messages, Groq reasoning and
  prompt caching docs. Settings: `LLM_FALLBACK_MODELS`, `LLM_REASONING_EFFORT`,
  `DAILY_MESSAGE_CAP`, `HISTORY_MAX_TOKENS`; `MAX_MESSAGES_PER_SESSION` default 15.
- Files: `src/emporio_agent/config.py`, `tests/test_config.py`, `.env.example`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 24 passed.

### 2026-11-01 19:40 — Step 3: SQLite models + CSV loader
- Done: SQLAlchemy 2.x typed models for the 6 store tables; engine factory with SQLite FK
  pragma; CSV loader with cleaning rules (D2, D3, D4, D8, D10 applied); `ensure_store_data`;
  `scripts/build_db.py`.
- Files: `src/emporio_agent/client/__init__.py`, `client/db/{__init__,models,engine,loader}.py`,
  `scripts/build_db.py`, `tests/test_db_loader.py`, `pyproject.toml`, `uv.lock`.
- Deps: sqlalchemy 2.1.6.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 38 passed. `uv run python
  scripts/build_db.py` builds `data/runtime/store.db` (gitignored).

### 2026-11-01 20:15 — Step 4: Data profiling script
- Done: `client/profiling.py` with 15 checks (D1–D10, E1, P1–P4), each with evidence and the
  decision; `scripts/profile_data.py` prints them. Pushed steps 1–3 (owner request).
- Files: `src/emporio_agent/client/profiling.py`, `scripts/profile_data.py`,
  `tests/test_profiling.py`, `pyproject.toml`, `uv.lock`, `PROGRESS.md`.
- Deps: pypdf 6.21.4.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 52 passed. Script output matches every
  number in AGENTS.md §5.3 (D4 orders 3/20; D5 orders 9/16/18; D9 categories 6/7/8; …).

### 2026-11-01 21:00 — Step 5: PDF ingestion + structure-aware chunking
- Done: pushed step 4 (owner approval). `core/retrieval/ingest.py` (layout extraction, header/
  footer removal, block rebuild, table rendering, heading chunking, fixed-size fallback,
  chunks JSON read/write); `scripts/build_index.py` writes `data/runtime/index/chunks.json`.
- Files: `src/emporio_agent/core/__init__.py`, `core/retrieval/{__init__,ingest}.py`,
  `src/emporio_agent/config.py` (`INDEX_DIR`), `scripts/build_index.py`, `tests/test_ingest.py`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 66 passed.
- Notes: the first noise rule (any line repeated on ≥ half the pages, digits ignored) would
  also drop body lines like "Item 1/Item 2"; a unit test caught it → restricted to page edges.

### 2026-11-01 21:45 — Step 6: BM25, dense and hybrid retrievers
- Done: pushed step 5 (owner approval); recorded the official-contacts guarantees (Decisions).
  `base.py` (protocol), `bm25.py` (accent/case-insensitive tokenizer + PT stopwords),
  `dense.py` (fastembed embedder, cosine retriever), `hybrid.py` (RRF), `index.py`
  (build/persist/staleness/load); `scripts/build_index.py` now also embeds.
- Files: `src/emporio_agent/core/retrieval/{base,bm25,dense,hybrid,index}.py`,
  `src/emporio_agent/config.py` (`MODEL_CACHE_DIR`), `scripts/build_index.py`,
  `tests/test_retrieval.py`, `pyproject.toml`, `uv.lock`, `PROGRESS.md`.
- Deps: rank-bm25, unidecode, fastembed, numpy.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 77 passed.
- Manual smoke (real model, top-3 sections): BM25 nails exact terms ("boquilha" → 4.4,
  "PIX … promoção" → 6.2); dense covers paraphrases where BM25 has no match ("em quantas vezes
  posso parcelar?" → 3.1). Weak spots to put in the golden set: "quanto tempo demora pra chegar
  em Dourados?" (expected 5.2; neither retriever finds it — the chunk never names a city);
  "o violão veio com defeito depois de 2 meses" (hybrid [4.2, 0, 10]: RRF lifts chunks that
  are mid-ranked in both lists over 8.2/8.3, each high in only one).

### 2026-11-01 22:20 — Step 7: Retrieval golden set + eval script
- Done: pushed step 6 (owner approval); recorded the `search_policies` query-rewriting rule
  for step 11. Golden set (21 cases × customer/agent styles), evaluation module, script,
  tests; metrics recorded in Verified facts.
- Files: `evals/retrieval_golden.yaml`, `src/emporio_agent/core/retrieval/evaluation.py`,
  `scripts/eval_retrieval.py`, `tests/test_retrieval_eval.py`, `pyproject.toml` (marker).
- Tests: ruff ok, format ok, `pytest -m "not llm"` 82 passed (incl. the `embeddings` test,
  ~1.4 s with the model cached).
- Notes: "defeito depois de 2 meses" scores fine in the eval because 4.2 also answers it (rank
  1); the RRF issue from step 6 shows up instead in `shipping_big_instrument`.

### 2026-11-01 23:00 — Step 8: Catalog and promotion tools
- Done: pushed step 7 (owner approval); recorded the README eval caveats and the CI split
  (Decisions). `StoreRepository` protocol + SQL implementation (catalog part), fuzzy catalog
  matching, `rules.py` (availability, discount, BRL formatting), tools `search_products`
  (default limit 5), `get_product_details`, `list_active_promotions`.
- Files: `src/emporio_agent/client/{rules,matching,repository,tools}.py`,
  `tests/test_catalog.py`, `pyproject.toml`, `uv.lock`, `PROGRESS.md`.
- Deps: rapidfuzz 3.14.6, langchain-core 1.7.3 (+ langsmith as its dependency).
- Tests: ruff ok, format ok, `pytest -m "not llm"` 122 passed.
- Notes: query "cordas" (strings, an accessory) matches the 7-string guitars by name; the
  prompt (step 12) handles accessory questions, and step 15 has a routing case for it.

### 2026-11-01 23:30 — Step 8 follow-up: partial-match and accessory signals (owner request)
- Done: `partial_match` + `unmatched_terms`, `possible_accessory_query`, category words in the
  query act as a filter, `unavailable` limited to exact matches; `build_tools(repo, client)`
  now takes the client config. Step 12/15 follow-ups recorded in Decisions.
- Files: `src/emporio_agent/client/{matching,repository,tools}.py`, `tests/test_catalog.py`,
  `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 132 passed.

### 2026-11-02 00:10 — Step 9: Payment rules
- Done: pushed step 8 + follow-up (owner approval). Payment rules in `rules.py`;
  `calculate_payment_options` tool; payment summary in `get_product_details`; profiling uses
  the rules function.
- Files: `src/emporio_agent/client/{rules,tools,profiling}.py`, `tests/test_payments.py`,
  `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 154 passed (band boundaries 99.99/100,
  150, 240, 319.99/320, 480, 699.99/700, 1199.99/1200; combined payment at 2000/2000.01).
- Notes: Takamine GD20 → R$ 2.199,00, PIX R$ 2.089,05, 12x R$ 183,25 (matches the AGENTS.md §9
  tone example).

### 2026-11-02 00:50 — Step 10: Order status + return eligibility with identity check
- Done: pushed step 9 (owner approval). [VERIFY] tool run config / session id (Verified
  facts). Repository `get_order_for_customer`; `rules.return_eligibility`;
  `client/identity.py` (generic errors, per-session lockout); tools `get_order_status`,
  `check_return_eligibility`; `returns.item_conditions` in client.yaml.
- Files: `config/client.yaml`, `src/emporio_agent/config.py`,
  `src/emporio_agent/client/{identity,repository,rules,tools}.py`, `tests/test_orders.py`,
  `tests/test_catalog.py`, `tests/test_payments.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 177 passed.
- Notes: AGENTS.md §15 scenarios check out: order 8 at 2026-03-25 → `delayed: true`; order 7
  at 2026-02-20, arrependimento → eligible until 24/02/2026 (3 days elapsed).

### 2026-11-02 01:10 — Step 10 follow-up: days since order (owner request)
- Done: pushed step 10 (owner approval). `days_since_order` in `get_order_status` (+ docstring
  hint); recorded the step 12 prompt rule and the step 17 counter persistence.
- Files: `src/emporio_agent/client/tools.py`, `tests/test_orders.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 178 passed.

### 2026-11-02 01:50 — Step 11: Store status, policy search, handoff
- Done: pushed step 10 (owner approval); `days_since_order` follow-up (ca153a7). Holiday
  calendar + `store_status` rules; `Handoff` model and `repo.create_handoff`; tools
  `get_store_status`, `search_policies` (content + artifact), `handoff_to_human`;
  `build_tools` takes the retriever.
- Files: `pyproject.toml`, `uv.lock`, `src/emporio_agent/config.py`,
  `src/emporio_agent/client/{rules,repository,tools}.py`, `src/emporio_agent/client/db/models.py`,
  `tests/conftest.py`, `tests/test_store_tools.py`, `tests/test_{catalog,payments,orders}.py`,
  `PROGRESS.md`.
- Deps: holidays 0.107.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 197 passed (Sunday, Saturday 12:59/13:00/
  13:01, Finados 2026-11-02, Good Friday, MS state holiday, municipal `extra`, protocol format
  and daily sequence).

### 2026-11-02 03:00 — Step 12: Persona prompt + agent with memory
- Done: pushed step 11 + recorded the session-deletion decision (owner). Agent core
  (`core/agent.py`), client wiring (`client/assistant.py`), persona prompt
  (`client/prompts/system.md`), tool tweaks from the measurement runs.
- Files: `pyproject.toml`, `uv.lock`, `src/emporio_agent/core/agent.py`,
  `src/emporio_agent/client/{assistant,tools}.py`, `src/emporio_agent/client/prompts/system.md`,
  `tests/test_agent.py`, `tests/test_payments.py`, `PROGRESS.md`.
- Deps: langchain 1.4.7, langchain-groq 1.1.3, langgraph-checkpoint-sqlite 3.1.3 (+ langgraph).
- Tests: ruff ok, format ok, `pytest -m "not llm"` 219 passed (fake chat model: session id in
  tools, memory per session, reasoning never emitted, context appended, 429 fallback, short
  retry-after waits and retries once, long retry-after falls back, non-429 propagates, all-429
  friendly message, trimming, prompt contacts and partner rule, model chain params).
- Measurement: 4 runs × 9 turns on Groq (throwaway script in the scratchpad, not committed);
  runs 3/4 ≈ 3,370 / 3,830 counted tokens/turn (see Verified facts). The owner reviewed the
  prompt and asked for the adjustments listed in Decisions before the commit.
- Quality notes from run 4 (to address later, not blocking): the 20b (fallback) said "3 dias a
  partir da compra" for order 7 (3 = days elapsed since receipt; deadline 24/02 was right) →
  candidate fix: return `days_left` instead of/besides `days_elapsed`; the 120b put an emoji
  in the complaint answer (rule says none) → step 15 eval case; retry-after of 11–13 s just
  missed the 10 s wait threshold → maybe raise to ~15 s (owner's call).

### 2026-11-02 04:00 — Step 12 follow-up: days left, 429 wait, invalid tool calls, prompt rules
- Done: owner adjustments (days_left + artifact, configurable 15 s wait, complaint and greeting
  rules, exact protocol); handling of Groq `tool_use_failed` 400s found in the re-run.
- Files: `.env.example`, `src/emporio_agent/config.py`, `src/emporio_agent/core/agent.py`,
  `src/emporio_agent/client/{assistant,tools}.py`, `src/emporio_agent/client/prompts/system.md`,
  `tests/test_{agent,config,orders}.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 223 passed.
- Live re-runs (complaint + return only): run 5 found the 400 crash and a mis-copied protocol;
  run 6 found an invented protocol and a duplicate handoff (Known issues). Stopped before
  step 13 for the owner's decision.

### 2026-11-02 05:00 — Step 12 follow-up: protocol guard and idempotent handoff
- Done: `core/guards.py` (`ProtocolGuard`, `claimed_protocols`, `issued_protocols`), wired as
  the outermost middleware via `build_agent(guards=...)`; `handoff_to_human(order_id)` +
  idempotency in `repo.create_handoff` (returns `HandoffResult`).
- Files: `src/emporio_agent/core/{agent,guards}.py`, `src/emporio_agent/client/{assistant,
  repository,tools}.py`, `src/emporio_agent/client/db/models.py`, `tests/test_guards.py`,
  `tests/test_agent.py`, `tests/test_store_tools.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 242 passed — incl. the real cases: protocol
  without EMP- prefix, mis-copied protocol, invalid answer never stored, duplicate handoff,
  two orders → two protocols.
- Live re-run: see Known issues "Guard run (run 7)".

### 2026-11-02 06:00 — Step 13: Event stream + JSONL tracing
- Done: pushed the guard commit (owner approval). `core/events.py` (AgentEvent, notify,
  TurnUsage, stream_turn), `core/tracing.py` (JsonlTracer, trace_path, traced); middleware
  notices (rate limit, invalid tool call, protocol guard with discarded usage); `run_turn`
  consumes the stream; `TRACES_DIR`.
- Files: `src/emporio_agent/core/{events,tracing,agent,guards}.py`,
  `src/emporio_agent/config.py`, `tests/{fakes,test_events,test_agent}.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 256 passed (event order, retrieval event,
  artifact notice, reasoning never in any event, rate-limit notice, guard notice + discarded
  usage, friendly error event, JSONL lines without tokens, session id validation, delete).
- Live smoke (Groq, 2 turns, traced): JSONL shows user_message, tool_start/end,
  invalid_tool_call and rate_limit notices (waits of 11 s and 4 s), artifact notice and final
  with usage (7,647 input / 2,304 cached on the 3-call turn).

### 2026-11-02 06:40 — Step 14: CLI with debug mode
- Done: pushed step 13 (owner approval); recorded the expected behaviour for the step 15
  "defeito" routing case. `cli.py` + `emporio-chat` script.
- Files: `src/emporio_agent/cli.py`, `pyproject.toml`, `uv.lock`, `tests/test_cli.py`,
  `PROGRESS.md`.
- Deps: typer 0.27.3, rich 15.0.0.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 264 passed (answer + trace, debug lines,
  tools hidden without debug, /new, --now, invalid options, missing key, error event).
- Live smoke (Groq, `--debug`, temp DB/memory): GD20 PIX question → search_products +
  calculate_payment_options, "R$ 2.089,05 no PIX … até 12x sem juros de R$ 183,25", 7,450
  input tokens (2,560 cached), 3 calls on the 120b, 1.8 s; "Vocês vendem cordas?" → only
  instruments, asks if the customer wants an instrument, no tool call.

### 2026-11-02 08:00 — Step 14 follow-up: fixes from the owner's manual CLI tests
- Done: pushed step 14 (owner approval). Alternatives for unsellable items; `on_promotion`
  filter and promotion descriptions/prompt; `OutputGuard` with protocol + price checks;
  verified-order requirement for order handoffs (+ `return_request`, SQLite table upgrade);
  prompt without example names; CLI session banner and silent package logs.
- Files: `src/emporio_agent/core/guards.py`, `src/emporio_agent/client/{assistant,identity,
  repository,tools}.py`, `src/emporio_agent/client/db/{models,loader}.py`,
  `src/emporio_agent/client/prompts/system.md`, `src/emporio_agent/cli.py`,
  `tests/test_{catalog,guards,agent,events,store_tools,db_loader,cli}.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 293 passed.
- Live re-run: see Known issues "Re-run of the 4 scenarios".

### 2026-11-02 09:00 — Step 15 (prepared): routing and adversarial eval cases and runner
- Done: eval runner, 27 cases, pytest `llm` tests with `--llm-model`/`--eval-pace`, script.
  Not run against the provider (owner instruction).
- Files: `src/emporio_agent/core/evals.py`, `src/emporio_agent/client/assistant.py`,
  `evals/{routing,adversarial}_cases.yaml`, `scripts/run_evals.py`, `tests/conftest.py`,
  `tests/test_llm_evals.py`, `tests/test_evals.py`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 335 passed; `-m llm` collects 27 cases.

### 2026-11-02 09:15 — Step 16: CI workflow
- Done: `.github/workflows/ci.yml` (fast / embeddings / frontend jobs).
- Files: `.github/workflows/ci.yml`, `PROGRESS.md`.
- Tests: YAML parses; `pytest -m "not llm and not embeddings"` 334 passed and `pytest -m
  embeddings` 1 passed locally. Not run on GitHub (no push).

### 2026-11-02 09:40 — Step 18: Frontend scaffold
- Done: Vite React TS app, Tailwind v4 theme, fonts, dev proxy, favicon; placeholder page.
- Files: `frontend/**` (package.json + lock, vite/ts configs, index.html, src/main.tsx,
  src/App.tsx, src/index.css, public/favicon.svg, README.md, .gitignore), `PROGRESS.md`.
- Checks: `npm run build` ok, `npm run lint` ok.

### 2026-11-02 10:30 — Step 19: Customer service chat with streaming (mock API)
- Done: API client (real SSE + mock), demo fixtures export, chat hook and components.
- Files: `frontend/src/{App.tsx,index.css}`, `frontend/src/api/*`, `frontend/src/hooks/useChat.ts`,
  `frontend/src/components/{Header,Monogram,MessageList,Composer,SuggestionChips}.tsx`,
  `frontend/src/mocks/conversations.json`, `scripts/export_demo_fixtures.py`, `PROGRESS.md`.
- Checks: `npm run build` ok, `npm run lint` ok; ruff ok. Mock build checked in headless
  Chrome with `?replay=violao-esgotado`.

### 2026-11-02 11:30 — Step 20: Bastidores panel + simulated date banner (mock API)
- Done: Bastidores console panel (side column / phone drawer), toggle, simulated-date banner,
  phone header layout, replay date + recorded duration; screenshots for review.
- Files: `frontend/src/App.tsx`, `frontend/src/index.css`,
  `frontend/src/components/{BackstagePanel,SimulatedDateBanner,Header}.tsx`,
  `frontend/src/api/{mock,index}.ts`, `PROGRESS.md`.
- Checks: `npm run build` ok, `npm run lint` ok; mock build verified in headless Chrome
  (desktop and 390 px phone), screenshots in `frontend/screenshots/`.
- Stopped here (owner instruction): step 17 and 21 not started; nothing pushed.

### 2026-11-02 10:20 — Refusal tone, third-party order case, demo chip typo (owner review)
- Done: prompt refusal rule; `persona_refusal` eval check + cases; chip typo fixed at the
  export script; eval script token/429 totals.
- Files: `src/emporio_agent/client/prompts/system.md`, `src/emporio_agent/core/evals.py`,
  `evals/adversarial_cases.yaml`, `scripts/{run_evals,export_demo_fixtures}.py`,
  `frontend/src/mocks/conversations.json`, `tests/test_evals.py`, `.gitignore`, `PROGRESS.md`.
- Tests: ruff ok, format ok, `pytest -m "not llm"` 342 passed; `npm run build` ok.
