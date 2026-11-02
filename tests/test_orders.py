import json
from datetime import date, datetime

import pytest

from emporio_agent.client import identity
from emporio_agent.client.db.engine import make_engine
from emporio_agent.client.db.loader import load_store_data
from emporio_agent.client.repository import SqlStoreRepository
from emporio_agent.client.rules import return_eligibility
from emporio_agent.client.tools import build_tools, payment_method_text
from emporio_agent.clock import FixedClock
from emporio_agent.config import get_client_config

CLIENT = get_client_config()
RETURNS = CLIENT.returns
ANA = "anacarol.ferreira@coldmail.com"  # order 8 (shipped, ETA 2026-02-28)
LETICIA = "leticia.rocha@jmail.com"  # order 7 (delivered, ETA 2026-02-17)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('db')}/store.db")
    load_store_data(engine)
    return SqlStoreRepository(engine)


@pytest.fixture
def make_tools(repo, policy_retriever):
    """Fresh tools (and a fresh lockout counter) at a simulated store time."""

    def make(now: str = "2026-03-25T10:30"):
        clock = FixedClock(datetime.fromisoformat(now).replace(tzinfo=CLIENT.store.tz))
        return {t.name: t for t in build_tools(repo, CLIENT, clock, policy_retriever)}

    return make


def session(name: str = "s1") -> dict:
    return {"configurable": {"thread_id": name}}


def status(tools, order_id, email, config=None) -> str:
    return tools["get_order_status"].invoke(
        {"order_id": order_id, "email": email}, config=config or session()
    )


# --- identity check ------------------------------------------------------------------------


def test_identity_accepts_email_with_other_case_and_spaces(make_tools):
    result = json.loads(status(make_tools(), 8, "  AnaCarol.Ferreira@ColdMail.com "))
    assert result["order_id"] == 8 and result["customer_first_name"] == "Ana"


def test_unknown_order_and_wrong_email_are_byte_identical(make_tools):
    tools = make_tools()
    unknown_order = status(tools, 999, ANA)
    wrong_email = status(tools, 8, LETICIA)  # a real customer's email, but not this order's
    malformed_email = status(tools, 8, "not-an-email")
    assert unknown_order == wrong_email == malformed_email == identity.ORDER_NOT_FOUND
    assert unknown_order.encode() == wrong_email.encode()


def test_shared_email_only_opens_orders_of_its_own_customer(make_tools):
    tools = make_tools()  # D8: customers 11 and 43 share an email; order 10 is customer 11's
    assert json.loads(status(tools, 10, "bruno.martins@jmail.com"))["order_id"] == 10
    assert status(tools, 8, "bruno.martins@jmail.com") == identity.ORDER_NOT_FOUND


def test_no_other_customer_data_in_output(make_tools):
    result = json.loads(status(make_tools(), 8, ANA))
    text = json.dumps(result, ensure_ascii=False)
    for private in (ANA, "99654-7821", "Ferreira", "Campo Grande", "customer_id"):
        assert private not in text


# --- lockout -------------------------------------------------------------------------------


def test_lockout_after_five_failures_even_with_right_data(make_tools):
    tools = make_tools()
    for _ in range(identity.MAX_FAILED_LOOKUPS):
        assert status(tools, 999, ANA) == identity.ORDER_NOT_FOUND
    assert status(tools, 8, ANA) == identity.TOO_MANY_ATTEMPTS
    returns = tools["check_return_eligibility"].invoke(
        {"order_id": 7, "email": LETICIA, "reason": "arrependimento"}, config=session()
    )
    assert returns == identity.TOO_MANY_ATTEMPTS  # both order tools share the counter
    assert json.loads(status(tools, 8, ANA, session("s2")))["order_id"] == 8  # other session


def test_success_does_not_reset_the_counter(make_tools):
    tools = make_tools()
    for _ in range(identity.MAX_FAILED_LOOKUPS - 1):
        status(tools, 999, ANA)
    assert json.loads(status(tools, 8, ANA))["order_id"] == 8
    status(tools, 999, ANA)
    assert status(tools, 8, ANA) == identity.TOO_MANY_ATTEMPTS


def test_session_comes_from_run_config_not_from_the_model(make_tools):
    tools = make_tools()
    for name in ("get_order_status", "check_return_eligibility"):
        visible = tools[name].tool_call_schema.model_json_schema()["properties"]
        assert "config" not in visible and "thread_id" not in visible
    # Without a session in the run config the lookup is refused (fail closed).
    assert tools["get_order_status"].invoke({"order_id": 8, "email": ANA}) == identity.NO_SESSION
    # A model-supplied session argument is ignored: the lockout follows the run config.
    for i in range(identity.MAX_FAILED_LOOKUPS):
        tools["get_order_status"].invoke(
            {"order_id": 999, "email": ANA, "thread_id": f"fake-{i}"}, config=session("real")
        )
    assert status(tools, 8, ANA, session("real")) == identity.TOO_MANY_ATTEMPTS


# --- order status --------------------------------------------------------------------------


def test_shipped_order_past_eta_is_delayed(make_tools):
    result = json.loads(status(make_tools("2026-03-25T10:30"), 8, ANA))
    assert result["status_text"] == "enviado"
    assert result["tracking_code"] == "BRJL5544332BR"
    assert result["estimated_delivery"] == "28/02/2026"
    assert result["delayed"] is True  # D7
    assert result["days_since_estimated_delivery"] == 25  # since 28/02, not since the purchase
    assert result["days_since_order"] == 38


def test_days_since_order_for_old_confirmed_order(make_tools):
    result = json.loads(status(make_tools("2026-03-25T10:30"), 9, "juliana.almeida@hayoo.com.br"))
    assert result["status_text"] == "confirmado, em preparação"
    assert result["days_since_order"] == 33  # ordered 2026-02-20
    assert result["delayed"] is False  # no ETA to compare with (D7)


def test_shipped_order_before_eta_is_not_delayed(make_tools):
    assert json.loads(status(make_tools("2026-02-20T10:00"), 8, ANA))["delayed"] is False


def test_delivered_order_reports_amount_paid(make_tools):
    result = json.loads(status(make_tools(), 3, "isabela.moreira@jmail.com"))
    assert result["status_text"] == "entregue" and result["delayed"] is False
    assert result["total_text"] == "R$ 3.450,00"  # D4: not the R$ 3.498 sum of items
    assert result["payment_method"] == "cartão de crédito em 12x"


def test_cancelled_order_shows_reason(make_tools):
    result = json.loads(status(make_tools(), 17, "matheus.correia@hayoo.com.br"))
    assert result["status_text"] == "cancelado"
    assert result["notes"] == "Pedido cancelado pelo cliente antes do processamento"


@pytest.mark.parametrize(
    ("method", "text"),
    [("pix", "PIX"), ("debit", "cartão de débito"), ("credit_6x", "cartão de crédito em 6x")],
)
def test_payment_method_text(method, text):
    assert payment_method_text(method) == text


# --- return windows (pure rule, fixed dates) -----------------------------------------------

ORDER_DATE, RECEIPT = date(2026, 2, 3), date(2026, 2, 17)  # order 7


def decide(reason, today, status="delivered"):
    return return_eligibility(reason, status, ORDER_DATE, RECEIPT, today, RETURNS)


def test_regret_window_is_seven_days_after_receipt():
    last_day = decide("arrependimento", date(2026, 2, 24))
    assert last_day.eligible and last_day.deadline == date(2026, 2, 24)
    assert last_day.days_elapsed == 7
    assert not decide("arrependimento", date(2026, 2, 25)).eligible


def test_preference_window():
    assert decide("preferencia", date(2026, 2, 24)).eligible
    assert decide("preferencia", date(2026, 2, 25)).rule_applied == "preference_window"


def test_defect_window_is_thirty_days_after_purchase_then_warranty():
    assert decide("defeito", date(2026, 3, 5)).eligible  # 2026-02-03 + 30 days
    late = decide("defeito", date(2026, 3, 6))
    assert not late.eligible and late.rule_applied == "defect_after_exchange_window"
    assert late.legal_warranty_until == date(2026, 5, 18)  # receipt + 90 days


@pytest.mark.parametrize(
    ("status", "rule"),
    [
        ("shipped", "order_not_delivered"),
        ("pending", "order_not_delivered"),
        ("cancelled", "order_cancelled"),
    ],
)
def test_only_delivered_orders_can_be_returned(status, rule):
    decision = decide("arrependimento", date(2026, 2, 20), status)
    assert not decision.eligible and decision.rule_applied == rule


def test_receipt_estimate_never_after_today():
    decision = return_eligibility(
        "arrependimento", "delivered", ORDER_DATE, date(2026, 2, 28), date(2026, 2, 20), RETURNS
    )
    assert decision.receipt_date == date(2026, 2, 20) and decision.days_elapsed == 0


# --- return tool ---------------------------------------------------------------------------


def test_return_tool_regret_example(make_tools):
    """AGENTS.md §15 example 4: order 7 on 2026-02-20 → eligible until 24/02/2026."""
    result = json.loads(
        make_tools("2026-02-20T10:00")["check_return_eligibility"].invoke(
            {"order_id": 7, "email": LETICIA, "reason": "arrependimento"}, config=session()
        )
    )
    assert result["eligible"] is True
    assert result["deadline"] == "24/02/2026"
    assert result["days_left"] == 4  # 20/02 → 24/02
    assert "days_elapsed" not in result  # kept out of the model's view (tracing only)
    assert result["receipt_date_is_estimate"] is True
    assert result["conditions_to_confirm"] == RETURNS.item_conditions["arrependimento"]
    assert result["not_eligible_items"] == RETURNS.ineligible_conditions
    assert "handoff_to_human" in result["next_steps"]


def test_return_tool_undelivered_order(make_tools):
    result = json.loads(
        make_tools()["check_return_eligibility"].invoke(
            {"order_id": 8, "email": ANA, "reason": "arrependimento"}, config=session()
        )
    )
    assert result["eligible"] is False and result["rule_applied"] == "order_not_delivered"
    assert "deadline" not in result
