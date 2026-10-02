"""SQLite: everything the app stores lives here, outside Clio.

`clio_items` is a verbatim cache of what Clio returned, one row per object, with
a content hash. The digest is keyed on that hash, so an unchanged item is never
sent to a model twice.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS clio_items (
    matter_id     INTEGER NOT NULL,
    kind          TEXT    NOT NULL,
    clio_id       TEXT    NOT NULL,  -- Clio ids are integers, except calendar entries (strings)
    etag          TEXT,
    content_hash  TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    first_seen_at TEXT    NOT NULL,
    changed_at    TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    removed_at    TEXT,
    PRIMARY KEY (matter_id, kind, clio_id)
);

CREATE TABLE IF NOT EXISTS item_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL,
    matter_id INTEGER NOT NULL,
    kind      TEXT    NOT NULL,
    clio_id   TEXT    NOT NULL,
    change    TEXT    NOT NULL CHECK (change IN ('new', 'updated', 'removed')),
    old_hash  TEXT,
    new_hash  TEXT,
    at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS item_changes_matter_at ON item_changes (matter_id, at);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id   INTEGER NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    requests    INTEGER,
    report      TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS document_blobs (
    matter_id     INTEGER NOT NULL,
    document_id   INTEGER NOT NULL,
    version_key   TEXT    NOT NULL,
    sha256        TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    path          TEXT    NOT NULL,
    content_type  TEXT,
    downloaded_at TEXT    NOT NULL,
    page_count    INTEGER,           -- PDFs only
    text_pages    INTEGER,           -- pages with a text layer; 0 means an image-only scan
    PRIMARY KEY (matter_id, document_id)
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    expires_at    TEXT,
    obtained_at   TEXT NOT NULL
);

-- Cases as the firm sees them: ones created here (ids below zero, which no source system
-- issues) and our own flags on imported ones. Archiving is ours alone; nothing goes back to the source.
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    client_name TEXT,
    number      TEXT,
    source      TEXT NOT NULL CHECK (source IN ('upload', 'clio')),
    created_at  TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Sharing with providers: our own state, never written to Clio.
CREATE TABLE IF NOT EXISTS share_policies (
    matter_id  INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    policy     TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (matter_id, contact_id)
);

CREATE TABLE IF NOT EXISTS shares (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token           TEXT    NOT NULL UNIQUE,
    matter_id       INTEGER NOT NULL,
    contact_id      INTEGER NOT NULL,
    policy          TEXT    NOT NULL,  -- the policy as it stood when the link was sent
    categories      TEXT    NOT NULL,
    item_count      INTEGER NOT NULL DEFAULT 0,
    sent_at         TEXT    NOT NULL,
    first_opened_at TEXT,
    last_opened_at  TEXT,
    open_count      INTEGER NOT NULL DEFAULT 0,
    snapshot        TEXT    NOT NULL,  -- the ProviderView exactly as it left the firm
    expires_at      TEXT,
    revoked_at      TEXT
);

-- The digest. Results are keyed on the content that produced them (document bytes,
-- Clio item hash) plus the prompt version and model, so nothing is digested twice.
CREATE TABLE IF NOT EXISTS page_reads (
    sha256         TEXT    NOT NULL,   -- of the document bytes
    page           INTEGER NOT NULL,   -- 1-based
    prompt_version TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    result         TEXT    NOT NULL,
    at             TEXT    NOT NULL,
    PRIMARY KEY (sha256, page, prompt_version, model)
);

CREATE TABLE IF NOT EXISTS item_claims (
    matter_id      INTEGER NOT NULL,
    kind           TEXT    NOT NULL,
    clio_id        TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    result         TEXT    NOT NULL,
    at             TEXT    NOT NULL,
    PRIMARY KEY (matter_id, kind, clio_id)
);

CREATE TABLE IF NOT EXISTS reconciliations (
    matter_id      INTEGER PRIMARY KEY,
    input_hash     TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL,
    model          TEXT    NOT NULL,
    result         TEXT    NOT NULL,
    at             TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS incoming_checks (
    matter_id      INTEGER NOT NULL,
    kind           TEXT    NOT NULL,
    clio_id        TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    ledger_version TEXT    NOT NULL,
    result         TEXT    NOT NULL,   -- the checker's CheckResult for the message body
    at             TEXT    NOT NULL,
    PRIMARY KEY (matter_id, kind, clio_id)
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id   INTEGER NOT NULL,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    state       TEXT    NOT NULL,   -- running | complete | partial | failed
    note        TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id          INTEGER NOT NULL,
    run_id             INTEGER,
    purpose            TEXT    NOT NULL,   -- pages | claims | reconcile | probe
    model              TEXT    NOT NULL,
    input_tokens       INTEGER NOT NULL,
    cached_tokens      INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    output_tokens      INTEGER NOT NULL,
    cost_usd           REAL,
    seconds            REAL,
    ok                 INTEGER NOT NULL,
    at                 TEXT    NOT NULL
);

-- The attorney's call on each notes-versus-document conflict.
CREATE TABLE IF NOT EXISTS conflict_reviews (
    matter_id   INTEGER NOT NULL,
    conflict_id TEXT    NOT NULL,
    review      TEXT    NOT NULL CHECK (review IN ('unreviewed', 'confirmed', 'dismissed')),
    reviewed_at TEXT    NOT NULL,
    PRIMARY KEY (matter_id, conflict_id)
);

CREATE TABLE IF NOT EXISTS provider_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id   INTEGER NOT NULL,
    contact_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    text        TEXT,
    created_at  TEXT    NOT NULL,
    state       TEXT    NOT NULL CHECK (state IN ('open', 'answered', 'declined')),
    answer      TEXT,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS provider_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id  INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    direction  TEXT    NOT NULL CHECK (direction IN ('from_provider', 'from_firm')),
    text       TEXT    NOT NULL,
    at         TEXT    NOT NULL,
    request_id INTEGER
);

CREATE TABLE IF NOT EXISTS send_overrides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id  INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    share_id   INTEGER,           -- the share that went out over a hold; NULL for a thread message
    item       TEXT    NOT NULL,  -- 'message' (cover note), 'thread', or the id of an ask
    text       TEXT    NOT NULL,  -- the held sentence, and no more of the text than that
    category   TEXT,              -- what it discloses, as the checker classified it
    state      TEXT    NOT NULL,  -- dont_send | unchecked | unavailable
    reason     TEXT    NOT NULL,  -- the attorney's reason for sending anyway
    at         TEXT    NOT NULL
);

-- The attorney's call on a provider's text that the checker read against the file.
CREATE TABLE IF NOT EXISTS incoming_reviews (
    matter_id   INTEGER NOT NULL,
    contact_id  INTEGER NOT NULL,
    item        TEXT    NOT NULL,  -- message:<id> | request:<id> | reply:<ask id>
    text_hash   TEXT    NOT NULL,  -- of the text that was reviewed; a later text under the same key is unreviewed
    review      TEXT    NOT NULL CHECK (review IN ('unreviewed', 'confirmed', 'dismissed')),
    reviewed_at TEXT    NOT NULL,
    PRIMARY KEY (matter_id, contact_id, item)
);

CREATE TABLE IF NOT EXISTS ask_replies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id  INTEGER NOT NULL,
    ask_id     TEXT    NOT NULL,
    contact_id INTEGER NOT NULL,
    reply      TEXT    NOT NULL,
    replied_at TEXT    NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    # Columns added after the first databases were created.
    for table, column in (("reconciliations", "claims TEXT"), ("conflict_reviews", "claim_ids TEXT")):
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column.split()[0] not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    return conn


def content_hash(payload: Any) -> str:
    """SHA-256 over canonical JSON: key order and whitespace cannot change it."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_item(
    conn: sqlite3.Connection, run_id: int, matter_id: int, kind: str, payload: dict[str, Any], at: str
) -> str:
    """Store one Clio object. Returns 'new', 'updated' or 'same'."""
    clio_id = str(payload["id"])
    new_hash = content_hash(payload)
    row = conn.execute(
        "SELECT content_hash, removed_at FROM clio_items WHERE matter_id=? AND kind=? AND clio_id=?",
        (matter_id, kind, clio_id),
    ).fetchone()
    body = json.dumps(payload, ensure_ascii=False)
    if row is None:
        conn.execute(
            "INSERT INTO clio_items (matter_id, kind, clio_id, etag, content_hash, payload,"
            " first_seen_at, changed_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (matter_id, kind, clio_id, payload.get("etag"), new_hash, body, at, at, at),
        )
        _log_change(conn, run_id, matter_id, kind, clio_id, "new", None, new_hash, at)
        return "new"
    if row["content_hash"] == new_hash and row["removed_at"] is None:
        conn.execute(
            "UPDATE clio_items SET last_seen_at=? WHERE matter_id=? AND kind=? AND clio_id=?",
            (at, matter_id, kind, clio_id),
        )
        return "same"
    conn.execute(
        "UPDATE clio_items SET etag=?, content_hash=?, payload=?, changed_at=?, last_seen_at=?, removed_at=NULL"
        " WHERE matter_id=? AND kind=? AND clio_id=?",
        (payload.get("etag"), new_hash, body, at, at, matter_id, kind, clio_id),
    )
    _log_change(conn, run_id, matter_id, kind, clio_id, "updated", row["content_hash"], new_hash, at)
    return "updated"


def mark_removed(
    conn: sqlite3.Connection, run_id: int, matter_id: int, kind: str, seen_ids: Iterable[Any], at: str
) -> int:
    """Flag items of `kind` that Clio no longer returns. Rows are kept, never deleted."""
    seen = {str(i) for i in seen_ids}
    rows = conn.execute(
        "SELECT clio_id, content_hash FROM clio_items WHERE matter_id=? AND kind=? AND removed_at IS NULL",
        (matter_id, kind),
    ).fetchall()
    # Ids below zero are records added in this app (an uploaded document), not Clio's: a sync never retires them.
    gone = [r for r in rows if r["clio_id"] not in seen and not str(r["clio_id"]).startswith("-")]
    for r in gone:
        conn.execute(
            "UPDATE clio_items SET removed_at=? WHERE matter_id=? AND kind=? AND clio_id=?",
            (at, matter_id, kind, r["clio_id"]),
        )
        _log_change(conn, run_id, matter_id, kind, r["clio_id"], "removed", r["content_hash"], None, at)
    return len(gone)


def _log_change(conn, run_id, matter_id, kind, clio_id, change, old_hash, new_hash, at) -> None:
    conn.execute(
        "INSERT INTO item_changes (run_id, matter_id, kind, clio_id, change, old_hash, new_hash, at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (run_id, matter_id, kind, clio_id, change, old_hash, new_hash, at),
    )


def items(conn: sqlite3.Connection, matter_id: int, kind: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT payload FROM clio_items WHERE matter_id=? AND kind=? AND removed_at IS NULL ORDER BY CAST(clio_id AS INTEGER), clio_id",
        (matter_id, kind),
    ).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def item(conn: sqlite3.Connection, matter_id: int, kind: str, clio_id: int | str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload FROM clio_items WHERE matter_id=? AND kind=? AND clio_id=?", (matter_id, kind, str(clio_id))
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def counts(conn: sqlite3.Connection, matter_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT kind, COUNT(*) AS n FROM clio_items WHERE matter_id=? AND removed_at IS NULL GROUP BY kind",
        (matter_id,),
    ).fetchall()
    return {r["kind"]: r["n"] for r in rows}


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
