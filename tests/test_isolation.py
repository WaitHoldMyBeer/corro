"""Two cases in one database: nothing of one appears under the other's routes.

Both matters are made up here, in the shape the source system returns, with every
free-text field of the first carrying a «a.…» marker and of the second a «b.…»
marker. The app is driven over HTTP. No request leaves the process and no model key
is set. (Written by backend2; critic owns this directory.)
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_provider_routes import ALL_ON, APPROVED, HAS_APPROVALS, PASSCODE, REASON

from server.db import connect, upsert_item

A, B = 71, 72
VALUE = {A: 111_100.0, B: 222_200.0}
EXPENSE = {A: 11.25, B: 22.5}
AT = "2031-01-01T00:00:00Z"


def mark(tag: str, field: str) -> str:
    return f"«{tag}.{field}»"


def rows(matter_id: int, tag: str) -> list[tuple[str, dict]]:
    """One synthetic matter. Ids are distinct between the two so a mix-up cannot pass by coincidence."""
    base = matter_id * 1000
    client, provider, staff = base + 1, base + 2, base + 9
    name = f"{tag.upper()} Example Clinic"
    party = lambda contact_id, kind: {"id": contact_id, "type": kind, "name": "Synthetic party"}  # noqa: E731
    return [
        ("matter", {
            "id": matter_id, "status": "Open", "description": mark(tag, "matter.description"), "display_number": f"{matter_id:05d}-Synthetic",
            "client": {"id": client}, "matter_stage": {"name": f"Stage {tag}"},
            "custom_field_values": [
                {"id": base + 1, "field_name": "Estimated Case Value", "field_type": "currency", "value": VALUE[matter_id], "custom_field": {"id": 1}},
            ],
        }),
        ("contact", {"id": client, "name": f"Casey Placeholder {tag}", "first_name": "Casey", "last_name": f"Placeholder{tag}", "type": "Person"}),
        ("contact", {"id": provider, "name": name, "type": "Company"}),
        ("relationship", {"id": base + 1, "description": f"Treating provider {mark(tag, 'rel.description')}", "contact": {"id": provider}}),
        ("note", {"id": base + 1, "subject": mark(tag, "note.subject"), "detail": f"{mark(tag, 'note.detail')} word{tag}unique about {name}", "date": "2031-01-05"}),
        ("communication", {
            "id": base + 1, "type": "EmailCommunication", "subject": mark(tag, "comm.subject"), "body": mark(tag, "comm.body"), "date": "2031-01-06",
            "senders": [party(staff, "User")], "receivers": [party(provider, "Company")],
        }),
        ("task", {"id": base + 1, "name": f"{name} - updated records {mark(tag, 'task.name')}", "description": mark(tag, "task.description"), "due_at": "2031-02-01", "status": "pending"}),
        ("task", {"id": base + 2, "name": mark(tag, "task_internal.name"), "description": mark(tag, "task_internal.description"), "due_at": "2031-02-03", "status": "pending"}),
        ("calendar_entry", {"id": f"{tag}1", "summary": mark(tag, "cal.summary"), "start_at": "2031-01-10T09:00:00-07:00", "end_at": "2031-01-10T10:00:00-07:00"}),
        ("expense", {"id": base + 1, "type": "ExpenseEntry", "date": "2031-01-02", "quantity": 1.0, "price": EXPENSE[matter_id], "total": EXPENSE[matter_id], "note": mark(tag, "expense.note")}),
        ("document", {"id": base + 1, "name": f"{mark(tag, 'document.name')}.pdf", "received_at": "2031-01-08T10:00:00Z"}),
    ]


TAG = {A: "a", B: "b"}
PROVIDER = {A: A * 1000 + 2, B: B * 1000 + 2}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(tmp_path / "swans.db")
    for matter_id in (A, B):
        for kind, payload in rows(matter_id, TAG[matter_id]):
            upsert_item(conn, 1, matter_id, kind, payload, AT)
    conn.commit()
    conn.close()
    from server import firm_auth
    from server.app import app

    with TestClient(app) as test_client:
        firm_auth.passcode.cache_clear()
        assert test_client.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
        yield test_client
    firm_auth.passcode.cache_clear()


@pytest.fixture
def visitor(client):
    """A provider's browser: the same app, no firm session."""
    with TestClient(client.app) as anyone:
        yield anyone


@pytest.fixture
def database(tmp_path):
    conn = connect(tmp_path / "swans.db")
    yield conn
    conn.close()


def at(matter_id: int, path: str) -> str:
    return f"/api/matters/{matter_id}{path}"


def other(matter_id: int) -> int:
    return B if matter_id == A else A


def foreign(matter_id: int) -> str:
    """The start of every marker of the other case."""
    return f"«{TAG[other(matter_id)]}."


def send(client, matter_id: int) -> tuple[str, int]:
    """What the attorney does: set the policy, approve the wording, look at the preview, send. Returns token and share id."""
    contact = PROVIDER[matter_id]
    policy = client.get(at(matter_id, f"/providers/{contact}/policy")).json()
    policy["allowed_categories"] = ALL_ON
    if HAS_APPROVALS:
        panel = next(p for p in client.get(at(matter_id, "/case")).json()["providers"] if p["contact"]["id"] == contact)
        policy["approved_asks"] = {ask["id"]: APPROVED for ask in panel["asks"]}
    assert client.put(at(matter_id, f"/providers/{contact}/policy"), json=policy).status_code == 200
    preview = client.get(at(matter_id, f"/providers/{contact}/preview")).json()
    body = {"preview_hash": preview["content_hash"], "override_reason": REASON} if "content_hash" in preview else None
    sent = client.post(at(matter_id, f"/providers/{contact}/share"), json=body)
    assert sent.status_code == 200, sent.text
    return sent.json()["link_href"].split("token=")[1], sent.json()["id"]


# Every firm route that reads one case. Each must answer for its own case and carry nothing of the other.
READ_ROUTES = (
    "/case", "/case?claims=all", "/graph", "/dashboard", "/important-documents",
    "/records/notes", "/records/communications", "/records/tasks", "/records/calendar", "/records/documents", "/records/fields",
    "/review-queue", "/review-queue/claims", "/review-queue/history",
    "/assistant/conversations", "/assistant/documents", "/negotiation", "/ingestions", "/check/stats",
    "/incoming-checks/unreviewed", "/digest",
)


@pytest.mark.parametrize("matter_id", [A, B])
def test_every_read_route_of_a_case_carries_only_that_case(client, matter_id) -> None:
    own = client.get(at(matter_id, "/case"))
    assert own.status_code == 200 and mark(TAG[matter_id], "matter.description") in own.text, "the case does not show its own records"
    answered = 0
    for path in READ_ROUTES:
        response = client.get(at(matter_id, path))
        assert response.status_code < 500, f"{path} failed: {response.status_code}"
        assert foreign(matter_id) not in response.text, f"{path} of one case carries a record of the other"
        answered += response.status_code == 200
    assert answered >= 10, "too few routes answered for the scan to mean anything"
    for contact_path in ("policy", "preview", "thread", "incoming-checks", "overrides", "customise"):
        response = client.get(at(matter_id, f"/providers/{PROVIDER[matter_id]}/{contact_path}"))
        assert foreign(matter_id) not in response.text, f"providers/{contact_path} carries a record of the other case"
        # The other case's provider is nobody on this one.
        crossed = client.get(at(matter_id, f"/providers/{PROVIDER[other(matter_id)]}/{contact_path}"))
        assert crossed.status_code in (404, 422), f"providers/{contact_path} answered for a contact of the other case"


def test_search_in_one_graph_does_not_find_the_other_cases_records(client) -> None:
    for matter_id in (A, B):
        text = client.get(at(matter_id, "/graph")).text
        assert f"word{TAG[matter_id]}unique" in text, "the graph's index does not hold the case's own note"
        assert f"word{TAG[other(matter_id)]}unique" not in text, "the search index of one case holds a word of the other"


def test_a_share_belongs_to_its_case_and_its_link_opens_nothing_of_the_other(client, visitor) -> None:
    token, share_id = send(client, A)
    view = visitor.get(f"/api/share/{token}")
    assert view.status_code == 200 and foreign(A) not in view.text
    assert client.get(at(B, f"/shares/{share_id}/snapshot")).status_code == 404
    assert client.post(at(B, f"/shares/{share_id}/revoke")).status_code == 404
    assert visitor.get(f"/api/share/{token}").status_code == 200, "revoking through the other case's route ended the link"
    case_b = client.get(at(B, "/case")).json()
    assert all(not panel["shares"] for panel in case_b["providers"]), "a share sent on one case is logged on the other"
    # The provider holding A's link has no firm session: every route of B refuses them.
    for path in READ_ROUTES + (f"/providers/{PROVIDER[B]}/preview", f"/providers/{PROVIDER[B]}/thread", f"/shares/{share_id}/snapshot"):
        assert visitor.get(at(B, path)).status_code in (401, 404), path
        assert visitor.get(f"/api/share/{token}/..{at(B, path).removeprefix('/api')}").status_code in (401, 404), path
        assert visitor.get(at(B, path), params={"token": token}).status_code in (401, 404), path
    assert visitor.get("/api/firm/overview").status_code == 401
    # Messages written through A's link land on A.
    posted = visitor.post(f"/api/share/{token}/messages", json={"text": "synthetic message from the provider"})
    if posted.status_code == 200:
        assert "synthetic message from the provider" in client.get(at(A, f"/providers/{PROVIDER[A]}/thread")).text
    assert "synthetic message from the provider" not in client.get(at(B, f"/providers/{PROVIDER[B]}/thread")).text
    assert "synthetic message from the provider" not in client.get(at(B, "/case?claims=all")).text


def test_the_dashboard_document_is_kept_per_case(client) -> None:
    layout = {"cards": [{"id": "money", "size": "wide"}, {"id": "custom:1", "size": None, "spec": {"kind": "text", "title": "Synthetic", "text": "synthetic card of case a"}}]}
    assert client.put(at(A, "/dashboard"), json=layout).status_code == 200
    mine, theirs = client.get(at(A, "/dashboard")).json(), client.get(at(B, "/dashboard")).json()
    assert mine["stored"] is True and "synthetic card of case a" in json.dumps(mine)
    assert theirs["stored"] is False and "synthetic card of case a" not in json.dumps(theirs)
    marked = client.put(at(A, "/important-documents"), json={"action": "mark", "document_id": A * 1000 + 1})
    assert str(A * 1000 + 1) not in client.get(at(B, "/important-documents")).text, marked.text


def test_review_decisions_and_assistant_conversations_are_kept_per_case(client, database) -> None:
    for matter_id in (A, B):  # the routes create their tables on first use
        client.get(at(matter_id, "/review-queue"))
        client.get(at(matter_id, "/assistant/conversations"))
    database.execute(
        "INSERT INTO claim_decisions (matter_id, claim_id, decision, note, statement_hash, snapshot, decided_at) VALUES (?,?,?,?,?,?,?)",
        (A, "synthetic-claim", "discard", "synthetic decision of case a", "0", json.dumps({"id": "synthetic-claim"}), AT),
    )
    database.execute(
        "INSERT INTO assistant_turns (matter_id, conversation_id, turn_id, at, mode, ledger_version, question_key, turn) VALUES (?,?,?,?,?,?,?,?)",
        (A, "synthetic-conversation", "t1", AT, "answer", "0", "k", "{}"),
    )
    database.commit()
    for path in ("/review-queue", "/review-queue/history", "/review-queue/claims", "/case?claims=all", "/graph"):
        text = client.get(at(B, path)).text
        assert "synthetic decision of case a" not in text and "synthetic-claim" not in text, path
    assert "synthetic-conversation" not in client.get(at(B, "/assistant/conversations")).text
    crossed = client.get(at(B, "/assistant/conversations/synthetic-conversation"))
    assert crossed.status_code == 404 or "t1" not in crossed.text
    assert client.delete(at(B, "/assistant/conversations/synthetic-conversation")).status_code in (204, 404)
    held = database.execute("SELECT COUNT(*) AS n FROM assistant_turns WHERE matter_id=?", (A,)).fetchone()["n"]
    assert held == 1, "deleting through the other case's route removed this case's conversation"


def test_the_visit_marker_is_kept_per_case(client) -> None:
    assert client.post(at(A, "/seen")).status_code == 200
    assert client.get(at(A, "/case")).json()["changes"]["since"] is not None
    assert client.get(at(B, "/case")).json()["changes"]["since"] is None, "marking one case as seen moved the other's marker"
    assert client.post(at(B, "/opened")).status_code == 200
    assert client.get(at(A, "/case")).json()["changes"]["since"] is not None


def node(client, matter_id: int, node_id: str) -> dict:
    return next(n for n in client.get(at(matter_id, "/case")).json()["nodes"] if n["id"] == node_id)


def test_the_fee_is_one_firm_setting_applied_to_each_cases_own_value(client) -> None:
    """The contingency percentage is the firm's standard term, set once. What it comes to is worked out per case."""
    assert client.get("/api/settings/fee").json()["percent"] is None
    for matter_id in (A, B):
        assert node(client, matter_id, "fee")["amount"] is None, "a fee is shown before the firm has set one"
    percent = 25.0
    assert client.put("/api/settings/fee", json={"percent": percent, "basis": "gross"}).status_code == 200
    assert client.get("/api/settings/fee").json() == {"percent": percent, "basis": "gross"}
    fees = {}
    for matter_id in (A, B):
        case = client.get(at(matter_id, "/case")).json()
        assert case["river"]["fee_percent"] == percent, "the firm's fee does not read the same under every case"
        fees[matter_id] = node(client, matter_id, "fee")["amount"]
        assert fees[matter_id] == VALUE[matter_id] * percent / 100, "the fee is not worked out from this case's own value"
        net = node(client, matter_id, "net")["amount"]
        assert net == VALUE[matter_id] - EXPENSE[matter_id] - fees[matter_id], "the net is not this case's value less its own costs and fee"
    assert fees[A] != fees[B]


def test_spend_is_counted_per_case(client, database) -> None:
    for n in range(3):
        database.execute(
            "INSERT INTO llm_calls (matter_id, run_id, purpose, model, input_tokens, cached_tokens, cache_write_tokens, output_tokens,"
            " cost_usd, seconds, ok, at) VALUES (?, NULL, 'pages', 'synthetic-model', 1000, 0, 0, 10, 0.5, 1.0, 1, ?)",
            (A, AT),
        )
    database.commit()
    assert client.get("/api/spend", params={"matter_id": A}).json()["total"]["calls"] == 3
    assert client.get("/api/spend", params={"matter_id": B}).json()["total"]["calls"] == 0
    assert client.get(at(B, "/case")).json()["meta"]["digest"]["cost_usd_total"] == 0
    for matter_id in (A, B):  # what the firm has spent on the case itself
        spend = client.get(at(matter_id, "/case")).json()["spend"]
        assert spend["total"] == EXPENSE[matter_id] and len(spend["lines"]) == 1


def test_the_overview_lists_both_cases_with_their_own_figures(client, database) -> None:
    overview = client.get("/api/firm/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    by_id = {row["id"]: row for row in body["cases"]}
    assert set(by_id) == {A, B}
    for matter_id, row in by_id.items():
        case = client.get(at(matter_id, "/case")).json()
        assert row["name"] == mark(TAG[matter_id], "matter.description")
        assert row["value"]["amount"] == VALUE[matter_id] == case["brief"]["case_value"]["amount"]
        assert row["spend"]["amount"] == EXPENSE[matter_id] == case["brief"]["firm_spend"]["amount"]
        assert row["stage"] == f"Stage {TAG[matter_id]}" and row["client_name"] == f"Casey Placeholder {TAG[matter_id]}"
        assert (row["overdue"], row["waiting"], row["coming"]) == tuple(len(case["agenda"][k]) for k in ("overdue", "waiting", "coming"))
        assert row["coverage"] is None and row["limitations"] is None, "a figure the case does not hold is null, not zero"
        assert row["documents"] == len(case["documents"]) == 1
        assert row["to_review"] == sum(1 for conflict in case["conflicts"] if conflict["review"] == "unreviewed")
        assert foreign(matter_id) not in json.dumps(row, ensure_ascii=False)
    totals = body["totals"]
    assert totals["open_cases"] == 2
    assert totals["overdue"] == sum(row["overdue"] for row in by_id.values())
    assert totals["to_review"] == sum(row["to_review"] for row in by_id.values())
    assert totals["value_total"] == VALUE[A] + VALUE[B] and totals["spend_total"] == EXPENSE[A] + EXPENSE[B]
    assert totals["coverage_total"] is None, "a total of figures nobody holds is unknown, not zero"
    for item in body["agenda"]:
        assert f"«{TAG[item['case_id']]}." in item["title"], "an agenda item is listed under the wrong case"
        assert item["kind"] in ("task", "calendar_entry")

    # A change to one case shows on the next call (the row cache is keyed by the case's data version), and only there.
    def open_items(row: dict) -> int:
        return row["overdue"] + row["coming"] + row["waiting"]

    task = {"id": A * 1000 + 3, "name": mark("a", "task_new.name"), "due_at": "2031-02-05", "status": "pending"}
    upsert_item(database, 2, A, "task", task, "2031-01-02T00:00:00Z")
    database.commit()
    again = {row["id"]: row for row in client.get("/api/firm/overview").json()["cases"]}
    assert open_items(again[A]) == open_items(by_id[A]) + 1
    assert again[B] == by_id[B]


def test_a_case_that_cannot_be_built_does_not_take_the_overview_down(client, database) -> None:
    broken = 73
    upsert_item(database, 1, broken, "matter", {"id": broken, "status": "Open", "client": "not an object"}, AT)
    database.commit()
    response = client.get("/api/firm/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {row["id"]: row for row in body["cases"]}
    assert {A, B} <= set(by_id), "one unreadable case removed the others"
    assert broken not in by_id or by_id[broken].get("error"), "a case that could not be built is shown as if it had figures"
    assert any(str(broken) in warning for warning in body["warnings"]) or broken in by_id
    assert body["totals"]["open_cases"] == 2, "a case that could not be built is counted in the totals"


def test_the_picker_keeps_our_own_cases_when_the_source_list_is_live(client, database) -> None:
    """The source system knows nothing of a case created here; its list must not replace ours."""
    import time

    from server import app as appmod
    from server.db import now_iso

    database.execute("INSERT INTO oauth_tokens (id, access_token, obtained_at) VALUES (1, 'synthetic-token', ?)", (now_iso(),))
    database.commit()
    created = client.post("/api/matters", json={"name": "Synthetic case made here"})
    assert created.status_code == 200, created.text
    appmod._matter_list.update(at=time.monotonic(), rows=[{"id": A, "display_number": "00071-Synthetic", "status": "Open"}], refreshing=False)
    try:
        listed = client.get("/api/matters").json()
    finally:
        appmod._matter_list.update(at=float("-inf"), rows=[], refreshing=False)
    ids = [item["id"] for item in listed["items"]]
    assert listed["live"] is True and A in ids
    assert created.json()["id"] in ids, "a case created in our own store is missing from the picker while the source is connected"
    assert len(ids) == len(set(ids)), "a case is listed twice"


def test_an_empty_case_made_here_answers_on_every_read_route(client) -> None:
    created = client.post("/api/matters", json={"name": "Synthetic empty case", "client_name": "Synthetic Client"})
    assert created.status_code == 200, created.text
    empty = created.json()["id"]
    assert empty not in (A, B)
    for path in READ_ROUTES:
        response = client.get(at(empty, path))
        assert response.status_code < 500, f"{path} failed on a case with nothing in it: {response.status_code}"
        assert "«a." not in response.text and "«b." not in response.text, f"{path} of an empty case carries another case's record"
    assert client.get(at(empty, "/case")).status_code == 200
    row = next(row for row in client.get("/api/firm/overview").json()["cases"] if row["id"] == empty)
    assert row["value"] is None and row["coverage"] is None and row["spend"] is None and row["limitations"] is None
    assert row["documents"] == 0 and row["pages"] is None, "an empty case shows a page count it does not have"
    # Its fresh state did not come from the other cases, and theirs is untouched.
    assert {r["id"] for r in client.get("/api/firm/overview").json()["cases"]} == {A, B, empty}


def test_an_assistant_conversation_and_its_documents_stay_in_their_case(client) -> None:
    """Driven through the routes with no model configured: the turn is answered in code from the case's own index."""
    asked = client.post(at(A, "/assistant"), json={"message": "wordbunique wordaunique"})  # a word of each case's note
    assert asked.status_code == 200, asked.text
    conversation = asked.json()["conversation_id"]
    assert foreign(A) not in asked.text, "an answer on one case cites a record of the other"
    assert conversation in client.get(at(A, "/assistant/conversations")).text
    assert conversation not in client.get(at(B, "/assistant/conversations")).text
    assert client.get(at(B, f"/assistant/conversations/{conversation}")).status_code == 404
    assert client.put(at(B, f"/assistant/conversations/{conversation}"), json={"title": "renamed from the other case"}).status_code == 404
    assert client.delete(at(B, f"/assistant/conversations/{conversation}")).status_code in (204, 404)
    kept = client.get(at(A, f"/assistant/conversations/{conversation}"))
    assert kept.status_code == 200 and "renamed from the other case" not in kept.text and len(kept.json()["turns"]) == 1

    kind = client.get(at(A, "/assistant/document-kinds")).json()[0]["kind"]
    built = client.post(at(A, "/assistant/documents"), json={"kind": kind})
    assert built.status_code == 200, built.text
    assert foreign(A) not in built.text, "a document built for one case holds a record of the other"
    document = built.json()["document"]["id"]
    assert document in client.get(at(A, "/assistant/documents")).text
    assert document not in client.get(at(B, "/assistant/documents")).text
    assert client.get(at(B, f"/assistant/documents/{document}")).status_code == 404
    assert client.get(at(B, f"/assistant/documents/{document}.pdf")).status_code == 404
    assert client.delete(at(B, f"/assistant/documents/{document}")).status_code == 404
    assert client.get(at(A, f"/assistant/documents/{document}")).status_code == 200, "deleting through the other case removed the document"


def test_a_change_in_one_case_moves_only_that_cases_graph(client, database) -> None:
    """The graph is cached per case: new material in one case rebuilds that graph and leaves the other's as it was."""
    before = {matter_id: client.get(at(matter_id, "/graph")) for matter_id in (A, B)}
    assert all(response.status_code == 200 and response.headers.get("etag") for response in before.values())
    note = {"id": A * 1000 + 5, "subject": mark("a", "note_new.subject"), "detail": f"{mark('a', 'note_new.detail')} wordanewunique", "date": "2031-01-09"}
    upsert_item(database, 2, A, "note", note, "2031-01-02T00:00:00Z")
    database.commit()
    changed = client.get(at(A, "/graph"))
    assert changed.headers["etag"] != before[A].headers["etag"] and "wordanewunique" in changed.text, "the case's own graph is stale"
    assert client.get(at(B, "/graph"), headers={"If-None-Match": before[B].headers["etag"]}).status_code == 304
    assert "wordanewunique" not in client.get(at(B, "/graph")).text


def synthetic_pdf(line: str) -> bytes:
    import pymupdf

    document = pymupdf.open()
    document.new_page().insert_text((72, 96), line)
    data = document.tobytes()
    document.close()
    return data


def upload(client, matter_id: int, name: str, data: bytes) -> dict:
    """Send one file and wait for the ingestion to end either way: no model is configured, so its pages may stay unread."""
    import time

    response = client.post(at(matter_id, "/documents/upload"), files={"file": (name, data, "application/pdf")})
    assert response.status_code in (200, 202), response.text
    report, deadline = response.json(), time.monotonic() + 30
    while report["state"] not in ("complete", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        report = client.get(at(matter_id, f"/ingestions/{report['id']}")).json()
    assert report["state"] in ("complete", "failed"), report
    return report


def test_an_upload_and_its_ingestion_belong_to_the_case_it_was_sent_to(client) -> None:
    data = synthetic_pdf("Synthetic uploaded page uploadaunique; no real person or event.")
    report = upload(client, A, "synthetic-upload-a.pdf", data)
    document, ingestion = report["document_id"], report["id"]
    documents = {matter_id: client.get(at(matter_id, "/case")).json()["documents"] for matter_id in (A, B)}
    assert len(documents[A]) == 2 and len(documents[B]) == 1, "an upload to one case is listed on the other"
    assert client.get(at(A, f"/documents/{document}/file")).status_code == 200
    assert client.get(at(B, f"/documents/{document}/file")).status_code == 404
    assert client.get(at(B, f"/documents/{document}/pages/1.png")).status_code == 404
    assert client.get(at(B, f"/sources/document/{document}")).status_code == 404
    assert client.get(at(B, f"/ingestions/{ingestion}")).status_code == 404
    assert client.get(at(B, "/ingestions")).json() in ([], {"items": []}) or "synthetic-upload-a" not in client.get(at(B, "/ingestions")).text
    for path in ("/ingestions", "/records/documents", "/graph", "/case?claims=all", "/important-documents"):
        text = client.get(at(B, path)).text
        assert "synthetic-upload-a" not in text and "uploadaunique" not in text, f"{path} of the other case shows the upload"

    # The same bytes sent to the other case are that case's own document, not a pointer to the first one.
    again = upload(client, B, "synthetic-upload-b.pdf", data)
    assert client.get(at(B, f"/documents/{again['document_id']}/file")).status_code == 200
    assert len(client.get(at(B, "/case")).json()["documents"]) == 2, "the other case's copy was taken as already in this file"
    assert "synthetic-upload-a" not in client.get(at(B, "/records/documents")).text
    assert "synthetic-upload-b" not in client.get(at(A, "/records/documents")).text
    assert len(client.get(at(A, "/case")).json()["documents"]) == 2
