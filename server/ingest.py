"""Document upload with incremental ingestion.

A lawyer uploads a file; it is stored in our own database (never sent to Clio),
and only what is new about it is read by the model. Mounted by `server/app.py`:

    from .ingest import router as ingest_router
    app.include_router(ingest_router)

What runs, in order, and what each step saves:

1. Whole-file hash. The same bytes already in the matter: nothing runs.
2. Per-page fingerprint. A page already in the matter under another document
   reuses that page's stored read, attached to the new document and page.
3. Near-copies. A text page that differs from a stored page only by a few
   tokens (a stamp, a page number, a fax header) reuses the read and is flagged.
4. Pages with a text layer are sent as text; only image pages are sent as images.
5. Reads are keyed by the file's hash and page, so reading again never duplicates.
6. Reconciliation is incremental: the new claims are compared with the entries
   that share a subject with them, in one small call. Existing cards and the
   attorney's reviews are not touched; the cards are never rebuilt here.
7. The stored rows change, so the graph version and the ledger stamp change by
   themselves and the graph, the search index and the checker pick the document up.

The uploaded document is a local record made by the digest's own entry point
(`digest/entry.add_document`): a `document` row with a negative id (Clio's ids are
positive, so the two can never collide) that carries its origin.

No document text is logged. Thresholds below are chosen, not measured; each says
what it guards against.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import io
import json
import mmap
import re
import secrets
import sqlite3
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import pages as page_files
from .case import source_href
from .config import Settings, get_settings
from .db import connect, content_hash, item, items, now_iso
from .digest import entry, llm, pipeline, prompts, schemas

router = APIRouter()

ORIGIN = "uploaded"
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000   # a picture is decoded whole to be rendered: 50 megapixels is about 150 MB of memory
MAX_PAGE_POINTS = 5000          # 69 inches; a page beyond it renders to hundreds of megabytes
IMAGE_PAGE_POINTS = 792         # a picture becomes a page this long on its longer side, whatever its pixel count
FINGERPRINT_VERSION = "f1"  # part of every stored fingerprint's key; bump when the rules below change

# -- near-copies of text pages: bottom-k MinHash over word shingles.
# MinHash rather than SimHash: it estimates the Jaccard overlap of the two shingle sets
# directly, with a known error (about sqrt(J(1-J)/k), 0.03 at k=128 and J=0.9), and a page
# with fewer than k shingles is compared exactly. SimHash's 64-bit Hamming distance is a
# coarser estimate on a single page's few hundred shingles, and its advantage - table
# lookups over millions of documents - is not needed for a matter's few hundred pages,
# which are compared directly. The estimate only nominates a candidate; reuse is decided
# on the two pages' actual text below.
SHINGLE_WORDS = 4
SKETCH_SIZE = 128
NEAR_ESTIMATE = 0.7          # MinHash estimate above which the stored page is opened and compared exactly
NEAR_JACCARD = 0.85          # exact shingle overlap required
NEAR_MAX_NEW_TOKENS = 8      # tokens the new page may add: room for a stamp or a page number, not for a sentence
TEXT_ONLY_CHARS_PER_PAGE = 20000  # no image carries the rest on the text path, so the cut is generous
IMAGE_COVER_FOR_IMAGE_PATH = 0.5  # a text-layer page that is mostly a picture is still read as an image
TICK_GLYPHS = "\u2610\u2611\u2612\u25a1\u25a0\u25a2\u2713\u2714\u2717\u2718"
TICK_BOX_POINTS = (5.0, 18.0)     # a drawn square this size (in points) is taken for a tick-box

# -- image pages: difference hash of the rendered page, confirmed block by block.
# The hash (16 x 16 comparisons of neighbouring cells) only nominates; a ticked box on an
# otherwise identical form does not move it, so a candidate is reused only if no 8 x 8 px
# block of the two renderings differs by more than BLOCK_TOLERANCE grey levels.
DHASH_DPI = 36
DHASH_COLS, DHASH_ROWS = 17, 16
DHASH_CANDIDATE_BITS = 10    # of 256
VERIFY_DPI = 50
STORE_SHRINK_EVERY_PAGES = 20  # measured on a made-up 250-page scan: peak 244 MB without, 106 MB with, 1.5 s slower
BLOCK = 8
BLOCK_TOLERANCE = 10.0       # of 255; re-encoding noise is below it, a pen stroke is above it

# -- incremental reconciliation
NEIGHBOURS_PER_CLAIM = 6
MAX_NEIGHBOURS = 80
MAX_NEW_CLAIMS = 150
INCREMENT_OUTPUT_TOKENS = 8000
READ_THREADS = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    matter_id     INTEGER NOT NULL,
    document_id   INTEGER NOT NULL,   -- negative: a local id, never one of Clio's
    upload_sha256 TEXT    NOT NULL,   -- of the bytes exactly as uploaded
    name          TEXT    NOT NULL,
    content_type  TEXT,
    size_bytes    INTEGER NOT NULL,
    uploaded_at   TEXT    NOT NULL,
    PRIMARY KEY (matter_id, document_id)
);
CREATE TABLE IF NOT EXISTS ingestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id   INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    state       TEXT    NOT NULL,     -- queued | running | complete | failed
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    report      TEXT    NOT NULL      -- the status object served to the page
);
CREATE TABLE IF NOT EXISTS import_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id   INTEGER NOT NULL,
    state       TEXT    NOT NULL,     -- queued | running | complete | failed
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    report      TEXT    NOT NULL      -- the status object served to the page
);
CREATE TABLE IF NOT EXISTS page_fingerprints (
    sha256  TEXT    NOT NULL,         -- of the stored document bytes
    page    INTEGER NOT NULL,
    version TEXT    NOT NULL,
    kind    TEXT    NOT NULL,         -- text | image
    exact   TEXT    NOT NULL,         -- equal means the same page
    sketch  TEXT    NOT NULL,         -- text: bottom-k MinHash, hex; image: difference hash, hex
    cover   REAL    NOT NULL,         -- share of the page covered by embedded images
    PRIMARY KEY (sha256, page, version)
);
"""

TEXT_ONLY_NOTE = "These pages are given as their text layer only; no image is attached. Report no checkbox whose state the text does not show."


STAGES = (
    ("stored", "Stored"),
    ("fingerprint", "Comparing pages with the matter"),
    ("read", "Reading new pages"),
    ("reconcile", "Comparing with the firm's entries"),
    ("done", "In the graph"),
)

_live: dict[int, str] = {}  # ingestion id -> status JSON while its thread runs
_live_lock = threading.Lock()
_id_lock = threading.Lock()
_matter_locks: dict[int, threading.Lock] = {}


def _settings() -> Settings:
    return get_settings()


def _open(cfg: Settings) -> sqlite3.Connection:
    conn = connect(cfg.db_path)
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------------- the upload itself


def parse_upload(content_type: str, body: bytes, query: dict[str, str]) -> tuple[str, bytes, dict[str, str]]:
    """(file name, file bytes, other form fields). Accepts multipart/form-data with a
    `file` part, or the raw file as the body with `?name=`. Parsed here so the server
    needs no extra dependency."""
    fields = dict(query)
    if not content_type.lower().startswith("multipart/form-data"):
        return fields.get("name") or "upload", body, fields
    found = re.search(r'boundary="?([^";]+)"?', content_type)
    if not found:
        raise HTTPException(400, "multipart body without a boundary")
    delimiter = b"--" + found.group(1).encode()
    name, data = None, None
    for part in body.split(delimiter)[1:]:
        if part.startswith(b"--"):
            break
        head, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload
        disposition = next(
            (line for line in head.decode("utf-8", "replace").split("\r\n") if line.lower().startswith("content-disposition")), ""
        )
        field = re.search(r'\bname="([^"]*)"', disposition)
        filename = re.search(r'\bfilename="([^"]*)"', disposition)
        if filename and data is None:
            name, data = filename.group(1), payload
        elif field:
            fields[field.group(1)] = payload.decode("utf-8", "replace").strip()
    if data is None:
        raise HTTPException(400, "no file in the upload: send it as the form field `file`")
    fields.setdefault("filename", name or "")
    return fields.get("name") or name or "upload", data, fields


def _file_kind(data: bytes) -> str | None:
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


class TooLarge(ValueError):
    """The file would cost too much memory to render; the message is the plain reason."""


def _as_pdf(data: bytes, kind: str) -> bytes:
    """A PDF is kept as it is; a picture becomes a one-page PDF so every page route serves it.
    Sizes are read from the file's own header and checked before anything is rendered."""
    import pymupdf

    if kind == "pdf":
        with pymupdf.open(stream=data, filetype="pdf") as opened:
            if any(max(sheet.rect.width, sheet.rect.height) > MAX_PAGE_POINTS for sheet in opened):
                raise TooLarge(f"a page is larger than {MAX_PAGE_POINTS} points on a side")
        return data
    with pymupdf.open(stream=data, filetype=kind) as image:
        width, height = image[0].rect.width, image[0].rect.height  # the picture's size in pixels
    if width * height > MAX_IMAGE_PIXELS:
        raise TooLarge(f"the picture is larger than {MAX_IMAGE_PIXELS // 1_000_000} megapixels")
    scale = IMAGE_PAGE_POINTS / max(width, height, 1)
    document = pymupdf.open()
    sheet = document.new_page(width=max(width * scale, 1), height=max(height * scale, 1))
    sheet.insert_image(sheet.rect, stream=data)
    pdf = document.tobytes()
    document.close()
    return pdf


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f]", "", name.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:200] or "upload"


def _new_report(matter_id: int, document_id: int, name: str) -> dict[str, Any]:
    return {
        "id": None,
        "matter_id": matter_id,
        "document_id": document_id,
        "node_id": f"document:{document_id}",
        "href": source_href(matter_id, "document", document_id),
        "name": name,
        "origin": ORIGIN,
        "state": "queued",
        "stage": "stored",
        "stages": [{"key": key, "label": label, "state": "pending", "detail": None} for key, label in STAGES],
        "counts": {
            "pages_total": 0, "pages_skipped": 0, "pages_identical": 0, "pages_near_copy": 0, "pages_text": 0,
            "pages_model": 0, "claims_added": 0, "cards_touched": 0, "model_calls": 0, "input_tokens": 0,
            "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "seconds": 0.0,
        },
        "duplicate_of": None,
        "pages": [],
        "cards": [],
        "graph_version": None,
        "summary": None,
        "saving": None,
        "error": None,
    }


def _stage(report: dict[str, Any], key: str, state: str, detail: str | None = None) -> None:
    for stage in report["stages"]:
        if stage["key"] == key:
            stage["state"], stage["detail"] = state, detail
    if state == "running":
        report["stage"] = key
    _publish(report)


def _publish(report: dict[str, Any]) -> None:
    if report.get("id") is not None:
        with _live_lock:
            _live[report["id"]] = json.dumps(report)


def _save(conn: sqlite3.Connection, report: dict[str, Any], finished: bool = False) -> None:
    conn.execute(
        "UPDATE ingestions SET state=?, finished_at=?, report=? WHERE id=?",
        (report["state"], now_iso() if finished else None, json.dumps(report), report["id"]),
    )
    conn.commit()
    if finished:
        with _live_lock:
            _live.pop(report["id"], None)  # the stored row is the status from here on
    else:
        _publish(report)


def _create(conn: sqlite3.Connection, report: dict[str, Any]) -> None:
    report["id"] = conn.execute(
        "INSERT INTO ingestions (matter_id, document_id, state, started_at, report) VALUES (?,?,?,?,?)",
        (report["matter_id"], report["document_id"], report["state"], now_iso(), "{}"),
    ).lastrowid
    _save(conn, report, finished=report["state"] in ("complete", "failed"))


def _unread_pages(cfg: Settings, conn: sqlite3.Connection, sha256: str, page_count: int) -> list[int]:
    done = {
        row["page"]
        for row in conn.execute(
            "SELECT page FROM page_reads WHERE sha256=? AND prompt_version=? AND model=?",
            (sha256, prompts.PAGES_VERSION, cfg.digest_model_bulk),
        )
    }
    return [page for page in range(1, page_count + 1) if page not in done]


def accept(cfg: Settings, matter_id: int, content_type: str, body: bytes, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """Store the upload and start its ingestion. Returns (HTTP status, status object)."""
    name, data, _fields = parse_upload(content_type, body, query)
    name = _clean_name(name)
    if not data:
        raise HTTPException(400, "the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    kind = _file_kind(data)
    if kind is None:
        raise HTTPException(415, "only PDF, PNG and JPEG files can be read")
    conn = _open(cfg)
    try:
        if not items(conn, matter_id, "matter"):
            raise HTTPException(404, f"matter {matter_id} has not been imported yet")
        upload_sha = hashlib.sha256(data).hexdigest()

        # 1. The same bytes are already in the matter: as an earlier upload, or as a document synced from Clio.
        twin = conn.execute(
            "SELECT u.document_id, b.sha256, b.page_count FROM uploads u JOIN document_blobs b"
            " ON b.matter_id=u.matter_id AND b.document_id=u.document_id WHERE u.matter_id=? AND u.upload_sha256=?",
            (matter_id, upload_sha),
        ).fetchone() or conn.execute(
            "SELECT document_id, sha256, page_count FROM document_blobs WHERE matter_id=? AND sha256=?", (matter_id, upload_sha)
        ).fetchone()
        if twin is not None:
            names = {int(d["id"]): d.get("name") or "" for d in items(conn, matter_id, "document")}
            existing = int(twin["document_id"])
            unread = _unread_pages(cfg, conn, twin["sha256"], twin["page_count"] or 0)
            report = _new_report(matter_id, existing, names.get(existing) or name)
            report["duplicate_of"] = {"document_id": existing, "name": names.get(existing) or name}
            if unread and existing < 0:
                # An earlier ingestion of this very file stopped part-way: carry on from where it stopped.
                _create(conn, report)
                _start(cfg, report)
                return 202, report
            total = twin["page_count"] or 0
            counts = report["counts"]
            counts["pages_total"], counts["pages_skipped"], counts["pages_identical"] = total, total - len(unread), total - len(unread)
            report["pages"] = [
                {"page": page, "how": "identical", "of": {"document_id": existing, "name": names.get(existing) or name, "page": page}, "note": "same file"}
                for page in range(1, total + 1)
            ]
            for key, _ in STAGES:
                _stage(report, key, "done" if key == "stored" else "skipped", "identical file already in the matter")
            report["state"], report["stage"] = "complete", "done"
            report["summary"] = (
                f"{name}: identical to a file already in the matter ({names.get(existing) or 'document ' + str(existing)},"
                f" {total} pages). Nothing was stored again and no model call was made."
            )
            if unread:
                report["summary"] += f" {len(unread)} of its pages have not been read yet; the matter's digest reads them."
            report["saving"] = _saving(conn, matter_id, cfg.digest_model_bulk, total, 0.0, 0)
            report["graph_version"] = _graph_version(cfg, conn, matter_id)  # unchanged: nothing was stored
            _create(conn, report)
            return 200, report

        try:
            pdf = _as_pdf(data, kind)
        except TooLarge as error:
            raise HTTPException(415, str(error)) from error
        except Exception as error:
            raise HTTPException(415, "the file could not be opened") from error
        import pymupdf

        try:
            with pymupdf.open(stream=pdf, filetype="pdf") as opened:
                page_count = opened.page_count
        except Exception as error:
            raise HTTPException(415, "the file could not be opened as a PDF") from error
        if not page_count:
            raise HTTPException(415, "the file has no pages")
        stored_name = f"{Path(name).stem}.pdf"  # what is stored is always a PDF, whatever suffix the sender gave it
        with _id_lock:
            # The digest's own entry point makes the local record: a document row with an id below zero.
            document_id = entry.add_document(cfg, conn, matter_id, stored_name, pdf)
            conn.execute(
                "INSERT OR REPLACE INTO uploads (matter_id, document_id, upload_sha256, name, content_type, size_bytes, uploaded_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (matter_id, document_id, upload_sha, name, kind, len(data), now_iso()),
            )
            conn.commit()
        report = _new_report(matter_id, document_id, stored_name)
        report["origin"] = (item(conn, matter_id, "document", document_id) or {}).get("origin") or ORIGIN
        report["counts"]["pages_total"] = page_count
        _create(conn, report)
        _stage(report, "stored", "done", f"{_n(page_count, 'page')}, {len(data)} bytes, kept in our database only")
        _save(conn, report)
        _start(cfg, report)
        return 202, report
    finally:
        conn.close()


def _start(cfg: Settings, report: dict[str, Any]) -> None:
    threading.Thread(target=_ingest, args=(cfg, report["id"]), name=f"ingest-{report['id']}", daemon=True).start()


# --------------------------------------------------------------------------- fingerprints (no model)


@dataclass(frozen=True)
class Fingerprint:
    sha256: str
    page: int
    kind: str      # text | image
    exact: str
    sketch: tuple[int, ...]  # text: bottom-k shingle hashes, ascending; image: one integer, the difference hash
    cover: float


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def shingles(words: list[str]) -> set[tuple[str, ...]]:
    if len(words) <= SHINGLE_WORDS:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def minhash(words: list[str]) -> tuple[int, ...]:
    """Bottom-k sketch: the k smallest 64-bit hashes of the page's shingles."""
    hashes = {int.from_bytes(hashlib.blake2b(" ".join(s).encode(), digest_size=8).digest(), "big") for s in shingles(words)}
    return tuple(sorted(hashes)[:SKETCH_SIZE])


def jaccard_estimate(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Share of the k smallest hashes of the union that both pages hold: an unbiased estimate of the
    shingle sets' Jaccard overlap, and exact when both pages have fewer than k shingles."""
    if not a or not b:
        return 0.0
    both = set(a) & set(b)
    union = sorted(set(a) | set(b))[: min(SKETCH_SIZE, max(len(a), len(b)))]
    return sum(1 for value in union if value in both) / len(union)


def jaccard(a: list[str], b: list[str]) -> float:
    one, two = shingles(a), shingles(b)
    return len(one & two) / len(one | two) if one and two else 0.0


def _grey(sheet, dpi: int) -> tuple[int, int, int, bytes]:
    import pymupdf

    pixmap = sheet.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return pixmap.width, pixmap.height, pixmap.stride, pixmap.samples


def difference_hash(width: int, height: int, stride: int, samples: bytes) -> int:
    """256 bits: the page averaged into 17 x 16 cells; each bit says whether a cell is darker than its right neighbour."""
    bits = 0
    for row in range(DHASH_ROWS):
        top, bottom = row * height // DHASH_ROWS, max((row + 1) * height // DHASH_ROWS, row * height // DHASH_ROWS + 1)
        means = []
        for column in range(DHASH_COLS):
            left, right = column * width // DHASH_COLS, max((column + 1) * width // DHASH_COLS, column * width // DHASH_COLS + 1)
            total = sum(sum(samples[y * stride + left : y * stride + right]) for y in range(top, min(bottom, height)))
            means.append(total / max((min(bottom, height) - top) * (right - left), 1))
        for column in range(DHASH_COLS - 1):
            bits = (bits << 1) | (1 if means[column] < means[column + 1] else 0)
    return bits


def same_picture(a: tuple[int, int, int, bytes], b: tuple[int, int, int, bytes]) -> bool:
    """True when no small block of the two renderings differs by more than the tolerance."""
    if a[:2] != b[:2]:
        return False
    width, height = a[0], a[1]
    columns = width // BLOCK
    for top in range(0, height - BLOCK + 1, BLOCK):
        sums_a, sums_b = [0] * columns, [0] * columns
        for y in range(top, top + BLOCK):
            row_a, row_b = a[3][y * a[2] : y * a[2] + width], b[3][y * b[2] : y * b[2] + width]
            for column in range(columns):
                sums_a[column] += sum(row_a[column * BLOCK : (column + 1) * BLOCK])
                sums_b[column] += sum(row_b[column * BLOCK : (column + 1) * BLOCK])
        if any(abs(x - y) > BLOCK_TOLERANCE * BLOCK * BLOCK for x, y in zip(sums_a, sums_b)):
            return False
    return True


def has_form_marks(sheet) -> bool:
    """True when the page looks like a form: form fields, tick-box glyphs or bracket boxes in the
    text, or two or more small drawn squares. A text layer does not say which box is ticked, so
    such a page is read as an image even though it has text."""
    try:
        if sheet.first_widget is not None:
            return True
        text = sheet.get_text()
        if any(glyph in text for glyph in TICK_GLYPHS) or re.search(r"\[[ xX]?\]", text):
            return True
        squares = 0
        for drawing in sheet.get_cdrawings():
            rect = drawing.get("rect")
            if not rect:
                continue
            width, height = rect[2] - rect[0], rect[3] - rect[1]
            if TICK_BOX_POINTS[0] <= width <= TICK_BOX_POINTS[1] and TICK_BOX_POINTS[0] <= height <= TICK_BOX_POINTS[1] and abs(width - height) <= 2:
                squares += 1
        return squares >= 2
    except Exception:
        return False


def fingerprint_page(sheet, sha256: str, number: int) -> Fingerprint:
    text = sheet.get_text()
    area = abs(sheet.rect.width * sheet.rect.height) or 1.0
    try:
        covered = sum(abs((info["bbox"][2] - info["bbox"][0]) * (info["bbox"][3] - info["bbox"][1])) for info in sheet.get_image_info())
    except Exception:
        covered = 0.0
    cover = round(min(covered / area, 1.0), 3)
    if len(text.strip()) >= page_files.MIN_TEXT_CHARS:
        # Two pages are the same page when their words are, and nothing but words differs either:
        # the counts of pictures and drawn paths and the values of form fields are part of the hash,
        # so a tick drawn on an otherwise identical form makes a different page.
        try:
            marks = [f"{widget.field_name}={widget.field_value}" for widget in sheet.widgets()]
            drawn = len(sheet.get_cdrawings())
        except Exception:
            marks, drawn = [], 0
        basis = "\x1f".join([" ".join(text.split()), str(len(sheet.get_images())), str(drawn), *marks])
        return Fingerprint(sha256, number, "text", hashlib.sha256(basis.encode()).hexdigest(), minhash(tokens(text)), cover)
    width, height, stride, samples = _grey(sheet, DHASH_DPI)
    return Fingerprint(
        sha256, number, "image", hashlib.sha256(samples).hexdigest(), (difference_hash(width, height, stride, samples),), cover
    )


def fingerprints(conn: sqlite3.Connection, path: Path, sha256: str, page_count: int) -> list[Fingerprint]:
    """Every page's fingerprint for one stored file, computed once per file and kept."""
    rows = conn.execute(
        "SELECT page, kind, exact, sketch, cover FROM page_fingerprints WHERE sha256=? AND version=? ORDER BY page",
        (sha256, FINGERPRINT_VERSION),
    ).fetchall()
    if len(rows) == page_count:
        return [
            Fingerprint(sha256, r["page"], r["kind"], r["exact"],
                        tuple(int(r["sketch"][i : i + 16], 16) for i in range(0, len(r["sketch"]), 16)) if r["kind"] == "text"
                        else (int(r["sketch"], 16),), r["cover"])
            for r in rows
        ]
    import pymupdf

    out: list[Fingerprint] = []
    try:
        with pymupdf.open(path) as document:
            for number, sheet in enumerate(document, start=1):
                out.append(fingerprint_page(sheet, sha256, number))
                if number % STORE_SHRINK_EVERY_PAGES == 0:
                    # The PDF library keeps every decoded page image in a process-wide cache (256 MB by default);
                    # over a long scan that cache, not the file, is the peak. It is emptied as we go.
                    pymupdf.TOOLS.store_shrink(100)
    except Exception:
        return []
    for mark in out:
        sketch = "".join(f"{value:016x}" for value in mark.sketch) if mark.kind == "text" else f"{mark.sketch[0]:x}"
        conn.execute(
            "INSERT OR REPLACE INTO page_fingerprints (sha256, page, version, kind, exact, sketch, cover) VALUES (?,?,?,?,?,?,?)",
            (sha256, mark.page, FINGERPRINT_VERSION, mark.kind, mark.exact, sketch, mark.cover),
        )
    conn.commit()
    return out


def near_copy(new_text: str, old_text: str, old_read: dict[str, Any]) -> tuple[bool, int]:
    """(may the stored read stand for the new page, tokens the new page adds). It may when the
    two pages overlap almost entirely, the new page adds no more than a stamp's worth of
    tokens, and every quote the stored read rests on is still on the new page word for word."""
    new_words, old_words = tokens(new_text), tokens(old_text)
    added = sum((Counter(new_words) - Counter(old_words)).values())
    if jaccard(new_words, old_words) < NEAR_JACCARD or added > NEAR_MAX_NEW_TOKENS:
        return False, added
    return all(pipeline.quote_in(fact.get("quote"), new_text) for fact in old_read.get("facts") or []), added


# --------------------------------------------------------------------------- the ingestion


def _matter_lock(matter_id: int) -> threading.Lock:
    with _id_lock:
        return _matter_locks.setdefault(matter_id, threading.Lock())


def _ingest(cfg: Settings, ingestion_id: int) -> None:
    conn = _open(cfg)
    started = time.monotonic()
    report = json.loads(conn.execute("SELECT report FROM ingestions WHERE id=?", (ingestion_id,)).fetchone()["report"])
    try:
        with _matter_lock(report["matter_id"]):  # one ingestion at a time per matter: they share the cards
            report["state"] = "running"
            _publish(report)
            unread = _run(cfg, conn, report, started)
        report["state"] = "failed" if unread else "complete"
        if unread:
            report["error"] = report["error"] or f"{unread} pages could not be read; upload the same file again to carry on from here"
    except llm.LLMError as error:
        report["state"], report["error"] = "failed", str(error)
    except Exception as error:  # the type only: a message may carry text from the file
        report["state"], report["error"] = "failed", type(error).__name__
    finally:
        report["counts"]["seconds"] = round(time.monotonic() - started, 2)
        try:
            _save(conn, report, finished=True)
        finally:
            conn.close()
            with _live_lock:
                _live.pop(ingestion_id, None)


def _read_key(cfg: Settings) -> tuple[str, str]:
    return prompts.PAGES_VERSION, cfg.digest_model_bulk


def _stored_read(cfg: Settings, conn: sqlite3.Connection, sha256: str, page: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT result FROM page_reads WHERE sha256=? AND page=? AND prompt_version=? AND model=?", (sha256, page, *_read_key(cfg))
    ).fetchone()
    return json.loads(row["result"]) if row else None


def _store_read(cfg: Settings, conn: sqlite3.Connection, sha256: str, page: int, read: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO page_reads (sha256, page, prompt_version, model, result, at) VALUES (?,?,?,?,?,?)",
        (sha256, page, *_read_key(cfg), json.dumps({**read, "page": page}, ensure_ascii=False), now_iso()),
    )


def read_text_pages(cfg: Settings, texts: dict[int, str], model: str) -> tuple[schemas.PageBatch, llm.Usage]:
    """The text path: the same instructions and answer shape as the digest's page read, without the images."""
    content = [llm.text_part(TEXT_ONLY_NOTE)]
    for page, text in texts.items():
        content.append(llm.text_part(f"Page {page}"))
        content.append(llm.text_part(f"Text layer of page {page}:\n{text[:TEXT_ONLY_CHARS_PER_PAGE]}"))
    return llm.structured(
        cfg, model=model, instructions=prompts.PAGES, content=content, schema=schemas.PageBatch, effort="low",
        max_output_tokens=pipeline.PAGE_BATCH_OUTPUT_TOKENS,
    )


class _Answer(schemas.Strict):
    ok: bool


def model_answers(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> bool:
    """One small request, no retries, short timeout: is the model there at all? Asked once per
    import before any page is sent, so an account that is out of credit costs one refused
    request instead of every file waiting through its own retries."""
    try:
        _, usage = llm.structured(
            cfg, model=cfg.digest_model_bulk, instructions="Return ok as true.", content=[llm.text_part("ready?")], schema=_Answer,
            effort="low", max_output_tokens=2000, timeout=20.0, max_retries=0,
        )
    except llm.LLMUsageError as error:  # it answered and was billed, even if the answer was cut short
        pipeline.record_call(conn, matter_id, None, "probe", error.usage, False)
        return True
    except llm.LLMError:
        return False
    pipeline.record_call(conn, matter_id, None, "probe", usage, True)
    return True


def _measured_rate(conn: sqlite3.Connection, matter_id: int, model: str) -> tuple[float | None, str]:
    """USD per page of the matter's own digest so far: what its page calls cost over the pages they read.
    An upload's calls carry no digest run id, so they are in the case's cost but not in this rate."""
    cost = conn.execute(
        "SELECT SUM(cost_usd) AS cost FROM llm_calls WHERE matter_id=? AND purpose='pages' AND ok=1 AND run_id IS NOT NULL", (matter_id,)
    ).fetchone()["cost"]
    read = conn.execute(
        "SELECT COUNT(*) AS n FROM page_reads r JOIN document_blobs b ON b.sha256=r.sha256 WHERE b.matter_id=? AND b.document_id>0"
        " AND r.prompt_version=? AND r.model=?",
        (matter_id, prompts.PAGES_VERSION, model),
    ).fetchone()["n"]
    if cost and read:
        return cost / read, f"this matter's digest: {read} pages read as images for ${cost:.4f}"
    return None, "no page of this matter has been read by the digest yet"


def _saving(conn: sqlite3.Connection, matter_id: int, model: str, pages_total: int, actual: float, pages_read: int,
            rate: tuple[float | None, str] | None = None) -> dict[str, Any]:
    per_page, basis = rate or _measured_rate(conn, matter_id, model)
    if per_page is None and pages_read:
        per_page, basis = actual / pages_read, f"this upload's own calls: {pages_read} pages read for ${actual:.4f}"
    scratch = None if per_page is None else round(per_page * pages_total, 4)
    return {
        "from_scratch_cost_usd": scratch,
        "actual_cost_usd": round(actual, 4),
        "saved_usd": None if scratch is None else round(max(scratch - actual, 0.0), 4),
        "per_page_rate_usd": None if per_page is None else round(per_page, 6),
        "rate_basis": basis,
    }


def _cards_current(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> bool:
    row = conn.execute("SELECT input_hash FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    return bool(row) and pipeline.reconcile_input(cfg, conn, matter_id)[0] == row["input_hash"]


def _run(cfg: Settings, conn: sqlite3.Connection, report: dict[str, Any], started: float, gate: dict[str, bool] | None = None) -> int:
    """Returns the number of pages left unread. `gate` is shared by the files of one import: once
    the model has proved unreachable, later files are stored and compared but not sent, instead of
    each waiting through its own retries; their pages stay unread for the ordinary digest."""
    matter_id, document_id, counts = report["matter_id"], report["document_id"], report["counts"]
    blob = conn.execute(
        "SELECT sha256, path, page_count FROM document_blobs WHERE matter_id=? AND document_id=?", (matter_id, document_id)
    ).fetchone()
    path, sha, total = cfg.data_dir / blob["path"], blob["sha256"], blob["page_count"]
    model = cfg.digest_model_bulk
    counts["pages_total"] = total

    # -- 2 and 3: compare each page with what the matter already holds
    _stage(report, "fingerprint", "running")
    cards_were_current = _cards_current(cfg, conn, matter_id)  # before anything of this document is stored
    rate = _measured_rate(conn, matter_id, model)
    names = {int(d["id"]): d.get("name") or "" for d in items(conn, matter_id, "document")}
    others: dict[str, tuple[int, Path]] = {}
    known: list[Fingerprint] = []
    for row in conn.execute(
        "SELECT document_id, sha256, path, page_count FROM document_blobs WHERE matter_id=? AND page_count IS NOT NULL AND sha256<>?",
        (matter_id, sha),
    ).fetchall():
        if row["document_id"] in names and (cfg.data_dir / row["path"]).exists():
            others[row["sha256"]] = (row["document_id"], cfg.data_dir / row["path"])
            known += fingerprints(conn, cfg.data_dir / row["path"], row["sha256"], row["page_count"])
    has_read = {
        (row["sha256"], row["page"])
        for row in conn.execute(
            "SELECT r.sha256, r.page FROM page_reads r JOIN document_blobs b ON b.sha256=r.sha256"
            " WHERE b.matter_id=? AND r.prompt_version=? AND r.model=?",
            (matter_id, *_read_key(cfg)),
        )
    }
    known = [mark for mark in known if (mark.sha256, mark.page) in has_read]  # only a page with a stored read can lend it
    by_exact: dict[tuple[str, str], Fingerprint] = {}
    for mark in known:
        by_exact.setdefault((mark.kind, mark.exact), mark)
    own = fingerprints(conn, path, sha, total)
    if len(own) != total:
        raise llm.LLMError("the stored file could not be opened to compare its pages")

    def origin_of(mark: Fingerprint) -> dict[str, Any]:
        other = others[mark.sha256][0] if mark.sha256 in others else document_id
        return {"document_id": other, "name": names.get(other, ""), "page": mark.page}

    import pymupdf

    plan: list[dict[str, Any]] = []
    first_here: dict[tuple[str, str], Fingerprint] = {}
    with pymupdf.open(path) as document:
        for mark in own:
            entry: dict[str, Any] = {"page": mark.page, "how": None, "of": None, "note": None, "source": None}
            plan.append(entry)
            if (sha, mark.page) in has_read:
                entry.update(how="identical", note="already read under this file", source=(sha, mark.page))
                continue
            twin = by_exact.get((mark.kind, mark.exact)) or first_here.get((mark.kind, mark.exact))
            if twin is not None:
                entry.update(how="identical", of=origin_of(twin), note="same page", source=(twin.sha256, twin.page))
                continue
            first_here[(mark.kind, mark.exact)] = mark
            sheet = document[mark.page - 1]
            if mark.kind == "text":
                best = max(
                    (other for other in known if other.kind == "text"),
                    key=lambda other: jaccard_estimate(mark.sketch, other.sketch), default=None,
                )
                if best is not None and jaccard_estimate(mark.sketch, best.sketch) >= NEAR_ESTIMATE:
                    old_text = page_files.page_text(others[best.sha256][1], best.page) or ""
                    reusable, added = near_copy(sheet.get_text(), old_text, _stored_read(cfg, conn, best.sha256, best.page) or {})
                    where = origin_of(best)
                    label = f"page {where['page']} of {where['name'] or 'document ' + str(where['document_id'])}"
                    if reusable:
                        entry.update(how="near_copy", of=where, note=f"near-copy of {label}: {added} tokens added, every quoted passage still present",
                                     source=(best.sha256, best.page))
                        continue
                    entry["of"], entry["note"] = where, f"resembles {label} but differs in substance, so it was read"
                if has_form_marks(sheet):
                    entry["how"], entry["note"] = "image", entry["note"] or "a form with tick-boxes or fields: read as an image so its marks are seen"
                elif mark.cover >= IMAGE_COVER_FOR_IMAGE_PATH:
                    entry["how"], entry["note"] = "image", entry["note"] or "mostly a picture: read as an image"
                else:
                    entry["how"], entry["note"] = "text", entry["note"] or "read from its text layer only, no image sent"
            else:
                for other in known:
                    if other.kind != "image" or (mark.sketch[0] ^ other.sketch[0]).bit_count() > DHASH_CANDIDATE_BITS:
                        continue
                    where = origin_of(other)
                    label = f"page {where['page']} of {where['name'] or 'document ' + str(where['document_id'])}"
                    with pymupdf.open(others[other.sha256][1]) as old:
                        same = same_picture(_grey(sheet, VERIFY_DPI), _grey(old[other.page - 1], VERIFY_DPI))
                    if same:
                        entry.update(how="identical", of=where, note=f"same scan as {label}", source=(other.sha256, other.page))
                        break
                    entry["of"], entry["note"] = where, f"resembles {label} but differs on the page, so it was read"
                entry["how"] = entry["how"] or "image"
            report["pages"] = [{key: value for key, value in e.items() if key != "source"} for e in plan]
    report["pages"] = [{key: value for key, value in e.items() if key != "source"} for e in plan]

    def copy_reads() -> None:
        for entry in plan:
            if entry["source"] is None or entry.get("stored"):
                continue
            if entry["source"] == (sha, entry["page"]):
                entry["stored"] = True
            else:
                read = _stored_read(cfg, conn, *entry["source"])
                if read is None:
                    continue  # its source is a page of this same file that has not been read yet
                _store_read(cfg, conn, sha, entry["page"], read)
                entry["stored"] = True
            counts["pages_near_copy" if entry["how"] == "near_copy" else "pages_identical"] += 1
            counts["pages_skipped"] += 1
        conn.commit()

    copy_reads()
    to_text = [entry["page"] for entry in plan if entry["how"] == "text"]
    to_image = [entry["page"] for entry in plan if entry["how"] == "image"]
    _stage(report, "fingerprint", "done",
           f"{counts['pages_skipped']} of {_n(total, 'page')} already known; {len(to_text)} to read as text, {len(to_image)} as images")

    # -- 4 and 5: read only the new pages, text where there is a text layer
    fresh: set[int] = set()
    if (to_text or to_image) and gate is not None and not gate.get("down") and "asked" not in gate:
        gate["asked"] = True
        gate["down"] = not (cfg.openai_api_key and model) or not model_answers(cfg, conn, matter_id)
    if (to_text or to_image) and gate is not None and gate.get("down"):
        _stage(report, "read", "skipped", "waiting to be read: the model could not be reached; the digest reads these pages later")
    elif to_text or to_image:
        _stage(report, "read", "running")
        if not cfg.openai_api_key or not model:
            raise llm.LLMError("the document is stored, but no model is set up on this server, so its new pages are waiting to be read")
        with pymupdf.open(path) as document:
            texts = {page: document[page - 1].get_text().strip() for page in to_text}
        size = pipeline.PAGES_PER_REQUEST
        jobs = [("text", to_text[i : i + size]) for i in range(0, len(to_text), size)]
        jobs += [("image", to_image[i : i + size]) for i in range(0, len(to_image), size)]

        def work(job: tuple[str, list[int]]) -> tuple[schemas.PageBatch, llm.Usage]:
            how, numbers = job
            if how == "text":
                return read_text_pages(cfg, {page: texts[page] for page in numbers}, model)
            return pipeline.read_pages(cfg, path, numbers, model)

        def run_jobs(batch: list[tuple[str, list[int]]]) -> list[tuple[str, list[int]]]:
            failed = []
            with ThreadPoolExecutor(max_workers=READ_THREADS) as pool:
                futures = {pool.submit(work, job): job for job in batch}
                for future in as_completed(futures):  # results are stored on this thread, the only one that writes
                    how, numbers = futures[future]
                    usage, answer = None, None
                    try:
                        answer, usage = future.result()
                    except llm.LLMUsageError as error:
                        usage = error.usage
                    except llm.LLMError:
                        pass
                    if usage is not None:
                        pipeline.record_call(conn, matter_id, None, "pages", usage, answer is not None)
                        _count(counts, usage)
                    if answer is None:
                        failed.append((how, numbers))
                        continue
                    returned = {read.page: read for read in answer.pages}
                    for page in numbers:
                        if page in returned:
                            _store_read(cfg, conn, sha, page, returned[page].model_dump())
                            fresh.add(page)
                            counts["pages_text" if how == "text" else "pages_model"] += 1
                    conn.commit()
                    counts["seconds"] = round(time.monotonic() - started, 2)
                    _publish(report)
            return failed

        failed = run_jobs(jobs)
        # A batch that was cut off or refused is retried one page at a time, as the digest does.
        run_jobs([(how, [page]) for how, numbers in failed if len(numbers) > 1 for page in numbers])
        copy_reads()  # pages that repeat a page of this same file
        if gate is not None and not fresh:
            gate["down"] = True  # every call failed: the rest of the import does not try again
        _stage(report, "read", "done", f"{_n(len(fresh), 'page')} read in {_n(counts['model_calls'], 'call')}")
    else:
        _stage(report, "read", "skipped", "every page was already known")

    unread = len(_unread_pages(cfg, conn, sha, total))
    counts["claims_added"] = sum(
        len(json.loads(row["result"]).get("facts") or [])
        for row in conn.execute("SELECT result FROM page_reads WHERE sha256=? AND prompt_version=? AND model=?", (sha, *_read_key(cfg)))
    )

    # -- 6: reconcile the new claims against their neighbourhood only
    _stage(report, "reconcile", "running")
    try:
        detail = _reconcile(cfg, conn, report, fresh, cards_were_current and not unread)
        _stage(report, "reconcile", "done" if report["cards"] or "call" in detail else "skipped", detail)
    except llm.LLMError as error:
        _stage(report, "reconcile", "skipped", f"not compared with the entries: {error}")

    # -- 7: the stored rows changed, so the graph and ledger versions did; report the new one
    report["graph_version"] = _graph_version(cfg, conn, matter_id)
    counts["seconds"] = round(time.monotonic() - started, 2)
    report["saving"] = _saving(conn, matter_id, model, total, counts["cost_usd"], len(fresh), rate)
    report["summary"] = _summary(report)
    report["stage"] = "done"
    _stage(report, "done", "done", "the graph, the search index and the checker read the new version")
    return unread


def _graph_version(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> str | None:
    """The version the graph route is keyed on, as it stands now."""
    try:
        from .graph import build as graph_build

        return graph_build.version(cfg, conn, matter_id)
    except Exception:
        return None


def _count(counts: dict[str, Any], usage: llm.Usage) -> None:
    counts["model_calls"] += 1
    counts["input_tokens"] += usage.input_tokens
    counts["cached_tokens"] += usage.cached_tokens
    counts["output_tokens"] += usage.output_tokens
    counts["cost_usd"] = round(counts["cost_usd"] + (usage.cost_usd or 0.0), 6)


def _n(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {plural or noun + 's'}"


def _summary(report: dict[str, Any]) -> str:
    counts, saving = report["counts"], report["saving"]
    parts = [f"{report['name']}: {_n(counts['pages_total'], 'page')}."]
    if counts["pages_identical"]:
        parts.append(f"{_n(counts['pages_identical'], 'page')} cost nothing: the same page is already in the matter and its stored read was reused.")
    if counts["pages_near_copy"]:
        parts.append(f"{_n(counts['pages_near_copy'], 'page')} cost nothing: a near-copy of a stored page with every quoted passage intact, flagged as such.")
    parts.append(
        f"{counts['pages_text']} read as text{' (text layer only, no image sent)' if counts['pages_text'] else ''} and {counts['pages_model']} as images,"
        f" in {_n(counts['model_calls'], 'model call')}"
        f" ({counts['input_tokens']} input tokens, {counts['cached_tokens']} of them cached, {counts['output_tokens']} output), costing ${counts['cost_usd']:.4f}."
    )
    parts.append(f"{_n(counts['claims_added'], 'claim')} attached to the document; {_n(counts['cards_touched'], 'card')} added, no existing card changed.")
    if saving["from_scratch_cost_usd"] is None:
        parts.append("No per-page rate has been measured for this matter yet, so no from-scratch figure is given.")
    else:
        parts.append(
            f"Reading the whole file ({_n(counts['pages_total'], 'page')}) from scratch at ${saving['per_page_rate_usd']:.4f} a page ({saving['rate_basis']})"
            f" would have cost ${saving['from_scratch_cost_usd']:.4f}: ${saving['saved_usd']:.4f} saved."
        )
    return " ".join(parts)


# --------------------------------------------------------------------------- incremental reconciliation


def neighbourhood(new: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries that share a subject with the new claims: BM25 over an FTS5 table built in
    memory for this one question (a few thousand short rows build in milliseconds; the graph's
    own index is a wire format for the browser, not something to query here)."""
    if not new or not entries:
        return []
    memory = sqlite3.connect(":memory:")
    try:
        memory.execute("CREATE VIRTUAL TABLE claim USING fts5(id UNINDEXED, body, tokenize='porter unicode61')")
        memory.executemany(
            "INSERT INTO claim (id, body) VALUES (?,?)",
            [(e["id"], " ".join(str(e[k]) for k in ("topic", "statement", "party", "quote") if e.get(k))) for e in entries],
        )
        best: dict[str, float] = {}
        for claim in new:
            words = list(dict.fromkeys(w for w in tokens(" ".join(str(claim[k]) for k in ("statement", "party") if claim.get(k))) if len(w) > 2))[:24]
            if not words:
                continue
            query = " OR ".join(f'"{word}"' for word in words)
            for claim_id, rank in memory.execute(
                "SELECT id, bm25(claim) FROM claim WHERE claim MATCH ? ORDER BY bm25(claim) LIMIT ?", (query, NEIGHBOURS_PER_CLAIM)
            ):
                best[claim_id] = min(best.get(claim_id, 0.0), rank)  # FTS5's bm25 is lower for a better match
    finally:
        memory.close()
    keep = set(sorted(best, key=lambda claim_id: best[claim_id])[:MAX_NEIGHBOURS])
    return [entry for entry in entries if entry["id"] in keep]


def _line(claim: dict[str, Any]) -> str:
    fields = {
        "id": claim["id"], "origin": claim["origin"], "kind": claim["kind"], "topic": claim.get("topic"),
        "statement": claim["statement"], "date": claim["date"], "amount_usd": claim["amount_usd"], "party": claim["party"],
        "recorded": (claim.get("record_date") or "")[:10] or None, "page_type": claim.get("page_type"), "issuer": claim.get("issuer"),
    }
    return json.dumps({key: value for key, value in fields.items() if value is not None}, ensure_ascii=False)


def conflict_id(notes_ids: list[str], document_ids: list[str]) -> str:
    """The card's id as the overlay and the ledger compute it: its two sides."""
    return "conflict:" + hashlib.sha256("|".join(sorted(notes_ids) + sorted(document_ids)).encode()).hexdigest()[:12]


def _reconcile(cfg: Settings, conn: sqlite3.Connection, report: dict[str, Any], fresh: set[int], keep_current: bool) -> str:
    matter_id, document_id, counts = report["matter_id"], report["document_id"], report["counts"]
    row = conn.execute("SELECT result, claims FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    if row is None:
        return "no cards have been built for this matter yet; its first digest builds them"

    def mark_current() -> None:
        # The cards were current before the upload and its claims have now been compared: they still are.
        if keep_current:
            conn.execute("UPDATE reconciliations SET input_hash=? WHERE matter_id=?", (pipeline.reconcile_input(cfg, conn, matter_id)[0], matter_id))
            conn.commit()

    claims = pipeline.collect_claims(cfg, conn, matter_id)
    # Only pages the model read just now are new to the matter; a reused page's claims were compared when it first came in.
    new = [
        claim for claim in claims
        if claim["source_kind"] == "document" and claim["clio_id"] == str(document_id) and claim["page"] in fresh
        and claim["kind"] in pipeline.RECONCILE_FACT_KINDS
    ][:MAX_NEW_CLAIMS]
    if not new:
        mark_current()
        return "no newly read page holds a fact of a kind the cards compare"
    near = neighbourhood(new, [claim for claim in claims if claim["origin"] != "document"])
    if not near:
        mark_current()
        return "no entry in the matter shares a subject with the new facts"
    content = [
        llm.text_part("Claims from the new document, one JSON object per line:\n" + "\n".join(_line(claim) for claim in new)),
        llm.text_part("Claims from the firm's entries that share a subject with them, one JSON object per line:\n" + "\n".join(_line(claim) for claim in near)),
    ]
    try:
        answer, usage = llm.structured(
            cfg, model=cfg.digest_model, instructions=prompts.RECONCILE_DOCUMENT, content=content, schema=schemas.NewConflicts, effort="low",
            max_output_tokens=INCREMENT_OUTPUT_TOKENS,
        )
    except llm.LLMUsageError as error:
        pipeline.record_call(conn, matter_id, None, "reconcile", error.usage, False)
        _count(counts, error.usage)
        raise
    pipeline.record_call(conn, matter_id, None, "reconcile", usage, True)
    _count(counts, usage)

    # Ids the model invented are dropped; a card needs both sides.
    entry_ids, new_ids = {claim["id"] for claim in near}, {claim["id"] for claim in new}
    by_id = {claim["id"]: claim for claim in claims}
    stored = json.loads(row["result"])
    snapshot = json.loads(row["claims"]) if row["claims"] else None
    existing = {conflict_id(c.get("notes_claim_ids") or [], c.get("document_claim_ids") or []) for c in stored.get("conflicts") or []}
    for conflict in answer.conflicts:
        conflict.notes_claim_ids = [i for i in dict.fromkeys(conflict.notes_claim_ids) if i in entry_ids]
        conflict.document_claim_ids = [i for i in dict.fromkeys(conflict.document_claim_ids) if i in new_ids]
        card = conflict_id(conflict.notes_claim_ids, conflict.document_claim_ids)
        if not conflict.notes_claim_ids or not conflict.document_claim_ids or card in existing:
            continue
        existing.add(card)
        stored.setdefault("conflicts", []).append(conflict.model_dump())
        if snapshot is not None:
            snapshot.update({i: by_id[i] for i in conflict.notes_claim_ids + conflict.document_claim_ids})
        report["cards"].append(
            {"id": card, "topic": conflict.topic, "summary": conflict.summary, "from_upload": document_id,
             "claim_ids": conflict.notes_claim_ids + conflict.document_claim_ids}
        )
    counts["cards_touched"] = len(report["cards"])
    if report["cards"]:
        # Appended only: every existing card, the key facts, the summary and the attorney's reviews stay as they were.
        conn.execute(
            "UPDATE reconciliations SET result=?, claims=? WHERE matter_id=?",
            (json.dumps(stored, ensure_ascii=False), json.dumps(snapshot) if snapshot is not None else None, matter_id),
        )
        conn.commit()
    mark_current()
    return f"one call: {_n(len(new), 'new claim')} against {_n(len(near), 'entry sharing', 'entries sharing')} a subject; {_n(len(report['cards']), 'card')} added"


# --------------------------------------------------------------------------- routes


@router.post("/api/matters/{matter_id}/documents/upload")
async def upload(matter_id: int, request: Request, cfg: Settings = Depends(_settings)) -> JSONResponse:
    """Store an uploaded PDF or picture in our own database and ingest what is new in it.
    Answers at once; the ingestion is followed at the status route. Nothing is sent to Clio."""
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 1024 * 1024:  # refused before the body is read
        raise HTTPException(413, f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    body = await request.body()
    status, report = await run_in_threadpool(
        accept, cfg, matter_id, request.headers.get("content-type") or "", body, dict(request.query_params)
    )
    return JSONResponse(report, status_code=status)


INTERRUPTED = "interrupted by a restart of the server; send the same file again to carry on from where it stopped"


def _status(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    with _live_lock:
        live = _live.get(row["id"])
    return _settled(json.loads(live or row["report"]), live is not None)


def _settled(report: dict[str, Any], running: bool) -> dict[str, Any]:
    """A row left unfinished by a thread that no longer exists is reported as failed, so no page waits on it for ever."""
    if not running and report.get("state") in ("queued", "running"):
        report["state"], report["error"] = "failed", INTERRUPTED
    return report


@router.get("/api/matters/{matter_id}/ingestions/{ingestion_id}")
def ingestion(matter_id: int, ingestion_id: int, cfg: Settings = Depends(_settings)) -> dict[str, Any]:
    """Stages and counts of one ingestion as they happen."""
    conn = _open(cfg)
    try:
        row = conn.execute("SELECT id, report FROM ingestions WHERE id=? AND matter_id=?", (ingestion_id, matter_id)).fetchone()
        if row is None:
            raise HTTPException(404, "no such ingestion")
        return _status(conn, row)
    finally:
        conn.close()


@router.get("/api/matters/{matter_id}/ingestions")
def ingestions(matter_id: int, cfg: Settings = Depends(_settings)) -> list[dict[str, Any]]:
    """The matter's recent ingestions, newest first."""
    conn = _open(cfg)
    try:
        rows = conn.execute("SELECT id, report FROM ingestions WHERE matter_id=? ORDER BY id DESC LIMIT 50", (matter_id,)).fetchall()
        return [_status(conn, row) for row in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- a whole case file as a zip
#
# Nothing in the archive decides where a byte is written: every file goes through
# `entry.add_document`, which names the stored file itself. An entry's path is only ever a
# label (its top-level folder becomes the document's folder). Zip-slip therefore has nothing
# to slip through; entries with an absolute path or `..` are still refused and listed.

MAX_ZIP_BYTES = 200 * 1024 * 1024        # the archive as sent
MAX_ZIP_FILES = 500
MAX_UNPACKED_BYTES = 500 * 1024 * 1024   # by the archive's own directory, checked before anything is unpacked
MAX_RATIO = 100                          # unpacked over packed; ordinary PDFs and scans compress under 10 to 1
RATIO_FLOOR_BYTES = 1024 * 1024          # a small text file may compress well; the ratio counts above this size
SYSTEM_NAMES = {"thumbs.db", "desktop.ini", "__macosx"}
ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log"}
EMAIL_SUFFIXES = {".eml"}
ROOT_FOLDER = "Imported"
CASE_NAME_CHARS, CASE_NUMBER_CHARS = 200, 80
TEXT_LINE_CHARS, TEXT_PAGE_LINES, TEXT_MAX_PAGES = 95, 60, 200
SEARCH_TEXT_CHARS = 6000

_live_jobs: dict[int, str] = {}


def open_zip(source: bytes | Path) -> zipfile.ZipFile:
    """Open an archive and judge it from its directory alone, before a byte is unpacked.
    An archive that is refused is refused whole, with the reason in plain words."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except (zipfile.BadZipFile, OSError) as error:
        raise HTTPException(400, "the file is not a zip archive") from error
    files = [info for info in archive.infolist() if not info.is_dir()]
    if not files:
        raise HTTPException(400, "the archive is empty")
    if len(files) > MAX_ZIP_FILES:
        raise HTTPException(413, f"the archive holds {len(files)} files; the limit is {MAX_ZIP_FILES}")
    unpacked, packed = sum(info.file_size for info in files), sum(info.compress_size for info in files)
    if unpacked > MAX_UNPACKED_BYTES:
        raise HTTPException(413, f"the archive unpacks to {unpacked // (1024 * 1024)} MB; the limit is {MAX_UNPACKED_BYTES // (1024 * 1024)} MB")
    worst = max(files, key=lambda info: info.file_size / max(info.compress_size, 1))
    for size, small, what in ((unpacked, packed, "the archive"), (worst.file_size, worst.compress_size, "a file in the archive")):
        if size > RATIO_FLOOR_BYTES and size / max(small, 1) > MAX_RATIO:
            raise HTTPException(400, f"{what} unpacks to {size // max(small, 1)} times its packed size; that is refused unopened (limit {MAX_RATIO})")
    return archive


def zip_entries(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    """Every file in the archive as {info, path, name, folder, skip}: `skip` is the plain reason it is left out."""
    out = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        raw = info.filename.replace("\\", "/")
        parts = [part for part in raw.split("/") if part not in ("", ".")]
        skip = None
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in parts or not parts:
            skip = "its path points outside the import folder"
        elif (info.external_attr >> 16) & 0o170000 == 0o120000:
            skip = "a link, not a file"
        elif any(part.startswith(".") or part.lower() in SYSTEM_NAMES for part in parts) or parts[-1].startswith("~$"):
            skip = "a hidden or system file"
        elif info.flag_bits & 0x1:
            skip = "password-protected"
        elif Path(parts[-1]).suffix.lower() in ARCHIVE_SUFFIXES:
            skip = "an archive inside the archive; unpack it and import it separately"
        elif info.file_size == 0:
            skip = "empty"
        elif info.file_size > MAX_UPLOAD_BYTES:
            skip = f"larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
        out.append({"info": info, "path": raw, "parts": parts, "name": _clean_name(parts[-1]) if parts else raw, "skip": skip})
    # A folder that wraps the whole archive is the archive's own name, not a folder of the case; neither is
    # a single folder that then wraps every foldered file (".../documents/<category>/file"). They are
    # stripped, so a document's folder is its path from the first level at which the files actually divide.
    kept = [each for each in out if each["skip"] is None]
    strip: list[str] = []
    paths = [list(each["parts"]) for each in kept]
    while True:
        deep = [parts for parts in paths if len(parts) > 1]
        whole = not strip and len(deep) == len(paths)            # the archive's own wrapper
        inner = bool(strip) and all(len(parts) > 2 for parts in deep)  # a wrapper that still leaves a folder below it
        if not deep or len({parts[0] for parts in deep}) != 1 or not (whole or inner):
            break
        strip.append(deep[0][0])
        for parts in deep:
            del parts[0]
    for each in out:
        parts = list(each["parts"])
        for level in strip:
            if len(parts) > 1 and parts[0] == level:
                del parts[0]
        each["folder"] = " / ".join(_clean_name(part) for part in parts[:-1])[:200] if len(parts) > 1 else ROOT_FOLDER
    return out


def text_pdf(text: str) -> bytes:
    """Plain text as a PDF with a text layer, so pages, quotes and the drawer work on it like any document."""
    import pymupdf

    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"):
        raw = raw.encode("latin-1", "replace").decode("latin-1")  # the built-in font's character set
        while len(raw) > TEXT_LINE_CHARS:
            cut = raw.rfind(" ", 0, TEXT_LINE_CHARS) + 1 or TEXT_LINE_CHARS
            lines.append(raw[:cut])
            raw = raw[cut:]
        lines.append(raw)
    lines = lines[: TEXT_PAGE_LINES * TEXT_MAX_PAGES]
    document = pymupdf.open()
    for start in range(0, max(len(lines), 1), TEXT_PAGE_LINES):
        document.new_page().insert_text((54, 60), "\n".join(lines[start : start + TEXT_PAGE_LINES]), fontsize=9, fontname="cour")
    data = document.tobytes()
    document.close()
    return data


def email_text(data: bytes) -> tuple[str, str | None]:
    """(headers and body of an .eml as text, its date as ISO or None). Attachments are not unpacked."""
    message = email.message_from_bytes(data, policy=email.policy.default)
    head = [f"{name}: {message[name]}" for name in ("From", "To", "Cc", "Date", "Subject") if message[name]]
    body = message.get_body(preferencelist=("plain", "html"))
    content = body.get_content() if body is not None else ""
    if body is not None and body.get_content_subtype() == "html":
        content = re.sub(r"<[^>]+>", " ", content)
    try:
        when = email.utils.parsedate_to_datetime(str(message["Date"])).strftime("%Y-%m-%dT%H:%M:%SZ") if message["Date"] else None
    except (TypeError, ValueError):
        when = None
    return "\n".join(head) + "\n\n" + str(content), when


def prepare(name: str, data: bytes) -> tuple[bytes | None, str | None, str | None]:
    """(PDF bytes to read, why it is not read, received date). A file of a type that is not read is kept as it is."""
    import pymupdf

    suffix = Path(name).suffix.lower()
    kind, when = _file_kind(data), None
    try:
        if kind is not None:
            pdf = _as_pdf(data, kind)
        elif suffix in EMAIL_SUFFIXES:
            text, when = email_text(data)
            pdf = text_pdf(text)
        elif suffix in TEXT_SUFFIXES:
            pdf = text_pdf(data.decode("utf-8", "replace"))
        else:
            return None, "a type that is not read; the file is kept as it is", None
        with pymupdf.open(stream=pdf, filetype="pdf") as opened:
            if not opened.page_count:
                return None, "the file has no pages", None
        return pdf, None, when
    except TooLarge:
        raise
    except Exception:
        return None, "the file could not be opened; it is kept as it is", None


def _relabel(conn: sqlite3.Connection, matter_id: int, document_id: int, name: str, folder: str, search_text: str | None, readable: bool) -> None:
    """Give the record made by `entry.add_document` the file's own name and its folder in the archive."""
    record = item(conn, matter_id, "document", document_id) or {}
    record.update(name=name, filename=name, parent={"id": 0, "type": "Folder", "name": folder})
    if matter_id < 0:
        record["origin"] = "zip"  # part of a case created here from an archive, not something added to a case later
    if not readable:
        record["content_type"] = "application/octet-stream"
    if search_text:
        record["search_text"] = search_text
    conn.execute(
        "UPDATE clio_items SET payload=?, content_hash=?, changed_at=? WHERE matter_id=? AND kind='document' AND clio_id=?",
        (json.dumps(record, ensure_ascii=False), content_hash(record), now_iso(), matter_id, str(document_id)),
    )


def _publish_job(job: dict[str, Any]) -> None:
    with _live_lock:
        _live_jobs[job["id"]] = json.dumps(job)


def _import_one(cfg: Settings, conn: sqlite3.Connection, job: dict[str, Any], archive: zipfile.ZipFile, entry_: dict[str, Any],
                seen: dict[str, int], gate: dict[str, bool]) -> dict[str, Any]:
    matter_id, files = job["matter_id"], job["files"]
    out: dict[str, Any] = {"path": entry_["path"], "folder": entry_["folder"], "name": entry_["name"], "outcome": "skipped",
                           "reason": entry_["skip"], "document_id": None, "pages": None, "read": None}
    if entry_["skip"]:
        files["skipped"] += 1
        return out
    info = entry_["info"]
    try:
        with archive.open(info) as handle:
            data = handle.read(info.file_size + 1)  # never more than the directory promised
    except Exception:
        data = b""
    if len(data) != info.file_size:
        files["skipped"] += 1
        return {**out, "reason": "damaged in the archive, or not the size the archive says"}
    sha = hashlib.sha256(data).hexdigest()
    if sha in seen:
        files["duplicates"] += 1
        return {**out, "outcome": "duplicate", "reason": "identical to another file in this archive; stored once", "document_id": seen[sha]}
    twin = conn.execute(
        "SELECT document_id FROM uploads WHERE matter_id=? AND upload_sha256=? UNION SELECT document_id FROM document_blobs WHERE matter_id=? AND sha256=?",
        (matter_id, sha, matter_id, sha),
    ).fetchone()
    if twin is not None:
        seen[sha] = twin["document_id"]
        files["already_in_file"] += 1
        out.update(outcome="already_in_file", reason="already in the file", document_id=twin["document_id"])
        blob = conn.execute("SELECT sha256, page_count FROM document_blobs WHERE matter_id=? AND document_id=?", (matter_id, twin["document_id"])).fetchone()
        if twin["document_id"] < 0 and blob and blob["page_count"] and _unread_pages(cfg, conn, blob["sha256"], blob["page_count"]):
            # Stored by an earlier import while the model could not be reached: read it now, by the same cheap path.
            out["pages"] = blob["page_count"]
            _read_stored(cfg, conn, job, out, gate)
        return out

    try:
        pdf, why_not, when = prepare(entry_["name"], data)
    except TooLarge as error:
        files["skipped"] += 1
        return {**out, "reason": f"{error}; not stored"}
    readable = pdf is not None
    # A file that is not read is stored under a neutral suffix, so the file route can only ever hand it
    # back as a download: a page or script inside an archive must never be served as one from this origin.
    stored_name = f"{Path(entry_['name']).stem}.pdf" if readable else "kept.bin"
    search_text = None
    if readable:
        import pymupdf

        with pymupdf.open(stream=pdf, filetype="pdf") as opened:
            out["pages"] = opened.page_count
            try:
                search_text = " ".join(" ".join(sheet.get_text() for sheet in opened).split())[:SEARCH_TEXT_CHARS] or None
            except Exception:  # one unreadable page must not end the import
                search_text = None
    with _id_lock:
        document_id = entry.add_document(cfg, conn, matter_id, stored_name, pdf if readable else data, when)
        conn.execute(
            "INSERT OR REPLACE INTO uploads (matter_id, document_id, upload_sha256, name, content_type, size_bytes, uploaded_at) VALUES (?,?,?,?,?,?,?)",
            (matter_id, document_id, sha, entry_["name"], Path(entry_["name"]).suffix.lower().lstrip(".") or None, len(data), now_iso()),
        )
        _relabel(conn, matter_id, document_id, entry_["name"], entry_["folder"], search_text, readable)
        conn.commit()
    seen[sha] = document_id
    out.update(document_id=document_id, outcome="stored" if readable else "not_read", reason=why_not, read=None if readable else "not_readable")
    files["stored" if readable else "not_read"] += 1
    if not readable:
        return out

    _read_stored(cfg, conn, job, out, gate)
    return out


def _read_stored(cfg: Settings, conn: sqlite3.Connection, job: dict[str, Any], out: dict[str, Any], gate: dict[str, bool]) -> None:
    """Run the single-upload ingestion on a document of the import and add what it did to the job's totals."""
    child = _new_report(job["matter_id"], out["document_id"], out["name"])
    child["origin"] = "imported from a zip"
    child["state"] = "running"
    _create(conn, child)
    try:
        unread = _run(cfg, conn, child, time.monotonic(), gate)
        child["state"] = "complete"
    except Exception as error:  # the file is stored; its pages stay for the digest
        unread, child["state"], child["error"] = out["pages"] or 0, "failed", type(error).__name__
    _save(conn, child, finished=True)
    counts, pages = child["counts"], job["pages"]
    pages["total"] += counts["pages_total"] or out["pages"] or 0
    pages["known"] += counts["pages_skipped"]
    pages["text"] += counts["pages_text"]
    pages["image"] += counts["pages_model"]
    pages["waiting"] += unread
    for key in ("model_calls", "input_tokens", "output_tokens", "claims_added"):
        job["counts"][key] += counts[key]
    job["counts"]["cost_usd"] = round(job["counts"]["cost_usd"] + counts["cost_usd"], 6)
    out["read"] = "waiting_to_be_read" if unread else "read"
    if unread:
        job["files"]["waiting_to_be_read"] += 1


def _import_zip(cfg: Settings, job_id: int, path: Path) -> None:
    conn = _open(cfg)
    started = time.monotonic()
    job = json.loads(conn.execute("SELECT report FROM import_jobs WHERE id=?", (job_id,)).fetchone()["report"])
    gate = {"down": not cfg.openai_api_key or not cfg.digest_model_bulk}
    try:
        with _matter_lock(job["matter_id"]):
            job["state"] = "running"
            archive = zipfile.ZipFile(path)
            entries = zip_entries(archive)
            job["files"]["total"] = len(entries)
            seen: dict[str, int] = {}
            for index, entry_ in enumerate(entries, start=1):
                job["current"] = {"index": index, "name": entry_["name"]}
                _publish_job(job)
                job["items"].append(_import_one(cfg, conn, job, archive, entry_, seen, gate))
                job["files"]["done"] = index
                job["counts"]["seconds"] = round(time.monotonic() - started, 2)
            archive.close()
        job["state"], job["current"] = "complete", None
    except Exception as error:  # the type only: a message may carry text from a file
        job["state"], job["error"] = "failed", type(error).__name__
    finally:
        path.unlink(missing_ok=True)
        files, pages, counts = job["files"], job["pages"], job["counts"]
        counts["seconds"] = round(time.monotonic() - started, 2)
        job["model"] = "unavailable" if pages["waiting"] else ("used" if counts["model_calls"] else "not_needed")
        job["summary"] = (
            f"{job['name']}: {_n(files['total'], 'file')}. {files['stored']} stored as documents, {files['already_in_file']} already in the file,"
            f" {files['duplicates']} identical to another file in the archive, {files['skipped']} skipped, {files['not_read']} kept but not read."
            f" {_n(pages['total'], 'page')}: {pages['known']} already known, {pages['text']} read as text, {pages['image']} as images,"
            f" {pages['waiting']} waiting to be read. {_n(counts['model_calls'], 'model call')}, ${counts['cost_usd']:.4f}, {counts['seconds']} s."
        )
        if not files["stored"] + files["already_in_file"] + files["not_read"]:
            job["summary"] += " Nothing in the archive could be imported; each file's reason is listed."
        if pages["waiting"]:
            job["summary"] += (
                " The model could not be reached for some pages: every file is stored and on the map, and those pages"
                " are waiting to be read; the digest reads them when the model answers."
            )
        try:
            conn.execute("UPDATE import_jobs SET state=?, finished_at=?, report=? WHERE id=?", (job["state"], now_iso(), json.dumps(job), job_id))
            conn.commit()
        finally:
            conn.close()
            with _live_lock:
                _live_jobs.pop(job_id, None)


async def spool(request: Request, cfg: Settings) -> Path:
    """Write the request body to a file as it arrives, so an archive is never held in memory."""
    folder = cfg.data_dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{secrets.token_hex(8)}.body"
    size = 0
    with path.open("wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_ZIP_BYTES + 1024 * 1024:  # the form's own framing rides on top of the archive
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"the archive is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
            out.write(chunk)
    return path


def zip_part(content_type: str, body: Path, query: dict[str, str]) -> tuple[Path, dict[str, str]]:
    """(the archive as a file of its own, the other form fields), cut out of the spooled body on disk."""
    fields = dict(query)
    if not content_type.lower().startswith("multipart/form-data"):
        return body, fields
    found = re.search(r'boundary="?([^";]+)"?', content_type)
    if not found or not body.stat().st_size:
        raise HTTPException(400, "no file in the upload: send it as the form field `file`")
    delimiter = b"--" + found.group(1).encode()
    target = None
    with body.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
        position = view.find(delimiter)
        while position != -1:
            start = position + len(delimiter)
            if view[start : start + 2] == b"--":
                break
            head_end = view.find(b"\r\n\r\n", start)
            following = view.find(b"\r\n" + delimiter, head_end + 4) if head_end != -1 else -1
            if following == -1:
                break
            head = view[start:head_end].decode("utf-8", "replace")
            field = re.search(r'\bname="([^"]*)"', head)
            filename = re.search(r'\bfilename="([^"]*)"', head)
            if filename and target is None:
                target = body.with_suffix(".zip")
                fields["filename"] = filename.group(1)
                with target.open("wb") as out:
                    for offset in range(head_end + 4, following, 1024 * 1024):
                        out.write(view[offset : min(offset + 1024 * 1024, following)])
            elif field and following - head_end < 8192:
                fields[field.group(1)] = view[head_end + 4 : following].decode("utf-8", "replace").strip()
            position = following + 2
    body.unlink(missing_ok=True)
    if target is None:
        raise HTTPException(400, "no file in the upload: send it as the form field `file`")
    return target, fields


def accept_zip(cfg: Settings, matter_id: int | None, content_type: str, body: Path, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """Check the archive, create the case if none was named, and start one import job."""
    path = body
    try:
        path, fields = zip_part(content_type, body, query)
        with open_zip(path) as archive:
            entries = zip_entries(archive)
        if all(each["skip"] for each in entries):
            reasons = sorted({each["skip"] for each in entries})
            raise HTTPException(400, "nothing in the archive can be imported: every file is " + "; or ".join(reasons[:3]))
        return _start_import(cfg, matter_id, path, fields)
    except BaseException:
        body.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def _start_import(cfg: Settings, matter_id: int | None, path: Path, fields: dict[str, str]) -> tuple[int, dict[str, Any]]:
    conn = _open(cfg)
    try:
        case_name = None
        if matter_id is None:
            case_name, client_name, number = ((fields.get(key) or "").strip() for key in ("name", "client_name", "number"))
            if not case_name:
                raise HTTPException(400, "give the new case a name")
            # The same limits as creating a case by hand.
            for label, value, limit in (("case name", case_name, CASE_NAME_CHARS), ("client name", client_name, CASE_NAME_CHARS), ("case number", number, CASE_NUMBER_CHARS)):
                if len(value) > limit:
                    raise HTTPException(400, f"the {label} is longer than {limit} characters")
            matter_id = int(entry.create_matter(cfg, conn, case_name, client_name=client_name or None, number=number or None))
        elif not items(conn, matter_id, "matter"):
            raise HTTPException(404, f"matter {matter_id} has not been imported yet")
        job: dict[str, Any] = {
            "id": None, "matter_id": matter_id, "kind": "zip", "name": _clean_name(fields.get("filename") or "import.zip"), "case_name": case_name,
            "state": "queued",
            "files": {"total": 0, "done": 0, "stored": 0, "already_in_file": 0, "duplicates": 0, "skipped": 0, "not_read": 0, "waiting_to_be_read": 0},
            "pages": {"total": 0, "known": 0, "text": 0, "image": 0, "waiting": 0},
            "current": None, "items": [],
            "counts": {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "claims_added": 0, "seconds": 0.0},
            "model": None, "summary": None, "error": None,
        }
        job["id"] = conn.execute(
            "INSERT INTO import_jobs (matter_id, state, started_at, report) VALUES (?,?,?,?)", (matter_id, "queued", now_iso(), "{}")
        ).lastrowid
        conn.execute("UPDATE import_jobs SET report=? WHERE id=?", (json.dumps(job), job["id"]))
        conn.commit()
        _publish_job(job)
        threading.Thread(target=_import_zip, args=(cfg, job["id"], path), name=f"import-{job['id']}", daemon=True).start()
        return 202, job
    finally:
        conn.close()


@router.post("/api/matters/import/zip")
async def import_new_case(request: Request, cfg: Settings = Depends(_settings)) -> JSONResponse:
    """A zip of a case file becomes a new case in our own store. Nothing is sent to Clio."""
    body = await spool(request, cfg)
    status, job = await run_in_threadpool(accept_zip, cfg, None, request.headers.get("content-type") or "", body, dict(request.query_params))
    return JSONResponse(job, status_code=status)


@router.post("/api/matters/{matter_id}/import/zip")
async def import_into_case(matter_id: int, request: Request, cfg: Settings = Depends(_settings)) -> JSONResponse:
    """Add the files of a zip to a case already in our store."""
    body = await spool(request, cfg)
    status, job = await run_in_threadpool(accept_zip, cfg, matter_id, request.headers.get("content-type") or "", body, dict(request.query_params))
    return JSONResponse(job, status_code=status)


@router.get("/api/matters/{matter_id}/imports/{job_id}")
def import_job(matter_id: int, job_id: int, cfg: Settings = Depends(_settings)) -> dict[str, Any]:
    """Files done, skipped and why, pages, and the file being worked on."""
    conn = _open(cfg)
    try:
        row = conn.execute("SELECT id, report FROM import_jobs WHERE id=? AND matter_id=?", (job_id, matter_id)).fetchone()
        if row is None:
            raise HTTPException(404, "no such import")
        with _live_lock:
            live = _live_jobs.get(job_id)
        return _settled(json.loads(live or row["report"]), live is not None)
    finally:
        conn.close()
