"""Edge cases on every route: hostile input, dead sessions, another case's ids.

Two made-up matters are written into a temporary database and the app is driven
over HTTP with input no page of ours would send: ids that do not exist, ids of
the other matter, malformed and dead share tokens, cookies that are expired or
forged, bodies that are empty, huge or the wrong type. No answer may be a
server error, nothing of one matter may be reached through the other, and
nothing may be stored for a request that was refused.

No model is configured and the Clio base URL is a closed local port.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from test_new_shell_server import synthetic_pdf
from test_provider_routes import ADVERSE, ALL_ON, CLIENT, MATTER, PASSCODE, STAFF, A, B, rows, send

from server.db import connect, upsert_item

OTHER, OTHER_CLIENT, OTHER_PROVIDER, NOWHERE = MATTER + 1, 200, 201, 999_999
STAMP = "2031-01-01T00:00:00Z"
TOO_BIG = 2**63  # one past what the database can hold as an integer
MODEL_ROUTES_DOWN = 503  # the plain "not available right now" a model-backed route gives with no model


def other_rows() -> list[tuple[str, dict]]:
    return [
        ("matter", {"id": OTHER, "status": "Open", "description": "Second synthetic matter", "client": {"id": OTHER_CLIENT}, "custom_field_values": []}),
        ("contact", {"id": OTHER_CLIENT, "name": "Robin Placeholder", "type": "Person"}),
        ("contact", {"id": OTHER_PROVIDER, "name": "Delta Example Therapy", "type": "Company"}),
        ("relationship", {"id": 9, "description": "Treating provider", "contact": {"id": OTHER_PROVIDER}}),
        ("note", {"id": 9, "subject": "Second matter note", "detail": "Synthetic detail", "date": "2031-01-05"}),
    ]


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("SWANS_DATA_DIR", str(data))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(data / "swans.db")
    for matter_id, source in ((MATTER, rows()), (OTHER, other_rows())):
        for kind, payload in source:
            upsert_item(conn, 1, matter_id, kind, payload, STAMP)
    conn.commit()
    conn.close()
    from server import firm_auth
    from server.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        firm_auth.passcode.cache_clear()
        assert client.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
        client.data_dir = data
        yield client
    firm_auth.passcode.cache_clear()


@pytest.fixture
def visitor(firm):
    with TestClient(firm.app, raise_server_exceptions=False) as client:
        yield client


def at(path: str, matter_id: int = MATTER) -> str:
    return f"/api/matters/{matter_id}{path}"


def operations(app) -> list[tuple[str, str]]:
    return [(method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item if method in ("get", "post", "put", "delete", "patch")]


def filled(path: str, matter_id: int = MATTER) -> str:
    for name, value in (("matter_id", matter_id), ("contact_id", A), ("token", "no-such-token"), ("tab", "notes"), ("kind", "note")):
        path = path.replace("{" + name + "}", str(value))
    while "{" in path:
        path = path[: path.index("{")] + "1" + path[path.index("}") + 1 :]
    return path


# --------------------------------------------------------------------------- no server errors


def hostile_requests(token: str) -> list[tuple[str, str, dict]]:
    big_text, odd = "x" * 200_000, "‮\u0000\U0001f600 é 中文"
    out: list[tuple[str, str, dict]] = []
    add = lambda method, path, **kw: out.append((method, path, kw))  # noqa: E731
    for matter in (0, -1, NOWHERE, "abc", "7.0"):
        for path in ("/case", "/digest", "/dashboard", "/graph", "/negotiation", "/review-queue", "/important-documents", "/records/notes",
                     "/ingestions", "/check/stats", "/assistant/conversations", "/assistant/documents", "/incoming-checks/unreviewed"):
            add("GET", at(path, matter))
        for path in ("/opened", "/seen", "/review-queue/refresh"):
            add("POST", at(path, matter))
    for query in ({"since": "garbage"}, {"since": ""}, {"since": "9999-99-99"}, {"since": "2031-01-01T00:00:00+99:00"}, {"claims": "zzz"}, {"since": "x" * 5000}):
        add("GET", at("/case"), params=query)
    for tab in ("notes", "documents", "communications", "tasks", "calendar", "nope", "..", "NOTES", " "):
        for query in ({}, {"offset": -1}, {"limit": 0}, {"limit": 10**9}, {"offset": 2**62}, {"limit": "x"}, {"sort": "date;drop"}, {"q": "%" * 500}):
            add("GET", at(f"/records/{tab}"), params=query)
    for kind in ("note", "communication", "task", "calendar_entry", "expense", "document", "contact", "matter", "custom_field", "nope"):
        for item in ("1", "c1", "0", "-1", str(NOWHERE), "abc", "x" * 300):
            for query in ({}, {"page": -1}, {"page": 10**9}, {"quote": "q" * 20000}, {"claim": "zzz"}, {"page": "x"}):
                add("GET", at(f"/sources/{kind}/{item}"), params=query)
    for document in (1, 0, -1, NOWHERE):
        add("GET", at(f"/documents/{document}/file"))
        for page in (0, -1, 1, 99999):
            for query in ({}, {"crop": "garbage"}, {"crop": "0,0,0,0"}, {"crop": "-1,-1,5,5"}, {"crop": "1e400,0,1,1"}, {"mark": "nan,nan,nan,nan"}):
                add("GET", at(f"/documents/{document}/pages/{page}.png"), params=query)
    for contact in (A, B, CLIENT, ADVERSE, STAFF, 0, -1, NOWHERE):
        for path in ("/policy", "/preview", "/thread", "/overrides", "/incoming-checks", "/customise", "/customise/suggested"):
            add("GET", at(f"/providers/{contact}{path}"))
        add("PUT", at(f"/providers/{contact}/policy"), json={"contact_id": contact, "allowed_categories": ["nope", "strategy", None, 5], "zzz": 1})
        add("PUT", at(f"/providers/{contact}/policy"), json={"contact_id": contact, "hidden_item_ids": ["x" * 5000, "", "bill:999"], "approved_asks": {"": "", "zz": big_text}, "message": big_text})
        add("PUT", at(f"/providers/{contact}/policy"), json={"contact_id": contact, "updated_at": "garbage", "message": odd})
        add("PUT", at(f"/providers/{contact}/policy"), content=b"{not json", headers={"content-type": "application/json"})
        add("POST", at(f"/providers/{contact}/share"), json={"preview_hash": ""})
        add("POST", at(f"/providers/{contact}/share"), json={"preview_hash": "x" * 10000, "override_reason": ""})
        for text in ("", "   ", big_text, odd + " $1,000.00"):
            add("POST", at(f"/providers/{contact}/messages"), json={"text": text, "override_reason": "synthetic reason"})
        add("POST", at(f"/providers/{contact}/customise"), json={"instruction": ""})
        add("PUT", at(f"/providers/{contact}/incoming-checks/review"), json={"item": "zz", "review": "zz"})
        for request in (0, -1, 1, NOWHERE):
            for body in ({"action": "nope"}, {"action": "answer", "text": ""}, {"action": "decline"}):
                add("POST", at(f"/providers/{contact}/requests/{request}/answer"), json=body)
    for share_id in (0, -1, 1, NOWHERE):
        add("GET", at(f"/shares/{share_id}/snapshot"))
        add("POST", at(f"/shares/{share_id}/revoke", NOWHERE))
    for bad in (token[:-1], token + "x", token.upper(), "x", "a" * 5000, "..", "%2e%2e", "%00", "é", " " + token, "null"):
        add("GET", f"/api/share/{bad}")
        add("GET", f"/api/share/{bad}", params={"peek": "zz"})
        for body in ({"text": ""}, {"text": big_text}, {"text": odd}, {}, {"text": None}, {"text": 5}):
            add("POST", f"/api/share/{bad}/messages", json=body)
            add("POST", f"/api/share/{bad}/asks/task:1/reply", json=body)
        add("POST", f"/api/share/{bad}/requests", json={"kind": "nope"})
    for body in ({"text": ""}, {"text": big_text}, {"text": odd}, {}, {"text": None}):
        add("POST", f"/api/share/{token}/messages", json=body)
        for ask in ("x", "task:1", "task:3", "a" * 3000):
            add("POST", f"/api/share/{token}/asks/{ask}/reply", json=body)
    for body in ({"kind": "nope"}, {"kind": ""}, {"kind": "x" * 10000}, {"kind": "status", "text": "x" * 1999}):
        add("POST", f"/api/share/{token}/requests", json=body)
    add("POST", f"/api/share/{token}/messages", content=b"\xff\xfe", headers={"content-type": "application/json"})
    for body in ({}, {"cards": None}, {"cards": [{"id": "nope"}]}, {"cards": [{}] * 61}, {"saved": [{}]}, {"dashboards": [{}]}, {"zzz": 1}, {"cards": "x"}):
        add("PUT", at("/dashboard"), json=body)
    add("PUT", at("/dashboard"), content=b"x" * 3_000_000, headers={"content-type": "application/json"})
    add("PUT", at("/dashboard"), content=json.dumps({"cards": [{"id": "a", "title": "t" * 2_000_000}]}), headers={"content-type": "application/json"})
    for body in ({"action": "nope"}, {"action": "mark"}, {"action": "mark", "document_id": NOWHERE}, {"action": "order", "order": [1, 1, 1]}, {"action": "order", "order": [NOWHERE, -1]},
                 {"action": "accept", "document_id": NOWHERE}, {"action": "reject", "document_id": NOWHERE}):
        add("PUT", at("/important-documents"), json=body)
    for item in ("x", "0", "-1", str(NOWHERE), "a" * 300, "..", "1.pdf"):
        add("GET", at(f"/assistant/documents/{item}"))
        add("GET", at(f"/assistant/documents/{item}.pdf"), params={"download": "zz"})
        add("DELETE", at(f"/assistant/documents/{item}"))
        add("GET", at(f"/assistant/conversations/{item}"))
        add("PUT", at(f"/assistant/conversations/{item}"), json={"title": ""})
        add("DELETE", at(f"/assistant/conversations/{item}"))
    for body in ({"kind": "nope"}, {"kind": ""}, {"kind": "medical_chronology", "date_from": "garbage", "date_to": "9999-99-99"}, {"kind": "medical_chronology", "provider": "nobody"}):
        add("POST", at("/assistant/documents"), json=body)
    for body in ({"message": ""}, {"message": " "}, {"message": "hi", "mode": "nope"}, {"message": "hi", "conversation_id": "nope"}, {"message": "hi", "context_items": [{"zz": 1}, None, "x"]}, {"message": odd}):
        add("POST", at("/assistant"), json=body)
    for body in ({"prompt": ""}, {"prompt": big_text}, {"prompt": "p", "cards": [{}]}):
        add("POST", at("/cards/design"), json=body)
        add("POST", at("/cards/dashboard"), json=body)
    for body in ({}, {"p_win": 0}, {"p_win": 100}, {"p_win": -1}, {"p_win": 101}, {"p_win": "x"}, {"p_win": 0, "value_share": 0, "p_gate": 0}, {"zz": 1}):
        add("PUT", at("/negotiation/inputs"), json=body)
        add("GET", at("/negotiation"))
    for body in ({"claim_id": "", "decision": ""}, {"claim_id": "zz", "decision": "accept"}, {"claim_id": "x" * 10000, "decision": "reject", "note": "n" * 2000}):
        add("PUT", at("/review-queue/decision"), json=body)
    add("POST", at("/review-queue/restore"), json={"claim_id": "zz"})
    for query in ({}, {"q": ""}, {"q": "x" * 10000}, {"q": "\u0000%_\\"}, {"q": '"unbalanced'}, {"q": "a OR b AND NOT *"}, {"q": "NEAR("}, {"focus": "zz"}):
        add("GET", at("/graph"), params=query)
    for body in ({}, {"percent": -1}, {"percent": 101}, {"percent": "x"}, {"percent": 33.333333333, "basis": "nope"}, {"percent": 0}, {"percent": 100}):
        add("PUT", "/api/settings/fee", json=body)
        add("GET", at("/case"))
    for query in ({}, {"matter_id": "abc"}, {"matter_id": -1}, {"matter_id": NOWHERE}):
        add("GET", "/api/spend", params=query)
        add("GET", "/api/settings/import", params=query)
    for query in ({}, {"code": "x"}, {"error": "denied"}, {"code": "x", "state": "y"}, {"state": "x" * 5000}):
        add("GET", "/oauth/callback", params=query, follow_redirects=False)
    for body in (None, {}, {"zz": 1}, "x"):
        add("POST", at("/opened"), json=body)
        add("POST", at("/seen"), json=body)
    add("PUT", at("/conflicts/zz/review"), json={"review": "zz"})
    add("POST", at("/digest"))
    add("POST", at("/sync"))
    add("POST", at("/import"))
    return out


def test_no_hostile_request_is_a_server_error(firm, visitor) -> None:
    token = send(firm, A, ALL_ON)
    failures = []
    for method, path, kwargs in hostile_requests(token):
        client = visitor if path.startswith("/api/share/") else firm
        response = client.request(method, path, **kwargs)
        if response.status_code >= 500 and response.status_code != MODEL_ROUTES_DOWN:
            failures.append(f"{response.status_code} {method} {path[:90]} {str(kwargs)[:80]}")
    assert not failures, f"{len(failures)} server errors, first ten:\n" + "\n".join(failures[:10])


def test_an_id_past_the_integer_range_is_refused_not_a_server_error(firm) -> None:
    paths = [at("/case", TOO_BIG), at("/dashboard", TOO_BIG), at("/records/notes", TOO_BIG), at(f"/documents/{TOO_BIG}/file"), at(f"/documents/{TOO_BIG}/pages/1.png"),
             at(f"/shares/{TOO_BIG}/snapshot"), at(f"/ingestions/{TOO_BIG}"), f"/api/spend?matter_id={TOO_BIG}"]
    answers = {path: firm.get(path).status_code for path in paths}
    answers[at(f"/providers/{A}/requests/{TOO_BIG}/answer")] = firm.post(at(f"/providers/{A}/requests/{TOO_BIG}/answer"), json={"action": "decline"}).status_code
    assert not {path: code for path, code in answers.items() if code >= 500}


# --------------------------------------------------------------------------- the firm session


def test_a_dead_or_forged_session_opens_no_firm_route(firm) -> None:
    """Expired, altered, re-dated, empty and oversized cookies, on every operation the app has."""
    from server import firm_auth

    name = next(iter(firm.cookies.keys()))
    good = firm.cookies.get(name)
    expired, _ = firm_auth.issue(now=time.time() - firm_auth.SESSION_SECONDS - 5)
    redated = str(int(time.time()) + 10**8) + good[good.index(".") :]
    cookies = {"expired": expired, "altered": good[:-2] + ("aa" if not good.endswith("aa") else "bb"), "re-dated": redated, "empty": "", "garbage": "x.y.z", "oversized": "a" * 9000}
    assert not firm_auth.valid(expired) and firm_auth.valid(good)
    open_to_all = (firm_auth.LOGIN, firm_auth.LOGOUT, firm_auth.SESSION)
    answered = []
    for label, value in cookies.items():
        with TestClient(firm.app, raise_server_exceptions=False) as stranger:
            stranger.cookies.set(name, value)
            stranger.cookies.set(firm_auth.COOKIE, value)
            for method, path in operations(firm.app):
                if not firm_auth.needs_session(filled(path)) or path in open_to_all:
                    continue
                code = stranger.request(method, filled(path), json={}).status_code
                if code != 401:
                    answered.append(f"{label}: {method} {path} -> {code}")
            assert stranger.get(firm_auth.SESSION).json().get("signed_in") is False
    assert not answered, "\n".join(answered[:10])


def test_paths_dressed_as_the_providers_are_still_the_firms(visitor) -> None:
    for path in ("/api", "/api/", "/api//matters", "/api/share/../matters", "/api/share/%2e%2e/matters", "/api/share/x/../../matters",
                 "/api/share/..%2fmatters", "/api/matters/", "/api/matters;x", f"/api/share/%2e%2e%2fmatters%2f{MATTER}%2fcase"):
        response = visitor.get(path, follow_redirects=False)
        assert response.status_code in (401, 404), f"{path} answered {response.status_code}"
        assert "providers" not in response.text and "custom_fields" not in response.text


def test_a_wrong_or_malformed_sign_in_is_refused_without_a_session(visitor) -> None:
    from server import firm_auth

    for kwargs in ({"json": {"passcode": "nope"}}, {"json": {}}, {"json": [1]}, {"json": {"passcode": 5}}, {"json": {"passcode": None}}, {"content": b"\xff"}, {"json": {"passcode": PASSCODE + " "}}):
        response = visitor.post(firm_auth.LOGIN, **kwargs)
        assert response.status_code in (400, 401, 422), f"{kwargs} answered {response.status_code}"
        assert visitor.get(at("/case")).status_code == 401


# --------------------------------------------------------------------------- one case never opens another


def test_one_cases_ids_open_nothing_through_another_case(firm, visitor) -> None:
    token = send(firm, A, ALL_ON)
    share_id = 1
    assert firm.get(at(f"/shares/{share_id}/snapshot")).status_code == 200
    for matter in (OTHER, NOWHERE):
        assert firm.get(at(f"/shares/{share_id}/snapshot", matter)).status_code == 404
        assert firm.post(at(f"/shares/{share_id}/revoke", matter)).status_code == 404
        for path in ("/policy", "/preview", "/thread", "/overrides", "/incoming-checks"):
            assert firm.get(at(f"/providers/{A}{path}", matter)).status_code == 404, path
        assert firm.post(at(f"/providers/{A}/messages", matter), json={"text": "Synthetic words.", "override_reason": "synthetic"}).status_code == 404
        assert firm.put(at(f"/providers/{A}/policy", matter), json={"contact_id": A, "allowed_categories": ["status"]}).status_code == 404
        assert firm.get(at("/sources/note/1", matter)).status_code == 404
        assert firm.get(at("/sources/task/1", matter)).status_code == 404
    assert visitor.get(f"/api/share/{token}").status_code == 200, "a revoke addressed through another case killed the link"
    assert firm.get(at(f"/providers/{OTHER_PROVIDER}/preview")).status_code == 404, "the other case's provider is previewed on this case"
    assert firm.get(at("/sources/note/9")).status_code == 404, "the other case's note is opened through this case"
    listed = json.dumps(firm.get(at("/records/notes", OTHER)).json())
    assert "Second matter note" in listed and "«" not in listed, "one case's record list carries another case's records"


@pytest.mark.parametrize("contact", [CLIENT, ADVERSE, STAFF, 0, -1, NOWHERE])
def test_a_contact_who_is_not_a_treating_provider_has_no_share_surface(firm, contact: int) -> None:
    for path in ("/policy", "/preview", "/thread", "/overrides", "/customise/suggested"):
        assert firm.get(at(f"/providers/{contact}{path}")).status_code == 404, path
    assert firm.put(at(f"/providers/{contact}/policy"), json={"contact_id": contact, "allowed_categories": ["status"]}).status_code == 404
    assert firm.post(at(f"/providers/{contact}/share"), json={"preview_hash": "x"}).status_code == 404
    assert firm.post(at(f"/providers/{contact}/messages"), json={"text": "Synthetic words.", "override_reason": "synthetic"}).status_code == 404


# --------------------------------------------------------------------------- share links and policies


def test_a_malformed_or_dead_token_reads_nothing_and_writes_nothing(firm, visitor) -> None:
    token = send(firm, A, ALL_ON)
    before = firm.get(at(f"/providers/{A}/thread")).text
    for bad in (token[:-1], token + "x", token.upper() if token.upper() != token else token.lower(), token[::-1], "x", "a" * 5000, "%00", " " + token, token + "%00", token + "/"):
        assert visitor.get(f"/api/share/{bad}").status_code in (404, 410), f"token {bad[:20]!r} was served"
        assert visitor.post(f"/api/share/{bad}/messages", json={"text": "Synthetic words."}).status_code in (404, 410, 405)
        assert visitor.post(f"/api/share/{bad}/requests", json={"kind": "status"}).status_code in (404, 410, 405, 422)
    assert firm.get(at(f"/providers/{A}/thread")).text == before, "a message sent with a token that is not a link was stored"
    assert firm.post(at("/shares/1/revoke")).status_code == 200
    assert firm.post(at("/shares/1/revoke")).status_code in (200, 404, 409), "revoking twice is a server error"
    for method, path, body in (("GET", "", None), ("POST", "/messages", {"text": "Synthetic words."}), ("POST", "/requests", {"kind": "status"}), ("POST", "/asks/x/reply", {"text": "Synthetic words."})):
        assert visitor.request(method, f"/api/share/{token}{path}", json=body).status_code in (404, 410), f"{method} {path} still answers on a revoked link"
    assert firm.get(at(f"/providers/{A}/thread")).text == before


def test_a_policy_with_unknown_keys_or_categories_is_refused_and_the_path_names_the_provider(firm) -> None:
    saved = firm.get(at(f"/providers/{A}/policy")).json()
    for extra in ({"zzz": 1}, {"share_strategy": True}, {"allowed_categories": ["status", "nope"]}, {"allowed_categories": "status"}, {"hidden_item_ids": "x"}, {"approved_asks": []}):
        assert firm.put(at(f"/providers/{A}/policy"), json={**saved, **extra}).status_code == 422, extra
    for never in ("valuation", "strategy", "other_party", "internal"):
        assert firm.put(at(f"/providers/{A}/policy"), json={**saved, "allowed_categories": ["status", never]}).status_code in (400, 422), never
    assert firm.get(at(f"/providers/{A}/policy")).json() == saved, "a refused policy changed what is stored"
    other_before = firm.get(at(f"/providers/{B}/policy")).json()
    answered = firm.put(at(f"/providers/{A}/policy"), json={**saved, "contact_id": B, "allowed_categories": ["status"]})
    if answered.status_code == 200:
        assert answered.json()["contact_id"] == A
    assert firm.get(at(f"/providers/{B}/policy")).json() == other_before, "a body naming another provider changed that provider's policy"


# --------------------------------------------------------------------------- record lists


@pytest.mark.parametrize("tab", ["notes", "communications", "tasks", "calendar", "documents"])
def test_record_lists_page_within_bounds(firm, tab: str) -> None:
    whole = firm.get(at(f"/records/{tab}"), params={"limit": 200}).json()
    total, ids = whole["total"], [item["id"] for item in whole["items"]]
    assert total == len(ids) == len(set(ids))
    walked = []
    for offset in range(total):
        page = firm.get(at(f"/records/{tab}"), params={"limit": 1, "offset": offset}).json()
        assert page["total"] == total and len(page["items"]) == 1
        walked.append(page["items"][0]["id"])
    assert walked == ids, "paging one at a time gives another order or other rows than the whole list"
    for offset in (total, total + 1, 2**62):
        past = firm.get(at(f"/records/{tab}"), params={"offset": offset})
        assert past.status_code == 200 and past.json()["items"] == [] and past.json()["total"] == total
    for query in ({"limit": 0}, {"limit": 201}, {"limit": 10**9}, {"offset": -1}, {"limit": "x"}, {"offset": "x"}):
        assert firm.get(at(f"/records/{tab}"), params=query).status_code == 422, query
    assert [item["id"] for item in firm.get(at(f"/records/{tab}"), params={"sort": "nope; drop", "order": "sideways"}).json()["items"]] == ids


def test_a_tab_that_does_not_exist_is_a_plain_404(firm) -> None:
    for tab in ("nope", "NOTES", "..", "notes ", "matter"):
        response = firm.get(at(f"/records/{tab}"))
        assert response.status_code == 404, tab


# --------------------------------------------------------------------------- uploads


def upload(firm, name: str | None, body: bytes, content_type: str | None = "application/pdf", matter_id: int = MATTER):
    headers = {"content-type": content_type} if content_type else {}
    return firm.post(at("/documents/upload", matter_id), params={} if name is None else {"name": name}, content=body, headers=headers)


def test_an_upload_that_is_not_a_readable_document_is_refused_in_plain_words(firm) -> None:
    for name, body, content_type in (
        ("empty.pdf", b"", "application/pdf"), ("head.pdf", b"%PDF-1.4\n", "application/pdf"), ("words.txt", b"plain words", "text/plain"),
        ("words.pdf", b"plain words", "application/pdf"), ("picture.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png"),
        ("picture.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"), ("form.pdf", b"x", "multipart/form-data"), ("form.pdf", b"--zz\r\n\r\n--zz--", "multipart/form-data; boundary=zz"),
    ):
        response = upload(firm, name, body, content_type)
        assert 400 <= response.status_code < 500, f"{name} ({content_type}) answered {response.status_code}"
        assert response.json().get("detail"), "the refusal carries no reason"
    assert firm.get(at("/ingestions")).json() == []
    assert not list((firm.data_dir / "documents").rglob("*.pdf")) if (firm.data_dir / "documents").exists() else True
    assert upload(firm, "x.pdf", synthetic_pdf(["Synthetic sheet."]), matter_id=NOWHERE).status_code == 404


def test_an_upload_name_is_a_label_and_never_a_path(firm, tmp_path) -> None:
    names = ["../../etc/passwd.pdf", "..\\..\\win.pdf", "/abs/path.pdf", "a/b/c.pdf", "n" * 300 + ".pdf", "a\x00b.pdf", "", "...", "‮\U0001f600.pdf", None]
    for index, name in enumerate(names):
        response = upload(firm, name, synthetic_pdf([f"Synthetic sheet number {index}."]))
        assert response.status_code in (200, 201, 202), f"{name!r}: {response.status_code} {response.text[:120]}"
        stored = response.json()["name"]
        assert stored and "/" not in stored and "\\" not in stored and "\x00" not in stored and len(stored) <= 255, f"{name!r} is stored as {stored!r}"
        assert stored not in ("..", "."), f"{name!r} is stored as {stored!r}"
    outside = [path for path in tmp_path.rglob("*") if path.is_file() and firm.data_dir not in path.parents]
    assert not outside, f"an upload wrote outside the data folder: {outside}"
    kept = [path for path in (firm.data_dir / "documents").rglob("*") if path.is_file()]
    assert len(kept) == len(names) and all(path.parent == firm.data_dir / "documents" / str(MATTER) for path in kept)
    listed = firm.get(at("/records/documents"), params={"limit": 200}).json()
    assert listed["total"] == 1 + len(names), "an upload is missing from the documents list"


def test_the_same_bytes_twice_are_one_document_and_another_cases_upload_is_its_own(firm) -> None:
    first, second = synthetic_pdf(["Synthetic sheet for the first matter."]), synthetic_pdf(["Other synthetic words for the second matter."])
    one = upload(firm, "first.pdf", first).json()
    again = upload(firm, "renamed.pdf", first).json()
    assert again["document_id"] == one["document_id"], "the same file uploaded twice became two documents"
    assert firm.get(at("/records/documents")).json()["total"] == 2
    other = upload(firm, "second.pdf", second, matter_id=OTHER).json()
    for matter, document, body in ((MATTER, one["document_id"], first), (OTHER, other["document_id"], second)):
        served = firm.get(at(f"/documents/{document}/file", matter))
        assert served.status_code == 200 and served.content == body, "an uploaded file is served with another upload's bytes"
        assert firm.get(at(f"/documents/{document}/pages/1.png", matter)).status_code == 200
        assert firm.get(at(f"/documents/{document}/pages/2.png", matter)).status_code in (404, 416, 422)
    pages = [firm.get(at(f"/documents/{document}/pages/1.png", matter)).content for matter, document in ((MATTER, one["document_id"]), (OTHER, other["document_id"]))]
    assert pages[0] != pages[1], "two cases' uploads share one rendered page"
    assert firm.get(at(f"/ingestions/{other['id']}")).status_code == 404, "one case reads another case's ingestion report"
    assert firm.get(at(f"/ingestions/{one['id']}", OTHER)).status_code == 404


# --------------------------------------------------------------------------- dashboard document


def test_a_dashboard_write_that_is_refused_changes_nothing_and_two_writers_leave_one_whole_document(firm) -> None:
    empty = firm.get(at("/dashboard")).json()
    for body, kwargs in ((None, {"content": b"x" * 3_000_000, "headers": {"content-type": "application/json"}}), ({"cards": [{"id": "a", "type": "nope"}]}, {}),
                         ({"cards": [{}] * 61}, {}), ({"cards": "x"}, {}), ({"zzz": 1}, {})):
        response = firm.put(at("/dashboard"), **(kwargs or {"json": body}))
        assert 400 <= response.status_code < 500, f"{str(body)[:40]} answered {response.status_code}"
    assert firm.get(at("/dashboard")).json() == empty, "a refused write changed the stored dashboard"
    with TestClient(firm.app, raise_server_exceptions=False) as second:
        from server import firm_auth

        assert second.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
        first_write = firm.put(at("/dashboard"), json={"cards": []})
        second_write = second.put(at("/dashboard"), json={"cards": []})
        assert first_write.status_code == second_write.status_code == 200
        assert firm.get(at("/dashboard")).json() == second.get(at("/dashboard")).json() == second_write.json()
    assert firm.get(at("/dashboard", OTHER)).json()["stored"] is False, "one case's layout was stored for another"


# --------------------------------------------------------------------------- the assistant's documents


def test_assistant_documents_of_no_case_another_case_or_no_kind_are_not_served(firm) -> None:
    for item in ("x", "0", "-1", str(NOWHERE), "a" * 300, "%2e%2e"):
        assert firm.get(at(f"/assistant/documents/{item}")).status_code == 404, item
        assert firm.get(at(f"/assistant/documents/{item}.pdf")).status_code in (404, 422), item
        assert firm.delete(at(f"/assistant/documents/{item}")).status_code in (200, 204, 404), item
        assert firm.get(at(f"/assistant/conversations/{item}")).status_code == 404, item
    for body in ({"kind": "nope"}, {"kind": ""}, {}, {"kind": None}, {"kind": "x" * 5000}):
        assert 400 <= firm.post(at("/assistant/documents"), json=body).status_code < 500, body
    assert firm.get(at("/assistant/documents")).json() in ([], {"items": []}) or not json.dumps(firm.get(at("/assistant/documents")).json()).count('"kind": "nope"')
    kinds = firm.get(at("/assistant/document-kinds")).json()
    made = firm.post(at("/assistant/documents"), json={"kind": kinds[0]["kind"]})
    assert made.status_code == 200, made.text[:200]
    document_id = made.json().get("id") or made.json().get("document", {}).get("id")
    assert document_id, "a built document carries no id"
    assert firm.get(at(f"/assistant/documents/{document_id}")).status_code == 200
    assert firm.get(at(f"/assistant/documents/{document_id}.pdf")).content.startswith(b"%PDF-")
    for matter in (OTHER, NOWHERE):
        assert firm.get(at(f"/assistant/documents/{document_id}", matter)).status_code == 404
        assert firm.get(at(f"/assistant/documents/{document_id}.pdf", matter)).status_code == 404
        assert firm.delete(at(f"/assistant/documents/{document_id}", matter)).status_code in (200, 204, 404)
    assert firm.get(at(f"/assistant/documents/{document_id}")).status_code == 200, "a delete addressed through another case removed the document"


# --------------------------------------------------------------------------- the since-last-visit marker, and new cases


def test_nothing_is_stored_for_a_matter_that_does_not_exist(firm) -> None:
    assert firm.post(at("/opened", NOWHERE)).status_code == 404
    assert firm.post(at("/seen", NOWHERE)).status_code == 404
    assert firm.put(at("/dashboard", NOWHERE), json={"cards": []}).status_code == 404
    assert firm.get(at("/dashboard", NOWHERE)).json().get("stored") is not True


def test_the_marker_of_one_case_does_not_move_anothers(firm) -> None:
    first = firm.post(at("/opened")).json()
    firm.post(at("/seen", OTHER))
    assert firm.post(at("/opened")).json()["since"] == first["since"]
    for body in ({}, {"zz": 1}, "x", None):
        assert firm.post(at("/opened"), json=body).status_code == 200
        assert firm.post(at("/seen"), json=body).status_code == 200


def test_a_new_case_needs_a_name_with_something_in_it(firm) -> None:
    if ("POST", "/api/matters") not in operations(firm.app):
        pytest.skip("creating a case is not mounted in this build")
    for name in ("", " ", "\t\n", " "):
        assert firm.post("/api/matters", json={"name": name}).status_code == 422, repr(name)


def test_an_empty_case_and_a_case_with_only_documents_open_on_every_view(firm) -> None:
    """Unknown stays unknown: a case with nothing in it shows no figure rather than a zero."""
    if ("POST", "/api/matters") not in operations(firm.app):
        pytest.skip("creating a case is not mounted in this build")
    made = firm.post("/api/matters", json={"name": "Synthetic empty case"})
    assert made.status_code in (200, 201), made.text[:200]
    case_id = made.json()["id"]
    views = ("/case", "/dashboard", "/graph", "/negotiation", "/review-queue", "/important-documents", "/records/documents", "/records/notes", "/ingestions", "/check/stats")
    for stage in ("empty", "documents only"):
        for path in views:
            response = firm.get(at(path, case_id))
            assert response.status_code == 200, f"{stage}: {path} answered {response.status_code} {response.text[:120]}"
        brief = firm.get(at("/case", case_id)).json()["brief"]
        assert brief["case_value"] is None and brief["coverage"] is None, f"{stage}: a figure nobody entered is shown"
        analysis = firm.get(at("/negotiation", case_id)).json()
        assert analysis["ready"] is False and analysis["expected_net"] is None and analysis["figures"] == [], f"{stage}: an analysis was computed from nothing"
        assert firm.post(at("/check", case_id), json={"text": "The bill was $500 on 2031-01-02."}).status_code == 200
        assert firm.post(at("/opened", case_id)).status_code == 200
        if stage == "empty":
            assert firm.get(at("/records/documents", case_id)).json()["total"] == 0
            assert upload(firm, "only.pdf", synthetic_pdf(["Synthetic only sheet."]), matter_id=case_id).status_code in (200, 201, 202)
    assert firm.get(at("/records/documents", case_id)).json()["total"] == 1
    overview = firm.get("/api/firm/overview")
    if overview.status_code == 200:
        row = next(case for case in overview.json()["cases"] if case["id"] == case_id)
        assert row["value"] is None and row["coverage"] is None, "the overview shows a figure for a case that has none"
        other = next(case for case in overview.json()["cases"] if case["id"] == OTHER)
        assert other["value"] is None, "a case with no estimate is listed with one"
