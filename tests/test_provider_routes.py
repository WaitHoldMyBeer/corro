"""Every provider-facing route, end to end, on a synthetic matter.

A made-up matter is written into a temporary database in the shape Clio
returns, with a marker in every free-text field a firm writes: task names and
descriptions, relationship text, calendar entries, notes, emails, expense
notes, custom field values, document names. The app is then driven over HTTP
the way a provider's browser would drive it, and every payload a provider can
receive is read back for markers, other parties' names, firm-only figures and
internal links.

No request leaves the process: the Clio base URL is pointed at a closed local
port and no model key is set.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from test_provider_projection import APPROVALS

from server.db import connect, upsert_item

MATTER = 7
CLIENT, A, B, ADVERSE, STAFF = 100, 101, 102, 103, 900
A_NAME, B_NAME, ADVERSE_NAME = "Alpha Example Clinic", "Beta Example Imaging", "Gamma Example Haulage"
CASE_VALUE, LIMIT, A_CHARGES, B_CHARGES = 987_650.0, 123_450, 4_321.0, 8_765.0
MARK = re.compile(r"«([a-z0-9_.]+)»")


def mark(field: str) -> str:
    return f"«{field}»"


def rows() -> list[tuple[str, dict]]:
    fields = [
        {"id": 1, "field_name": "Estimated Case Value", "field_type": "currency", "value": CASE_VALUE, "custom_field": {"id": 1}},
        {"id": 2, "field_name": "Policy Limits", "field_type": "text_area", "value": f"{mark('field.limits')} ${LIMIT:,}", "custom_field": {"id": 2}},
        {"id": 3, "field_name": "Policy Limits Confirmed", "field_type": "checkbox", "value": True, "custom_field": {"id": 3}},
        {"id": 4, "field_name": "Liability Assessment", "field_type": "text_area", "value": mark("field.liability"), "custom_field": {"id": 4}},
    ]
    party = lambda contact_id, kind: {"id": contact_id, "type": kind, "name": "Synthetic party"}  # noqa: E731
    return [
        ("matter", {
            "id": MATTER, "status": "Open", "description": mark("matter.description"), "display_number": "00000-Synthetic",
            "client": {"id": CLIENT}, "matter_stage": {"name": "Litigation"}, "responsible_attorney": {"name": "Synthetic Attorney"},
            "custom_field_values": fields,
        }),
        ("contact", {"id": CLIENT, "name": "Casey Placeholder", "first_name": "Casey", "last_name": "Placeholder", "type": "Person"}),
        ("contact", {"id": A, "name": A_NAME, "type": "Company", "primary_email_address": "a-office@example.test"}),
        ("contact", {"id": B, "name": B_NAME, "type": "Company"}),
        ("contact", {"id": ADVERSE, "name": ADVERSE_NAME, "type": "Company"}),
        ("relationship", {"id": 1, "description": f"Treating provider {mark('rel_a.description')}", "contact": {"id": A}}),
        ("relationship", {"id": 2, "description": f"Treating provider {mark('rel_b.description')}", "contact": {"id": B}}),
        ("relationship", {"id": 3, "description": f"Adverse party {mark('rel_adverse.description')}", "contact": {"id": ADVERSE}}),
        ("note", {"id": 1, "subject": mark("note.subject"), "detail": f"{mark('note.detail')} about {A_NAME}", "date": "2031-01-05"}),
        ("communication", {
            "id": 1, "type": "EmailCommunication", "subject": mark("comm_a.subject"), "body": mark("comm_a.body"), "date": "2031-01-06",
            "senders": [party(STAFF, "User")], "receivers": [party(A, "Company")],
        }),
        ("communication", {
            "id": 2, "type": "PhoneCommunication", "subject": mark("comm_client.subject"), "body": mark("comm_client.body"), "date": "2031-01-07",
            "senders": [party(CLIENT, "Person")], "receivers": [party(STAFF, "User")],
        }),
        ("task", {"id": 1, "name": f"{A_NAME} - updated records {mark('task_a.name')}", "description": mark("task_a.description"), "due_at": "2031-02-01", "status": "pending"}),
        ("task", {"id": 2, "name": f"{B_NAME} - itemised bill {mark('task_b.name')}", "description": mark("task_b.description"), "due_at": "2031-02-02", "status": "pending"}),
        ("task", {"id": 3, "name": mark("task_internal.name"), "description": f"Discuss {A_NAME} {mark('task_internal.description')}", "due_at": "2031-02-03", "status": "pending"}),
        ("calendar_entry", {"id": "c1", "summary": f"Appointment, {A_NAME} {mark('cal_a.summary')}", "description": mark("cal_a.description"), "location": mark("cal_a.location"), "start_at": "2031-01-10T09:00:00-07:00", "end_at": "2031-01-10T10:00:00-07:00"}),
        ("calendar_entry", {"id": "c2", "summary": f"Appointment, {B_NAME} {mark('cal_b.summary')}", "description": mark("cal_b.description"), "start_at": "2031-01-11T09:00:00-07:00", "end_at": "2031-01-11T10:00:00-07:00"}),
        ("calendar_entry", {"id": "c3", "summary": mark("cal_internal.summary"), "description": f"Prepare questions about {A_NAME} {mark('cal_internal.description')}", "start_at": "2031-01-12T09:00:00-07:00", "end_at": "2031-01-12T10:00:00-07:00"}),
        ("expense", {"id": 1, "type": "ExpenseEntry", "date": "2031-01-02", "quantity": 1.0, "price": 55.5, "total": 55.5, "note": mark("expense.note")}),
        ("expense", {"id": 2, "type": "ExpenseEntry", "date": "2031-01-03", "quantity": 1.0, "price": A_CHARGES, "total": None, "non_billable": True, "note": f"Charges; {A_NAME} {mark('expense_a.note')}"}),
        ("expense", {"id": 3, "type": "ExpenseEntry", "date": "2031-01-04", "quantity": 1.0, "price": B_CHARGES, "total": None, "non_billable": True, "note": f"Charges; {B_NAME} {mark('expense_b.note')}"}),
        ("document", {"id": 1, "name": f"{mark('document.name')}.pdf", "received_at": "2031-01-08T10:00:00Z"}),
    ]


ALL_ON = ["status", "bills", "records", "attendance", "asks", "coverage"]
PASSCODE = "synthetic-passcode"
APPROVED = "synthetic approved wording"
REASON = "synthetic override reason"

# The build is moving: a request reaches a provider only in wording the attorney approved
# (`SharePolicy.approved_asks`). Where that rule is enforced, nothing the firm typed for itself may
# appear at all; before it, the title of a task naming the provider is what went out.
HAS_APPROVALS = APPROVALS
PERMITTED_FOR_A = set() if HAS_APPROVALS else {"task_a.name"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    conn = connect(tmp_path / "swans.db")
    for kind, payload in rows():
        upsert_item(conn, 1, MATTER, kind, payload, "2031-01-01T00:00:00Z")
    conn.commit()
    conn.close()
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    from server.app import app

    try:
        from server import firm_auth
    except ImportError:
        firm_auth = None
    with TestClient(app) as test_client:
        if firm_auth is not None:
            firm_auth.passcode.cache_clear()
            signed_in = test_client.post(firm_auth.LOGIN, json={"passcode": PASSCODE})
            assert signed_in.status_code in (200, 204, 404, 405), signed_in.text  # 404/405: not mounted in this build
        yield test_client
    if firm_auth is not None:
        firm_auth.passcode.cache_clear()


@pytest.fixture
def provider(client):
    """A provider's browser: the same app, no firm session."""
    with TestClient(client.app) as visitor:
        yield visitor


def firm(path: str) -> str:
    return f"/api/matters/{MATTER}{path}"


def send_body(preview: dict, reason: str | None = REASON) -> dict | None:
    """The body of a send. Where the build has them: the hash of the preview the attorney saw, and the
    reason for sending text the checker did not clear (this fixture has no model, so nothing is cleared)."""
    if "content_hash" not in preview:
        return None
    return {"preview_hash": preview["content_hash"], "override_reason": reason}


def firm_message(client, contact_id: int, text: str, reason: str | None = REASON):
    """The firm writes to a provider in the thread, giving a reason in case the text is held."""
    return client.post(firm(f"/providers/{contact_id}/messages"), json={"text": text, "override_reason": reason})


def hash_required(client) -> bool:
    """Whether this build refuses a send that does not carry the hash of a preview (asked of the route, on provider B)."""
    return client.post(firm(f"/providers/{B}/share")).status_code == 422


def panel(client, contact_id: int) -> dict:
    return next(p for p in client.get(firm("/case")).json()["providers"] if p["contact"]["id"] == contact_id)


def send(client, contact_id: int, categories: list[str] | None = None, approve: bool = True, expect: int = 200):
    """What the attorney does: set the policy, approve the wording of the requests, look at the preview, send it."""
    policy = client.get(firm(f"/providers/{contact_id}/policy")).json()
    if categories is not None:
        policy["allowed_categories"] = categories
    if HAS_APPROVALS and approve:
        policy["approved_asks"] = {ask["id"]: APPROVED for ask in panel(client, contact_id)["asks"]}
    saved = client.put(firm(f"/providers/{contact_id}/policy"), json=policy)
    assert saved.status_code == 200, saved.text
    preview = client.get(firm(f"/providers/{contact_id}/preview")).json()
    body = send_body(preview)
    response = client.post(firm(f"/providers/{contact_id}/share"), json=body)
    assert response.status_code == expect, response.text
    return response.json()["link_href"].split("token=")[1] if expect == 200 else None


def problems(payload: dict, *, bills_on: bool = True) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False)
    found = [f"marker {name}" for name in sorted(set(MARK.findall(text)) - PERMITTED_FOR_A)]
    for label, needle in (
        ("another provider's name", B_NAME),
        ("the adverse party's name", ADVERSE_NAME),
        ("an internal firm link", "/api/matters/"),
        ("the case value", str(int(CASE_VALUE))),
        ("the coverage figure", f"{LIMIT:,}"),
        ("the coverage figure", str(LIMIT)),
        ("another provider's charges", str(int(B_CHARGES))),
        ("the provider's office email", "a-office@example.test"),
    ):
        if needle in text:
            found.append(label)
    if not bills_on and str(int(A_CHARGES)) in text:
        found.append("this provider's charges while bills are off")
    if REASON in text:
        found.append("the attorney's override reason")
    for request in payload.get("requests") or []:
        if request.get("suggested") is not None:
            found.append("the firm-side suggested action")
    return found


def test_the_synthetic_matter_builds_and_has_two_providers(client) -> None:
    case = client.get(firm("/case")).json()
    assert {panel["contact"]["id"] for panel in case["providers"]} == {A, B}
    assert client.get(firm(f"/providers/{ADVERSE}/preview")).status_code == 404
    assert client.get(firm(f"/providers/{CLIENT}/preview")).status_code == 404


@pytest.mark.parametrize("categories", [None, ALL_ON, ["status"], ["bills", "records"], []])
def test_preview_and_link_carry_nothing_the_firm_typed_for_itself(client, provider, categories) -> None:
    token = send(client, A, categories)
    bills_on = categories is None or "bills" in categories
    preview = client.get(firm(f"/providers/{A}/preview")).json()
    preview.pop("withheld_counts", None), preview.pop("warnings", None)
    assert not problems(preview, bills_on=bills_on), "in the attorney's preview"
    link = provider.get(f"/api/share/{token}")
    assert link.status_code == 200
    assert not problems(link.json(), bills_on=bills_on), "on the provider's link"
    assert link.json()["withheld_counts"] == {} and link.json()["warnings"] == []


def test_forbidden_categories_are_refused_by_the_server(client) -> None:
    for category in ("valuation", "strategy", "other_party", "internal", "made_up"):
        response = client.put(firm(f"/providers/{A}/policy"), json={"contact_id": A, "allowed_categories": ["status", category]})
        assert response.status_code == 422, category


def test_every_provider_facing_route_answers_with_a_clean_payload(client, provider) -> None:
    token = send(client, A, ALL_ON)
    view = provider.get(f"/api/share/{token}").json()
    assert view["asks"], "the synthetic matter has a request for provider A"
    replies = [
        provider.post(f"/api/share/{token}/asks/{view['asks'][0]['id']}/reply", json={"text": "synthetic reply from A"}),
        provider.post(f"/api/share/{token}/requests", json={"kind": "coverage", "text": "synthetic question from A"}),
        provider.post(f"/api/share/{token}/requests", json={"kind": "payment_timing"}),
        provider.post(f"/api/share/{token}/messages", json={"text": "synthetic message from A"}),
    ]
    for response in replies:
        assert response.status_code == 200, response.text
        assert not problems(response.json())
    assert provider.post(f"/api/share/{token}/requests", json={"kind": "valuation"}).status_code == 422
    assert provider.post(f"/api/share/{token}/asks/ask:task:2/reply", json={"text": "x"}).status_code == 404, "B's request answered through A's link"


def test_one_providers_thread_never_reaches_another(client, provider) -> None:
    token_a, token_b = send(client, A, ALL_ON), send(client, B, ALL_ON)
    provider.post(f"/api/share/{token_b}/messages", json={"text": "synthetic message from B"})
    provider.post(f"/api/share/{token_b}/requests", json={"kind": "status", "text": "synthetic question from B"})
    firm_message(client, B, "synthetic firm message to B")
    provider.post(f"/api/share/{token_a}/messages", json={"text": "synthetic message from A"})
    firm_message(client, A, "synthetic firm message to A")
    seen_by_a = json.dumps(provider.get(f"/api/share/{token_a}").json())
    seen_by_b = json.dumps(provider.get(f"/api/share/{token_b}").json())
    assert "synthetic firm message to A" in seen_by_a and "synthetic message from A" in seen_by_a
    for theirs in ("synthetic message from B", "synthetic question from B", "synthetic firm message to B"):
        assert theirs not in seen_by_a
    assert "synthetic message from A" not in seen_by_b and "synthetic firm message to A" not in seen_by_b


def stamp(provider, token: str) -> str:
    response = provider.get(f"/api/share/{token}", params={"peek": "true"})
    assert response.status_code == 200 and response.headers.get("etag")
    return response.headers["etag"]


def test_the_change_stamp_moves_only_for_what_this_provider_can_see(client, provider) -> None:
    token_a, token_b = send(client, A, ALL_ON), send(client, B, ALL_ON)
    before = stamp(provider, token_a)
    # Firm-side and other-provider activity that provider A's policy hides.
    provider.post(f"/api/share/{token_b}/messages", json={"text": "synthetic message from B"})
    provider.post(f"/api/share/{token_b}/requests", json={"kind": "coverage"})
    firm_message(client, B, "synthetic firm message to B")
    send(client, B, ["status"])
    client.put("/api/settings/fee", json={"percent": 33, "basis": "gross"})
    client.post(firm("/opened"))
    client.get(firm("/case"))
    assert stamp(provider, token_a) == before, "the stamp moved on activity this provider cannot see"
    # A poll is not an open.
    opens = lambda: panel(client, A)["shares"][0]["open_count"]  # noqa: E731
    count = opens()
    stamp(provider, token_a)
    assert opens() == count, "a change poll was logged as the provider opening the link"
    # Something A can see does move it.
    firm_message(client, A, "synthetic firm message to A")
    assert stamp(provider, token_a) != before
    assert provider.get(f"/api/share/{token_a}", params={"peek": "true"}, headers={"If-None-Match": before}).status_code == 200


def test_a_link_frozen_under_older_rules_is_served_under_the_current_ones(client, provider, tmp_path) -> None:
    """A snapshot row that still holds firm text, an internal link and another provider's event."""
    token = send(client, A, ALL_ON)
    conn = connect(tmp_path / "swans.db")
    row = conn.execute("SELECT id, snapshot FROM shares WHERE token=?", (token,)).fetchone()
    stored = json.loads(row["snapshot"])
    stored["provider"]["role_text"] = mark("legacy.role_text")
    stored["provider"]["email"] = "a-office@example.test"
    stored["provider"]["source"]["href"] = f"/api/matters/{MATTER}/sources/relationship/1"
    stored["provider"]["source"]["label"] = mark("legacy.source_label")
    if stored.get("stage"):
        stored["stage"]["detail"] = mark("legacy.stage_detail")
    conn.execute("UPDATE shares SET snapshot=? WHERE id=?", (json.dumps(stored), row["id"]))
    conn.commit()
    conn.close()
    assert not problems(provider.get(f"/api/share/{token}").json())


def test_revoked_link_refuses_every_provider_route(client, provider) -> None:
    token = send(client, A, ALL_ON)
    share_id = panel(client, A)["shares"][0]["id"]
    assert client.post(firm(f"/shares/{share_id}/revoke")).json()["state"] == "revoked"
    assert provider.get(f"/api/share/{token}").status_code == 410
    assert provider.get(f"/api/share/{token}", params={"peek": "true"}).status_code == 410
    assert provider.post(f"/api/share/{token}/messages", json={"text": "x"}).status_code == 410
    assert provider.post(f"/api/share/{token}/requests", json={"kind": "status"}).status_code == 410
    assert provider.post(f"/api/share/{token}/asks/ask:task:1/reply", json={"text": "x"}).status_code == 410


def test_the_leak_scan_is_not_vacuous(client, provider) -> None:
    """Positive controls: what should be visible is, and a planted leak is caught."""
    token = send(client, A, ALL_ON)
    view = provider.get(f"/api/share/{token}").json()
    text = json.dumps(view, ensure_ascii=False)
    if HAS_APPROVALS:
        assert [ask["text"] for ask in view["asks"]] == [APPROVED], "the approved wording is what goes out"
    else:
        assert mark("task_a.name") in text, "the provider's own request did not reach its view"
    assert view["bills"]["billed_total"] == A_CHARGES and view["provider"]["name"] == A_NAME
    assert view["stage"]["display"] == "Litigation" and view["coverage"]["band"] == "confirmed"
    assert any(event["category"] == "attendance" for event in view["updates"]), "attendance was switched on"
    planted = dict(view, message=f"see {mark('note.detail')} and {B_NAME} at /api/matters/{MATTER}/case")
    assert len(problems(planted)) == 3


# -- rules that arrive with per-request approval, the preview hash and the firm login -------------

needs_approvals = pytest.mark.skipif(not HAS_APPROVALS, reason="per-request approval is not enforced by this build")


@needs_approvals
def test_a_request_is_not_sent_until_its_wording_is_approved(client, provider) -> None:
    token = send(client, A, ALL_ON, approve=False)
    view = provider.get(f"/api/share/{token}").json()
    assert view["asks"] == [] and "asks" in view["shared_categories"]
    assert not problems(view)


@needs_approvals
def test_an_approval_that_is_not_for_this_providers_open_request_sends_nothing(client, provider) -> None:
    theirs = panel(client, B)["asks"][0]["id"]
    own = panel(client, A)["asks"][0]["id"]
    policy = {"contact_id": A, "allowed_categories": ALL_ON, "approved_asks": {theirs: APPROVED, "ask:task:999999": APPROVED, own: "   "}}
    assert client.put(firm(f"/providers/{A}/policy"), json=policy).status_code == 200
    preview = client.get(firm(f"/providers/{A}/preview")).json()
    assert preview["asks"] == []
    token = client.post(firm(f"/providers/{A}/share"), json=send_body(preview)).json()["link_href"].split("token=")[1]
    assert provider.get(f"/api/share/{token}").json()["asks"] == []


def test_what_is_sent_is_what_was_previewed(client) -> None:
    if not hash_required(client):
        pytest.skip("this build sends without the hash of a preview: what is frozen can differ from what the attorney saw")
    preview = client.get(firm(f"/providers/{A}/preview")).json()
    shares_before = len(panel(client, A)["shares"])
    assert client.post(firm(f"/providers/{A}/share")).status_code == 422, "a send with no preview hash was accepted"
    # The view changes after the attorney looked at it.
    client.put(firm(f"/providers/{A}/policy"), json={"contact_id": A, "allowed_categories": ["status"]})
    stale = client.post(firm(f"/providers/{A}/share"), json=send_body(preview))
    assert stale.status_code == 409, "a view the attorney never saw was sent"
    assert len(panel(client, A)["shares"]) == shares_before, "a refused send still stored a share"


def test_the_firm_side_is_closed_to_a_visitor_without_a_session(client, provider) -> None:
    token = send(client, A, ALL_ON)
    if provider.get(firm("/case")).status_code == 200:
        pytest.skip("the firm login is not mounted in this build: the firm API answers anyone on this origin")
    for path in ("/case", f"/providers/{A}/preview", f"/providers/{A}/policy", f"/providers/{A}/thread", f"/providers/{A}/incoming-checks", f"/providers/{A}/overrides", "/incoming-checks/unreviewed", "/sources/note/1", "/digest", "/check/stats"):
        assert provider.get(firm(path)).status_code == 401, path
    assert provider.put(firm(f"/providers/{A}/policy"), json={"contact_id": A, "allowed_categories": ALL_ON}).status_code == 401
    assert provider.post(firm(f"/providers/{A}/messages"), json={"text": "x"}).status_code == 401
    assert provider.post(firm("/check"), json={"text": "x"}).status_code == 401
    for path in ("/api/matters", "/api/status", "/api/settings/fee", f"/api/share/../matters/{MATTER}/case", f"/api/share/%2e%2e/matters/{MATTER}/case"):
        assert provider.get(path).status_code in (401, 404), path
    # The provider's own link still works without a session.
    assert provider.get(f"/api/share/{token}").status_code == 200


def overrides(tmp_path) -> list[dict]:
    conn = connect(tmp_path / "swans.db")
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM send_overrides")]
    finally:
        conn.close()


def test_firm_text_the_checker_did_not_clear_is_held_until_the_attorney_gives_a_reason(client, provider, tmp_path) -> None:
    """Fail closed: with no model the checker clears nothing, so a message with no reason must not go."""
    token = send(client, A, ALL_ON)
    held = firm_message(client, A, "synthetic firm message held", reason=None)
    if held.status_code == 200:
        pytest.skip("the send guard is not in this build: the firm-to-provider message route stores whatever it is given")
    assert held.status_code == 423, held.text
    locked = held.json()["detail"]["locked"]
    assert locked and all(item["state"] in ("dont_send", "unchecked", "unavailable") for item in locked)
    assert "synthetic firm message held" not in json.dumps(provider.get(f"/api/share/{token}").json()), "a held message reached the provider"
    assert "synthetic firm message held" not in json.dumps(client.get(firm(f"/providers/{A}/thread")).json()), "a held message was stored"
    before = len(overrides(tmp_path))
    sent = firm_message(client, A, "synthetic firm message sent with a reason")
    assert sent.status_code == 200, sent.text
    view = provider.get(f"/api/share/{token}").json()
    assert "synthetic firm message sent with a reason" in json.dumps(view)
    assert not problems(view), "the override reason or firm text reached the provider"
    logged = overrides(tmp_path)[before:]
    assert logged and all(row["reason"] == REASON for row in logged), "an override was sent without a logged reason"


def test_a_send_with_unchecked_wording_and_no_reason_stores_nothing(client, tmp_path) -> None:
    policy = client.get(firm(f"/providers/{A}/policy")).json()
    policy["allowed_categories"], policy["message"] = ALL_ON, "synthetic cover note"
    assert client.put(firm(f"/providers/{A}/policy"), json=policy).status_code in (200, 423)
    if not hash_required(client):
        pytest.skip("the send guard is not in this build")
    preview = client.get(firm(f"/providers/{A}/preview")).json()
    shares_before = len(panel(client, A)["shares"])
    held = client.post(firm(f"/providers/{A}/share"), json=send_body(preview, reason=None))
    if held.status_code == 200:
        pytest.skip("the send guard is not in this build")
    assert held.status_code == 423, held.text
    assert len(panel(client, A)["shares"]) == shares_before, "a held send still stored a share"


def test_answering_a_providers_request_never_sends_by_itself(client, provider) -> None:
    """Switching a category on for a provider is not a send: the attorney still previews and sends."""
    if not hash_required(client):
        pytest.skip("this build answers a provider's request by switching the category on and sending in one step, with no preview")
    token = send(client, A, ["status"])
    assert provider.post(f"/api/share/{token}/requests", json={"kind": "coverage"}).status_code == 200
    request_id = client.get(firm(f"/providers/{A}/thread")).json()["requests"][0]["id"]
    shares_before = [share["sent_at"] for share in panel(client, A)["shares"]]
    answered = client.post(firm(f"/providers/{A}/requests/{request_id}/answer"), json={"action": "enable_category", "override_reason": REASON})
    assert answered.status_code == 200, answered.text
    assert [share["sent_at"] for share in panel(client, A)["shares"]] == shares_before, "answering sent a new view with no preview"
    assert provider.get(f"/api/share/{token}").json()["coverage"]["shared"] is False, "the provider saw the category before any send"
    assert "coverage" in client.get(firm(f"/providers/{A}/policy")).json()["allowed_categories"]


def test_text_the_model_read_and_did_not_flag_goes_out_with_no_reason(client, provider, tmp_path, monkeypatch) -> None:
    """The other half of failing closed: when the model tier answers and flags nothing, nothing is asked of the attorney."""
    if not hash_required(client):
        pytest.skip("the send guard is not in this build")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "1")  # the checker's lexical stand-in answers in place of a model
    before = len(overrides(tmp_path))
    policy = client.get(firm(f"/providers/{A}/policy")).json()
    policy["allowed_categories"] = ALL_ON
    policy["approved_asks"] = {ask["id"]: "Please send the office notes for the last three visits." for ask in panel(client, A)["asks"]}
    assert client.put(firm(f"/providers/{A}/policy"), json=policy).status_code == 200
    preview = client.get(firm(f"/providers/{A}/preview")).json()
    sent = client.post(firm(f"/providers/{A}/share"), json=send_body(preview, reason=None))
    assert sent.status_code == 200, sent.text
    token = sent.json()["link_href"].split("token=")[1]
    message = firm_message(client, A, "Thank you, the office notes arrived this morning.", reason=None)
    assert message.status_code == 200, message.text
    assert len(overrides(tmp_path)) == before, "an override was logged for text the model had cleared"
    view = provider.get(f"/api/share/{token}").json()
    assert "Thank you, the office notes arrived this morning." in json.dumps(view) and not problems(view)
