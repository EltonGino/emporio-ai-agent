"""The HTTP API with a scripted model (no LLM): real tools, real SQLite files in tmp_path."""

import json
from datetime import datetime, timedelta

import pytest
from fakes import ScriptedModel, call
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from emporio_agent.api.main import LIMIT_MESSAGES, create_app
from emporio_agent.client.assistant import output_guard
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import ensure_store_data
from emporio_agent.client.db.models import Handoff
from emporio_agent.client.identity import MAX_FAILED_LOOKUPS, SqlVerifiedOrders
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.tools import build_tools
from emporio_agent.clock import FixedClock
from emporio_agent.config import Settings, get_client_config
from emporio_agent.core.agent import build_agent
from emporio_agent.core.evals import EMOJI
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
            return build_agent(
                [model],
                tools,
                "S",
                lambda messages: "C",
                checkpointer,
                20,
                2500,
                guards=[output_guard()],
            )

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
            "store_status": {
                "now": "quarta-feira, 25/03/2026 às 10:30",
                "is_open": True,
                "today_hours": "09:00–18:00",
                "closes_at": "18:00",
            },
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
    assert types == {"user_message", "token", "final", "products"}  # no tool_*, retrieval or notice
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
    assert not EMOJI.search(blocked.json()["detail"])  # it may come right after a complaint


@pytest.mark.parametrize("kind", sorted(LIMIT_MESSAGES))
def test_limit_messages_have_no_emoji(kind):
    assert not EMOJI.search(LIMIT_MESSAGES[kind])


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


def test_delete_session_unbinds_its_customer(make_api, tmp_path):
    lookup = call("get_order_status", {"order_id": 8, "email": "anacarol.ferreira@coldmail.com"})
    client, _, _ = make_api(lookup, AIMessage("Seu pedido 8 foi enviado."))
    with client:
        chat(client, "pedido 8")
        engine = make_engine(f"sqlite:///{tmp_path}/store.db")
        assert SqlVerifiedOrders(engine).customer("s1") == 2  # bound to Ana (customer 2)
        assert client.delete("/api/sessions/s1").status_code == 204
    assert SqlVerifiedOrders(engine).customer("s1") is None
    assert not SqlVerifiedOrders(engine).verified("s1", 8)


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
    # The session's customer lives in the DB too (shared by replicas, survives restarts).
    assert SqlVerifiedOrders(engine).bind("s1", 2) == 2
    assert SqlVerifiedOrders(engine).bind("s1", 6) == 2
    assert SqlVerifiedOrders(engine).customer("s1") == 2
    assert SqlVerifiedOrders(engine).customer("s2") is None


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


# --- per-IP limits and real client IP (security) ---------------------------------------------

from datetime import date  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from starlette.datastructures import Headers  # noqa: E402

from emporio_agent.api.main import client_ip, client_key  # noqa: E402
from emporio_agent.client import identity  # noqa: E402
from emporio_agent.client.identity import FailedLookups  # noqa: E402


def fake_request(peer: str, *forwarded: str):
    headers = Headers(raw=[(b"x-forwarded-for", f.encode()) for f in forwarded])
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


@pytest.mark.parametrize(
    ("hops", "forwarded", "expected"),
    [
        (0, ["6.6.6.6"], "10.0.0.1"),  # no trusted proxy: a forged header is ignored
        (1, ["6.6.6.6, 203.0.113.7"], "203.0.113.7"),  # our proxy appended the real one
        (1, ["6.6.6.6", "203.0.113.7"], "203.0.113.7"),  # repeated headers count as one list
        (2, ["6.6.6.6, 203.0.113.7, 10.1.1.1"], "203.0.113.7"),
        (1, [], "10.0.0.1"),  # header missing: fall back to the peer
    ],
)
def test_client_ip_trusts_x_forwarded_for_only_behind_a_configured_proxy(hops, forwarded, expected):
    assert client_ip(fake_request("10.0.0.1", *forwarded), hops) == expected


def test_client_key_is_a_salted_hash():
    key = client_key("203.0.113.7", "salt")
    assert key == client_key("203.0.113.7", "salt") and "203" not in key and len(key) == 32
    assert key != client_key("203.0.113.7", "other-salt")


def test_per_ip_daily_limit_across_sessions(make_api):
    client, _, _ = make_api(AIMessage("1"), AIMessage("2"), ip_daily_message_cap=2)
    with client:
        assert chat(client, session_id="a").status_code == 200
        assert chat(client, session_id="b").status_code == 200  # a new session doesn't help
        blocked = chat(client, session_id="c")
    assert blocked.status_code == 429 and "muitas mensagens hoje" in blocked.json()["detail"]


def test_forged_x_forwarded_for_does_not_bypass_the_ip_limit(make_api):
    client, _, _ = make_api(AIMessage("1"), ip_daily_message_cap=1)  # TRUSTED_PROXY_HOPS=0
    with client:
        body = {"session_id": "a", "message": "oi"}
        assert client.post(
            "/api/chat", json=body, headers={"X-Forwarded-For": "1.1.1.1"}
        ).is_success
        body = {"session_id": "b", "message": "oi"}
        second = client.post("/api/chat", json=body, headers={"X-Forwarded-For": "2.2.2.2"})
    assert second.status_code == 429


def test_behind_a_trusted_proxy_each_client_ip_has_its_own_limit(make_api):
    client, _, _ = make_api(
        AIMessage("1"), AIMessage("2"), ip_daily_message_cap=1, trusted_proxy_hops=1
    )
    with client:
        for session_id, ip in (("a", "203.0.113.7"), ("b", "198.51.100.9")):
            body = {"session_id": session_id, "message": "oi"}
            assert client.post("/api/chat", json=body, headers={"X-Forwarded-For": ip}).is_success
        body = {"session_id": "c", "message": "oi"}
        again = client.post("/api/chat", json=body, headers={"X-Forwarded-For": "203.0.113.7"})
    assert again.status_code == 429


def test_identity_lockout_counts_per_ip_across_new_sessions(make_api):
    """Before: 5 failures per session, bypassed by opening a new session."""
    wrong = call("get_order_status", {"order_id": 8, "email": "x@y.z"})
    script = []
    for _ in range(3):
        script += [wrong, AIMessage("Não encontrei.")]
    client, _, _ = make_api(*script, ip_identity_failure_limit=2)
    with client:
        results = []
        for session_id in ("a", "b", "c"):  # one failure per session, all from the same IP
            events = sse_events(chat(client, "pedido 8", session_id=session_id))
            results += [e["data"]["result_preview"] for e in events if e["type"] == "tool_end"]
    assert "order_not_found" in results[0] and "order_not_found" in results[1]
    assert "too_many_attempts" in results[2]  # locked by IP, although "c" is a new session


def test_sql_lockout_per_client_resets_the_next_day(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    ensure_store_data(engine)
    create_session_tables(engine)
    day = {"today": date(2026, 3, 25)}
    lookups = SqlFailedLookups(engine, 5, client_limit=2, today=lambda: day["today"])
    lookups.record_failure("s1", "ip-a")
    lookups.record_failure("s2", "ip-a")
    assert lookups.locked("s3", "ip-a") and not lookups.locked("s3", "ip-b")
    day["today"] = date(2026, 3, 26)
    assert not lookups.locked("s3", "ip-a")  # a shared IP isn't locked forever


def test_in_memory_lockout_per_client():
    lookups = FailedLookups(limit=5, client_limit=2)
    lookups.record_failure("s1", "ip")
    lookups.record_failure("s2", "ip")
    assert lookups.locked("s3", "ip") and not lookups.locked("s3", None)
    assert identity.client_key({"configurable": {"client_key": "k"}}) == "k"
    assert identity.client_key(None) is None


@pytest.mark.parametrize(
    "now,is_open,next_day",
    [
        (datetime(2026, 3, 29, 10), False, "30/03/2026"),
        (datetime(2026, 11, 2, 10), False, "03/11/2026"),
        (datetime(2026, 3, 28, 13, 1), False, "30/03/2026"),
    ],
)
def test_config_uses_store_clock_and_calendar(make_api, now, is_open, next_day):
    client, _, _ = make_api(agent_now=now)
    with client:
        status = client.get("/api/config").json()["store_status"]
    assert status["is_open"] is is_open
    assert next_day in status["next_opening"]


def test_product_cards_only_public_tool_fields_and_mentioned_products(make_api):
    client, _, _ = make_api(
        call("list_active_promotions", {}), AIMessage("Temos o Ohana CK-20."), demo_mode=False
    )
    with client:
        events = sse_events(chat(client))
    cards = next(e["data"]["products"] for e in events if e["type"] == "products")
    assert len(cards) == 1 and "CK-20" in cards[0]["name"]
    assert set(cards[0]) == {"id", "name", "price_text", "availability", "promo"}
    assert set(cards[0]["promo"]) == {"percent", "final_price_text"}


def test_cards_follow_corrected_final_answer_and_do_not_leak_previous_turn(make_api):
    client, _, _ = make_api(
        GD20,
        AIMessage("GD20 por R$ 999.999,00"),
        AIMessage("Quer ajuda com um instrumento?"),
        AIMessage("O Takamine GD20 é uma opção."),
        demo_mode=False,
    )
    with client:
        first = sse_events(chat(client))
        second = sse_events(chat(client, "e agora?"))
    assert all(e["type"] != "products" for e in first + second)
    assert "999.999" not in str(first)


def test_product_card_pix_comes_from_tool(make_api):
    client, _, _ = make_api(GD20, ANSWER, demo_mode=False)
    with client:
        events = sse_events(chat(client))
    product_event = next(e for e in events if e["type"] == "products")
    card = product_event["data"]["products"][0]
    assert card["pix_price_text"] == "R$ 2.089,05"
    assert card["price_text"] == "R$ 2.199,00"
    assert events.index(product_event) > max(
        i for i, e in enumerate(events) if e["type"] == "user_message"
    )
