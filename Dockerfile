# syntax=docker/dockerfile:1
# Empório da Música — customer service agent: FastAPI + the built frontend, one image.
#
#   docker compose up --build        (reads .env: GROQ_API_KEY, LLM_*, AGENT_NOW, DEMO_MODE…)
#
# The embedding model (~1 GB) and the policy index are baked in at build time, so a container
# starts without downloading anything. Databases and traces live in volumes (see compose).

# --- 1. frontend ---------------------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. application ------------------------------------------------------------------------
FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

# Non-root (uid 1000, as Hugging Face Spaces expects). Created first so every file is owned
# by it from the start: a later `chown -R` would copy the 1 GB model into another layer.
RUN useradd --uid 1000 --create-home app && mkdir /app && chown app:app /app
WORKDIR /app
USER app

# Dependencies first (cached layer), then the project itself. The uv cache is a BuildKit
# cache mount, so it never ends up in the image.
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 uv sync --frozen --no-dev --no-install-project
COPY --chown=app:app src ./src
COPY --chown=app:app config ./config
COPY --chown=app:app data/raw ./data/raw
COPY --chown=app:app scripts ./scripts
RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000 uv sync --frozen --no-dev

# Bake the embedding model and the policy index (chunks + vectors) into the image; create the
# runtime dirs the volumes mount on (DB, traces).
RUN python scripts/build_index.py && mkdir -p data/runtime/db data/runtime/traces

COPY --chown=app:app --from=frontend /frontend/dist ./frontend/dist

ENV DATABASE_URL=sqlite:////app/data/runtime/db/store.db \
    CHECKPOINT_DB=/app/data/runtime/db/checkpoints.db \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/health', timeout=4)"

# --no-proxy-headers: the app itself decides whether X-Forwarded-For is trusted
# (TRUSTED_PROXY_HOPS), so uvicorn must not rewrite the client address on its own.
CMD ["sh", "-c", "exec uvicorn emporio_agent.api.main:app --host 0.0.0.0 --port ${PORT} --no-proxy-headers"]
