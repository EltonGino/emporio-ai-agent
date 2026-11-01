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

Step 9 — `feat(tools): payment rules` (+ tests) (AGENTS.md §7 `calculate_payment_options`,
§14 boundaries R$ 240 → 3x, R$ 480 → 6x, R$ 1.200 → 12x). Pure functions in
`client/rules.py` (already holds availability/discount/format_brl); replace
`profiling._max_installments` with the rules function; add the tool to `build_tools`; add the
payment summary to `get_product_details` (deferred from step 8).

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
| 8 | Catalog and promotion tools | done | `feat(tools): catalog and promotion tools` |
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
- **Step 8** — Tools are created by `build_tools(repo)` (closures over the repository) with
  pydantic `args_schema`s; later steps extend it. Deps: `rapidfuzz`, `langchain-core` (the rest
  of LangChain arrives with the agent in step 12).
- **Convention** — The "Commit" column holds the commit subject; hashes are in `git log`
  (a commit cannot contain its own hash).

## Known issues / TODO

<!-- Open bugs, flaky tests, things to revisit. -->

- 8K TPM per model may still be tight for multi-tool turns without cache hits — measure in
  step 12 with the owner's key; recalibrate `DAILY_MESSAGE_CAP`.
- The persona prompt path in `client.yaml` points to a file created in step 12.
- **E1 (new data finding, not in AGENTS.md §5.3):** product 130 is `coming_soon`, but appears in
  order 3 (delivered, 2 units) and order 14 (pending). Handling: status wins for new sales
  (D1); past orders are reported as they are. Detected by the profiler; add to README (step 23).
- Step 9: replace `profiling._max_installments` with the `rules.py` installment function.
- The 1 GB embedding model must be available in CI (step 7's hit@3 ≥ 0.85 pytest needs it) and
  in Docker (step 21): cache it (actions/cache on `data/runtime/models`) / bake it into the
  image or a volume. CI: two jobs (see "CI split" decision); Docker: decide in step 21.
- Chunks are verbatim: chunk 1.2 contains the accented email (P2) and chunk 7 the other
  WhatsApp number (P1). Steps 11/12: contacts must come from `client.yaml` (store status tool /
  prompt), and the prompt must say config contacts win over retrieved text.

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
