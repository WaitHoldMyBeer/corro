"""Several cases in one database, the firm overview at its edges, and assertions from the server review.

One case as the source system would hold it, one made here, and one more with
odd money rows, all synthetic. Each case's routes answer for that case only;
the overview answers with no cases, with an archived case and with a case that
cannot be built; and input a reviewer found to break a route is pinned.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_new_shell_server import synthetic_pdf
from test_provider_routes import MATTER, PASSCODE, rows

from server.db import connect, upsert_item

ROOT = Path(__file__).resolve().parent.parent
STAMP = "2031-01-01T00:00:00Z"
ODD = MATTER + 5
FORMER_NAME = ("Lien", "of", "Sight")  # what the product was called before it was renamed
SERVED_TEXT = {".html", ".js", ".mjs", ".css", ".json", ".svg", ".txt", ".md", ".webmanifest", ".xml"}


def odd_rows() -> list[tuple[str, dict]]:
    """A case whose money rows are malformed: no figure at all, and a figure that is not a number."""
    return [
        ("matter", {"id": ODD, "status": "Open", "description": "Synthetic odd-rows matter", "custom_field_values": []}),
        ("expense", {"id": 70, "type": "ExpenseEntry", "date": "2031-01-02", "quantity": 1.0, "note": "Synthetic entry with no figure"}),
        ("expense", {"id": 71, "type": "ExpenseEntry", "date": "2031-01-03", "quantity": 1.0, "price": "abc", "total": "abc", "note": "Synthetic entry with words for a figure"}),
        ("expense", {"id": 72, "type": "ExpenseEntry", "date": "2031-01-04", "quantity": 1.0, "price": 12.5, "total": 12.5, "note": "Synthetic entry with a figure"}),
    ]


def make_client(tmp_path, monkeypatch, sources: list[tuple[int, list[tuple[str, dict]]]]):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(tmp_path / "swans.db")
    for matter_id, source in sources:
        for kind, payload in source:
            upsert_item(conn, 1, matter_id, kind, payload, STAMP)
    conn.commit()
    conn.close()
    from server import firm_auth
    from server.app import app

    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    firm_auth.passcode.cache_clear()
    assert client.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
    return client


@pytest.fixture
def firm(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, [(MATTER, rows()), (ODD, odd_rows())])
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def empty_firm(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, [])
    yield client
    client.__exit__(None, None, None)


def at(path: str, matter_id: int = MATTER) -> str:
    return f"/api/matters/{matter_id}{path}"


def mounted(client, method: str, path: str) -> bool:
    return method.lower() in client.app.openapi()["paths"].get(path, {})


def create(client, name: str = "Synthetic case made here") -> int:
    if not mounted(client, "POST", "/api/matters"):
        pytest.skip("creating a case is not mounted in this build")
    made = client.post("/api/matters", json={"name": name})
    assert made.status_code in (200, 201), made.text[:200]
    return made.json()["id"]


# --------------------------------------------------------------------------- the product's name


def test_the_former_product_name_is_served_nowhere(firm) -> None:
    former = re.compile(r"[\s_-]*".join(FORMER_NAME), re.IGNORECASE)
    found = []
    for path in sorted((ROOT / "web").rglob("*")):
        if path.is_file() and path.suffix.lower() in SERVED_TEXT and former.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(path.relative_to(ROOT)))
    assert not found, f"the former name is still in: {found[:10]}"
    spec = firm.app.openapi()
    assert not former.search(firm.app.title) and not former.search(json.dumps(spec["info"])), "the API still carries the former name"
    for page in ("/", "/v2/", "/provider.html", "/classic.html"):
        response = firm.get(page)
        if response.status_code == 200:
            assert not former.search(response.text), f"{page} is served with the former name"


# --------------------------------------------------------------------------- a source-system case and a case made here


VIEWS = ("/case", "/dashboard", "/graph", "/negotiation", "/review-queue", "/important-documents", "/records/documents", "/records/notes",
         "/records/activities", "/ingestions", "/check/stats", "/digest", "/assistant/conversations", "/assistant/documents")


def test_a_case_made_here_and_a_case_from_the_source_answer_each_for_itself(firm) -> None:
    made = create(firm)
    assert made != MATTER and made < 0, "a case made here must not take an id the source system could give"
    for matter in (MATTER, made, ODD):
        for path in VIEWS:
            response = firm.get(at(path, matter))
            assert response.status_code == 200, f"case {matter} {path}: {response.status_code} {response.text[:120]}"
    listed = {item["id"]: item for item in firm.get("/api/matters").json()["items"]}
    assert {MATTER, made, ODD} <= set(listed) and listed[made]["source"] != listed[MATTER]["source"]
    mine = json.dumps(firm.get(at("/case", made)).json(), ensure_ascii=False)
    assert "«" not in mine, "a case made here shows text of the source-system case"
    assert firm.get(at("/records/notes", made)).json()["total"] == 0
    assert firm.get(at("/sources/note/1", made)).status_code == 404
    assert firm.get(at("/sources/note/1")).status_code == 200
    body = synthetic_pdf(["Synthetic sheet for the case made here."])
    kept = firm.post(at("/documents/upload", made), params={"name": "made-here.pdf"}, content=body, headers={"content-type": "application/pdf"})
    assert kept.status_code in (200, 201, 202)
    document = kept.json()["document_id"]
    assert firm.get(at(f"/documents/{document}/file", made)).content == body
    assert firm.get(at(f"/documents/{document}/file")).status_code == 404, "the source-system case serves the made case's upload"
    assert firm.get(at("/records/documents")).json()["total"] == 1, "an upload to one case is listed under another"
    for path in ("/sync", "/import"):
        answered = firm.post(at(path, made))
        assert 400 <= answered.status_code < 500, f"{path} on a case made here answered {answered.status_code}; it has nothing in the source system to read"
    again = create(firm, "Synthetic second case made here")
    assert again not in (made, MATTER, ODD)
    assert firm.get(at("/records/documents", again)).json()["total"] == 0


def test_the_checker_keeps_each_cases_answers_apart(firm) -> None:
    """The same sentence, checked under two cases: each answer is that case's own, first time and from the cache."""
    made = create(firm)
    from test_provider_routes import CASE_VALUE, A

    sentence = f"We hold the amount at ${int(CASE_VALUE):,} for now."
    for _ in range(2):
        here = firm.post(at("/check"), json={"text": sentence, "audience": "provider", "audience_contact_id": A}).json()
        there = firm.post(at("/check", made), json={"text": sentence}).json()
        assert here["matter_id"] == MATTER and there["matter_id"] == made
        assert any(span.get("verdict") == "dont_send" for span in here["spans"]), "the case's own firm-only figure is not locked"
        assert not json.dumps(there).count("/api/matters/%d/" % MATTER), "a check under one case cites another case's records"
        assert not [span for span in there["spans"] if span.get("claim_ids")], "a case with no records matched a claim"


# --------------------------------------------------------------------------- the firm overview


def overview(client, **params):
    if not mounted(client, "GET", "/api/firm/overview"):
        pytest.skip("the firm overview is not mounted in this build")
    response = client.get("/api/firm/overview", params=params)
    assert response.status_code == 200, response.text[:200]
    return response.json()


def test_the_overview_with_no_cases_is_an_empty_page_and_not_zeros(empty_firm) -> None:
    body = overview(empty_firm)
    assert body["cases"] == [] and body["agenda"] == []
    totals = body["totals"]
    assert totals["cases"] == 0 and totals["open_cases"] == 0
    assert totals["value_total"] is None and totals["coverage_total"] is None and totals["spend_total"] is None, "with no cases the totals are unknown, not zero"
    assert empty_firm.get("/api/matters").status_code == 200


def test_an_archived_case_leaves_the_open_totals_and_comes_back(firm) -> None:
    if not mounted(firm, "POST", "/api/matters/{matter_id}/archive"):
        pytest.skip("archiving is not mounted in this build")
    before = overview(firm)["totals"]
    assert firm.post(at("/archive")).status_code == 200
    assert firm.post(at("/archive")).status_code == 200, "archiving twice is an error"
    assert firm.post(at("/archive", 999_999)).status_code == 404
    after = overview(firm)
    assert after["totals"]["archived"] == before["archived"] + 1 and after["totals"]["open_cases"] == before["open_cases"] - 1
    assert after["totals"]["value_total"] != before["value_total"], "an archived case's value is still in the open total"
    assert MATTER not in {row["id"] for row in overview(firm, archived="false")["cases"]}
    assert MATTER not in {item["id"] for item in firm.get("/api/matters").json()["items"]}
    assert firm.get(at("/case")).status_code == 200, "an archived case can no longer be opened"
    undo = [path for path in firm.app.openapi()["paths"] if path.endswith(("/unarchive", "/restore")) and "review-queue" not in path]
    if undo:
        assert firm.post(undo[0].replace("{matter_id}", str(MATTER))).status_code == 200
        assert overview(firm)["totals"] == before


def test_a_case_that_cannot_be_built_does_not_blank_the_overview(firm, monkeypatch) -> None:
    from server import cases

    real = cases.case_overview

    def broken(cfg, conn, matter_id, *args, **kwargs):
        if matter_id == ODD:
            raise RuntimeError("synthetic failure while building one case")
        return real(cfg, conn, matter_id, *args, **kwargs)

    monkeypatch.setattr(cases, "case_overview", broken)
    body = overview(firm)
    assert MATTER in {row["id"] for row in body["cases"]} and ODD not in {row["id"] for row in body["cases"]}
    assert body["warnings"], "a case left out of the overview is not mentioned"
    assert "synthetic failure" not in json.dumps(body), "the error's own text is shown to the firm"


# --------------------------------------------------------------------------- from the server review


def test_an_expense_with_no_figure_is_absent_and_never_a_zero(firm) -> None:
    spend = firm.get(at("/case", ODD)).json()["spend"]
    by_id = {str(line["clio_id"]): line for line in spend["lines"]}
    assert "70" not in by_id or by_id["70"]["amount"] is None, "an entry with no figure is listed as an amount"
    assert all(line["amount"] != 0 for line in spend["lines"] if str(line["clio_id"]) in ("70", "71")), "an entry with no usable figure is counted as zero"
    assert spend["total"] == pytest.approx(12.5)


def test_a_record_tab_with_a_malformed_amount_still_lists(firm) -> None:
    response = firm.get(at("/records/activities", ODD))
    assert response.status_code == 200, response.text[:200]
    assert response.json()["total"] == 3


def test_an_upload_dressed_as_a_page_is_served_as_a_pdf_or_a_download(firm) -> None:
    body = b"<script>x</script>\n" + synthetic_pdf(["Synthetic sheet behind markup."])
    kept = firm.post(at("/documents/upload"), params={"name": "a.html"}, content=body, headers={"content-type": "text/html"})
    if kept.status_code not in (200, 201, 202):
        assert 400 <= kept.status_code < 500
        return
    served = firm.get(at(f"/documents/{kept.json()['document_id']}/file"))
    kind = served.headers.get("content-type", "")
    assert not kind.startswith(("text/html", "image/svg", "application/xhtml")), f"an upload is served to the browser as {kind}"
    assert kind.startswith("application/pdf") or "attachment" in served.headers.get("content-disposition", "")


def test_inputs_and_settings_past_any_sensible_number_are_refused_and_the_page_still_opens(firm) -> None:
    for body in ({"offer": 1e30}, {"offer": -1}, {"p_win": 1e30}):
        answered = firm.put(at("/negotiation/inputs"), json=body)
        assert answered.status_code in (200, 422), f"{body}: {answered.status_code}"
        assert firm.get(at("/negotiation")).status_code == 200, f"the analysis no longer opens after {body}"
    assert firm.put(at("/negotiation/inputs"), json={"offer": 1e30}).status_code == 422
    assert firm.get(at("/case")).status_code == 200 and firm.get(at("/negotiation")).status_code == 200
    assert firm.get("/api/matters/99999999999999999999999999/case").status_code in (404, 422)


@pytest.mark.xfail(reason="backend: PUT /api/settings/fee with a body of {\"percent\": NaN} is answered 500, not 422", strict=False)
def test_a_fee_that_is_not_a_number_is_refused(firm) -> None:
    for raw in ('{"percent": NaN}', '{"percent": Infinity}', '{"percent": -Infinity}', '{"percent": 1e400}'):
        answered = firm.put("/api/settings/fee", content=raw, headers={"content-type": "application/json"})
        assert answered.status_code in (400, 422), f"{raw}: {answered.status_code}"
    assert firm.get(at("/case")).status_code == 200 and firm.get(at("/negotiation")).status_code == 200


@pytest.mark.xfail(reason="backend: a digest run left in state running by a stopped server is still shown as running after a restart", strict=False)
def test_a_digest_left_running_by_a_stopped_server_is_not_shown_as_running(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch, [(MATTER, rows())])
    client.__exit__(None, None, None)
    conn = connect(tmp_path / "swans.db")
    conn.execute("INSERT INTO digest_runs (matter_id, started_at, state) VALUES (?,?,?)", (MATTER, "2031-01-01T00:00:00Z", "running"))
    conn.commit()
    conn.close()
    from server import firm_auth

    with TestClient(client.app, raise_server_exceptions=False) as restarted:
        firm_auth.passcode.cache_clear()
        assert restarted.post(firm_auth.LOGIN, json={"passcode": PASSCODE}).status_code == 200
        shown = restarted.get(at("/digest"))
        assert shown.status_code == 200
        assert shown.json().get("state") != "running", "a run nobody is working on is shown as running after a restart"


def test_the_firms_lists_are_closed_without_a_session(firm) -> None:
    with TestClient(firm.app, raise_server_exceptions=False) as stranger:
        for path in ("/api/status", "/api/matters", "/api/firm/overview", "/api/spend", "/api/cards/catalog", "/api/share/../matters", "/api/share/%2e%2e/matters"):
            assert stranger.get(path).status_code == 401, path
        assert stranger.post("/api/matters/import/zip", content=b"PK").status_code == 401
        assert stranger.post("/api/matters", json={"name": "Synthetic"}).status_code == 401
        assert stranger.post(at("/archive")).status_code == 401
