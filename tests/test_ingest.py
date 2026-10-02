"""Upload and incremental ingestion: only what is new is sent to the model.

The files are generated here at run time from invented, neutral sentences; the
model is replaced by a function that counts its calls. Nothing from any matter.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import ingest
from server.config import Settings
from server.db import connect, now_iso, upsert_item
from server.digest import llm, schemas

MATTER = 7
WORDS = "amber birch cedar delta ember fjord grove harbor inlet juniper kettle lagoon meadow nectar orchard pebble quarry ridge".split()


def page_text(seed: int, lines: int = 12) -> str:
    """Invented sentences, different for every seed."""
    out = []
    for line in range(lines):
        picks = [WORDS[(seed * 7 + line * 3 + k * 5) % len(WORDS)] for k in range(6)]
        out.append(f"Line {line + 1} of sheet {seed}: the {picks[0]} {picks[1]} sits beside the {picks[2]} {picks[3]} near {picks[4]} {picks[5]}.")
    return "\n".join(out)


def pdf(seeds: list[int], stamp: dict[int, str] | None = None) -> bytes:
    document = pymupdf.open()
    for seed in seeds:
        sheet = document.new_page()
        sheet.insert_text((54, 72), page_text(seed), fontsize=10)
        if stamp and seed in stamp:
            sheet.insert_text((54, 700), stamp[seed], fontsize=8)
    data = document.tobytes()
    document.close()
    return data


def scan(seed: int) -> bytes:
    """A one-page PDF that is only a picture: no text layer."""
    source = pymupdf.open()
    source.new_page().insert_text((54, 72), page_text(seed), fontsize=10)
    png = source[0].get_pixmap(dpi=60).tobytes("png")
    source.close()
    document = pymupdf.open()
    sheet = document.new_page()
    sheet.insert_image(sheet.rect, stream=png)
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture()
def site(tmp_path: Path, monkeypatch):
    cfg = Settings(
        clio_client_id="", clio_client_secret="", clio_redirect_uri="", clio_base_url="http://clio.invalid", clio_matter_id=MATTER,
        openai_api_key="test", digest_model="model-a", digest_model_bulk="model-b", data_dir=tmp_path,
    )
    conn = connect(cfg.db_path)
    upsert_item(conn, 0, MATTER, "matter", {"id": MATTER, "display_number": "T-1", "description": "Test matter"}, now_iso())
    conn.commit()
    calls: list[tuple[str, list[int]]] = []

    def answer(pages: list[int], texts: dict[int, str]) -> schemas.PageBatch:
        reads = []
        for page in pages:
            first = (texts.get(page) or "").split("\n", 1)[0]
            facts = [schemas.PageFact(kind="other", statement=f"Page says: {first}", quote=first, date=None, amount_usd=None, party=None)] if first else []
            reads.append(schemas.PageRead(page=page, page_type="other", title=None, issuer=None, document_date=None,
                                          shows_photo_of_a_person=False, checkboxes=[], facts=facts))
        return schemas.PageBatch(pages=reads)

    def fake_text(cfg_, texts, model):
        calls.append(("text", sorted(texts)))
        return answer(sorted(texts), texts), llm.Usage(model=model, input_tokens=100 * len(texts), output_tokens=50, cost_usd=0.001 * len(texts))

    def fake_image(cfg_, path, pages, model, **_):
        calls.append(("image", list(pages)))
        return answer(list(pages), {}), llm.Usage(model=model, input_tokens=1000 * len(pages), output_tokens=50, cost_usd=0.01 * len(pages))

    monkeypatch.setattr(ingest, "read_text_pages", fake_text)
    monkeypatch.setattr(ingest.pipeline, "read_pages", fake_image)
    monkeypatch.setattr(ingest, "model_answers", lambda *_: True)
    app = FastAPI()
    app.include_router(ingest.router)
    app.dependency_overrides[ingest._settings] = lambda: cfg
    yield TestClient(app), cfg, conn, calls
    conn.close()


def send(client: TestClient, name: str, data: bytes) -> dict:
    response = client.post(f"/api/matters/{MATTER}/documents/upload", files={"file": (name, data, "application/pdf")})
    assert response.status_code in (200, 202), response.text
    report = response.json()
    deadline = time.monotonic() + 30
    while report["state"] not in ("complete", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        report = client.get(f"/api/matters/{MATTER}/ingestions/{report['id']}").json()
    assert report["state"] == "complete", report
    return report


def test_multipart_body_is_parsed_byte_for_byte() -> None:
    data = bytes(range(256)) * 4 + b"\r\n--x\r\n"
    boundary = "edge123"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="folder_id"\r\n\r\n12\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    name, parsed, fields = ingest.parse_upload(f"multipart/form-data; boundary={boundary}", body, {})
    assert (name, fields["folder_id"]) == ("a.pdf", "12")
    assert hashlib.sha256(parsed).digest() == hashlib.sha256(data).digest()


def test_new_then_identical_then_shared_pages(site) -> None:
    client, cfg, conn, calls = site

    # A new three-page document: every page is read, as text, in one batched call.
    first = send(client, "first.pdf", pdf([1, 2, 3]))
    assert first["document_id"] < 0 and first["origin"]
    assert (first["counts"]["pages_total"], first["counts"]["pages_text"], first["counts"]["pages_skipped"]) == (3, 3, 0)
    assert calls == [("text", [1, 2, 3])]
    assert first["counts"]["claims_added"] == 3 and first["graph_version"]

    # The identical file: recognised by its hash, nothing stored, no call.
    calls.clear()
    again = send(client, "renamed copy.pdf", _stored_bytes(cfg, conn, first["document_id"]))
    assert again["duplicate_of"]["document_id"] == first["document_id"]
    assert again["counts"]["model_calls"] == 0 and calls == []
    assert conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 1

    # A document sharing two pages with the first: only its one new page is read.
    third = send(client, "third.pdf", pdf([2, 9, 3]))
    assert calls == [("text", [2])]
    assert (third["counts"]["pages_identical"], third["counts"]["pages_text"], third["counts"]["model_calls"]) == (2, 1, 1)
    assert [page["how"] for page in third["pages"]] == ["identical", "text", "identical"]
    assert third["pages"][0]["of"] == {"document_id": first["document_id"], "name": "first.pdf", "page": 2}
    assert third["counts"]["claims_added"] == 3  # the reused reads are attached to the new document and its own pages
    assert third["saving"]["actual_cost_usd"] == pytest.approx(0.001)
    assert third["graph_version"] != first["graph_version"]

    # The local records are documents with a negative id and their origin, and never left for Clio.
    rows = conn.execute("SELECT clio_id, payload FROM clio_items WHERE kind='document' ORDER BY clio_id").fetchall()
    assert len(rows) == 2 and all(int(row["clio_id"]) < 0 and json.loads(row["payload"])["origin"] == first["origin"] for row in rows)


def _stored_bytes(cfg: Settings, conn, document_id: int) -> bytes:
    path = conn.execute("SELECT path FROM document_blobs WHERE document_id=?", (document_id,)).fetchone()["path"]
    return (cfg.data_dir / path).read_bytes()


def test_near_copy_reuses_the_read_and_a_changed_page_does_not(site) -> None:
    client, _cfg, _conn, calls = site
    send(client, "base.pdf", pdf([4, 5]))
    calls.clear()
    # Page 4 with a short stamp added is a near-copy; page 5 with a new sentence is not.
    report = send(client, "stamped.pdf", pdf([4, 5], stamp={4: "COPY 0042", 5: "The kettle was moved to the orchard on the second day of the survey."}))
    assert [page["how"] for page in report["pages"]] == ["near_copy", "text"]
    assert calls == [("text", [2])]
    assert report["counts"]["pages_near_copy"] == 1 and "near-copy of page 1 of base.pdf" in report["pages"][0]["note"]


def test_image_page_goes_to_the_model_once_and_its_copy_never(site) -> None:
    client, _cfg, _conn, calls = site
    picture = scan(6)
    one = send(client, "scan.pdf", picture)
    assert calls == [("image", [1])] and one["counts"]["pages_model"] == 1
    calls.clear()
    # The same scanned page inside another file, behind a new text page.
    mixed = pymupdf.open()
    mixed.insert_pdf(pymupdf.open(stream=pdf([8]), filetype="pdf"))
    mixed.insert_pdf(pymupdf.open(stream=picture, filetype="pdf"))
    two = send(client, "bundle.pdf", mixed.tobytes())
    assert calls == [("text", [1])]
    assert [page["how"] for page in two["pages"]] == ["text", "identical"]


def test_uploaded_document_is_a_graph_node_with_searchable_claims(site) -> None:
    client, cfg, conn, _calls = site
    report = send(client, "node.pdf", pdf([11]))
    from server.case import CaseBuilder
    from server.graph import build

    payload = build.build(cfg, conn, CaseBuilder(conn, MATTER))
    node = next(i for i, n in enumerate(payload["nodes"]) if n["id"] == report["node_id"])
    assert payload["nodes"][node]["href"] == report["href"]
    mine = [text for text, parent in zip(payload["claims"]["text"], payload["claims"]["node"]) if parent == node]
    assert len(mine) == 1 and "sheet 11" in mine[0]
    assert "sheet" in payload["index"]["terms"]


def test_new_claims_are_set_against_neighbouring_entries_and_cards_are_only_appended(site, monkeypatch) -> None:
    client, cfg, conn, _calls = site
    from server.digest import pipeline, prompts

    # One entry of the firm's, with its stored claim, and cards that are current and hold one reviewed conflict.
    upsert_item(conn, 0, MATTER, "note", {"id": 5, "subject": "Survey status", "detail": "The kettle count for the orchard survey is unknown.", "date": "2031-05-01"}, now_iso())
    entry_row = next(e for e in pipeline.text_items(conn, MATTER) if e["key"] == "note:5")
    claim = {"item": "note:5", "kind": "valuation", "topic": "kettle count", "statement": "The kettle count for the orchard survey is unknown.",
             "quote": "The kettle count for the orchard survey is unknown.", "date": None, "amount_usd": None, "party": None, "category": "internal"}
    conn.execute(
        "INSERT INTO item_claims (matter_id, kind, clio_id, content_hash, prompt_version, model, result, at) VALUES (?,?,?,?,?,?,?,?)",
        (MATTER, "note", "5", entry_row["hash"], prompts.CLAIMS_VERSION, cfg.digest_model, json.dumps([claim]), now_iso()),
    )
    kept = {"topic": "earlier card", "summary": "Entries say one thing; a document shows another.", "notes_claim_ids": ["note:5#0"],
            "document_claim_ids": ["document:900:p1#0"], "entry_position": "different", "affects": "none", "severity": 1}
    empty = {"conflicts": [kept], "key_facts": [], "events": [], "node_evidence": [], "economics": [], "summary": [], "issuers": []}
    conn.execute(
        "INSERT INTO reconciliations (matter_id, input_hash, prompt_version, model, result, at, claims) VALUES (?,?,?,?,?,?,?)",
        (MATTER, pipeline.reconcile_input(cfg, conn, MATTER)[0], prompts.RECONCILE_VERSION, cfg.digest_model, json.dumps(empty), now_iso(), "{}"),
    )
    conn.commit()

    def fake_text(cfg_, texts, model):
        reads = [
            schemas.PageRead(page=page, page_type="other", title=None, issuer=None, document_date=None, shows_photo_of_a_person=False, checkboxes=[],
                             facts=[schemas.PageFact(kind="valuation", statement="The kettle count for the orchard survey is forty.", quote="Line 1", date=None, amount_usd=None, party=None)])
            for page in sorted(texts)
        ]
        return schemas.PageBatch(pages=reads), llm.Usage(model=model, input_tokens=10, output_tokens=5, cost_usd=0.001)

    sent: list[str] = []

    def fake_structured(cfg_, *, model, instructions, content, schema, **_):
        sent.extend(part["text"] for part in content)
        found = schemas.ConflictOut(topic="kettle count", summary="Entries say it is unknown; a document shows a count.",
                                    notes_claim_ids=["note:5#0", "note:999#0"], document_claim_ids=["document:-1:p1#0", "document:77:p1#0"],
                                    entry_position="outstanding", affects="none", severity=1)
        return schema(conflicts=[found]), llm.Usage(model=model, input_tokens=20, output_tokens=10, cost_usd=0.002)

    monkeypatch.setattr(ingest, "read_text_pages", fake_text)
    monkeypatch.setattr(ingest.llm, "structured", fake_structured)
    report = send(client, "count.pdf", pdf([14]))

    assert report["counts"]["cards_touched"] == 1 and report["counts"]["model_calls"] == 2
    assert report["cards"][0]["claim_ids"] == ["note:5#0", "document:-1:p1#0"]  # the ids the model invented are gone
    assert any("note:5#0" in text for text in sent)
    row = conn.execute("SELECT result, claims, input_hash FROM reconciliations WHERE matter_id=?", (MATTER,)).fetchone()
    stored = schemas.Reconciled.model_validate(json.loads(row["result"]))
    assert [c.topic for c in stored.conflicts] == ["earlier card", "kettle count"]  # appended; the earlier card is as it was
    assert stored.conflicts[0].model_dump() == kept
    assert set(json.loads(row["claims"])) == {"note:5#0", "document:-1:p1#0"}
    assert row["input_hash"] == pipeline.reconcile_input(cfg, conn, MATTER)[0]  # the cards were current and still are


def test_a_text_page_with_tick_boxes_is_read_as_an_image(site) -> None:
    client, _cfg, _conn, calls = site
    document = pymupdf.open()
    sheet = document.new_page()
    sheet.insert_text((54, 72), page_text(17), fontsize=10)
    for row in range(3):  # three small drawn squares, as on a printed form
        sheet.draw_rect(pymupdf.Rect(54, 300 + 20 * row, 64, 310 + 20 * row))
    report = send(client, "form.pdf", document.tobytes())
    assert calls == [("image", [1])]
    assert report["pages"][0]["how"] == "image" and "tick-boxes" in report["pages"][0]["note"]


# --------------------------------------------------------------------------- a case file as a zip


def make_zip(files: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return buffer.getvalue()


def send_zip(client: TestClient, data: bytes, url: str = f"/api/matters/{MATTER}/import/zip", fields: dict | None = None) -> dict:
    response = client.post(url, files={"file": ("box.zip", data, "application/zip")}, data=fields or {})
    assert response.status_code == 202, response.text
    job = response.json()
    deadline = time.monotonic() + 30
    while job["state"] not in ("complete", "failed") and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/matters/{job['matter_id']}/imports/{job['id']}").json()
    assert job["state"] == "complete", job
    return job


def by_path(job: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in job["items"]}


def test_zip_paths_that_leave_the_folder_are_refused_and_nothing_is_written_outside(site) -> None:
    client, cfg, _conn, _calls = site
    job = send_zip(client, make_zip({"../escape.pdf": pdf([31]), "/absolute.pdf": pdf([32]), "Letters/kept.pdf": pdf([33])}))
    found = by_path(job)
    assert found["../escape.pdf"]["outcome"] == found["/absolute.pdf"]["outcome"] == "skipped"
    assert "outside the import folder" in found["../escape.pdf"]["reason"]
    assert found["Letters/kept.pdf"]["outcome"] == "stored" and job["files"] == {**job["files"], "total": 3, "stored": 1, "skipped": 2}
    written = {path.relative_to(cfg.data_dir).parts[0] for path in cfg.data_dir.rglob("*") if path.is_file()}
    assert written <= {"documents", "swans.db", "swans.db-wal", "swans.db-shm"}  # the spooled archive is gone too
    assert not (cfg.data_dir.parent / "escape.pdf").exists()


def test_zip_over_a_cap_is_refused_whole_with_a_plain_reason(site, monkeypatch) -> None:
    client, _cfg, conn, _calls = site
    url = f"/api/matters/{MATTER}/import/zip"
    bomb = client.post(url, files={"file": ("b.zip", make_zip({"zeros.txt": b"0" * (3 * 1024 * 1024)}), "application/zip")})
    assert bomb.status_code == 400 and "times its packed size" in bomb.json()["detail"]
    monkeypatch.setattr(ingest, "MAX_ZIP_FILES", 2)
    many = client.post(url, files={"file": ("m.zip", make_zip({f"{n}.txt": b"invented line" for n in range(3)}), "application/zip")})
    assert many.status_code == 413 and "limit is 2" in many.json()["detail"]
    assert client.post(url, files={"file": ("n.zip", b"not an archive", "application/zip")}).status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM clio_items WHERE kind='document'").fetchone()[0] == 0


def test_zip_folders_duplicates_nested_archives_and_unreadable_files(site) -> None:
    client, _cfg, conn, calls = site
    one = pdf([41])
    files = {
        "Box/Records/first.pdf": one,
        "Box/Bills/2031/copy of first.pdf": one,                      # identical bytes: stored once
        "Box/Bills/2031/statement.txt": b"Invented statement.\nTotal due: 12.00\n",
        "Box/Letters/note.eml": b"From: a@example.invalid\nTo: b@example.invalid\nSubject: Invented subject\nDate: Mon, 03 Mar 2031 10:00:00 +0000\n\nAn invented message body.\n",
        "Box/Records/broken.pdf": b"%PDF-1.7 this is not really a document",
        "Box/Records/sheet.xyz": b"\x00\x01 some other format",
        "Box/more.zip": make_zip({"inner.txt": b"inside"}),
        "Box/.hidden/secret.pdf": pdf([42]),
        "Box/top.txt": b"A file at the top of the box.\n",
    }
    job = send_zip(client, make_zip(files))
    found = by_path(job)
    assert (found["Box/Records/first.pdf"]["folder"], found["Box/Bills/2031/statement.txt"]["folder"], found["Box/top.txt"]["folder"]) == ("Records", "Bills / 2031", ingest.ROOT_FOLDER)
    assert found["Box/Bills/2031/copy of first.pdf"]["outcome"] == "duplicate"
    assert found["Box/Bills/2031/copy of first.pdf"]["document_id"] == found["Box/Records/first.pdf"]["document_id"]
    assert found["Box/more.zip"]["outcome"] == "skipped" and "archive inside" in found["Box/more.zip"]["reason"]
    assert found["Box/.hidden/secret.pdf"]["reason"] == "a hidden or system file"
    assert found["Box/Records/broken.pdf"]["outcome"] == found["Box/Records/sheet.xyz"]["outcome"] == "not_read"
    assert found["Box/Letters/note.eml"]["outcome"] == "stored" and found["Box/Letters/note.eml"]["read"] == "read"
    assert job["files"] == {"total": 9, "done": 9, "stored": 4, "already_in_file": 0, "duplicates": 1, "skipped": 2, "not_read": 2, "waiting_to_be_read": 0}
    rows = {json.loads(r["payload"])["name"]: json.loads(r["payload"]) for r in conn.execute("SELECT payload FROM clio_items WHERE kind='document'")}
    assert rows["statement.txt"]["parent"]["name"] == "Bills / 2031" and rows["note.eml"]["received_at"].startswith("2031-03-03")
    assert len(rows) == 6 and all(kind == "text" for kind, _ in calls)  # text files and the email were read from their text, no image sent

    # The same archive again: everything readable is already in the file, and no call is made.
    calls.clear()
    again = send_zip(client, make_zip(files))
    assert again["files"]["stored"] == 0 and again["files"]["already_in_file"] == 6 and calls == []


def test_zip_import_completes_without_the_model_and_leaves_the_pages_for_the_digest(site, monkeypatch) -> None:
    client, cfg, conn, _calls = site
    attempts: list[int] = []

    def unreachable(cfg_, texts, model):
        attempts.append(len(texts))
        raise llm.LLMError("model call failed: APIConnectionError")

    monkeypatch.setattr(ingest, "read_text_pages", unreachable)
    monkeypatch.setattr(ingest, "model_answers", lambda *_: False)
    box = make_zip({"A/one.pdf": pdf([51, 52]), "A/two.pdf": pdf([53]), "B/three.pdf": pdf([54])})
    job = send_zip(client, box)
    assert job["model"] == "unavailable" and job["files"]["stored"] == 3 and job["files"]["waiting_to_be_read"] == 3
    assert job["pages"] == {"total": 4, "known": 0, "text": 0, "image": 0, "waiting": 4}
    assert all(entry["read"] == "waiting_to_be_read" for entry in job["items"])
    assert attempts == []  # one fail-fast question was asked; not a single page was sent, for any file
    from server.digest import pipeline

    total, pending = pipeline.pages_pending(cfg, conn, MATTER)
    assert total == 4 and sum(len(pages) for _, pages in pending) == 4  # the ordinary digest finds them

    # The model answers again: importing the same archive reads the stored files instead of storing them twice.
    def answers(cfg_, texts, model):
        reads = [schemas.PageRead(page=page, page_type="other", title=None, issuer=None, document_date=None, shows_photo_of_a_person=False, checkboxes=[], facts=[])
                 for page in sorted(texts)]
        return schemas.PageBatch(pages=reads), llm.Usage(model=model, input_tokens=10, output_tokens=5, cost_usd=0.001)

    monkeypatch.setattr(ingest, "read_text_pages", answers)
    monkeypatch.setattr(ingest, "model_answers", lambda *_: True)
    later = send_zip(client, box)
    assert later["files"]["stored"] == 0 and later["files"]["already_in_file"] == 3 and later["model"] == "used"
    assert later["pages"] == {"total": 4, "known": 0, "text": 4, "image": 0, "waiting": 0}
    assert conn.execute("SELECT COUNT(*) FROM clio_items WHERE kind='document'").fetchone()[0] == 3


def test_zip_creates_a_new_case_through_the_digest_entry_point(site, monkeypatch) -> None:
    client, _cfg, conn, _calls = site
    made: list[tuple] = []

    def create_matter(cfg_, conn_, name, client_name=None, number=None):  # stands in for the digest's own
        made.append((name, client_name, number))
        upsert_item(conn_, 0, -9, "matter", {"id": -9, "display_number": number or name, "description": name}, now_iso())
        conn_.commit()
        return -9

    monkeypatch.setattr(ingest.entry, "create_matter", create_matter, raising=False)
    refused = client.post("/api/matters/import/zip", files={"file": ("box.zip", make_zip({"a.pdf": pdf([61])}), "application/zip")})
    assert refused.status_code == 400 and "name" in refused.json()["detail"]
    for fields in ({"name": "N" * 201}, {"name": "Invented case", "client_name": "C" * 201}, {"name": "Invented case", "number": "9" * 81}):
        long = client.post("/api/matters/import/zip", files={"file": ("box.zip", make_zip({"a.pdf": pdf([61])}), "application/zip")}, data=fields)
        assert long.status_code == 400 and "longer than" in long.json()["detail"]
    assert made == []
    job = send_zip(client, make_zip({"a.pdf": pdf([61])}), url="/api/matters/import/zip", fields={"name": "Invented case", "number": "T-2"})
    assert made == [("Invented case", None, "T-2")] and job["matter_id"] == -9 and job["case_name"] == "Invented case"
    assert conn.execute("SELECT COUNT(*) FROM clio_items WHERE matter_id=-9 AND kind='document'").fetchone()[0] == 1


def test_zip_folder_is_the_level_at_which_the_files_divide_and_unread_types_are_stored_inert(site) -> None:
    client, cfg, conn, _calls = site
    job = send_zip(client, make_zip({
        "Wrapper/Papers/Records/a.pdf": pdf([71]), "Wrapper/Papers/Bills/b.pdf": pdf([72]), "Wrapper/Papers/Bills/2031/c.pdf": pdf([73]),
        "Wrapper/setup.json": b'{"invented": true}', "Wrapper/page.html": b"<script>invented()</script>",
    }))
    found = by_path(job)
    assert [found[path]["folder"] for path in ("Wrapper/Papers/Records/a.pdf", "Wrapper/Papers/Bills/b.pdf", "Wrapper/Papers/Bills/2031/c.pdf", "Wrapper/setup.json")] == ["Records", "Bills", "Bills / 2031", ingest.ROOT_FOLDER]
    assert found["Wrapper/setup.json"]["outcome"] == found["Wrapper/page.html"]["outcome"] == "not_read"
    kept = conn.execute("SELECT path FROM document_blobs WHERE document_id=?", (found["Wrapper/page.html"]["document_id"],)).fetchone()["path"]
    assert kept.endswith(".bin") and not list(cfg.data_dir.rglob("*.html"))
    single = send_zip(client, make_zip({"a/b/c/d/e/deep.pdf": pdf([74])}))
    assert single["items"][0]["folder"] == "e"


def test_zip_with_nothing_importable_is_refused_before_a_case_is_made(site) -> None:
    client, _cfg, conn, _calls = site
    refused = client.post("/api/matters/import/zip", files={"file": ("box.zip", make_zip({".hidden/a.pdf": pdf([75]), "inner.zip": b"PK"}), "application/zip")}, data={"name": "Invented case"})
    assert refused.status_code == 400 and "nothing in the archive can be imported" in refused.json()["detail"]
    assert conn.execute("SELECT COUNT(*) FROM clio_items WHERE kind='matter'").fetchone()[0] == 1


def test_only_a_real_pdf_is_shown_in_the_browser_everything_else_is_a_download(site) -> None:
    client, cfg, conn, _calls = site
    from server.app import document_file

    # A PDF sent under a web page's name is stored as a PDF; a web page inside an archive is stored inert.
    sent = client.post(f"/api/matters/{MATTER}/documents/upload?name=looks-like-a-page.html", content=pdf([81]), headers={"content-type": "application/pdf"})
    assert sent.status_code == 202
    stored = conn.execute("SELECT path FROM document_blobs WHERE document_id=?", (sent.json()["document_id"],)).fetchone()["path"]
    assert stored.endswith(".pdf")
    job = send_zip(client, make_zip({"Letters/page.html": b"<html><script>invented()</script></html>", "Letters/drawing.svg": b"<svg onload='invented()'/>", "Letters/real.pdf": pdf([82])}))
    found = by_path(job)
    for path in ("Letters/page.html", "Letters/drawing.svg"):
        response = document_file(MATTER, found[path]["document_id"], cfg, conn)
        assert response.media_type == "application/octet-stream" and response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].startswith("attachment")
    shown = document_file(MATTER, found["Letters/real.pdf"]["document_id"], cfg, conn)
    assert shown.media_type == "application/pdf" and "content-disposition" not in shown.headers


def test_a_picture_or_page_too_large_to_render_is_refused_before_it_is_rendered(site, monkeypatch) -> None:
    client, _cfg, conn, _calls = site
    source = pymupdf.open()
    source.new_page().insert_text((54, 72), page_text(83), fontsize=10)
    picture = source[0].get_pixmap(dpi=40).tobytes("png")
    url = f"/api/matters/{MATTER}/documents/upload"
    # An ordinary picture becomes a page of ordinary size, however many pixels it has.
    fine = client.post(url, files={"file": ("photo.png", picture, "image/png")})
    assert fine.status_code == 202
    path = conn.execute("SELECT path FROM document_blobs WHERE document_id=?", (fine.json()["document_id"],)).fetchone()["path"]
    with pymupdf.open(_cfg.data_dir / path) as stored:
        assert max(stored[0].rect.width, stored[0].rect.height) <= ingest.IMAGE_PAGE_POINTS + 1
    monkeypatch.setattr(ingest, "MAX_IMAGE_PIXELS", 10_000)
    refused = client.post(url, files={"file": ("huge.png", source[0].get_pixmap(dpi=50).tobytes("png"), "image/png")})
    assert refused.status_code == 415 and "megapixels" in refused.json()["detail"]
    monkeypatch.setattr(ingest, "MAX_PAGE_POINTS", 500)
    assert client.post(url, files={"file": ("wide.pdf", pdf([84]), "application/pdf")}).status_code == 415
    job = send_zip(client, make_zip({"A/wide.pdf": pdf([85]), "A/note.txt": b"An invented note.\n"}))
    assert by_path(job)["A/wide.pdf"]["outcome"] == "skipped" and "larger than" in by_path(job)["A/wide.pdf"]["reason"]
