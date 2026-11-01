import re

import pytest

from emporio_agent.client.db.loader import UNRELIABLE_DESCRIPTION_IDS
from emporio_agent.client.profiling import run_checks


@pytest.fixture(scope="module")
def findings():
    return {finding.id: finding for finding in run_checks()}


def _ids(finding, prefix: str) -> set[int]:
    """Numbers following `prefix` (e.g. 'order 3:') in the evidence lines."""
    return {
        int(m.group(1)) for line in finding.evidence for m in re.finditer(rf"{prefix} (\d+)", line)
    }


def test_every_documented_finding_is_reported(findings):
    expected = [f"D{n}" for n in range(1, 11)] + ["E1", "P1", "P2", "P3", "P4"]
    assert sorted(findings) == sorted(expected)
    assert all(f.evidence and f.decision for f in findings.values())


def test_d1_availability_conflicts(findings):
    assert _ids(findings["D1"], "product") == {96, 113, 130, 136}


def test_d2_contradictions_are_inside_the_distrusted_batch(findings):
    detected = _ids(findings["D2"], "product")
    assert detected <= UNRELIABLE_DESCRIPTION_IDS
    assert detected == {135, 136, 137, 139, 140, 142, 144}
    assert "138, 141, 143" in findings["D2"].evidence[-1]


def test_d3_active_promotions(findings):
    active = [line for line in findings["D3"].evidence if line.startswith("active:")]
    assert {int(line.split()[2]) for line in active} == {90, 94, 121, 127}


def test_d4_order_total_mismatches(findings):
    assert _ids(findings["D4"], "order") == {3, 20}


def test_d5_installments_against_current_bands(findings):
    assert _ids(findings["D5"], "order") == {9, 16, 18}


def test_d7_delayed_shipment(findings):
    assert any(line.startswith("order 8: shipped") for line in findings["D7"].evidence)


def test_d8_shared_email(findings):
    assert findings["D8"].evidence == ["bruno.martins@jmail.com: customers 11, 43"]


def test_d9_empty_categories(findings):
    assert _ids(findings["D9"], "category") == {6, 7, 8}


def test_e1_coming_soon_product_in_orders(findings):
    (line,) = findings["E1"].evidence
    assert "product 130" in line and "3 (delivered)" in line and "14 (pending)" in line


def test_p1_two_whatsapp_numbers(findings):
    assert findings["P1"].evidence == [
        "(67) 3341-4444: 2 occurrence(s)",
        "(67) 3321-4500: 1 occurrence(s)",
    ]


def test_p2_accented_email(findings):
    assert any(
        "empóriodamusica" in line and "non-ASCII" in line for line in findings["P2"].evidence
    )


def test_p3_conflicting_minimums(findings):
    values = {re.search(r"R\$ ?([\d.]+),", line).group(1) for line in findings["P3"].evidence}
    assert values == {"100", "80", "50"}


def test_p4_partner_stores_unnamed(findings):
    assert "parceiras" in findings["P4"].evidence[0]
