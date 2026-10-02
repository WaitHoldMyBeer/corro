"""Where the assistant keeps conversations and the documents it drafted: our own database, never Clio."""

from __future__ import annotations

import sqlite3
import threading

from shared import assistant_contract as a

TITLE_CHARS = 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id       INTEGER NOT NULL,
    conversation_id TEXT    NOT NULL,
    turn_id         TEXT    NOT NULL,
    at              TEXT    NOT NULL,
    mode            TEXT    NOT NULL,
    ledger_version  TEXT    NOT NULL,
    question_key    TEXT    NOT NULL,   -- hash of everything the answer depends on; a repeat is served from here
    turn            TEXT    NOT NULL    -- the AssistantTurn as it was returned
);
CREATE INDEX IF NOT EXISTS assistant_turns_conversation ON assistant_turns (matter_id, conversation_id, id);
CREATE INDEX IF NOT EXISTS assistant_turns_key ON assistant_turns (matter_id, question_key);

CREATE TABLE IF NOT EXISTS assistant_documents (
    matter_id       INTEGER NOT NULL,
    document_id     TEXT    NOT NULL,
    conversation_id TEXT,
    kind            TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    ledger_version  TEXT    NOT NULL,
    entries         INTEGER NOT NULL,
    body            TEXT    NOT NULL,   -- SavedDocument: the document and its resolved citations
    citations       INTEGER,            -- how many references the document holds
    pages           INTEGER,            -- page count of its PDF, once it has been rendered
    PRIMARY KEY (matter_id, document_id)
);
"""


CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS assistant_conversations (
    matter_id       INTEGER NOT NULL,
    conversation_id TEXT    NOT NULL,
    title           TEXT    NOT NULL,   -- the name the lawyer gave it; without a row the title is the first message
    PRIMARY KEY (matter_id, conversation_id)
);
"""


_ensuring = threading.Lock()


def ensure(conn: sqlite3.Connection) -> None:
    """Create this module's tables if they are not there. Several requests arrive together when the
    section opens, so one at a time in this process, and a column another process added first is not an error."""
    with _ensuring:
        conn.executescript(SCHEMA + CONVERSATIONS)
        present = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_documents)")}
        for column in ("citations INTEGER", "pages INTEGER"):  # databases created before these columns existed
            if column.split()[0] in present:
                continue
            try:
                conn.execute(f"ALTER TABLE assistant_documents ADD COLUMN {column}")
            except sqlite3.OperationalError as error:
                if "duplicate column" not in str(error).lower():
                    raise
        # Documents saved before the count was kept: count the references each one holds, once.
        if conn.execute("SELECT 1 FROM assistant_documents WHERE citations IS NULL LIMIT 1").fetchone():
            conn.execute(
                "UPDATE assistant_documents SET citations=(SELECT COUNT(*) FROM json_each(assistant_documents.body, '$.citations'))"
                " WHERE citations IS NULL"
            )
        conn.commit()


def title_of(question: str) -> str:
    """A conversation's title, in code: the first line of its first message, cut at a word."""
    line = " ".join((question or "").split())
    if len(line) <= TITLE_CHARS:
        return line or "Conversation"
    return line[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def pdf_href(matter_id: int, document_id: str) -> str:
    return f"/api/matters/{matter_id}/assistant/documents/{document_id}.pdf"


def save_turn(conn: sqlite3.Connection, matter_id: int, key: str, turn: a.AssistantTurn) -> None:
    conn.execute(
        "INSERT INTO assistant_turns (matter_id, conversation_id, turn_id, at, mode, ledger_version, question_key, turn)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (matter_id, turn.conversation_id, turn.turn_id, turn.at, turn.mode, turn.ledger_version, key, turn.model_dump_json()),
    )
    conn.commit()


def answered(conn: sqlite3.Connection, matter_id: int, key: str) -> a.AssistantTurn | None:
    row = conn.execute(
        "SELECT turn FROM assistant_turns WHERE matter_id=? AND question_key=? ORDER BY id DESC LIMIT 1", (matter_id, key)
    ).fetchone()
    return a.AssistantTurn.model_validate_json(row["turn"]) if row else None


def turns(conn: sqlite3.Connection, matter_id: int, conversation_id: str) -> list[a.AssistantTurn]:
    rows = conn.execute(
        "SELECT turn FROM assistant_turns WHERE matter_id=? AND conversation_id=? ORDER BY id", (matter_id, conversation_id)
    ).fetchall()
    return [a.AssistantTurn.model_validate_json(row["turn"]) for row in rows]


def conversations(conn: sqlite3.Connection, matter_id: int, only: str | None = None) -> list[a.ConversationInfo]:
    """The most recent conversations, newest activity first; with `only`, that one conversation however old it is."""
    rows = conn.execute(
        "SELECT conversation_id, COUNT(*) AS n, MIN(at) AS first_at, MAX(at) AS last, MIN(id) AS first FROM assistant_turns"
        " WHERE matter_id=? AND (? IS NULL OR conversation_id=?) GROUP BY conversation_id ORDER BY MAX(id) DESC LIMIT 200",
        (matter_id, only, only),
    ).fetchall()
    named = {row["conversation_id"]: row["title"] for row in conn.execute(
        "SELECT conversation_id, title FROM assistant_conversations WHERE matter_id=?", (matter_id,))}
    made: dict[str, list[a.ConversationDocument]] = {}
    for row in conn.execute(
        "SELECT document_id, conversation_id, kind, title FROM assistant_documents WHERE matter_id=? AND conversation_id IS NOT NULL"
        " ORDER BY created_at", (matter_id,)):
        made.setdefault(row["conversation_id"], []).append(a.ConversationDocument(
            id=row["document_id"], title=row["title"], kind=row["kind"], pdf_href=pdf_href(matter_id, row["document_id"])))
    out = []
    for row in rows:
        first = conn.execute("SELECT json_extract(turn, '$.question') AS question FROM assistant_turns WHERE id=?", (row["first"],)).fetchone()
        out.append(a.ConversationInfo(
            id=row["conversation_id"], title=named.get(row["conversation_id"]) or title_of(first["question"] if first else ""),
            created_at=row["first_at"], updated_at=row["last"], turns=row["n"], documents=made.get(row["conversation_id"], [])))
    return out


def rename_conversation(conn: sqlite3.Connection, matter_id: int, conversation_id: str, title: str) -> None:
    conn.execute(
        "INSERT INTO assistant_conversations (matter_id, conversation_id, title) VALUES (?,?,?)"
        " ON CONFLICT(matter_id, conversation_id) DO UPDATE SET title=excluded.title",
        (matter_id, conversation_id, title.strip()),
    )
    conn.commit()


def delete_conversation(conn: sqlite3.Connection, matter_id: int, conversation_id: str) -> int:
    """Remove a conversation's turns. Documents it created stay in the files list."""
    gone = conn.execute("DELETE FROM assistant_turns WHERE matter_id=? AND conversation_id=?", (matter_id, conversation_id)).rowcount
    conn.execute("DELETE FROM assistant_conversations WHERE matter_id=? AND conversation_id=?", (matter_id, conversation_id))
    conn.commit()
    return gone


def save_document(conn: sqlite3.Connection, matter_id: int, saved: a.SavedDocument) -> None:
    document = saved.document
    conn.execute(
        "INSERT INTO assistant_documents (matter_id, document_id, conversation_id, kind, title, created_at, ledger_version,"
        " entries, body, citations) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(matter_id, document_id) DO UPDATE SET body=excluded.body, citations=excluded.citations,"
        " conversation_id=COALESCE(assistant_documents.conversation_id, excluded.conversation_id)",
        (matter_id, document.id, document.conversation_id, document.kind, document.title, document.created_at,
         document.ledger_version, document.totals.entries, saved.model_dump_json(), len(saved.citations)),
    )
    conn.commit()


def documents(conn: sqlite3.Connection, matter_id: int) -> list[a.DocumentInfo]:
    rows = conn.execute(
        "SELECT document_id, kind, title, created_at, entries, conversation_id, citations, pages FROM assistant_documents"
        " WHERE matter_id=? ORDER BY created_at DESC, rowid DESC LIMIT 300",
        (matter_id,),
    ).fetchall()
    return [
        a.DocumentInfo(id=row["document_id"], kind=row["kind"], title=row["title"], created_at=row["created_at"],
                       entries=row["entries"], citations=row["citations"] or 0, pages=row["pages"], conversation_id=row["conversation_id"],
                       href=f"/api/matters/{matter_id}/assistant/documents/{row['document_id']}", pdf_href=pdf_href(matter_id, row["document_id"]))
        for row in rows
    ]


def document(conn: sqlite3.Connection, matter_id: int, document_id: str) -> a.SavedDocument | None:
    row = conn.execute(
        "SELECT body FROM assistant_documents WHERE matter_id=? AND document_id=?", (matter_id, document_id)
    ).fetchone()
    if row is None:
        return None
    saved = a.SavedDocument.model_validate_json(row["body"])
    saved.document.pdf_href = saved.document.pdf_href or pdf_href(matter_id, document_id)
    return saved


def set_pages(conn: sqlite3.Connection, matter_id: int, document_id: str, pages: int) -> None:
    conn.execute("UPDATE assistant_documents SET pages=? WHERE matter_id=? AND document_id=? AND pages IS NOT ?",
                 (pages, matter_id, document_id, pages))
    conn.commit()


def delete_document(conn: sqlite3.Connection, matter_id: int, document_id: str) -> int:
    gone = conn.execute("DELETE FROM assistant_documents WHERE matter_id=? AND document_id=?", (matter_id, document_id)).rowcount
    conn.commit()
    return gone
