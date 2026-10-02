"""The assistant's documents: saved in our own database, served as PDFs to the firm only.

Checked on a synthetic matter over HTTP, with no model and no case data:
- a saved document comes back as a parsable PDF whose table header is drawn on
  every page of the table and whose references are listed at the end;
- the PDF, the files list and the history list need the firm's session;
- a document id asked for under another matter is 404;
- a document can be built in code with no model call, and the kinds on offer
  come from the server;
- with no model reachable a turn is still answered in code, never with an error
  and never with an uncited row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_provider_routes import MATTER, PASSCODE, rows

from server.db import connect, upsert_item
from shared import assistant_contract as a

BASE = f"/api/matters/{MATTER}/assistant"
ROWS = 90  # enough rows to run the table over several pages


def synthetic_document() -> a.SavedDocument:
    """A document made of invented values, shaped as the assistant saves one."""
    citations, entries = {}, []
    for n in range(ROWS):
        ref = f"document:7:p{n + 1}#0"
        citations[ref] = a.Citation(
            id=ref, claim_id=ref, node_id="document:7", kind="document", clio_id=7, page=n + 1, label="synthetic_records.pdf",
            date="2031-01-10", text=f"Synthetic statement {n}.", quote=f"synthetic quote {n}", quote_verified=n % 2 == 0,
            href=f"/api/matters/{MATTER}/sources/document/7?page={n + 1}",
        )
        entries.append(a.DocumentEntry(
            date=f"2031-02-{n % 28 + 1:02d}", date_basis="document", provider="Synthetic Clinic", provider_as_printed="SYNTHETIC CLINIC INC",
            what=f"Synthetic visit {n}: an invented sentence long enough to wrap inside its column of the table. " * 2,
            group="Synthetic Clinic", kind="treatment", source_label="synthetic_records.pdf", page=n + 1, cite=[ref],
        ))
    document = a.AssistantDocument(
        id="doc_synthetic0001", kind="medical_chronology", title="Synthetic chronology", created_at="2031-03-01T00:00:00Z",
        ledger_version="synthetic", conversation_id="cv_synthetic",
        columns=[a.DocumentColumn(key=key, label=label) for key, label in
                 (("date", "Date"), ("provider", "Provider"), ("what", "What the record says"), ("source", "Source"))],
        entries=entries, totals=a.DocumentTotals(entries=ROWS), href=f"{BASE}/documents/doc_synthetic0001",
    )
    return a.SavedDocument(document=document, citations=citations)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("CHECK_FAKE_MODEL", "")
    monkeypatch.setenv("FIRM_PASSCODE", PASSCODE)
    conn = connect(tmp_path / "swans.db")
    for kind, payload in rows():
        upsert_item(conn, 1, MATTER, kind, payload, "2031-01-01T00:00:00Z")
    conn.commit()
    from server.assistant import store

    store.ensure(conn)
    store.save_document(conn, MATTER, synthetic_document())
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


def test_a_saved_document_is_served_as_a_parsable_pdf(client):
    import pymupdf

    response = client.get(f"{BASE}/documents/doc_synthetic0001.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    pdf = pymupdf.open(stream=response.content, filetype="pdf")
    assert pdf.page_count >= 2 and int(response.headers["x-page-count"]) == pdf.page_count
    text = [page.get_text() for page in pdf]
    assert "Synthetic chronology" in text[0] and "not checked by a lawyer" in text[0]
    table_pages = [page for page in text if "Synthetic visit" in page]
    assert len(table_pages) >= 2 and all("What the record says" in page for page in table_pages)  # the header row repeats
    assert "References" in "".join(text) and f"[{ROWS}]" in text[-1] and "synthetic quote" in text[-1]


def test_download_and_conditional_requests(client):
    first = client.get(f"{BASE}/documents/doc_synthetic0001.pdf?download=1")
    assert first.headers["content-disposition"].startswith("attachment")
    again = client.get(f"{BASE}/documents/doc_synthetic0001.pdf", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_the_files_and_history_routes_are_the_firms(client, visitor):
    for path in ("/documents", "/documents/doc_synthetic0001", "/documents/doc_synthetic0001.pdf", "/conversations", "/document-kinds"):
        assert visitor.get(BASE + path).status_code == 401, path
    assert visitor.post(f"{BASE}/documents", json={"kind": "medical_chronology"}).status_code == 401
    assert visitor.post(BASE, json={"message": "anything"}).status_code == 401


def test_a_document_of_another_matter_is_not_found(client):
    assert client.get(f"/api/matters/{MATTER + 1}/assistant/documents/doc_synthetic0001.pdf").status_code == 404
    assert client.get(f"/api/matters/{MATTER + 1}/assistant/documents/doc_synthetic0001").status_code == 404
    assert client.get(f"{BASE}/documents/doc_missing.pdf").status_code == 404


def test_the_files_list_carries_counts_and_the_pdf_link(client):
    listed = client.get(f"{BASE}/documents").json()
    row = next(row for row in listed if row["id"] == "doc_synthetic0001")
    assert row["entries"] == ROWS and row["citations"] == ROWS and row["pages"] is None
    assert row["pdf_href"] == f"{BASE}/documents/doc_synthetic0001.pdf"
    pages = int(client.get(row["pdf_href"]).headers["x-page-count"])
    assert next(row for row in client.get(f"{BASE}/documents").json() if row["id"] == "doc_synthetic0001")["pages"] == pages


def test_a_document_is_built_in_code_without_a_model(client):
    kinds = client.get(f"{BASE}/document-kinds").json()
    assert kinds and all(kind["title"] and kind["prompt"] for kind in kinds)
    for kind in kinds:
        made = client.post(f"{BASE}/documents", json={"kind": kind["kind"]})
        assert made.status_code == 200, kind["kind"]
        body = made.json()
        assert all(ref in body["citations"] for entry in body["document"]["entries"] for ref in entry["cite"])
        assert client.get(body["document"]["pdf_href"]).headers["content-type"] == "application/pdf"


def test_deleting_a_document_removes_only_our_copy(client):
    assert client.delete(f"{BASE}/documents/doc_synthetic0001").status_code == 204
    assert client.get(f"{BASE}/documents/doc_synthetic0001.pdf").status_code == 404
    assert client.delete(f"{BASE}/documents/doc_synthetic0001").status_code == 404


def test_with_no_model_a_turn_is_answered_in_code(client):
    """No key is set here, so no model can be reached: the turn still returns, says so, and cites every row it shows."""
    response = client.post(BASE, json={"message": "synthetic question about a chronology", "mode": "fast"})
    assert response.status_code == 200
    turn = response.json()
    assert turn["usage"]["model_calls"] == 0 and any("model" in warning for warning in turn["warnings"])
    assert turn["blocks"] and all(row["cite"] for block in turn["blocks"] for row in block.get("rows") or [])
    assert all(ref in turn["citations"] for block in turn["blocks"] for row in block.get("rows") or [] for ref in row["cite"])
    assert any(step["tool"] == "search_file" for step in turn["trace"])
    listed = client.get(f"{BASE}/conversations").json()
    assert listed and listed[0]["id"] == turn["conversation_id"] and listed[0]["turns"] == 1 and listed[0]["title"]
    # asked again, it is worked out again: an answer made without the model is not kept as the saved answer
    again = client.post(BASE, json={"message": "synthetic question about a chronology", "mode": "fast"}).json()
    assert not any(warning.startswith("Saved answer") for warning in again["warnings"])


def test_a_document_saved_before_the_count_was_kept_is_counted(client, tmp_path):
    """A row with no stored count (saved by an earlier version) is listed with the number of references it holds."""
    conn = connect(tmp_path / "swans.db")
    conn.execute("UPDATE assistant_documents SET citations=NULL")
    conn.commit()
    conn.close()
    row = next(row for row in client.get(f"{BASE}/documents").json() if row["id"] == "doc_synthetic0001")
    assert row["citations"] == ROWS


def test_regenerate_skips_the_saved_answer(tmp_path, client):
    """A saved answer is replayed for the same question; `fresh` works it out again."""
    from server.assistant import engine, store
    from server.assistant.tools import Toolbox
    from server.config import get_settings

    cfg = get_settings()
    conn = connect(tmp_path / "swans.db")
    request = a.AssistantRequest(message="synthetic question", mode="fast")
    turn = engine.Turn(cfg, MATTER, request)
    box = Toolbox(cfg, conn, MATTER)
    key = engine._key(engine.PROMPT_VERSION, box.version, turn.mode, turn.model, turn.budget.effort, "synthetic question", [], [])
    saved = a.AssistantTurn(conversation_id="cv_saved", turn_id="t_saved", at="2031-01-01T00:00:00Z", mode="fast", model=turn.model,
                            question="synthetic question", ledger_version=box.version,
                            blocks=[a.Block(type="heading", text="A saved synthetic answer")])
    store.ensure(conn)
    store.save_turn(conn, MATTER, key, saved)
    conn.close()
    replayed = client.post(BASE, json={"message": "synthetic question", "mode": "fast"}).json()
    assert replayed["blocks"][0]["text"] == "A saved synthetic answer" and replayed["warnings"][-1].startswith("Saved answer")
    again = client.post(BASE, json={"message": "synthetic question", "mode": "fast", "fresh": True}).json()
    assert not any(warning.startswith("Saved answer") for warning in again["warnings"])
    assert again["blocks"][0].get("text") != "A saved synthetic answer"


def test_a_conversation_older_than_the_list_shows_can_still_be_renamed_and_opened(tmp_path, client):
    """The history list shows the 200 most recent; one outside it is still found by its id."""
    from server.assistant import store

    conn = connect(tmp_path / "swans.db")
    store.ensure(conn)
    for n in range(205):
        store.save_turn(conn, MATTER, f"key-{n}", a.AssistantTurn(
            conversation_id=f"cv_{n:03d}", turn_id=f"t_{n:03d}", at="2031-01-01T00:00:00Z", mode="fast", model="synthetic",
            question=f"synthetic question {n}", ledger_version="synthetic"))
    conn.close()
    assert len(client.get(f"{BASE}/conversations").json()) == 200
    renamed = client.put(f"{BASE}/conversations/cv_000", json={"title": "Synthetic name"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Synthetic name" and renamed.json()["turns"] == 1
    assert client.get(f"{BASE}/conversations/cv_000").json()["title"] == "Synthetic name"
    assert client.put(f"{BASE}/conversations/cv_missing", json={"title": "x"}).status_code == 404
    assert client.delete(f"{BASE}/conversations/cv_000").status_code == 204
    assert client.get(f"{BASE}/conversations/cv_000").status_code == 404


def test_opening_the_section_on_a_new_database_from_several_requests_at_once(tmp_path):
    """The section asks for its lists together. On a database with no assistant tables yet, and on one made
    before the count columns existed, every one of those first requests must find the tables ready."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    from server.assistant import store

    def first_request(path) -> int:
        conn = connect(path)
        try:
            store.ensure(conn)
            return len(store.documents(conn, MATTER)) + len(store.conversations(conn, MATTER))
        finally:
            conn.close()

    new = tmp_path / "new.db"
    connect(new).close()
    older = tmp_path / "older.db"
    conn = sqlite3.connect(older)
    conn.execute(  # the table as the first version of this module created it
        "CREATE TABLE assistant_documents (matter_id INTEGER NOT NULL, document_id TEXT NOT NULL, conversation_id TEXT,"
        " kind TEXT NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL, ledger_version TEXT NOT NULL,"
        " entries INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (matter_id, document_id))"
    )
    conn.commit()
    conn.close()
    connect(older).close()  # as at server start: the database is opened once before any request arrives
    for path in (new, older):
        with ThreadPoolExecutor(8) as pool:
            assert list(pool.map(first_request, [path] * 16)) == [0] * 16
        columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(assistant_documents)")}
        assert {"citations", "pages"} <= columns
