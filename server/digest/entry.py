"""Entry points into the digest for code outside it (document upload, review queues).

Four calls, and nothing else in `server/digest/` should need reaching into:

    add_document(cfg, conn, matter_id, filename, data)   -> document id
    digest_document(cfg, conn, matter_id, document_id)   -> DocumentDigest
    reconcile_document(cfg, conn, matter_id, document_id) -> DocumentReconciliation
    ledger_version(cfg, conn, matter_id)                 -> str

A document added here is stored exactly like one read from Clio (same tables, same
page reads keyed on the file's SHA-256), under an id below zero so that it can
never collide with a Clio id and a sync never retires it. Nothing here writes to Clio.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import content_hash, now_iso
from ..pages import text_layer_stats
from . import llm, pipeline, prompts, schemas

UPLOAD_FOLDER = "Uploaded"


@dataclass
class DocumentDigest:
    document_id: int
    pages_total: int = 0
    pages_read: int = 0  # read by the model in this call; 0 when the same bytes were read before
    claims: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class DocumentReconciliation:
    document_id: int
    new_conflicts: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def create_matter(
    cfg: Settings, conn: sqlite3.Connection, name: str, client_name: str | None = None, number: str | None = None
) -> int:
    """Create an empty case in our own store and return its id (below zero). The same
    function as `server.cases.create_case`, here so an importer needs only this module."""
    from ..cases import create_case

    return create_case(conn, name, client_name, number)


def add_document(
    cfg: Settings, conn: sqlite3.Connection, matter_id: int, filename: str, data: bytes, received_at: str | None = None
) -> int:
    """Store a PDF added in this app as a document of the matter and return its id (below zero).
    The same bytes added twice return the first id."""
    sha256 = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "SELECT document_id FROM document_blobs WHERE matter_id=? AND sha256=? AND document_id<0", (matter_id, sha256)
    ).fetchone()
    if existing:
        return existing["document_id"]
    lowest = conn.execute(
        "SELECT MIN(CAST(clio_id AS INTEGER)) AS low FROM clio_items WHERE matter_id=? AND kind='document'", (matter_id,)
    ).fetchone()["low"]
    document_id = min(lowest or 0, 0) - 1
    name = Path(filename).name or f"upload{-document_id}.pdf"
    relative = Path("documents") / str(matter_id) / f"upload{-document_id}{Path(name).suffix.lower() or '.pdf'}"
    target = cfg.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    at = now_iso()
    payload: dict[str, Any] = {
        "id": document_id, "name": name, "filename": name, "content_type": "application/pdf", "size": len(data),
        "received_at": received_at or at, "created_at": at, "updated_at": at, "origin": "uploaded",
        "parent": {"id": 0, "type": "Folder", "name": UPLOAD_FOLDER},
    }
    conn.execute(
        "INSERT INTO clio_items (matter_id, kind, clio_id, etag, content_hash, payload, first_seen_at, changed_at,"
        " last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (matter_id, "document", str(document_id), None, content_hash(payload), json.dumps(payload), at, at, at),
    )
    page_count, text_pages = text_layer_stats(target)
    conn.execute(
        "INSERT INTO document_blobs (matter_id, document_id, version_key, sha256, size_bytes, path, content_type,"
        " downloaded_at, page_count, text_pages) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (matter_id, document_id, sha256, sha256, len(data), str(relative), "application/pdf", at, page_count, text_pages),
    )
    conn.commit()
    return document_id


def digest_document(cfg: Settings, conn: sqlite3.Connection, matter_id: int, document_id: int) -> DocumentDigest:
    """Read one document's pages with the model (only pages not already read for these
    bytes) and return what came of it. The claims join the ledger at once."""
    progress = pipeline.Progress()
    pipeline.run_pages(cfg, conn, matter_id, None, progress, threading.Event(), only_document_id=document_id)
    row = conn.execute(
        "SELECT page_count FROM document_blobs WHERE matter_id=? AND document_id=?", (matter_id, document_id)
    ).fetchone()
    claims = [c for c in pipeline.collect_claims(cfg, conn, matter_id) if c["clio_id"] == str(document_id) and c["origin"] == "document"]
    return DocumentDigest(
        document_id=document_id,
        pages_total=(row["page_count"] or 0) if row else 0,
        pages_read=progress.pages_sent,
        claims=len(claims),
        calls=progress.calls,
        cost_usd=round(progress.cost_usd, 4),
        errors=progress.errors,
    )


def reconcile_document(cfg: Settings, conn: sqlite3.Connection, matter_id: int, document_id: int) -> DocumentReconciliation:
    """Set one document's claims against the firm's own entries and add any conflict
    found to the existing cards. The cards already there, their order and their reviews
    are left as they are; this is one small call, not a rebuild."""
    out = DocumentReconciliation(document_id=document_id)
    everything = pipeline.collect_claims(cfg, conn, matter_id)
    new = [c for c in everything if c["origin"] == "document" and c["clio_id"] == str(document_id) and c["kind"] in pipeline.RECONCILE_FACT_KINDS]
    entries = [c for c in everything if c["origin"] != "document"]
    row = conn.execute("SELECT result, claims, input_hash FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    if not new or not entries or row is None:
        return out  # nothing to compare, or no cards yet: the first full digest builds them

    def line(claim: dict[str, Any]) -> str:
        keep = {k: claim.get(k) for k in ("id", "origin", "kind", "topic", "statement", "date", "amount_usd", "party")}
        return json.dumps({k: v for k, v in keep.items() if v is not None}, ensure_ascii=False)

    content = [
        llm.text_part("Claims from the new document, one JSON object per line:\n" + "\n".join(line(c) for c in new)),
        llm.text_part("Claims from the firm's entries, one JSON object per line:\n" + "\n".join(line(c) for c in entries)),
    ]
    try:
        result, usage = llm.structured(
            cfg, model=cfg.digest_model, instructions=prompts.RECONCILE_DOCUMENT, content=content,
            schema=schemas.NewConflicts, effort="low", max_output_tokens=12000,
        )
    except llm.LLMUsageError as error:
        pipeline.record_call(conn, matter_id, None, "reconcile", error.usage, False)
        out.cost_usd, out.errors = error.usage.cost_usd or 0.0, [str(error)]
        return out
    except llm.LLMError as error:
        out.errors = [str(error)]
        return out
    pipeline.record_call(conn, matter_id, None, "reconcile", usage, True)
    out.calls, out.cost_usd, out.seconds = 1, round(usage.cost_usd or 0.0, 4), usage.seconds

    # Only conflicts that really have both sides, with ids the ledger knows, are kept.
    new_ids, entry_ids = {c["id"] for c in new}, {c["id"] for c in entries}
    kept = []
    for conflict in result.conflicts:
        conflict.notes_claim_ids = [i for i in conflict.notes_claim_ids if i in entry_ids]
        conflict.document_claim_ids = [i for i in conflict.document_claim_ids if i in new_ids]
        if conflict.notes_claim_ids and conflict.document_claim_ids:
            kept.append(conflict)
    stored = json.loads(row["result"])
    stored.setdefault("conflicts", []).extend(json.loads(c.model_dump_json()) for c in kept)
    snapshot = json.loads(row["claims"]) if row["claims"] else {}
    by_id = {c["id"]: c for c in everything}
    for conflict in kept:
        for claim_id in conflict.notes_claim_ids + conflict.document_claim_ids:
            snapshot[claim_id] = by_id[claim_id]
    # The cards now account for this document. If nothing else had changed since they were built, they
    # are up to date again; if other records had changed too, they stay marked out of date.
    current_hash = pipeline.reconcile_input(cfg, conn, matter_id)[0]
    was_current = row["input_hash"] == _hash_without(cfg, conn, matter_id, document_id)
    conn.execute(
        "UPDATE reconciliations SET result=?, claims=?, input_hash=? WHERE matter_id=?",
        (json.dumps(stored), json.dumps(snapshot), current_hash if was_current else row["input_hash"], matter_id),
    )
    conn.commit()
    out.new_conflicts = len(kept)
    return out


def _hash_without(cfg: Settings, conn: sqlite3.Connection, matter_id: int, document_id: int) -> str:
    """What the reconciliation input hash would be without this document's claims and issuers:
    equal to the stored hash exactly when this document is the only thing that changed."""
    _, payload = pipeline.reconcile_input(cfg, conn, matter_id)
    prefix = f'"id": "document:{document_id}:'
    own_issuers = {read["issuer"] for doc, _, read in pipeline.page_reads(cfg, conn, matter_id) if doc == document_id and read.get("issuer")}
    other_issuers = {read["issuer"] for doc, _, read in pipeline.page_reads(cfg, conn, matter_id) if doc != document_id and read.get("issuer")}
    reduced = {
        "contacts": payload["contacts"],
        "issuers": [issuer for issuer in payload["issuers"] if issuer in other_issuers or issuer not in own_issuers],
        "claims": [line for line in payload["claims"] if prefix not in line],
    }
    return content_hash(reduced)


def ledger_version(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> str:
    """The checker's ledger version: a hash of the claims, so it changes by itself the
    moment `digest_document` or `reconcile_document` changes them. Nothing to bump by hand."""
    from ..check import ledger as ledgers

    return ledgers.current(cfg, conn, matter_id).version
