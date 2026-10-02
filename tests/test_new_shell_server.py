"""The server pieces behind the new shell: the graph, the dashboard layout, the
ten important documents and the described card.

Checked on a synthetic matter over HTTP:
- every one of these routes is the firm's and answers 401 without a session;
- a system suggestion is never presented as the lawyer's choice, in any order
  of marking, accepting, rejecting and reordering;
- a described card can only be built from the catalog: a source, field or
  setting that is not on the list is refused in a plain sentence;
- the catalog and the graph hold nothing that did not come from the matter
  being viewed, and nothing of the new routes is reachable from a provider's link.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_provider_routes import ALL_ON, MARK, MATTER, PASSCODE, A, firm, mark, rows, send

from server.db import connect, upsert_item

EXTRA_DOCUMENTS = [
    ("document", {"id": n, "name": f"synthetic-document-{n}.pdf", "received_at": f"2031-01-{10 + n:02d}T10:00:00Z"})
    for n in range(2, 6)
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(tmp_path / "swans.db")
    for kind, payload in rows() + EXTRA_DOCUMENTS:
        upsert_item(conn, 1, MATTER, kind, payload, "2031-01-01T00:00:00Z")
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
    with TestClient(client.app) as anyone:
        yield anyone


NEW_FIRM_ROUTES = ("/graph", "/dashboard", "/important-documents", "/records/notes", "/records/documents")


def test_the_new_routes_are_the_firms(client, visitor) -> None:
    for path in NEW_FIRM_ROUTES:
        assert visitor.get(firm(path)).status_code == 401, path
        assert client.get(firm(path)).status_code == 200, path
    assert visitor.get("/api/cards/catalog").status_code == 401
    assert visitor.post(firm("/cards/design"), json={"prompt": "overdue tasks"}).status_code == 401
    assert visitor.put(firm("/dashboard"), json={"cards": []}).status_code == 401
    assert visitor.put(firm("/important-documents"), json={"action": "mark", "document_id": 1}).status_code == 401


def important(client) -> list[dict]:
    response = client.get(firm("/important-documents"))
    assert response.status_code == 200, response.text
    return response.json()["items"]


def change(client, **body):
    return client.put(firm("/important-documents"), json=body)


def test_every_suggestion_is_labelled_as_one_until_the_lawyer_acts(client) -> None:
    items = important(client)
    assert len(items) == 5, "the synthetic matter has five documents"
    assert all(item["chosen_by"] == "ai" for item in items), "nothing is the lawyer's before the lawyer has chosen"
    assert all((item.get("why") or "").strip() for item in items), "a suggestion with no stated reason"
    assert all(not item.get("origin") for item in items), "a suggestion carries the origin of a lawyer's choice"


def test_marking_accepting_rejecting_and_reordering_never_blur_who_chose(client) -> None:
    first, second, third = (item["document"]["id"] for item in important(client)[:3])
    assert change(client, action="mark", document_id=third).status_code == 200
    assert change(client, action="accept", document_id=first).status_code == 200
    assert change(client, action="reject", document_id=second).status_code == 200
    items = important(client)
    lawyers = [item for item in items if item["chosen_by"] == "lawyer"]
    system = [item for item in items if item["chosen_by"] == "ai"]
    assert [item["document"]["id"] for item in lawyers] == [third, first], "the lawyer's documents come first, in the lawyer's order"
    assert {item["document"]["id"]: item["origin"] for item in lawyers} == {third: "marked", first: "accepted"}
    assert items[: len(lawyers)] == lawyers, "a suggestion is listed among the lawyer's own"
    assert second not in {item["document"]["id"] for item in items}, "a rejected suggestion is offered again"
    assert all(item["chosen_by"] == "ai" and item.get("why") and not item.get("origin") for item in system)
    # Reorder keeps provenance; an order naming a suggestion is refused.
    assert change(client, action="reorder", order=[first, third]).status_code == 200
    assert [i["document"]["id"] for i in important(client) if i["chosen_by"] == "lawyer"] == [first, third]
    assert change(client, action="reorder", order=[first, third, system[0]["document"]["id"]]).status_code == 422
    # Unmarking gives the slot back to the system, labelled as the system's again.
    assert change(client, action="unmark", document_id=third).status_code == 200
    back = next(item for item in important(client) if item["document"]["id"] == third)
    assert back["chosen_by"] == "ai" and back.get("why") and not back.get("origin")
    # Restoring a rejection makes it a suggestion again, not a choice.
    assert change(client, action="restore", document_id=second).status_code == 200
    restored = next(item for item in important(client) if item["document"]["id"] == second)
    assert restored["chosen_by"] == "ai"


def test_only_a_current_suggestion_can_be_accepted_and_only_a_real_document_marked(client) -> None:
    first = important(client)[0]["document"]["id"]
    assert change(client, action="mark", document_id=first).status_code == 200
    assert change(client, action="accept", document_id=first).status_code == 409, "the lawyer's own document accepted as a suggestion"
    assert change(client, action="reject", document_id=first).status_code == 409
    assert change(client, action="mark", document_id=999_999).status_code == 404
    assert change(client, action="made_up", document_id=first).status_code == 422


def test_a_described_card_is_built_only_from_the_catalog() -> None:
    from server.cards import design
    from server.cards.catalog import catalog

    sources = catalog()
    path, source = next((p, s) for p, s in sources.items() if s.many and len(s.fields) >= 2)
    names = list(source.fields)

    def out(**changes):
        base = {
            "possible": True, "reason": None, "title": "Synthetic card", "kind": "list", "source": path, "fields": names[:2],
            "filter": None, "sort": None, "limit": 5, "stat": None, "text": None, "template": "none", "template_settings": [],
        }
        return design.CardOut.model_validate({**base, **changes})

    spec = design.validate(out())
    assert spec.source == path and spec.fields == names[:2]
    for bad in (
        out(source="clio_items"),
        out(source="../settings"),
        out(fields=[names[0], "access_token"]),
        out(filter={"field": "payload", "op": "eq", "value": "x"}),
        out(sort={"field": "__class__", "dir": "asc"}),
        out(fields=[]),
    ):
        with pytest.raises(ValueError) as refused:
            design.validate(bad)
        assert "Traceback" not in str(refused.value) and len(str(refused.value)) < 400
    assert design.validate(out(limit=10_000)).limit <= 25
    assert design.validate_template(out(template="timeline", template_settings=[{"key": "made_up", "value": "x"}])) is None


def test_the_catalog_describes_shapes_not_a_matter(client) -> None:
    response = client.get("/api/cards/catalog")
    assert response.status_code == 200
    text = json.dumps(response.json(), ensure_ascii=False)
    assert not MARK.findall(text), "the catalog carries text from the matter"
    assert "Casey Placeholder" not in text and "Alpha Example Clinic" not in text


def test_the_card_designer_without_a_model_fails_in_one_plain_sentence(client) -> None:
    response = client.post(firm("/cards/design"), json={"prompt": "show me the overdue tasks"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert isinstance(detail, str) and "Traceback" not in detail and len(detail) < 300


def test_the_graph_holds_this_matters_records_and_nothing_else(client) -> None:
    response = client.get(firm("/graph"))
    assert response.status_code == 200 and response.headers.get("etag")
    text = response.text
    assert mark("note.subject") in text or mark("note.detail") in text, "the matter's own records are not in its graph"
    assert client.get(firm("/graph"), headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    assert client.get("/api/matters/424242/graph").status_code == 404


def test_nothing_of_the_new_shell_is_reachable_from_a_providers_link(client, visitor) -> None:
    token = send(client, A, ALL_ON)
    assert visitor.get(f"/api/share/{token}").status_code == 200
    for path in NEW_FIRM_ROUTES:
        assert visitor.get(f"/api/share/{token}/..{firm(path).removeprefix('/api')}").status_code in (401, 404)
        assert visitor.get(firm(path)).status_code == 401
    view = json.dumps(visitor.get(f"/api/share/{token}").json())
    for word in ("important", "dashboard", "graph", "chosen_by"):
        assert word not in view, f"the provider payload mentions {word}"


def test_a_dashboard_layout_round_trips_and_is_kept_per_matter(client) -> None:
    fresh = client.get(firm("/dashboard")).json()
    assert fresh["stored"] is False
    custom = {"kind": "text", "title": "Synthetic note", "text": "synthetic card text"}
    layout = {"cards": [{"id": "money", "size": "wide"}, {"id": "custom:1", "size": None, "spec": custom}]}
    saved = client.put(firm("/dashboard"), json=layout)
    assert saved.status_code == 200, saved.text
    again = client.get(firm("/dashboard")).json()
    assert again["stored"] is True and [card["id"] for card in again["cards"]] == ["money", "custom:1"]
    assert again["cards"][1]["spec"] == custom
    assert client.put(firm("/dashboard"), json={"cards": [{"id": "money"}, {"id": "money"}]}).status_code == 422
    assert client.put(firm("/dashboard"), json={"cards": [{"id": "money", "made_up": 1}]}).status_code == 422
    assert client.put(firm("/dashboard"), json={"cards": [{"id": f"card-{n}"} for n in range(61)]}).status_code == 422
    other = client.get("/api/matters/424242/dashboard")
    assert other.status_code == 404 or "custom:1" not in other.text, "one matter's layout shows under another matter"


RECORD_TABS = ("notes", "communications", "tasks", "calendar", "documents", "activities", "fields", "bills", "transactions", "cocounsel")


def test_record_tabs_list_this_matters_records_honestly(client) -> None:
    for tab in RECORD_TABS:
        response = client.get(firm(f"/records/{tab}"))
        assert response.status_code == 200, tab
        listing = response.json()
        for row in listing.get("items", []):
            assert len(row.get("snippet") or "") <= 240 and "<" not in (row.get("snippet") or "")
        note = listing.get("note") or ""
        assert "clio" not in note.lower(), f"the {tab} tab's note names the source system"
    notes = client.get(firm("/records/notes")).json()
    assert notes["total"] == 1 and mark("note.subject") in json.dumps(notes, ensure_ascii=False)
    assert client.get(firm("/records/made_up")).status_code == 404
    assert client.get(firm("/records/notes"), params={"limit": 201}).status_code == 422
    # Kinds this fixture never synced must not claim "none on this matter".
    bills = client.get(firm("/records/bills")).json()
    assert bills["total"] == 0 and (bills.get("available") is False or "None on this matter" in (bills.get("note") or ""))


def api_operations(app) -> list[tuple[str, str]]:
    """(METHOD, path template) for every /api operation, read from the app's own OpenAPI description
    so routes added through any router are included."""
    return sorted(
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in operations
        if method.lower() in ("get", "post", "put", "patch", "delete")
    )


def test_every_api_route_the_app_has_is_closed_without_a_firm_session(client, visitor) -> None:
    """Enumerated from the app itself, so a route added tomorrow is covered today. The only /api
    paths a visitor may call are a provider's own link, the public contract and the sign-in routes."""
    import re

    from server import firm_auth

    open_paths = set(firm_auth.OPEN_PATHS) | {firm_auth.LOGIN, firm_auth.LOGOUT, firm_auth.SESSION}
    fill = {"matter_id": str(MATTER), "contact_id": str(A), "document_id": "1", "share_id": "1", "request_id": "1", "page": "1"}
    checked, leaks = 0, []
    for method, template in api_operations(client.app):
        if template in open_paths or template.startswith(firm_auth.OPEN_PREFIXES):
            continue
        path = re.sub(r"\{(\w+)\}", lambda m: fill.get(m.group(1), "x"), template)
        status = visitor.request(method, path, json={} if method != "GET" else None).status_code
        checked += 1
        if status != 401:
            leaks.append(f"{method} {template} -> {status}")
    assert checked >= 35, f"only {checked} operations found; this test may be verifying less than it claims"
    assert not leaks, "routes that answer a visitor with no firm session:\n  " + "\n  ".join(leaks)


def test_a_providers_link_can_only_reach_its_own_routes(client, visitor) -> None:
    """The open prefix is the provider's link; nothing else may be mounted under it."""
    from server import firm_auth

    under_open = sorted({path for _, path in api_operations(client.app) if path.startswith(firm_auth.OPEN_PREFIXES)})
    assert under_open, "no provider routes found"
    assert all(path.startswith("/api/share/{token}") for path in under_open), under_open


def synthetic_pdf(lines: list[str]) -> bytes:
    import pymupdf

    document = pymupdf.open()
    sheet = document.new_page()
    for n, line in enumerate(lines):
        sheet.insert_text((72, 72 + 18 * n), line)
    data = document.tobytes()
    document.close()
    return data


def test_an_uploaded_document_stays_ours_is_labelled_and_never_reaches_a_provider(client, visitor) -> None:
    """Upload writes to our database only (the read-only test forbids any path to the source system),
    the document is marked as uploaded wherever it is listed, and no provider payload mentions it."""
    paths = {path for _, path in api_operations(client.app)}
    upload = "/api/matters/{matter_id}/documents/upload"
    if upload not in paths:
        pytest.skip("document upload is not mounted in this build")
    token = send(client, A, ALL_ON)
    before = json.dumps(visitor.get(f"/api/share/{token}").json())
    body = synthetic_pdf(["Synthetic uploaded sheet.", f"It carries a marker: {mark('upload.page')}."])
    assert visitor.post(firm("/documents/upload"), params={"name": "x.pdf"}, content=body).status_code == 401
    refused = client.post(firm("/documents/upload"), params={"name": "notes.txt"}, content=b"plain text, not a document")
    assert 400 <= refused.status_code < 500, "a file that is neither a PDF nor a picture was accepted"
    accepted = client.post(firm("/documents/upload"), params={"name": f"{mark('upload.name')}.pdf"}, content=body,
                           headers={"content-type": "application/pdf"})
    assert accepted.status_code in (200, 201, 202), accepted.text
    listing = client.get(firm("/records/documents")).json()
    uploaded = [row for row in listing["items"] if mark("upload.name") in json.dumps(row, ensure_ascii=False)]
    assert len(uploaded) == 1, "the uploaded document is not listed among the matter's documents"
    assert "upload" in json.dumps(uploaded[0]).lower(), "the uploaded document is not marked as uploaded where it is listed"
    for item in client.get(firm("/important-documents")).json()["items"]:
        if mark("upload.name") in json.dumps(item, ensure_ascii=False):
            assert item["chosen_by"] == "ai", "an upload was presented as the lawyer's own choice of important document"
    after = json.dumps(visitor.get(f"/api/share/{token}").json())
    assert mark("upload.name") not in after and mark("upload.page") not in after
    fresh = send(client, A, ALL_ON)
    assert mark("upload.name") not in json.dumps(visitor.get(f"/api/share/{fresh}").json()), "a new share carries the upload"
    assert len(after) == len(before) or "upload" not in after.lower()


def test_the_firm_session_cookie_is_this_instances_own(client, visitor, monkeypatch) -> None:
    """Several instances can run on one machine; a browser keeps cookies per host, not per port."""
    from server import firm_auth

    signed = visitor.post(firm_auth.LOGIN, json={"passcode": PASSCODE})
    assert signed.status_code == 200
    names = [name for name in visitor.cookies.keys() if name.startswith(firm_auth.COOKIE)]
    assert names and all(name != firm_auth.COOKIE for name in names), f"the session cookie is not named for the port: {names}"
    token = visitor.cookies.get(names[0])
    assert firm_auth.valid(token)
    assert visitor.get(firm("/case")).status_code == 200
    # A token signed under another passcode is refused, under either cookie name.
    monkeypatch.setenv("FIRM_PASSCODE", "another-synthetic-passcode")
    firm_auth.passcode.cache_clear()
    assert not firm_auth.valid(token)
    assert visitor.get(firm("/case")).status_code == 401
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    firm_auth.passcode.cache_clear()
    assert firm_auth.valid(token), "a session must survive a restart under the same passcode"
    assert visitor.post(firm_auth.LOGOUT).status_code in (200, 204)
    assert visitor.get(firm("/case")).status_code == 401
    assert visitor.post(firm_auth.LOGIN, json={"passcode": "not-it"}).status_code == 401


def test_a_stored_upload_is_marked_wherever_documents_are_listed(client, tmp_path) -> None:
    conn = connect(tmp_path / "swans.db")
    upsert_item(conn, 1, MATTER, "document", {"id": -1, "name": "synthetic-upload.pdf", "received_at": "2031-02-01T10:00:00Z"}, "2031-02-01T00:00:00Z")
    conn.commit()
    conn.close()
    rows = {row["title"]: row for row in client.get(firm("/records/documents")).json()["items"]}
    origins = {title: json.dumps(row).lower() for title, row in rows.items()}
    assert "uploaded" in origins["synthetic-upload.pdf"]
    assert all("uploaded" not in text for title, text in origins.items() if title != "synthetic-upload.pdf"), "an imported document is marked as uploaded"
    items = client.get(firm("/important-documents")).json()["items"]
    upload = next(item for item in items if item["document"]["id"] == -1)
    assert upload.get("document_origin") == "uploaded" and upload["chosen_by"] == "ai" and upload.get("why")
    assert client.put(firm("/important-documents"), json={"action": "mark", "document_id": -1}).status_code == 200
    marked = next(item for item in client.get(firm("/important-documents")).json()["items"] if item["document"]["id"] == -1)
    assert marked["chosen_by"] == "lawyer" and marked["origin"] == "marked" and marked.get("document_origin") == "uploaded"
