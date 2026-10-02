"""The money arithmetic at its edges: nothing entered, zero, negative, charges above coverage.

Each made-up matter holds only the fields named in its row below. The rule
under test is the one the dashboard states for itself: a figure nobody entered
is shown as unknown and is never counted as zero.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_provider_routes import PASSCODE

from server.db import connect, upsert_item

STAMP = "2031-01-01T00:00:00Z"
# matter id: (estimated value, coverage field text, the provider's charges, what the firm laid out)
MATTERS: dict[int, tuple[float | None, str | None, list[float], list[float]]] = {
    21: (10_000.0, "$5,000", [50_000.0], [100.0]),  # charges above coverage and above the estimate
    22: (10_000.0, None, [-50.0, 0.0], [-20.0, 0.0]),  # negative and zero amounts
    23: (-5_000.0, "$0", [100.0], []),  # a negative estimate, zero coverage
    24: (10_000.0, "no figure in this text", [], []),  # coverage text with no figure
    25: (None, None, [], []),  # nothing entered
    26: (0.0, None, [], []),  # zero entered
}


def write(conn, matter_id: int) -> None:
    value, limits, charges, spend = MATTERS[matter_id]
    fields = []
    if value is not None:
        fields.append({"id": 1, "field_name": "Estimated Case Value", "field_type": "currency", "value": value, "custom_field": {"id": 1}})
    if limits is not None:
        fields.append({"id": 2, "field_name": "Policy Limits", "field_type": "text_area", "value": limits, "custom_field": {"id": 2}})
    client, provider = matter_id * 10, matter_id * 10 + 1
    rows = [
        ("matter", {"id": matter_id, "status": "Open", "description": f"Synthetic matter {matter_id}", "client": {"id": client}, "custom_field_values": fields}),
        ("contact", {"id": client, "name": "Casey Placeholder", "type": "Person"}),
        ("contact", {"id": provider, "name": "Alpha Example Clinic", "type": "Company"}),
        ("relationship", {"id": 1, "description": "Treating provider", "contact": {"id": provider}}),
    ]
    for n, price in enumerate(charges):
        rows.append(("expense", {"id": 10 + n, "type": "ExpenseEntry", "date": "2031-01-03", "quantity": 1.0, "price": price, "total": None, "non_billable": True, "note": "Charges; Alpha Example Clinic"}))
    for n, price in enumerate(spend):
        rows.append(("expense", {"id": 50 + n, "type": "ExpenseEntry", "date": "2031-01-02", "quantity": 1.0, "price": price, "total": price, "note": "Synthetic filing cost"}))
    for kind, payload in rows:
        upsert_item(conn, 1, matter_id, kind, payload, STAMP)


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(tmp_path / "swans.db")
    for matter_id in MATTERS:
        write(conn, matter_id)
    conn.commit()
    conn.close()
    from server import firm_auth
    from server.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        firm_auth.passcode.cache_clear()
        assert client.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
        yield client
    firm_auth.passcode.cache_clear()


def case(firm, matter_id: int) -> dict:
    response = firm.get(f"/api/matters/{matter_id}/case")
    assert response.status_code == 200, response.text[:200]
    return response.json()


def analysis(firm, matter_id: int, **inputs) -> dict:
    if inputs:
        assert firm.put(f"/api/matters/{matter_id}/negotiation/inputs", json=inputs).status_code == 200
    response = firm.get(f"/api/matters/{matter_id}/negotiation")
    assert response.status_code == 200, response.text[:200]
    return response.json()


@pytest.mark.parametrize("matter_id", MATTERS)
def test_every_edge_matter_opens_on_the_case_the_analysis_and_the_overview(firm, matter_id: int) -> None:
    case(firm, matter_id)
    for settings in ({}, {"p_win": 0, "value_share": 0, "p_gate": 0}, {"p_win": 100, "value_share": 100, "p_gate": 100}):
        analysis(firm, matter_id, **settings)
    for percent in (0, 100):
        assert firm.put("/api/settings/fee", json={"percent": percent}).status_code == 200
        analysis(firm, matter_id)
        case(firm, matter_id)
    assert firm.get("/api/firm/overview").status_code in (200, 404)


def test_a_figure_nobody_entered_is_unknown_and_not_zero(firm) -> None:
    brief = case(firm, 25)["brief"]
    assert brief["case_value"] is None and brief["coverage"] is None
    assert brief["coverage_signal"]["band"] == "not_established"
    nodes = {node["id"]: node for node in case(firm, 25)["nodes"]}
    for node_id in ("value", "coverage", "lien", "net"):
        if node_id in nodes:
            assert nodes[node_id]["amount"] is None, f"the {node_id} node holds {nodes[node_id]['amount']} for a matter with nothing entered"
    worked = analysis(firm, 25)
    assert worked["ready"] is False and worked["reason"] and worked["figures"] == [] and worked["expected_net"] is None
    text_only = case(firm, 24)["brief"]["coverage"]
    assert text_only is None or text_only["amount"] is None, "coverage text with no figure in it was given an amount"


@pytest.mark.parametrize("matter_id", [23, 26])
def test_an_estimate_of_zero_or_less_is_not_bargained_over(firm, matter_id: int) -> None:
    worked = analysis(firm, matter_id, p_win=100, value_share=100, p_gate=100)
    assert worked["ready"] is False and worked["figures"] == [] and worked["expected_net"] is None and worked["walk_away_weighted"] is None


def test_charges_above_coverage_never_give_a_negative_net_or_a_figure_above_the_estimate(firm) -> None:
    value = MATTERS[21][0]
    for settings in ({}, {"p_win": 0, "value_share": 0, "p_gate": 0}, {"p_win": 100, "value_share": 100, "p_gate": 100}, {"p_win": 50, "value_share": 50, "p_gate": 0}):
        worked = analysis(firm, 21, **settings)
        assert worked["ready"] is True
        for name in ("expected_net", "walk_away_weighted"):
            assert 0 <= worked[name]["lo"] <= worked[name]["hi"] <= value, f"{name} is {worked[name]} with {settings}"
        for figure in worked["figures"]:
            assert 0 <= figure["value"]["lo"] <= figure["value"]["hi"] <= value, f"{figure['id']} is {figure['value']} with {settings}"
    assert case(firm, 21)["providers"][0]["bills"]["billed_total"] == MATTERS[21][2][0]


def test_an_unknown_lien_total_is_not_printed_as_zero_in_the_analysis(firm) -> None:
    nodes = {node["id"]: node for node in case(firm, 21)["nodes"]}
    assert nodes["lien"]["amount"] is None, "the fixture no longer has an unknown lien total; rewrite this test"
    worked = analysis(firm, 21, p_win=100, value_share=100, p_gate=100)
    liens = [line for figure in worked["figures"] for line in figure.get("trace", []) if line["label"] == nodes["lien"]["label"]]
    assert liens, "the analysis no longer shows a liens line"
    assert all(line["display"] != "$0" for line in liens), "liens nobody totalled are shown as $0"
    assert worked["expected_net"]["exact"] is False, "a net that leaves out unknown liens is called exact"


def test_a_negative_amount_is_written_with_the_sign_before_the_currency(firm) -> None:
    shown = [case(firm, 23)["brief"]["case_value"]["display"], analysis(firm, 22, p_win=0, value_share=0, p_gate=0)["walk_away_weighted"]["display"]]
    assert not [text for text in shown if "$-" in text], shown


def test_the_overview_adds_only_what_is_known(firm) -> None:
    response = firm.get("/api/firm/overview")
    if response.status_code == 404:
        pytest.skip("the firm overview is not mounted in this build")
    body = response.json()
    rows = {row["id"]: row for row in body["cases"]}
    assert rows[25]["value"] is None and rows[25]["coverage"] is None, "a case with nothing entered is listed with a figure"
    known = [row["value"]["amount"] for row in rows.values() if row["value"] is not None and row["value"]["amount"] is not None]
    assert body["totals"]["value_total"] == pytest.approx(sum(known))
    assert body["totals"]["cases"] == len(MATTERS)
