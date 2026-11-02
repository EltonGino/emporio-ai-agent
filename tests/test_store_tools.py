import json
import re
from datetime import date, datetime

import pytest
from langchain_core.messages import ToolCall
from sqlalchemy import select
from sqlalchemy.orm import Session

from emporio_agent.client import identity
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data
from emporio_agent.client.db.models import Handoff
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import HolidayCalendar, store_status
from emporio_agent.client.tools import br_datetime, build_tools
from emporio_agent.clock import FixedClock
from emporio_agent.config import HolidaysConfig, get_client_config

CLIENT = get_client_config()
STORE = CLIENT.store
CALENDAR = HolidayCalendar(STORE.holidays)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=STORE.tz)


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    load_store_data(engine)
    return engine


@pytest.fixture
def make_tools(engine, policy_retriever):
    """Tools at a simulated time. Orders 7, 8 and 9 count as already verified (number + email)
    in sessions s1 and s2, as if get_order_status had succeeded there."""
    verified = identity.VerifiedOrders()
    for name in ("s1", "s2"):
        for order_id in (7, 8, 9):
            verified.record(name, order_id)

    def make(now: str = "2026-03-25T10:30"):
        repo = SqlStoreRepository(engine)
        clock = FixedClock(at(now))
        tools = build_tools(repo, CLIENT, clock, policy_retriever, verified_orders=verified)
        return {t.name: t for t in tools}

    return make


def session(name: str = "s1") -> dict:
    return {"configurable": {"thread_id": name}}


# --- store hours (manual §2) ----------------------------------------------------------------


def test_open_on_a_weekday():
    status = store_status(at("2026-03-25T10:30"), STORE, CALENDAR)
    assert status.is_open and status.closes_at == at("2026-03-25T18:00")


def test_sunday_is_closed_until_monday():
    status = store_status(at("2026-03-29T11:00"), STORE, CALENDAR)
    assert not status.is_open and status.today_hours is None
    assert status.next_opening == at("2026-03-30T09:00")


@pytest.mark.parametrize(("time", "is_open"), [("12:59", True), ("13:00", False), ("13:01", False)])
def test_saturday_closes_at_13(time, is_open):
    status = store_status(at(f"2026-03-28T{time}"), STORE, CALENDAR)
    assert status.is_open is is_open
    if not is_open:
        assert status.next_opening == at("2026-03-30T09:00")


def test_before_opening_opens_later_today():
    status = store_status(at("2026-03-25T08:15"), STORE, CALENDAR)
    assert not status.is_open and status.next_opening == at("2026-03-25T09:00")


def test_national_holiday_finados_is_closed():
    status = store_status(at("2026-11-02T10:00"), STORE, CALENDAR)
    assert not status.is_open and status.holiday == "Finados"
    assert status.next_opening == at("2026-11-03T09:00")


def test_state_holiday_ms():
    assert CALENDAR.name(date(2027, 10, 11)) == "Criação do Estado"  # a Monday in 2027


def test_good_friday_pushes_next_opening_to_saturday():
    status = store_status(at("2026-04-02T19:00"), STORE, CALENDAR)
    assert status.next_opening == at("2026-04-04T09:00")


def test_municipal_holiday_from_config():
    calendar = HolidayCalendar(HolidaysConfig(country="BR", subdiv="MS", extra=["2026-08-26"]))
    assert not store_status(at("2026-08-26T10:00"), STORE, calendar).is_open


def test_br_datetime():
    assert br_datetime(at("2026-03-25T10:30")) == "quarta-feira, 25/03/2026 às 10:30"
    assert br_datetime(at("2026-03-28T09:00")) == "sábado, 28/03/2026 às 09:00"


def test_store_status_tool(make_tools):
    result = json.loads(make_tools("2026-03-29T11:00")["get_store_status"].invoke({}))
    assert result["is_open"] is False
    assert result["next_opening"] == "segunda-feira, 30/03/2026 às 09:00"
    assert result["weekly_hours"]["sábado"] == "09:00–13:00"
    assert result["weekly_hours"]["domingo"] == "fechado"


# --- policy search -------------------------------------------------------------------------


def test_search_policies_returns_sections_and_artifact(make_tools):
    tool = make_tools()["search_policies"]
    call = ToolCall(
        name="search_policies", args={"query": "boquilha troca"}, id="1", type="tool_call"
    )
    message = tool.invoke(call)
    results = json.loads(message.content)["results"]
    assert 1 <= len(results) <= CLIENT.retrieval.top_k
    assert results[0]["section"].endswith("4.4 Itens Não Elegíveis para Troca")
    assert message.artifact[0]["section_id"] == "4.4"  # scores go to tracing, not the model
    assert {"score", "matched_by", "page"} <= set(message.artifact[0])
    assert "score" not in message.content


def test_search_policies_description_asks_for_rewritten_query(make_tools):
    description = make_tools()["search_policies"].description
    assert "self-contained" in description and "Dourados" in description
    assert "official contacts" in description


# --- handoff -------------------------------------------------------------------------------


def handoff(tools, session_name="s1", **args):
    args = {"reason": "complaint", "summary": "Cliente reclama do atraso do pedido 8."} | args
    return json.loads(tools["handoff_to_human"].invoke(args, config=session(session_name)))


def test_handoff_protocol_format_and_daily_sequence(make_tools):
    tools = make_tools("2026-03-25T10:30")
    first, second = handoff(tools, order_id=8), handoff(tools, order_id=9)
    assert re.fullmatch(r"EMP-\d{8}-\d{4}", first["protocol"])
    assert (first["protocol"], second["protocol"]) == ("EMP-20260325-0001", "EMP-20260325-0002")
    assert handoff(make_tools("2026-03-26T10:30"))["protocol"] == "EMP-20260326-0001"


def test_handoff_is_stored_with_session_from_config(make_tools, engine):
    handoff(make_tools(), reason="other", summary="Quer devolver o pedido 7.", contact="(67) 9")
    with Session(engine) as db:
        row = db.scalars(select(Handoff)).one()
    assert (row.reason, row.session_id, row.contact) == ("other", "s1", "(67) 9")
    assert row.created_at == datetime(2026, 3, 25, 10, 30)


def test_handoff_while_open_and_while_closed(make_tools):
    open_now = handoff(make_tools("2026-03-25T10:30"))
    assert open_now["team_available_now"] is True and "team_back_at" not in open_now
    assert open_now["return_promise"] == "até 24 horas úteis"
    closed = handoff(make_tools("2026-03-28T15:00"))
    assert closed["team_available_now"] is False
    assert closed["team_back_at"] == "segunda-feira, 30/03/2026 às 09:00"


def test_handoff_rejects_invalid_reason(make_tools):
    with pytest.raises(ValueError):
        handoff(make_tools(), reason="refund")


def test_all_tools_registered(make_tools):
    assert set(make_tools()) == {
        "search_products",
        "get_product_details",
        "list_active_promotions",
        "calculate_payment_options",
        "get_order_status",
        "check_return_eligibility",
        "get_store_status",
        "search_policies",
        "handoff_to_human",
    }


# --- idempotent handoff (real case: the same return opened twice) ----------------------------


def test_same_case_twice_returns_the_existing_protocol(make_tools, engine):
    tools = make_tools("2026-02-20T10:00")
    first = handoff(tools, reason="return_request", summary="Devolução do pedido 7.", order_id=7)
    again = handoff(
        tools, reason="return_request", summary="Cliente confirmou a devolução.", order_id=7
    )
    assert again["protocol"] == first["protocol"] == "EMP-20260220-0001"
    assert again["already_registered"] is True and "already_registered" not in first
    with Session(engine) as db:
        assert len(db.scalars(select(Handoff)).all()) == 1


def test_two_orders_in_one_conversation_get_two_cases(make_tools):
    tools = make_tools()
    first = handoff(tools, summary="Atraso do pedido 8.", order_id=8)
    second = handoff(tools, summary="Atraso do pedido 9.", order_id=9)
    assert first["protocol"] != second["protocol"]
    assert "already_registered" not in second


def test_idempotency_key_is_session_reason_and_order(make_tools):
    tools = make_tools()
    base = handoff(tools, order_id=8)["protocol"]
    assert handoff(tools, order_id=8)["protocol"] == base  # same key
    assert handoff(tools, "s2", order_id=8)["protocol"] != base  # other session
    assert handoff(tools, reason="manager_question", order_id=8)["protocol"] != base
    no_order = handoff(tools, summary="Dúvida geral para a gerência.")["protocol"]
    assert handoff(tools, summary="Mesma dúvida de novo.")["protocol"] == no_order


# --- order-related handoffs need a verified order (real case: "Tô com um pedido atrasado") --


def raw_handoff(tools, session_name="s9", **args):
    args = {"reason": "delayed_order", "summary": "Cliente relatou pedido atrasado"} | args
    return tools["handoff_to_human"].invoke(args, config=session(session_name))


@pytest.mark.parametrize("reason", ["delayed_order", "return_request"])
def test_order_reasons_without_a_verified_order_are_refused(make_tools, engine, reason):
    tools = make_tools()
    assert raw_handoff(tools, reason=reason) == identity.ORDER_NOT_VERIFIED  # order_id 0
    assert raw_handoff(tools, reason=reason, order_id=8) == identity.ORDER_NOT_VERIFIED
    with Session(engine) as db:
        assert db.scalars(select(Handoff)).all() == []


def test_any_order_id_given_must_be_verified(make_tools):
    tools = make_tools()
    assert raw_handoff(tools, reason="complaint", order_id=8) == identity.ORDER_NOT_VERIFIED
    assert "protocol" in json.loads(raw_handoff(tools, reason="complaint", contact=""))


def test_order_verified_by_lookup_allows_the_handoff(make_tools):
    tools = make_tools()
    lookup = {"order_id": 8, "email": "anacarol.ferreira@coldmail.com"}
    tools["get_order_status"].invoke(lookup, config=session("s9"))
    result = json.loads(raw_handoff(tools, order_id=8))
    assert result["protocol"].startswith("EMP-")
    assert raw_handoff(tools, "s10", order_id=8) == identity.ORDER_NOT_VERIFIED  # per session


def test_failed_lookup_does_not_verify(make_tools):
    tools = make_tools()
    tools["get_order_status"].invoke({"order_id": 8, "email": "x@y.z"}, config=session("s9"))
    assert raw_handoff(tools, order_id=8) == identity.ORDER_NOT_VERIFIED
