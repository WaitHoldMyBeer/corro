"""The digest run: pages -> claims -> reconciliation. Incremental and resumable.

Each stage looks up what is already stored for the exact content in hand
(document bytes by SHA-256, Clio items by content hash) and sends only the rest
to the model. A second run over an unchanged matter makes no model call at all.
Every call's token usage is written to `llm_calls`; cost is summed from there.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import Settings
from ..db import content_hash, items, now_iso
from . import llm, prompts, schemas

# Tunables. 150 dpi is the resolution office scanners commonly store (a letter page
# as 1275 x 1650 px); rendering above the scan's own resolution adds bytes, not detail.
# `digest-probe` measures tokens and cost per page before a full run.
PAGE_DPI = 150
PAGE_JPEG_QUALITY = 70
PAGES_PER_REQUEST = 5
ITEMS_PER_REQUEST = 12
MAX_PARALLEL_REQUESTS = 16
PAGE_BATCH_OUTPUT_TOKENS = 16000
CLAIMS_OUTPUT_TOKENS = 20000
RECONCILE_OUTPUT_TOKENS = 60000
TEXT_LAYER_CHARS_PER_PAGE = 6000  # a text layer longer than this is cut; the image still carries the page

# Clio object types whose free text is read for claims, with the fields that hold it.
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "note": ("subject", "detail"),
    "communication": ("subject", "body"),
    "task": ("name", "description"),
    "calendar_entry": ("summary", "description", "location"),
}
DATE_FIELDS = {"note": "date", "communication": "date", "task": "due_at", "calendar_entry": "start_at"}

# Page kinds that go to reconciliation one fact at a time. Visit-by-visit and
# line-by-line facts stay in the page store and are counted per provider in code.
RECONCILE_FACT_KINDS = {
    "injury_or_diagnosis",
    "incident_fact",
    "liability_fact",
    "insurance_or_coverage",
    "employment_or_income",
    "person_or_witness",
    "legal_event",
    "valuation",
}


@dataclass
class Progress:
    pages_total: int = 0
    pages_done: int = 0
    pages_sent: int = 0
    items_total: int = 0
    items_done: int = 0
    items_sent: int = 0
    reconciled: bool = False
    reconcile_stale: bool = False
    incoming_checked: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- shared helpers


def record_call(conn: sqlite3.Connection, matter_id: int, run_id: int | None, purpose: str, usage: llm.Usage, ok: bool) -> None:
    conn.execute(
        "INSERT INTO llm_calls (matter_id, run_id, purpose, model, input_tokens, cached_tokens, cache_write_tokens,"
        " output_tokens, cost_usd, seconds, ok, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            matter_id, run_id, purpose, usage.model, usage.input_tokens, usage.cached_tokens,
            usage.cache_write_tokens, usage.output_tokens, usage.cost_usd, usage.seconds, int(ok), now_iso(),
        ),
    )
    conn.commit()


def _run_parallel(
    jobs: list[Any], work: Callable[[Any], tuple[Any, llm.Usage]], on_done: Callable[[Any, Any, llm.Usage | None, str | None], None]
) -> None:
    """Run `work` over `jobs` on a few threads; results are handed back on the calling
    thread, which is the only one that touches the database."""
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                result, usage = future.result()
                on_done(job, result, usage, None)
            except llm.LLMUsageError as error:
                on_done(job, None, error.usage, str(error))
            except llm.LLMError as error:
                on_done(job, None, None, str(error))


def normalise(text: str) -> str:
    """For quote checking: case, whitespace and typographic quotes do not count."""
    table = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
    return " ".join(text.translate(table).lower().split())


def letters_and_digits(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def quote_in(quote: str | None, source_text: str | None) -> bool:
    """True when the quote appears verbatim in the source text. Decided here, never by the model.

    First with case, whitespace and typographic quotes ignored. A PDF text layer
    breaks lines and spaces words differently from how the page reads, so the
    second test compares letters and digits only, in order: every character of
    the quote must still be there, consecutively."""
    if not quote or not source_text:
        return False
    needle = normalise(quote)
    if len(needle) < 4:
        return False
    if needle in normalise(source_text):
        return True
    bare = letters_and_digits(quote)
    return len(bare) >= 8 and bare in letters_and_digits(source_text)


# --------------------------------------------------------------------------- stage 1: pages


def render_page(path: Path, page: int, dpi: int = PAGE_DPI) -> tuple[bytes, str]:
    """(JPEG of the page, its text layer). The text layer is empty for a scan."""
    import pymupdf

    with pymupdf.open(path) as document:
        target = document[page - 1]
        jpeg = target.get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=PAGE_JPEG_QUALITY)
        return jpeg, target.get_text().strip()


def _documents(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT document_id, sha256, path, page_count FROM document_blobs"
        " WHERE matter_id=? AND page_count IS NOT NULL ORDER BY page_count, document_id",
        (matter_id,),
    ).fetchall()
    live = {int(d["id"]) for d in items(conn, matter_id, "document")}
    return [row for row in rows if row["document_id"] in live]


def pages_pending(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> tuple[int, list[tuple[sqlite3.Row, list[int]]]]:
    """(total pages, [(document, pages not yet read with this prompt and model)])."""
    total, pending = 0, []
    for document in _documents(cfg, conn, matter_id):
        total += document["page_count"]
        done = {
            row["page"]
            for row in conn.execute(
                "SELECT page FROM page_reads WHERE sha256=? AND prompt_version=? AND model=?",
                (document["sha256"], prompts.PAGES_VERSION, cfg.digest_model_bulk),
            )
        }
        missing = [page for page in range(1, document["page_count"] + 1) if page not in done]
        if missing:
            pending.append((document, missing))
    return total, pending


def read_pages(cfg: Settings, path: Path, pages: list[int], model: str, dpi: int = PAGE_DPI, detail: str = "high") -> tuple[schemas.PageBatch, llm.Usage]:
    content: list[dict] = []
    for page in pages:
        jpeg, text = render_page(path, page, dpi)
        content.append(llm.text_part(f"Page {page}"))
        content.append(llm.image_part(jpeg, detail))
        if text:
            content.append(llm.text_part(f"Text layer of page {page}:\n{text[:TEXT_LAYER_CHARS_PER_PAGE]}"))
    return llm.structured(
        cfg,
        model=model,
        instructions=prompts.PAGES,
        content=content,
        schema=schemas.PageBatch,
        effort="low",
        max_output_tokens=PAGE_BATCH_OUTPUT_TOKENS,
    )


def run_pages(
    cfg: Settings,
    conn: sqlite3.Connection,
    matter_id: int,
    run_id: int | None,
    progress: Progress,
    stop: threading.Event,
    only_document_id: int | None = None,
) -> None:
    progress.pages_total, pending = pages_pending(cfg, conn, matter_id)
    progress.pages_done = progress.pages_total - sum(len(pages) for _, pages in pending)
    if only_document_id is not None:
        pending = [(document, pages) for document, pages in pending if document["document_id"] == only_document_id]
    jobs = []
    for document, pages in pending:
        for start in range(0, len(pages), PAGES_PER_REQUEST):
            jobs.append((document, pages[start : start + PAGES_PER_REQUEST]))
    model = cfg.digest_model_bulk

    def work(job):
        if stop.is_set():
            raise llm.LLMError("stopped")
        document, pages = job
        return read_pages(cfg, cfg.data_dir / document["path"], pages, model)

    def on_done(job, batch, usage, error):
        document, pages = job
        if usage:
            record_call(conn, matter_id, run_id, "pages", usage, error is None)
            progress.calls += 1
            progress.cost_usd += usage.cost_usd or 0.0
        if error:
            if error != "stopped":
                progress.errors.append(f"document {document['document_id']} pages {pages[0]}-{pages[-1]}: {error}")
            return
        progress.pages_sent += len(pages)
        returned = {read.page: read for read in batch.pages}
        for page in pages:
            read = returned.get(page)
            if read is None:
                progress.errors.append(f"document {document['document_id']} page {page}: missing from the model's answer")
                continue
            conn.execute(
                "INSERT OR REPLACE INTO page_reads (sha256, page, prompt_version, model, result, at) VALUES (?,?,?,?,?,?)",
                (document["sha256"], page, prompts.PAGES_VERSION, model, read.model_dump_json(), now_iso()),
            )
            progress.pages_done += 1
        conn.commit()

    failed: list[tuple[sqlite3.Row, list[int]]] = []
    first_pass = on_done

    def on_done(job, batch, usage, error):  # noqa: F811 - wraps the handler above to collect failures
        if error and error != "stopped" and len(job[1]) > 1:
            if usage:
                record_call(conn, matter_id, run_id, "pages", usage, False)
                progress.calls += 1
                progress.cost_usd += usage.cost_usd or 0.0
            failed.append(job)
            return
        first_pass(job, batch, usage, error)

    _run_parallel(jobs, work, on_done)
    # A batch that was cut off or refused is retried one page at a time, so one dense page cannot sink four others.
    retries = [(document, [page]) for document, pages in failed for page in pages]
    _run_parallel(retries, work, first_pass)


# --------------------------------------------------------------------------- stage 2: claims from Clio text


def item_text(kind: str, row: dict[str, Any]) -> str:
    return "\n".join(str(row[key]) for key in TEXT_FIELDS[kind] if row.get(key))


def text_items(conn: sqlite3.Connection, matter_id: int) -> list[dict[str, Any]]:
    """Every Clio record with free text, plus each custom field value, as {key, kind, clio_id, hash, date, text}."""
    out = []
    for kind in TEXT_FIELDS:
        for row in items(conn, matter_id, kind):
            text = item_text(kind, row)
            if text.strip():
                out.append(
                    {"key": f"{kind}:{row['id']}", "kind": kind, "clio_id": str(row["id"]), "date": row.get(DATE_FIELDS[kind]),
                     "text": text, "hash": content_hash([kind, text, row.get(DATE_FIELDS[kind])])}
                )
    for matter in items(conn, matter_id, "matter"):
        for value in matter.get("custom_field_values") or []:
            field_id = (value.get("custom_field") or {}).get("id") or value.get("id")
            if value.get("value") in (None, ""):
                continue
            text = f"{value.get('field_name')}: {value.get('value')}"
            out.append(
                {"key": f"custom_field:{field_id}", "kind": "custom_field", "clio_id": str(field_id),
                 "date": None, "text": text, "hash": content_hash(["custom_field", text])}
            )
    return out


def run_claims(cfg: Settings, conn: sqlite3.Connection, matter_id: int, run_id: int, progress: Progress, stop: threading.Event) -> None:
    everything = text_items(conn, matter_id)
    progress.items_total = len(everything)
    stored = {
        (row["kind"], row["clio_id"]): row
        for row in conn.execute("SELECT kind, clio_id, content_hash, prompt_version, model FROM item_claims WHERE matter_id=?", (matter_id,))
    }
    model = cfg.digest_model

    def fresh(entry) -> bool:
        row = stored.get((entry["kind"], entry["clio_id"]))
        return bool(row) and row["content_hash"] == entry["hash"] and row["prompt_version"] == prompts.CLAIMS_VERSION and row["model"] == model

    pending = [entry for entry in everything if not fresh(entry)]
    progress.items_done = len(everything) - len(pending)
    jobs = [pending[start : start + ITEMS_PER_REQUEST] for start in range(0, len(pending), ITEMS_PER_REQUEST)]

    def work(job):
        if stop.is_set():
            raise llm.LLMError("stopped")
        parts = [llm.text_part(f"[{entry['key']}] date: {entry['date'] or 'none'}\n{entry['text']}") for entry in job]
        return llm.structured(
            cfg, model=model, instructions=prompts.CLAIMS, content=parts, schema=schemas.TextClaims,
            effort="low", max_output_tokens=CLAIMS_OUTPUT_TOKENS,
        )

    def on_done(job, result, usage, error):
        if usage:
            record_call(conn, matter_id, run_id, "claims", usage, error is None)
            progress.calls += 1
            progress.cost_usd += usage.cost_usd or 0.0
        if error:
            if error != "stopped":
                progress.errors.append(f"claims batch of {len(job)} records: {error}")
            return
        progress.items_sent += len(job)
        by_key: dict[str, list[dict]] = {entry["key"]: [] for entry in job}
        for claim in result.claims:
            if claim.item in by_key:  # a claim attributed to a record that was not sent is dropped
                by_key[claim.item].append(claim.model_dump())
        for entry in job:
            conn.execute(
                "INSERT OR REPLACE INTO item_claims (matter_id, kind, clio_id, content_hash, prompt_version, model, result, at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (matter_id, entry["kind"], entry["clio_id"], entry["hash"], prompts.CLAIMS_VERSION, model,
                 json.dumps(by_key[entry["key"]]), now_iso()),
            )
            progress.items_done += 1
        conn.commit()

    _run_parallel(jobs, work, on_done)
    # Records deleted or emptied in Clio no longer carry claims.
    live = {(entry["kind"], entry["clio_id"]) for entry in everything}
    for kind, clio_id in set(stored) - live:
        conn.execute("DELETE FROM item_claims WHERE matter_id=? AND kind=? AND clio_id=?", (matter_id, kind, clio_id))
    conn.commit()


# --------------------------------------------------------------------------- the claim set (built in code)


ORIGIN = {"note": "notes", "task": "notes", "calendar_entry": "notes", "communication": "correspondence", "custom_field": "field"}


def collect_claims(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> list[dict[str, Any]]:
    """Every stored claim for the matter's current content, with stable ids and
    quotes checked against their source text. Plain dicts; `overlay` turns them
    into contract objects."""
    claims: list[dict[str, Any]] = []
    current = {(entry["kind"], entry["clio_id"]): entry for entry in text_items(conn, matter_id)}
    rows = conn.execute("SELECT kind, clio_id, content_hash, result FROM item_claims WHERE matter_id=?", (matter_id,)).fetchall()
    for row in rows:
        entry = current.get((row["kind"], row["clio_id"]))
        if entry is None or entry["hash"] != row["content_hash"]:
            continue  # stale: the record changed in Clio since it was read
        for index, claim in enumerate(json.loads(row["result"])):
            claims.append(
                {
                    "id": f"{row['kind']}:{row['clio_id']}#{index}",
                    "origin": ORIGIN[row["kind"]],
                    "source_kind": row["kind"],
                    "clio_id": row["clio_id"],
                    "page": None,
                    "record_date": entry["date"],
                    "label": entry["text"].split("\n", 1)[0][:140],
                    "quote_verified": quote_in(claim["quote"], entry["text"]),
                    **{key: claim[key] for key in ("kind", "topic", "statement", "quote", "date", "amount_usd", "party", "category")},
                }
            )
    names = {int(d["id"]): d.get("name") or "" for d in items(conn, matter_id, "document")}
    dates = {int(d["id"]): d.get("received_at") for d in items(conn, matter_id, "document")}
    for document in _documents(cfg, conn, matter_id):
        reads = conn.execute(
            "SELECT page, result FROM page_reads WHERE sha256=? AND prompt_version=? AND model=? ORDER BY page",
            (document["sha256"], prompts.PAGES_VERSION, cfg.digest_model_bulk),
        ).fetchall()
        if not reads:
            continue
        layers = _text_layers(str(cfg.data_dir / document["path"]), document["sha256"])
        for row in reads:
            read = json.loads(row["result"])
            layer = layers.get(row["page"], "")
            for index, fact in enumerate(read["facts"]):
                claims.append(
                    {
                        "id": f"document:{document['document_id']}:p{row['page']}#{index}",
                        "origin": "document",
                        "source_kind": "document",
                        "clio_id": str(document["document_id"]),
                        "page": row["page"],
                        "record_date": dates.get(document["document_id"]),
                        "label": names.get(document["document_id"], ""),
                        "quote_verified": quote_in(fact["quote"], layer),
                        "scanned": not layer,
                        "page_type": read["page_type"],
                        "issuer": read["issuer"],
                        "document_date": read["document_date"],
                        "topic": None,
                        "category": "internal",
                        **{key: fact[key] for key in ("kind", "statement", "quote", "date", "amount_usd", "party")},
                    }
                )
    return claims


@lru_cache(maxsize=64)
def _text_layers(path: str, sha256: str) -> dict[int, str]:
    """Text layer of every page of one document version, read once per process."""
    import pymupdf

    try:
        with pymupdf.open(path) as document:
            return {number: page.get_text() for number, page in enumerate(document, start=1)}
    except Exception:
        return {}


def page_reads(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> Iterable[tuple[int, int, dict[str, Any]]]:
    """(document id, page, stored read) for every page read so far."""
    for document in _documents(cfg, conn, matter_id):
        for row in conn.execute(
            "SELECT page, result FROM page_reads WHERE sha256=? AND prompt_version=? AND model=? ORDER BY page",
            (document["sha256"], prompts.PAGES_VERSION, cfg.digest_model_bulk),
        ):
            yield document["document_id"], row["page"], json.loads(row["result"])


# --------------------------------------------------------------------------- stage 3: reconciliation


def reconcile_input(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> tuple[str, dict[str, Any]]:
    claims = collect_claims(cfg, conn, matter_id)
    lines = []
    for claim in claims:
        if claim["origin"] == "document" and claim["kind"] not in RECONCILE_FACT_KINDS:
            continue
        lines.append(
            json.dumps(
                {k: v for k, v in {
                    "id": claim["id"], "origin": claim["origin"], "kind": claim["kind"], "topic": claim["topic"],
                    "statement": claim["statement"], "date": claim["date"], "amount_usd": claim["amount_usd"],
                    "party": claim["party"], "recorded": (claim["record_date"] or "")[:10] or None,
                    "page_type": claim.get("page_type"), "issuer": claim.get("issuer"),
                }.items() if v is not None},
                ensure_ascii=False,
            )
        )
    contacts = [{"contact_id": int(c["id"]), "name": c.get("name")} for c in items(conn, matter_id, "contact")]
    issuers = sorted({read["issuer"] for _, _, read in page_reads(cfg, conn, matter_id) if read.get("issuer")})
    payload = {"contacts": contacts, "issuers": issuers, "claims": lines}
    return content_hash(payload), payload


def run_reconcile(
    cfg: Settings, conn: sqlite3.Connection, matter_id: int, run_id: int, progress: Progress, force: bool = False
) -> None:
    """Build the conflict cards, key facts and summary from the current claims.

    Runs by itself only the first time. After that a digest re-reads what changed
    and leaves the cards alone: an attorney's reviewed cards must not reshuffle
    because one email arrived. Rebuilding them is an explicit request (`force`)."""
    input_hash, payload = reconcile_input(cfg, conn, matter_id)
    row = conn.execute("SELECT input_hash, prompt_version, model FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    if row and (row["input_hash"], row["prompt_version"], row["model"]) == (input_hash, prompts.RECONCILE_VERSION, cfg.digest_model):
        progress.reconciled = True
        return
    if row and not force:
        progress.reconciled = True  # cards exist; they are marked out of date in meta.digest, not rebuilt
        progress.reconcile_stale = True
        return
    if not payload["claims"]:
        return
    content = [
        llm.text_part("Contacts on the matter:\n" + json.dumps(payload["contacts"], ensure_ascii=False)),
        llm.text_part("Issuers printed on document pages:\n" + json.dumps(payload["issuers"], ensure_ascii=False)),
        llm.text_part("Claims, one JSON object per line:\n" + "\n".join(payload["claims"])),
    ]
    try:
        result, usage = llm.structured(
            cfg, model=cfg.digest_model, instructions=prompts.RECONCILE, content=content,
            schema=schemas.Reconciled, effort="medium", max_output_tokens=RECONCILE_OUTPUT_TOKENS,
        )
    except llm.LLMUsageError as error:
        record_call(conn, matter_id, run_id, "reconcile", error.usage, False)
        progress.cost_usd += error.usage.cost_usd or 0.0
        progress.errors.append(f"reconcile: {error}")
        return
    except llm.LLMError as error:
        progress.errors.append(f"reconcile: {error}")
        return
    record_call(conn, matter_id, run_id, "reconcile", usage, True)
    progress.calls += 1
    progress.cost_usd += usage.cost_usd or 0.0
    conn.execute(
        "INSERT OR REPLACE INTO reconciliations (matter_id, input_hash, prompt_version, model, result, at, claims)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            matter_id, input_hash, prompts.RECONCILE_VERSION, cfg.digest_model, result.model_dump_json(), now_iso(),
            json.dumps(referenced_claims(cfg, conn, matter_id, result)),
        ),
    )
    conn.commit()
    progress.reconciled = True


def referenced_ids(result: schemas.Reconciled) -> set[str]:
    ids: set[str] = set()
    for conflict in result.conflicts:
        ids.update(conflict.notes_claim_ids + conflict.document_claim_ids)
    for group in (result.key_facts, result.node_evidence, result.summary):
        for entry in group:
            ids.update(entry.claim_ids)
    ids.update(event.claim_id for event in result.events)
    ids.update(economic.claim_id for economic in result.economics)
    return ids


def referenced_claims(cfg: Settings, conn: sqlite3.Connection, matter_id: int, result: schemas.Reconciled) -> dict[str, Any]:
    """The claims the cards rest on, as they read when the cards were built. Kept so a
    card still shows what it was built from after its record changes in Clio."""
    wanted = referenced_ids(result)
    return {claim["id"]: claim for claim in collect_claims(cfg, conn, matter_id) if claim["id"] in wanted}


# --------------------------------------------------------------------------- incoming communications


# Part of each stored result's key, so results made under an older rule are redone, not shown.
INCOMING_RULE = "as-of-date, own claims excluded"


def incoming_messages(conn: sqlite3.Connection, matter_id: int) -> list[dict[str, Any]]:
    """Logged communications sent by a contact other than the client: what the outside world told the firm."""
    matter = (items(conn, matter_id, "matter") or [{}])[0]
    client_id = (matter.get("client") or {}).get("id")
    out = []
    for row in items(conn, matter_id, "communication"):
        senders = [int(p["id"]) for p in row.get("senders") or [] if p.get("type") != "User"]
        body = (row.get("body") or "").strip()
        if not senders or senders[0] == client_id or not body:
            continue
        out.append(
            {"clio_id": str(row["id"]), "sender": senders[0], "text": body, "date": (row.get("date") or "")[:10] or None,
             "hash": content_hash(["incoming", INCOMING_RULE, body])}
        )
    return out


def checker_version() -> str:
    """The checker's own prompt-and-rules version: results made by an older one are redone, not shown."""
    from ..check.tier2 import CHECK_VERSION

    return CHECK_VERSION


def run_incoming(cfg: Settings, conn: sqlite3.Connection, matter_id: int, run_id: int, progress: Progress, stop: threading.Event) -> None:
    """Run each incoming communication through the live checker (the same engine the
    Write surface uses) and keep the result, keyed on the message and the ledger it was checked against."""
    from ..check import check_text
    from ..check import ledger as ledgers

    # A stored result is good for the ledger it was checked against and the checker that made it.
    version = f"{ledgers.current(cfg, conn, matter_id).version}/{checker_version()}"
    stored = {
        row["clio_id"]: row
        for row in conn.execute(
            "SELECT clio_id, content_hash, ledger_version FROM incoming_checks WHERE matter_id=? AND kind='communication'",
            (matter_id,),
        )
    }
    for message in incoming_messages(conn, matter_id):
        if stop.is_set():
            return
        row = stored.get(message["clio_id"])
        if row and row["content_hash"] == message["hash"] and row["ledger_version"] == version:
            continue
        try:
            # Checked as of the day it was written, and never against its own claims: the message is
            # itself a record in the ledger, and a later record cannot contradict what was true then.
            result = check_text(
                cfg, conn, matter_id, message["text"], mode="incoming", author_contact_id=message["sender"],
                written_on=message["date"], source=("communication", message["clio_id"]), max_tier=2,
            )
        except Exception as error:  # the checker is a separate module; a failure there must not fail the digest
            progress.errors.append(f"incoming check of communication {message['clio_id']}: {type(error).__name__}")
            continue
        if result.pending:
            continue  # the model tier did not answer; left for the next run rather than stored half-checked
        conn.execute(
            "INSERT OR REPLACE INTO incoming_checks (matter_id, kind, clio_id, content_hash, ledger_version, result, at)"
            " VALUES (?,'communication',?,?,?,?,?)",
            (matter_id, message["clio_id"], message["hash"], version, result.model_dump_json(), now_iso()),
        )
        conn.commit()
        progress.incoming_checked += 1


# --------------------------------------------------------------------------- client photo


PHOTO_MARGIN = 0.02  # of the page, added round the model's box so a tight estimate does not clip the face


def locate_client_photo(cfg: Settings, conn: sqlite3.Connection, matter_id: int, run_id: int, progress: Progress) -> None:
    """Find the portrait on the first identification page that shows a face, and
    remember the box so the header can show the client. One small call, once per document version."""
    for document in _documents(cfg, conn, matter_id):
        for row in conn.execute(
            "SELECT page, result FROM page_reads WHERE sha256=? AND prompt_version=? AND model=? ORDER BY page",
            (document["sha256"], prompts.PAGES_VERSION, cfg.digest_model_bulk),
        ):
            read = json.loads(row["result"])
            if not (read["shows_photo_of_a_person"] and read["page_type"] == "identification"):
                continue
            key = f"client_photo:{matter_id}"
            stored = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if stored and json.loads(stored["value"]).get("sha256") == document["sha256"]:
                return
            jpeg, _ = render_page(cfg.data_dir / document["path"], row["page"])
            found: dict[str, Any] = {"document_id": document["document_id"], "page": row["page"], "sha256": document["sha256"]}
            try:
                box, usage = llm.structured(
                    cfg, model=cfg.digest_model_bulk, instructions=prompts.PHOTO, content=[llm.image_part(jpeg)],
                    schema=schemas.PhotoBox, effort="low", max_output_tokens=2000,
                )
                record_call(conn, matter_id, run_id, "photo", usage, True)
                progress.calls += 1
                progress.cost_usd += usage.cost_usd or 0.0
                if box.found and box.right > box.left and box.bottom > box.top:
                    found["crop"] = [
                        max(box.left - PHOTO_MARGIN, 0.0), max(box.top - PHOTO_MARGIN, 0.0),
                        min(box.right + PHOTO_MARGIN, 1.0), min(box.bottom + PHOTO_MARGIN, 1.0),
                    ]
            except llm.LLMError as error:
                progress.errors.append(f"client photo: {error}")
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(found)),
            )
            conn.commit()
            return


# --------------------------------------------------------------------------- the run


_running: dict[int, tuple[threading.Thread, threading.Event, Progress]] = {}
_running_lock = threading.Lock()


def run(
    cfg: Settings,
    conn: sqlite3.Connection,
    matter_id: int,
    stop: threading.Event | None = None,
    progress: Progress | None = None,
    reconcile: bool = False,
) -> Progress:
    """Digest whatever is new or changed. Safe to interrupt and run again.
    `reconcile` rebuilds the conflict cards even though some already exist."""
    if not cfg.openai_api_key or not cfg.digest_model:
        missing = " and ".join(name for name, value in (("OPENAI_API_KEY", cfg.openai_api_key), ("DIGEST_MODEL", cfg.digest_model)) if not value)
        raise llm.LLMError(f"{missing} is not set in .env: the digest cannot run. See .env.example.")
    stop = stop or threading.Event()
    progress = progress or Progress()
    run_id = conn.execute(
        "INSERT INTO digest_runs (matter_id, started_at, state) VALUES (?,?,'running')", (matter_id, now_iso())
    ).lastrowid
    conn.commit()
    state = "failed"
    try:
        # Claims from Clio text are quick and make the first reconciliation possible while pages are still being read.
        run_claims(cfg, conn, matter_id, run_id, progress, stop)
        run_pages(cfg, conn, matter_id, run_id, progress, stop)
        if not stop.is_set():
            locate_client_photo(cfg, conn, matter_id, run_id, progress)
            run_reconcile(cfg, conn, matter_id, run_id, progress, force=reconcile)
            run_incoming(cfg, conn, matter_id, run_id, progress, stop)
        complete = progress.pages_done == progress.pages_total and progress.items_done == progress.items_total and progress.reconciled
        state = "complete" if complete else "partial"
    finally:
        conn.execute(
            "UPDATE digest_runs SET finished_at=?, state=?, note=? WHERE id=?",
            (now_iso(), state, "; ".join(progress.errors[:20]) or None, run_id),
        )
        conn.commit()
    return progress


def start_background(cfg: Settings, matter_id: int, reconcile: bool = False) -> bool:
    """Start a run on its own thread and connection. False if one is already running."""
    from ..db import connect

    with _running_lock:
        current = _running.get(matter_id)
        if current and current[0].is_alive():
            return False
        stop, progress = threading.Event(), Progress()

        def target() -> None:
            conn = connect(cfg.db_path)
            try:
                run(cfg, conn, matter_id, stop, progress, reconcile)
            finally:
                conn.close()

        thread = threading.Thread(target=target, name=f"digest-{matter_id}", daemon=True)
        _running[matter_id] = (thread, stop, progress)
        thread.start()
        return True


def is_running(matter_id: int) -> bool:
    current = _running.get(matter_id)
    return bool(current and current[0].is_alive())


def stop_background(matter_id: int) -> None:
    current = _running.get(matter_id)
    if current:
        current[1].set()
