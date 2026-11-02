"""FastAPI app (AGENTS.md §11): health, config, chat as SSE, session deletion (LGPD).

Async end to end (async model calls, async checkpointer, async session store) and no state in
process memory: sessions, limits, identity failures and verified orders live in the database,
so several replicas can run behind a load balancer (with Postgres) without code changes. The
only per-process objects are the compiled agent and DB engines, which hold no conversation
state.

    uv run emporio-api            # or: uv run uvicorn emporio_agent.api.main:app
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from emporio_agent.api.schemas import SESSION_ID_PATTERN, ChatRequest, ConfigResponse
from emporio_agent.client.assistant import build_store_agent
from emporio_agent.client.db.engine import make_async_engine, make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.identity import (
    MAX_FAILED_LOOKUPS,
    FailedLookupStore,
    SqlVerifiedOrders,
    VerifiedOrderStore,
)
from emporio_agent.client.product_cards import product_cards
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import HolidayCalendar, store_status
from emporio_agent.client.tools import store_status_data
from emporio_agent.clock import SystemClock, get_clock, to_store_time
from emporio_agent.config import (
    PROJECT_ROOT,
    TRACES_DIR,
    ClientConfig,
    Settings,
    get_client_config,
    get_settings,
)
from emporio_agent.core.agent import CHECKPOINT_PRAGMAS
from emporio_agent.core.events import CUSTOMER_EVENTS, AgentEvent, astream_turn
from emporio_agent.core.sessions import (
    AsyncSessionStore,
    SqlFailedLookups,
    create_session_tables,
    utcnow,
)
from emporio_agent.core.tracing import JsonlTracer

logger = logging.getLogger(__name__)

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite dev server
LIMIT_MESSAGES = {
    "session_limit": (
        "Esta conversa chegou ao limite de mensagens da demonstração. "
        "Clique em “Nova conversa” para continuar 😊"
    ),
    "client_limit": (
        "Você já enviou muitas mensagens hoje nesta demonstração. "
        "Volte amanhã, vai ser um prazer te atender! 🎶"
    ),
    "daily_cap": (
        "O atendimento de demonstração atingiu o limite de mensagens de hoje. "
        "Volte amanhã, vai ser um prazer te atender! 🎶"
    ),
}

AgentBuilder = Callable[
    [Settings, ClientConfig, BaseCheckpointSaver, FailedLookupStore, VerifiedOrderStore], Any
]


def default_agent_builder(
    settings: Settings,
    client: ClientConfig,
    checkpointer: BaseCheckpointSaver,
    failed_lookups: FailedLookupStore,
    verified_orders: VerifiedOrderStore,
):
    """The real agent: hybrid policy search (builds the index on first start) and Groq."""
    from emporio_agent.core.retrieval.index import get_retrievers

    return build_store_agent(
        settings,
        client,
        get_clock(),
        get_retrievers()[2],
        checkpointer,
        failed_lookups=failed_lookups,
        verified_orders=verified_orders,
    )


@dataclass
class ApiState:
    settings: Settings
    client: ClientConfig
    agent: Any
    checkpointer: AsyncSqliteSaver
    sessions: AsyncSessionStore
    repo: SqlStoreRepository
    tracer: JsonlTracer


async def forget_session(state: ApiState, session_id: str) -> None:
    """LGPD deletion: the conversation (checkpoints), its trace, the session row; handoffs are
    anonymized, not deleted (owner decision: they stay as the team's operational record)."""
    await state.checkpointer.adelete_thread(session_id)
    await asyncio.to_thread(state.tracer.delete, session_id)
    await asyncio.to_thread(state.repo.forget_session, session_id)
    await state.sessions.delete(session_id)


async def cleanup_expired_sessions(state: ApiState, now: datetime | None = None) -> int:
    """Sessions idle for more than SESSION_RETENTION_DAYS (wall-clock time) are forgotten."""
    before = (now or utcnow()) - timedelta(days=state.settings.session_retention_days)
    expired = await state.sessions.expired(before)
    for session_id in expired:
        await forget_session(state, session_id)
    return len(expired)


@asynccontextmanager
async def open_runtime(
    settings: Settings,
    client: ClientConfig,
    traces_dir: Path,
    agent_builder: AgentBuilder | None = None,
) -> AsyncIterator[ApiState]:
    """Databases, checkpointer, trace store and (if a builder is given) the agent. Used by the
    API lifespan and by scripts/cleanup_sessions.py."""
    engine = make_engine(settings.database_url)
    await asyncio.to_thread(ensure_store_data, engine)  # build the store DB if missing
    await asyncio.to_thread(create_session_tables, engine)
    async_engine = make_async_engine(settings.database_url)
    settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
            for pragma in CHECKPOINT_PRAGMAS:
                await saver.conn.execute(pragma)
            await saver.setup()  # tables exist before the first delete/cleanup
            agent = None
            if agent_builder is not None:
                failed_lookups = SqlFailedLookups(
                    engine,
                    MAX_FAILED_LOOKUPS,
                    client_limit=settings.ip_identity_failure_limit,
                    today=lambda: SystemClock(client.store.tz).now().date(),
                )
                agent = await asyncio.to_thread(
                    agent_builder,
                    settings,
                    client,
                    saver,
                    failed_lookups,
                    SqlVerifiedOrders(engine),
                )
            yield ApiState(
                settings,
                client,
                agent,
                saver,
                AsyncSessionStore(async_engine),
                SqlStoreRepository(engine),
                JsonlTracer(traces_dir),
            )
    finally:
        await async_engine.dispose()
        engine.dispose()


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The caller's IP. X-Forwarded-For is read only when the app is explicitly configured to
    sit behind N trusted proxies; then the N-th entry from the right is the address our own
    proxy saw (entries to its left are whatever the client sent, and can be forged)."""
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    forwarded = [
        part.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for part in header.split(",")
        if part.strip()
    ]
    return forwarded[-trusted_proxy_hops] if len(forwarded) >= trusted_proxy_hops else peer


def client_key(ip: str, salt: str) -> str:
    """Opaque per-client key: IPs are personal data (LGPD), so only a salted hash is stored."""
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:32]


def for_client(event: AgentEvent, demo_mode: bool) -> AgentEvent | None:
    """What may leave the server: outside demo mode only customer events (no tool calls,
    retrieved sections or notices), and never an error's technical detail."""
    if not demo_mode and event.type not in CUSTOMER_EVENTS:
        return None
    if event.type == "error":
        return event.model_copy(update={"data": {"message": event.data.get("message")}})
    return event


def simulated_now(settings: Settings, client: ClientConfig) -> str | None:
    if settings.agent_now is None:
        return None
    return f"{to_store_time(settings.agent_now, client.store.tz):%Y-%m-%dT%H:%M}"


def create_app(
    settings: Settings | None = None,
    client: ClientConfig | None = None,
    agent_builder: AgentBuilder = default_agent_builder,
    traces_dir: Path = TRACES_DIR,
    frontend_dist: Path = FRONTEND_DIST,
) -> FastAPI:
    settings = settings or get_settings()
    client = client or get_client_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_runtime(settings, client, traces_dir, agent_builder) as state:
            app.state.api = state
            removed = await cleanup_expired_sessions(state)
            logger.info(
                "API ready (demo_mode=%s); %d expired sessions removed", settings.demo_mode, removed
            )
            yield

    app = FastAPI(title="Empório da Música — atendimento", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    async def config() -> ConfigResponse:
        return ConfigResponse(
            assistant_name=client.assistant.name,
            store_name=client.store.name,
            slogan=client.store.slogan,
            demo_mode=settings.demo_mode,
            simulated_now=simulated_now(settings, client),
            store_status=store_status_data(
                store_status(
                    (
                        to_store_time(settings.agent_now, client.store.tz)
                        if settings.agent_now
                        else SystemClock(client.store.tz).now()
                    ),
                    client.store,
                    HolidayCalendar(client.store.holidays),
                )
            ),
        )

    @app.post("/api/chat", response_model=None)
    async def chat(body: ChatRequest, request: Request) -> Response:
        state: ApiState = request.app.state.api
        # Demo caps use the real store-local day, not AGENT_NOW (a frozen clock never resets).
        day = SystemClock(client.store.tz).now().date()
        key = client_key(client_ip(request, settings.trusted_proxy_hops), settings.ip_hash_salt)
        admission = await state.sessions.admit(
            body.session_id,
            utcnow(),
            day,
            settings.max_messages_per_session,
            settings.daily_message_cap,
            client_key=key,
            client_cap=settings.ip_daily_message_cap,
        )
        if not admission.allowed:
            return JSONResponse({"detail": LIMIT_MESSAGES[admission.reason]}, status_code=429)
        return StreamingResponse(
            _sse(state, body.session_id, body.message, key),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(
        request: Request, session_id: str = PathParam(pattern=SESSION_ID_PATTERN)
    ) -> Response:
        """Idempotent: 204 whether or not the session existed (no session enumeration)."""
        await forget_session(request.app.state.api, session_id)
        return Response(status_code=204)

    if frontend_dist.is_dir():  # step 21 builds it; API routes above take precedence
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


async def _sse(state: ApiState, session_id: str, message: str, key: str) -> AsyncIterator[str]:
    # The client key reaches the tools through the run config (per-IP identity lockout).
    async for event in astream_turn(
        state.agent,
        session_id,
        message,
        context={"client_key": key},
        product_projector=product_cards,
    ):
        await asyncio.to_thread(state.tracer.write, event)  # the trace keeps everything
        if (visible := for_client(event, state.settings.demo_mode)) is not None:
            yield f"data: {visible.model_dump_json()}\n\n"


app = create_app()


def run() -> None:
    """Console script `emporio-api`: uvicorn on 127.0.0.1:8000 (the Vite dev proxy target).
    `proxy_headers=False`: uvicorn would otherwise trust X-Forwarded-For from localhost on its
    own; the app decides with TRUSTED_PROXY_HOPS (run uvicorn directly with
    `--no-proxy-headers` too)."""
    import uvicorn

    uvicorn.run("emporio_agent.api.main:app", host="127.0.0.1", port=8000, proxy_headers=False)
