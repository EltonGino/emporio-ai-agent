"""The HTTP API with a scripted model (no LLM): real tools, real SQLite files in tmp_path."""

import json
from datetime import datetime, timedelta

import pytest
from fakes import ScriptedModel, call
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from emporio_agent.api.main import create_app
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.db.models import Handoff
from emporio_agent.client.identity import MAX_FAILED_LOOKUPS, SqlVerifiedOrders
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import FixedClock
from emporio_agent.config import Settings, get_client_config
from emporio_agent.core.agent import build_agent
from emporio_agent.core.sessions import (
    SqlFailedLookups,
    create_session_tables,
    sessions,
    utcnow,
)

CLIENT = get_client_config()
NOW = datetime(2026, 3, 25, 10, 30, tzinfo=CLIENT.store.tz)


@pytest.fixture
def make_api(tmp_path, policy_retriever):
    """An API over a scripted model; returns (TestClient, model, settings)."""

    def make(*script, **overrides):
        defaults = {
            "database_url": f"sqlite:///{tmp_path}/store.db",
            "checkpoint_db": tmp_path / "checkpoints.db",
            "demo_mode": True,
            "agent_now": NOW,
        }
        settings = Settings(**(defaults | overrides))
        model = ScriptedModel(script=list(script))

        def builder(settings, client, checkpointer, failed_lookups, verified_orders):
            repo = SqlStoreRepository(make_engine(settings.database_url))
            tools = build_tools(
                repo,
                client,
                FixedClock(NOW),
                policy_retriever,
                failed_lookups=failed_lookups,
                verified_orders=verified_orders,
            )
            return build_agent([model], tools, "S", lambda: "C", checkpointer, 20, 2500)

        app = create_app(
            settings,
            CLIENT,
            builder,
            traces_dir=tmp_path / "traces",
            frontend_dist=tmp_path / "dist",
        )
        return TestClient(app), model, settings

    return make


def sse_events(response) -> list[dict]:
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in response.text.split("\n\n") if b.startswith("data: ")]
    return [json.loads(b.removeprefix("data: ")) for b in blocks]


def chat(client, message="oi", session_id="s1"):
    return client.post("/api/chat", json={"session_id": session_id, "message": message})


GD20 = call("search_products", {"query": "takamine gd20"})
ANSWER = AIMessage("O GD20 sai por R$ 2.199,00.")


def test_health_and_config(make_api):
    client, _, _ = make_api()
    with client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/config").json() == {
            "assistant_name": "Tom",
            "store_name": "Empório da Música",
            "slogan": "Sua música começa aqui.",
            "demo_mode": True,
            "simulated_now": "2026-03-25T10:30",
        }


def test_chat_streams_agent_events_in_demo_mode(make_api, tmp_path):
    client, _, _ = make_api(GD20, ANSWER)
    with client:
        events = sse_events(chat(client, "quanto custa o GD20?"))
    types = [e["type"] for e in events]
    assert types[:3] == ["user_message", "tool_start", "tool_end"] and types[-1] == "final"
    assert "token" in types
    assert events[-1]["data"]["text"] == ANSWER.content
    assert all(e["session_id"] == "s1" and e["at"] for e in events)
    trace = (tmp_path / "traces" / "s1.jsonl").read_text().splitlines()
    assert [json.loads(line)["type"] for line in trace][-1] == "final"


def test_outside_demo_mode_only_customer_events_are_sent(make_api, tmp_path):
    client, _, _ = make_api(GD20, ANSWER, demo_mode=False)
    with client:
        assert client.get("/api/config").json()["demo_mode"] is False
        types = {e["type"] for e in sse_events(chat(client, "quanto custa o GD20?"))}
    assert types == {"user_message", "token", "final"}  # no tool_*, retrieval or notice
    trace = (tmp_path / "traces" / "s1.jsonl").read_text()
    assert '"tool_start"' in trace  # the server-side trace still has everything


def test_error_detail_never_leaves_the_server(make_api, tmp_path):
    client, _, _ = make_api(RuntimeError("segredo interno"))
    with client:
        error = sse_events(chat(client))[-1]
    assert error["type"] == "error" and "detail" not in error["data"]
    assert "problema técnico" in error["data"]["message"]
    assert "segredo interno" in (tmp_path / "traces" / "s1.jsonl").read_text()


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "../etc", "message": "oi"},
        {"session_id": "s1", "message": "   "},
        {"session_id": "s1", "message": "x" * 1001},
    ],
)
def test_invalid_requests(make_api, body):
    client, _, _ = make_api()
    with client:
        assert client.post("/api/chat", json=body).status_code == 422


def test_per_session_limit(make_api):
    client, model, _ = make_api(AIMessage("1"), AIMessage("2"), max_messages_per_session=2)
    with client:
        chat(client)
        chat(client)
        blocked = chat(client)
        assert blocked.status_code == 429
        assert "Nova conversa" in blocked.json()["detail"]
    assert len(model.seen) == 2  # the blocked message never reached the model


def test_daily_cap_across_sessions(make_api):
    client, _, _ = make_api(AIMessage("1"), AIMessage("2"), daily_message_cap=2)
    with client:
        assert chat(client, session_id="a").status_code == 200
        assert chat(client, session_id="b").status_code == 200
        blocked = chat(client, session_id="c")
    assert blocked.status_code == 429 and "hoje" in blocked.json()["detail"]


def test_rejected_message_is_not_counted(make_api, tmp_path):
    client, _, _ = make_api(AIMessage("1"), max_messages_per_session=5, daily_message_cap=1)
    with client:
        chat(client, session_id="a")
        assert chat(client, session_id="b").status_code == 429
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    with engine.connect() as conn:
        rows = conn.execute(select(sessions.c.session_id, sessions.c.message_count)).all()
        counts = {row.session_id: row.message_count for row in rows}
    assert counts.get("b", 0) == 0  # rolled back with the daily cap


def test_delete_session_forgets_everything_and_anonymizes_handoffs(make_api, tmp_path):
    handoff = call(
        "handoff_to_human",
        {"reason": "complaint", "summary": "Cliente reclama.", "contact": "(67) 99999-0000"},
    )
    client, model, _ = make_api(
        handoff, AIMessage("Registrei."), AIMessage("Oi de novo, conversa nova.")
    )
    with client:
        chat(client, "quero reclamar")
        assert client.delete("/api/sessions/s1").status_code == 204
        assert client.delete("/api/sessions/s1").status_code == 204  # idempotent
        chat(client, "oi")  # same id after deletion: the old conversation is gone
    humans = [m.content for m in model.seen[-1] if m.type == "human"]
    assert humans == ["oi"]
    trace = (tmp_path / "traces" / "s1.jsonl").read_text()
    assert "quero reclamar" not in trace
    with Session(make_engine(f"sqlite:///{tmp_path}/store.db")) as db:
        row = db.scalars(select(Handoff)).one()
    assert row.protocol.startswith("EMP-") and row.reason == "complaint"
    assert (row.summary, row.contact, row.session_id) == (None, None, None)


def test_invalid_session_id_on_delete(make_api):
    client, _, _ = make_api()
    with client:
        assert client.delete("/api/sessions/a%20b").status_code == 422


def test_retention_cleanup_at_startup(make_api, tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    ensure_store_data(engine)
    create_session_tables(engine)
    old = utcnow() - timedelta(days=31)
    with engine.begin() as conn:
        for session_id, seen in (("old", old), ("recent", utcnow())):
            conn.execute(
                insert(sessions).values(
                    session_id=session_id, created_at=seen, last_activity=seen, message_count=1
                )
            )
    SqlStoreRepository(engine).create_handoff("other", "Resumo antigo.", "x@y.z", "old", NOW)
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "old.jsonl").write_text("{}\n")

    client, _, _ = make_api()
    with client:
        pass  # startup runs the cleanup
    with engine.connect() as conn:
        remaining = {row.session_id for row in conn.execute(select(sessions.c.session_id))}
    assert remaining == {"recent"}
    assert not (tmp_path / "traces" / "old.jsonl").exists()
    with Session(engine) as db:
        assert db.scalars(select(Handoff)).one().summary is None


def test_identity_state_is_in_the_database(tmp_path):
    """Owner decision: the lockout counter and verified orders survive restarts/replicas."""
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    ensure_store_data(engine)
    create_session_tables(engine)
    first = SqlFailedLookups(engine, MAX_FAILED_LOOKUPS)
    for _ in range(MAX_FAILED_LOOKUPS):
        first.record_failure("s1")
    assert SqlFailedLookups(engine, MAX_FAILED_LOOKUPS).locked("s1")  # a new instance
    assert not SqlFailedLookups(engine, MAX_FAILED_LOOKUPS).locked("s2")
    SqlVerifiedOrders(engine).record("s1", 8)
    SqlVerifiedOrders(engine).record("s1", 8)  # idempotent
    assert SqlVerifiedOrders(engine).verified("s1", 8)
    assert not SqlVerifiedOrders(engine).verified("s2", 8)


def test_sqlite_connections_use_wal_and_busy_timeout(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_serves_the_built_frontend(make_api, tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<h1>Empório</h1>")
    client, _, _ = make_api()
    with client:
        assert "Empório" in client.get("/").text
        assert client.get("/api/health").json() == {"status": "ok"}  # API still wins
