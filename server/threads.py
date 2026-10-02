"""Requests from a provider and the message thread with them. All of it lives in
our database; nothing here reads from or writes to Clio.

What a provider sees of a thread is only what that provider wrote and what the
firm sent to that provider. The firm-side suggestion ("turn on category X") is
computed in code and is never part of a provider-facing payload.
"""

from __future__ import annotations

import sqlite3

from shared import contract as c

from .db import now_iso

# What a provider can ask the firm for, and the share category that would answer it.
REQUEST_KINDS: dict[str, tuple[str, c.DisclosureCategory | None]] = {
    "coverage": ("Is there coverage behind the case?", c.DisclosureCategory.coverage),
    "status": ("Where does the case stand?", c.DisclosureCategory.status),
    "records_needed": ("What records does the firm need from us?", c.DisclosureCategory.asks),
    "payment_timing": ("When can we expect payment?", None),
    "other": ("Something else", None),
}

DECLINE_TEXT = "The firm is not able to share this at present."


def _request(row: sqlite3.Row, allowed: list[str] | None) -> c.ProviderRequest:
    """`allowed` is the provider's current allowlist on the firm side; None builds the provider-facing form."""
    suggested = None
    if allowed is not None and row["state"] == "open":
        category = REQUEST_KINDS.get(row["kind"], REQUEST_KINDS["other"])[1]
        if category is not None and category.value not in allowed:
            suggested = c.SuggestedAction(
                action="enable_category", category=category, label=f"Turn on “{category.value}” for this provider"
            )
        elif category is not None:
            suggested = c.SuggestedAction(
                action="resend", category=category,
                label=f"“{category.value}” is already on for this provider: send them the current view",
            )
        else:
            suggested = c.SuggestedAction(action="reply", label="Write a reply; it is checked before it is sent")
    return c.ProviderRequest(
        id=str(row["id"]),
        contact_id=row["contact_id"],
        kind=row["kind"],
        question=REQUEST_KINDS.get(row["kind"], REQUEST_KINDS["other"])[0],
        text=row["text"],
        created_at=row["created_at"],
        state=row["state"],
        answer=row["answer"],
        answered_at=row["answered_at"],
        suggested=suggested,
    )


def requests(conn: sqlite3.Connection, matter_id: int, contact_id: int, allowed: list[str] | None) -> list[c.ProviderRequest]:
    rows = conn.execute(
        "SELECT * FROM provider_requests WHERE matter_id=? AND contact_id=? ORDER BY id DESC", (matter_id, contact_id)
    ).fetchall()
    return [_request(row, allowed) for row in rows]


def messages(conn: sqlite3.Connection, matter_id: int, contact_id: int) -> list[c.Message]:
    rows = conn.execute(
        "SELECT * FROM provider_messages WHERE matter_id=? AND contact_id=? ORDER BY id", (matter_id, contact_id)
    ).fetchall()
    return [
        c.Message(id=str(row["id"]), contact_id=contact_id, direction=row["direction"], text=row["text"], at=row["at"],
                  request_id=str(row["request_id"]) if row["request_id"] else None)
        for row in rows
    ]


def create_request(conn: sqlite3.Connection, matter_id: int, contact_id: int, kind: str, text: str | None) -> None:
    if kind not in REQUEST_KINDS:
        raise ValueError(f"unknown request kind: {kind}")
    conn.execute(
        "INSERT INTO provider_requests (matter_id, contact_id, kind, text, created_at, state) VALUES (?,?,?,?,?,'open')",
        (matter_id, contact_id, kind, (text or "").strip() or None, now_iso()),
    )
    conn.commit()


def add_message(
    conn: sqlite3.Connection, matter_id: int, contact_id: int, direction: str, text: str, request_id: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO provider_messages (matter_id, contact_id, direction, text, at, request_id) VALUES (?,?,?,?,?,?)",
        (matter_id, contact_id, direction, text.strip(), now_iso(), request_id),
    )
    conn.commit()


def answer(conn: sqlite3.Connection, matter_id: int, contact_id: int, request_id: int, state: str, text: str) -> bool:
    """Close a request as answered or declined, and put the answer in the thread."""
    done = conn.execute(
        "UPDATE provider_requests SET state=?, answer=?, answered_at=? WHERE id=? AND matter_id=? AND contact_id=?",
        (state, text, now_iso(), request_id, matter_id, contact_id),
    ).rowcount
    conn.commit()
    if done:
        add_message(conn, matter_id, contact_id, "from_firm", text, request_id)
    return bool(done)
