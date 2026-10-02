"""The review queue: every five days, five statements in the file that look contradicted
or out of date, put in front of the lawyer to keep, discard or comment on.

Selection is code over the stored digest; no model is called here. A candidate is a
statement taken from one of the firm's own entries (a note, a task, a calendar entry,
a logged message, a field) that is:

  difference    on the entry side of a difference nobody has reviewed, highest rank first;
  superseded    followed by a later-dated entry on the same subject that gives another figure or date;
  entry_only    a figure no page in the file carries, where a page whose quote was found verbatim gives another;
  field_figure  a figure read by rule from a free-text field, where a document gives another.

A statement is only called "may be out of date" when two dates of the same kind support it:
an entry's date against a later entry's, or against the day a document was received. The
date printed on a page says when something happened, not when it reached the file, so it is
shown as what it is and never used to call one side newer.

Each is scored by what rests on it (terms of the value, next moves, cards that cite it)
and by its age; five are taken; one the lawyer has decided is never offered again.

The lawyer's decisions live in our database and nowhere else. Nothing is written to the
practice system, and nothing is deleted: a discarded statement is marked retired, with the
decision, the time and the reason, and can be restored.

What other modules call (all read-only):

    review_queue.retired(conn, matter_id)         -> set of claim ids the lawyer discarded
    review_queue.comments(conn, matter_id)        -> {claim id: the lawyer's note on how to read it}
    review_queue.active(conn, matter_id, claims)  -> the claims without the retired ones; a dict claim
                                                     with a note gains "lawyer_comment"
    review_queue.stamp(conn, matter_id)           -> changes whenever a decision does (for cache keys)

Mounted by `server/app.py`, before the static files:

    from .review_queue import router as review_queue_router
    app.include_router(review_queue_router)
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Body, Depends, HTTPException

from .case import CaseBuilder, MatterNotSynced, source_href
from .config import Settings, get_settings
from .db import connect, now_iso

router = APIRouter()

# Product defaults: how often a queue is made, and how many items it holds.
CYCLE_DAYS = 5
QUEUE_SIZE = 5
MAX_NOTE_CHARS = 2000
MAX_NEWER = 3

# At most this many of one kind, and one per difference and per record, while other candidates remain.
MAX_PER_KIND = 2

# Scoring. What rests on a statement counts for more than its age.
WEIGHT_NODE, WEIGHT_MOVE, WEIGHT_CARD = 3.0, 2.0, 1.0
AGE_CAP_DAYS, WEIGHT_AGE = 730, 2.0
KIND_BASE = {"difference": 4.0, "entry_only": 3.0, "field_figure": 3.0, "superseded": 2.0}

# Two statements are about the same subject when they are of one kind, name no different
# party, and share at least this many subject words carrying this share of the entry's weight.
MIN_SHARED_WORDS, MIN_SHARED_WEIGHT = 3, 0.5

KIND_LABEL = {
    "answered_gap": "The entry calls it outstanding",
    "record": "A document reads differently",
    "expert_opinion": "An expert's opinion differs",
    "superseded": "A later entry on the same subject",
    "entry_only": "Figure found only in an entry",
    "field_figure": "Figure read from a text field",
}
PILL_OUTDATED, PILL_DIFFERENCE, PILL_FIGURE = "May be out of date", "Unreviewed difference", "A different figure in the file"

# Bumped when the shape or wording of a stored item changes; a stored queue of another version is rebuilt in place.
ITEM_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_decisions (
    matter_id      INTEGER NOT NULL,
    claim_id       TEXT    NOT NULL,
    decision       TEXT    NOT NULL CHECK (decision IN ('keep', 'discard', 'comment')),
    note           TEXT,              -- the reason for a discard, or how the statement should be read
    statement_hash TEXT    NOT NULL,  -- of the statement that was decided; a later text under the same id is undecided
    snapshot       TEXT    NOT NULL,  -- the item as the lawyer saw it
    decided_at     TEXT    NOT NULL,
    restored_at    TEXT,              -- set when the lawyer undoes the decision; the row is kept
    PRIMARY KEY (matter_id, claim_id)
);

-- Every decision and every restore, in order. Rows are only ever added.
CREATE TABLE IF NOT EXISTS claim_decision_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    matter_id INTEGER NOT NULL,
    claim_id  TEXT    NOT NULL,
    action    TEXT    NOT NULL CHECK (action IN ('keep', 'discard', 'comment', 'restore')),
    note      TEXT,
    snapshot  TEXT    NOT NULL,
    at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queues (
    matter_id    INTEGER NOT NULL,
    cycle        INTEGER NOT NULL,
    generated_at TEXT    NOT NULL,
    due_at       TEXT    NOT NULL,   -- when the next queue is made
    items        TEXT    NOT NULL,
    candidates   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (matter_id, cycle)
);
"""


def ensure(conn: sqlite3.Connection) -> None:
    """Create this module's tables if they are not there. One statement at a time, so a
    transaction the caller has open on this connection is not committed under it."""
    for statement in SCHEMA.split(";\n\n"):
        if "CREATE TABLE" in statement:
            conn.execute(statement[statement.index("CREATE TABLE"):].rstrip().rstrip(";"))


# --------------------------------------------------------------------------- what other modules read


def _hash(statement: str | None) -> str:
    return hashlib.sha256((statement or "").encode("utf-8")).hexdigest()[:16]


def _read(conn: sqlite3.Connection, sql: str, args: tuple) -> list[sqlite3.Row]:
    """One query. The tables are created only when the query finds them missing, so a
    reader on every case request costs one indexed lookup."""
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error):
            raise
        try:
            ensure(conn)
        except sqlite3.OperationalError:
            return []  # a read-only connection to a database without these tables: nothing has been decided
        return conn.execute(sql, args).fetchall()


def _decisions(conn: sqlite3.Connection, matter_id: int) -> dict[str, sqlite3.Row]:
    rows = _read(conn, "SELECT * FROM claim_decisions WHERE matter_id=? AND restored_at IS NULL", (matter_id,))
    return {row["claim_id"]: row for row in rows}


def retired(conn: sqlite3.Connection, matter_id: int) -> set[str]:
    """Claim ids the lawyer discarded as out of date. Leave them out of anything built from claims."""
    return {claim_id for claim_id, row in _decisions(conn, matter_id).items() if row["decision"] == "discard"}


def comments(conn: sqlite3.Connection, matter_id: int) -> dict[str, str]:
    """Claim id -> the lawyer's note on how the statement should be read or updated."""
    return {
        claim_id: row["note"]
        for claim_id, row in _decisions(conn, matter_id).items()
        if row["decision"] != "discard" and (row["note"] or "").strip()
    }


def active(conn: sqlite3.Connection, matter_id: int, claims: Iterable[Any]) -> list[Any]:
    """`claims` without the retired ones. Works on the digest's dicts and on objects with an
    `id`. A decision made on other text under the same id (the record changed and was read
    again) does not apply. A dict claim the lawyer commented on gains `lawyer_comment`."""
    decisions = _decisions(conn, matter_id)
    out = []
    for claim in claims:
        is_dict = isinstance(claim, dict)
        row = decisions.get(claim["id"] if is_dict else claim.id)
        if row is not None:
            statement = (claim.get("statement") if is_dict else getattr(claim, "statement", None))
            same = statement is None or _hash(statement) == row["statement_hash"]
            if same and row["decision"] == "discard":
                continue
            if same and is_dict and (row["note"] or "").strip():
                claim = {**claim, "lawyer_comment": row["note"]}
        out.append(claim)
    return out


def stamp(conn: sqlite3.Connection, matter_id: int) -> tuple:
    """Cheap to read; changes whenever a decision is made or restored."""
    rows = _read(conn, "SELECT COUNT(*), MAX(id) FROM claim_decision_log WHERE matter_id=?", (matter_id,))
    return tuple(rows[0]) if rows else (0, None)


# --------------------------------------------------------------------------- selection


def _days_old(when: str | None, today: date) -> int | None:
    try:
        return max((today - date.fromisoformat((when or "")[:10])).days, 0)
    except ValueError:
        return None


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _title(name: str | None) -> str:
    """A stored file name as a title: extension, folder and docket prefixes dropped, separators to spaces."""
    name = (name or "").strip()
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}\.?$", "", name)
    return stem.split("__")[-1].replace("-", " ").replace("_", " ").strip() or name


def _entered(claim) -> str | None:
    """The date on the record a statement was read from: an entry's own date, or the day a document was received."""
    return (claim.record_date or "")[:10] or None


def candidates(cfg: Settings, build: CaseBuilder, include_decided: bool = False) -> list[dict[str, Any]]:
    """Every statement that qualifies, scored, best first. Reads only."""
    from .check.ledger import Ledger
    from .digest import overlay, pipeline

    conn, matter_id = build.conn, build.matter_id
    raw = pipeline.collect_claims(cfg, conn, matter_id)
    raw_by_id = {claim["id"]: claim for claim in raw}
    decisions = _decisions(conn, matter_id)
    decided = set() if include_decided else {
        claim_id for claim_id, row in decisions.items()
        if claim_id not in raw_by_id or _hash(raw_by_id[claim_id]["statement"]) == row["statement_hash"]
    }
    case = overlay.apply(cfg, build, build.build(None), all_claims=True)
    shown = {claim.id: claim for claim in case.claims}
    roles = {contact.id: contact.role for contact in build.contacts}
    ledger = Ledger(matter_id, raw, contacts=[{**contact, "role": roles.get(int(contact["id"]), "other")} for contact in build.contacts_raw])
    claims = ledger.claims
    entries = [claim for claim in claims.values() if claim.origin != "document" and claim.id not in decided]
    documents = [claim for claim in claims.values() if claim.origin == "document"]

    # What rests on each statement: terms of the value, next moves, and cards that cite it.
    rests: dict[str, dict[str, dict[str, str]]] = {}

    def rest(claim_id: str, kind: str, key: str, label: str) -> None:
        rests.setdefault(claim_id, {})[f"{kind}:{key}"] = {"type": kind, "id": key, "label": label}

    nodes = {node.id: node for node in case.nodes}
    for node in case.nodes:
        for claim_id in node.claim_ids:
            rest(claim_id, "node", node.id, node.label)
    open_conflicts = [conflict for conflict in case.conflicts if conflict.review == "unreviewed"]
    for conflict in case.conflicts:
        for claim_id in conflict.notes_claim_ids:
            rest(claim_id, "card", conflict.id, f"Difference for review: {conflict.topic}")
            for node_id in conflict.node_ids:
                if node_id in nodes:
                    rest(claim_id, "node", node_id, nodes[node_id].label)
    row = conn.execute("SELECT result FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    stored = json.loads(row["result"]) if row else {}
    for index, fact in enumerate((stored.get("key_facts") or [])[: overlay.MAX_KEY_FACTS]):
        for claim_id in fact.get("claim_ids") or []:
            rest(claim_id, "card", f"key:{index}", f"Key fact: {fact.get('label') or ''}".strip())
    for index, sentence in enumerate(stored.get("summary") or []):
        for claim_id in sentence.get("claim_ids") or []:
            rest(claim_id, "card", f"summary:{index}", "Summary sentence")
    for event in (stored.get("events") or [])[: overlay.MAX_EVENTS]:
        if event.get("claim_id"):
            rest(event["claim_id"], "card", f"event:{event['claim_id']}", "Timeline entry")
    for move in case.moves:
        source = (getattr(move.source.kind, "value", move.source.kind), str(move.source.clio_id))
        for claim_id in ledger.by_source.get(source, []):
            rest(claim_id, "move", move.id, move.title)
        for claim_id, held in list(rests.items()):
            on_node = move.node_id and f"node:{move.node_id}" in held
            on_card = move.id.startswith("move:conflict:") and f"card:{move.id[len('move:'):]}" in held
            if on_node or on_card:
                rest(claim_id, "move", move.id, move.title)

    def same_subject(entry, other) -> bool:
        if entry.kind != other.kind:
            return False
        if entry.contact_ids and other.contact_ids and not (entry.contact_ids & other.contact_ids):
            return False
        shared, share = ledger.overlap(set(entry.tokens), other)
        return shared >= MIN_SHARED_WORDS and share >= MIN_SHARED_WEIGHT

    def page(claim) -> str:
        where = _title(claim.label)[:80] or "a document"
        return f"{where}, p. {claim.page}" if claim.page else where

    def entry(claim, start: bool = True) -> str:
        if claim.origin == "field":
            return "A text field" if start else "a text field"
        when = _entered(claim)
        text = f"an entry dated {when}" if when else "an undated entry"
        return text[:1].upper() + text[1:] if start else text

    found: dict[str, dict[str, Any]] = {}

    def add(claim, kind: str, detail: str, tone: str, pill: str, reason: str, others: list, conflict=None, later: bool = False) -> None:
        if claim.id in found or claim.id not in shown:
            return
        held = list(rests.get(claim.id, {}).values())
        counts = {kind_: sum(1 for item in held if item["type"] == kind_) for kind_ in ("node", "move", "card")}
        age = _days_old(_entered(claim), build.today)
        score = (
            KIND_BASE[kind]
            + WEIGHT_NODE * counts["node"] + WEIGHT_MOVE * counts["move"] + WEIGHT_CARD * counts["card"]
            + WEIGHT_AGE * min(age or 0, AGE_CAP_DAYS) / AGE_CAP_DAYS
            + (1.0 / conflict.rank if conflict is not None and conflict.rank else 0.0)
        )
        found[claim.id] = {
            "v": ITEM_VERSION,
            "id": claim.id,
            "kind": kind,
            "kind_label": KIND_LABEL[detail],
            "tone": tone,
            "pill": pill,
            "reason": reason,
            "score": round(score, 3),
            "age_days": age,
            "statement_hash": _hash(claim.statement),
            "conflict_id": conflict.id if conflict is not None else None,
            "conflict_rank": conflict.rank if conflict is not None else None,
            "record": f"{claim.source_kind}:{claim.clio_id}",
            "older": _side(shown[claim.id], claim),
            "newer": [_side(shown[other.id], other, later) for other in others[:MAX_NEWER] if other.id in shown],
            "depends": sorted(held, key=lambda item: ("node", "move", "card").index(item["type"])),
            "depends_counts": counts,
        }

    # (a) the entry side of a difference nobody has reviewed, highest rank first
    for conflict in sorted(open_conflicts, key=lambda x: x.rank):
        pages = [claims[i] for i in conflict.document_claim_ids if i in claims]
        if not pages:
            continue
        where = page(pages[0]) + (f" and {_plural(len(pages) - 1, 'other page')}" if len(pages) > 1 else "")
        received = [_entered(other) for other in pages]
        for claim_id in conflict.notes_claim_ids:
            claim = claims.get(claim_id)
            if claim is None or claim.id in decided:
                continue
            written = _entered(claim)
            tone, pill = ("contradicted" if conflict.kind == "record" else "check"), PILL_DIFFERENCE
            if conflict.kind == "answered_gap":
                reason = f"{entry(claim)} calls this outstanding; it is in the file ({where})."
                # Out of date only when the page reached the file after the entry was written: two dates of one kind.
                if written and all(received) and min(received) > written:
                    tone, pill = "outdated", PILL_OUTDATED
                    reason += f" The document was received on {min(received)}, after the entry was written."
                elif all(received):
                    reason += f" The document was received on {min(received)}."
            elif conflict.kind == "expert_opinion":
                reason = f"{entry(claim)} says this; an expert's report in the file gives a different opinion ({where})."
            else:
                reason = f"{entry(claim)} says this; a document in the file reads differently ({where})."
            add(claim, "difference", conflict.kind, tone, pill, reason + " Nobody has reviewed the difference.", pages, conflict)

    # (b) a later-dated entry on the same subject that gives another figure or date. Both dates are entries' own.
    dated_entries = [claim for claim in claims.values() if claim.origin != "document" and _entered(claim)]

    def same_topic(one, other) -> bool:
        named = one.topic and other.topic and one.topic.strip().lower() == other.topic.strip().lower()
        return bool(named) and (one.party or "").strip().lower() == (other.party or "").strip().lower()

    for claim in dated_entries:
        if claim.id in decided or (claim.amount is None and not claim.date):
            continue
        later = [
            other for other in dated_entries
            if _entered(other) > _entered(claim) and other.kind == claim.kind
            and (other.source_kind, other.clio_id) != (claim.source_kind, claim.clio_id)
        ]
        figures = [
            o for o in later
            if claim.amount is not None and o.amount and o.amount != claim.amount and (same_topic(claim, o) or same_subject(claim, o))
        ]
        # A date only supersedes a date when both entries read as one subject; appointments recur, so the calendar is left out.
        days = [
            o for o in later
            if not figures and claim.date and o.date and o.date != claim.date and same_topic(claim, o) and same_subject(claim, o)
            and "calendar_entry" not in (claim.source_kind, o.source_kind)
        ]
        newer = sorted(figures or days, key=_entered, reverse=True)
        if newer:
            what = "a different figure" if figures else "a different date"
            add(claim, "superseded", "superseded", "outdated", PILL_OUTDATED,
                f"This entry is dated {_entered(claim)}; a later entry on the same subject, dated {_entered(newer[0])}, gives {what}.",
                newer, later=True)

    # (c) and (d): a figure in an entry or a text field that no page carries, where a page gives another
    for claim in entries:
        if claim.amount is None:
            continue
        if any(claims[i].origin == "document" for i in ledger.by_amount.get(claim.amount, [])):
            continue  # a page in the file carries the same figure
        others = [
            (ledger.overlap(set(claim.tokens), other)[1], other) for other in documents
            if other.amount and other.amount != claim.amount and same_subject(claim, other)
        ]
        others = [other for _, other in sorted(others, key=lambda pair: -pair[0])]
        if not others:
            continue
        if claim.origin == "field":
            # A field carries no date, so nothing here says which is the later figure.
            add(claim, "field_figure", "field_figure", "check", PILL_FIGURE,
                f"This figure ({_money(claim.amount)}) was read from a text field, which carries no date; a document in the"
                f" file gives a different figure on the same subject ({page(others[0])}).", others)
            continue
        verified = [other for other in others if other.quote_verified]
        if verified:
            add(claim, "entry_only", "entry_only", "contradicted", PILL_FIGURE,
                f"This figure ({_money(claim.amount)}) appears only in {entry(claim, start=False)}; no page in the file carries it,"
                f" and a page whose quote was found word for word gives a different one ({page(verified[0])}).", verified)

    return sorted(found.values(), key=lambda item: (-item["score"], item["conflict_rank"] or 10**6, item["id"]))


def _side(shown, claim, later: bool = False) -> dict[str, Any]:
    """One side of an item: what kind of source it is, what it says, each date with what
    that date is, and the source to open. `shown` is the contract claim, `claim` the ledger's."""
    if claim.origin == "document":
        what = "A document in the file says"
        dates = [{"label": "Dated", "value": claim.document_date[:10]}] if claim.document_date else []
        if _entered(claim):
            dates.append({"label": "Received", "value": _entered(claim)})
    else:
        what = "A text field says" if claim.origin == "field" else "A later entry says" if later else "An entry says"
        dates = [{"label": "Entry dated", "value": _entered(claim)}] if _entered(claim) else []
    return {
        "claim_id": claim.id,
        "what": what,
        "text": shown.text,
        "dates": dates,
        "origin": claim.origin,
        "source": shown.source.model_dump(mode="json"),
    }


def select(found: list[dict[str, Any]], size: int = QUEUE_SIZE) -> list[dict[str, Any]]:
    """Best first, spread out: at most two of a kind, one per difference and one per record,
    until those rules would leave the queue short."""
    chosen: list[dict[str, Any]] = []
    for limit_kind, one_each in ((MAX_PER_KIND, True), (size, True), (size, False)):
        for item in found:
            if len(chosen) >= size:
                break
            if item in chosen:
                continue
            if sum(1 for other in chosen if other["kind"] == item["kind"]) >= limit_kind:
                continue
            if one_each and any(
                other["record"] == item["record"] or (item["conflict_id"] and other["conflict_id"] == item["conflict_id"])
                for other in chosen
            ):
                continue
            chosen.append(item)
    return sorted(chosen, key=lambda item: -item["score"])


# --------------------------------------------------------------------------- the queue


def _due(generated_at: str) -> str:
    made = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (made + timedelta(days=CYCLE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate(cfg: Settings, build: CaseBuilder, cycle: int) -> sqlite3.Row:
    found = candidates(cfg, build)
    at = now_iso()
    build.conn.execute(
        # Two requests can find the queue due at once; the first to store its five wins and both return it.
        "INSERT OR IGNORE INTO review_queues (matter_id, cycle, generated_at, due_at, items, candidates) VALUES (?,?,?,?,?,?)",
        (build.matter_id, cycle, at, _due(at), json.dumps(select(found), ensure_ascii=False), len(found)),
    )
    build.conn.commit()
    return _latest(build.conn, build.matter_id)


def _latest(conn: sqlite3.Connection, matter_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM review_queues WHERE matter_id=? ORDER BY cycle DESC LIMIT 1", (matter_id,)).fetchone()


def current(cfg: Settings, build: CaseBuilder, force: bool = False) -> dict[str, Any]:
    """The matter's queue. A new one is made when there is none, when the last one is five
    days old, or when asked for; otherwise the stored one is returned as it was made."""
    conn, matter_id = build.conn, build.matter_id
    ensure(conn)
    row = _latest(conn, matter_id)
    if row is None or force or now_iso() >= row["due_at"]:
        row = _generate(cfg, build, (row["cycle"] if row else 0) + 1)
    decisions = _decisions(conn, matter_id)
    items = json.loads(row["items"])
    if any(item.get("v") != ITEM_VERSION for item in items):
        # Made by an older version of this module: the same statements, described as this version describes them.
        found = {item["id"]: item for item in candidates(cfg, build, include_decided=True)}
        items = [found[item["id"]] for item in items if item["id"] in found]
        items += [item for item in select([x for x in found.values() if x["id"] not in decisions]) if item not in items][: QUEUE_SIZE - len(items)]
        conn.execute("UPDATE review_queues SET items=? WHERE matter_id=? AND cycle=?", (json.dumps(items, ensure_ascii=False), matter_id, row["cycle"]))
        conn.commit()
    for item in items:
        held = decisions.get(item["id"])
        applies = held is not None and held["statement_hash"] == item["statement_hash"]
        item["decision"] = {"decision": held["decision"], "note": held["note"], "decided_at": held["decided_at"]} if applies else None
    counts = {"retired": 0, "kept": 0, "commented": 0}
    for held in decisions.values():
        counts[{"discard": "retired", "keep": "kept", "comment": "commented"}[held["decision"]]] += 1
    today = datetime.now(timezone.utc).date()
    return {
        "matter_id": matter_id,
        "cycle": row["cycle"],
        "cycle_days": CYCLE_DAYS,
        "generated_at": row["generated_at"],
        "due_at": row["due_at"],
        "days_until_due": max((date.fromisoformat(row["due_at"][:10]) - today).days, 0),
        "total": len(items),
        "decided": sum(1 for item in items if item["decision"]),
        "candidates": row["candidates"],
        "counts": counts,
        "items": items,
    }


def decide(cfg: Settings, build: CaseBuilder, claim_id: str, decision: str, note: str | None) -> dict[str, Any]:
    """Record the lawyer's call on one statement. Our database only."""
    from .digest import pipeline

    conn, matter_id = build.conn, build.matter_id
    ensure(conn)
    note = (note or "").strip()[:MAX_NOTE_CHARS] or None
    if decision == "comment" and not note:
        raise HTTPException(422, "A comment needs some text.")
    row = _latest(conn, matter_id)
    snapshot = next((item for item in json.loads(row["items"]) if item["id"] == claim_id), None) if row else None
    if snapshot is None:
        # Not in the queue: any current statement can still be decided (from a source drawer, say).
        claim = next((c for c in pipeline.collect_claims(cfg, conn, matter_id) if c["id"] == claim_id), None)
        if claim is None:
            raise HTTPException(404, "No such statement in this matter.")
        snapshot = {
            "v": ITEM_VERSION, "id": claim_id, "kind": None, "kind_label": None, "tone": None, "pill": None, "reason": None, "statement_hash": _hash(claim["statement"]),
            "older": {
                "claim_id": claim_id, "text": claim["statement"], "origin": claim["origin"],
                "what": "A document in the file says" if claim["origin"] == "document" else "An entry says",
                "dates": [{"label": "Received" if claim["origin"] == "document" else "Entry dated", "value": claim["record_date"][:10]}] if claim["record_date"] else [],
                "source": {
                    "kind": claim["source_kind"], "clio_id": claim["clio_id"], "label": claim["label"], "date": claim["record_date"],
                    "page": claim["page"], "quote": claim["quote"], "quote_verified": claim["quote_verified"],
                    "href": source_href(matter_id, claim["source_kind"], claim["clio_id"], claim["page"]) + ("&" if claim["page"] else "?") + "claim=" + quote_plus(claim_id),
                },
            },
            "newer": [], "depends": [],
        }
    at, body = now_iso(), json.dumps(snapshot, ensure_ascii=False)
    conn.execute(
        "INSERT INTO claim_decisions (matter_id, claim_id, decision, note, statement_hash, snapshot, decided_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(matter_id, claim_id) DO UPDATE SET decision=excluded.decision, note=excluded.note,"
        " statement_hash=excluded.statement_hash, snapshot=excluded.snapshot, decided_at=excluded.decided_at, restored_at=NULL",
        (matter_id, claim_id, decision, note, snapshot["statement_hash"], body, at),
    )
    conn.execute(
        "INSERT INTO claim_decision_log (matter_id, claim_id, action, note, snapshot, at) VALUES (?,?,?,?,?,?)",
        (matter_id, claim_id, decision, note, body, at),
    )
    conn.commit()
    return {"claim_id": claim_id, "decision": decision, "note": note, "decided_at": at}


def restore(conn: sqlite3.Connection, matter_id: int, claim_id: str) -> dict[str, Any]:
    """Undo the decision on a statement: it counts again everywhere and may be offered again.
    The decision and the restore both stay in the log."""
    ensure(conn)
    row = conn.execute(
        "SELECT * FROM claim_decisions WHERE matter_id=? AND claim_id=? AND restored_at IS NULL", (matter_id, claim_id)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "There is no decision on this statement to restore.")
    at = now_iso()
    conn.execute(
        "INSERT INTO claim_decision_log (matter_id, claim_id, action, note, snapshot, at) VALUES (?,?,?,?,?,?)",
        (matter_id, claim_id, "restore", None, row["snapshot"], at),
    )
    conn.execute("UPDATE claim_decisions SET restored_at=? WHERE matter_id=? AND claim_id=?", (at, matter_id, claim_id))
    conn.commit()
    return {"claim_id": claim_id, "restored_at": at}


def history(conn: sqlite3.Connection, matter_id: int) -> list[dict[str, Any]]:
    """Every decision and restore, newest first. `current` marks the one in force for its statement."""
    decisions = _decisions(conn, matter_id)
    out = []
    for row in conn.execute("SELECT * FROM claim_decision_log WHERE matter_id=? ORDER BY id DESC", (matter_id,)):
        snapshot = json.loads(row["snapshot"])
        held = decisions.get(row["claim_id"])
        out.append(
            {
                "id": row["id"],
                "claim_id": row["claim_id"],
                "action": row["action"],
                "note": row["note"],
                "at": row["at"],
                "current": held is not None and row["action"] != "restore" and held["decided_at"] == row["at"],
                "kind": snapshot.get("kind"),
                "kind_label": snapshot.get("kind_label"),
                "reason": snapshot.get("reason"),
                "older": snapshot.get("older"),
            }
        )
    return out


# --------------------------------------------------------------------------- routes (firm side: under /api/matters/)


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _builder(matter_id: int, conn: Annotated[sqlite3.Connection, Depends(_db)]) -> CaseBuilder:
    try:
        return CaseBuilder(conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error


@router.get("/api/matters/{matter_id}/review-queue")
def get_queue(cfg: Annotated[Settings, Depends(_settings)], build: Annotated[CaseBuilder, Depends(_builder)]) -> dict:
    """The current five. Makes a new queue when the last is five days old."""
    return current(cfg, build)


@router.post("/api/matters/{matter_id}/review-queue/refresh")
def refresh_queue(cfg: Annotated[Settings, Depends(_settings)], build: Annotated[CaseBuilder, Depends(_builder)]) -> dict:
    """Review now: a new queue from what is undecided today."""
    return current(cfg, build, force=True)


@router.put("/api/matters/{matter_id}/review-queue/decision")
def put_decision(
    cfg: Annotated[Settings, Depends(_settings)],
    build: Annotated[CaseBuilder, Depends(_builder)],
    claim_id: Annotated[str, Body(max_length=200)],
    decision: Annotated[str, Body(pattern="^(keep|discard|comment)$")],
    note: Annotated[str | None, Body(max_length=MAX_NOTE_CHARS)] = None,
) -> dict:
    """Keep, discard or comment on one statement. Stored in our database; never sent anywhere."""
    decide(cfg, build, claim_id, decision, note)
    return current(cfg, build)


@router.post("/api/matters/{matter_id}/review-queue/restore")
def post_restore(
    cfg: Annotated[Settings, Depends(_settings)],
    build: Annotated[CaseBuilder, Depends(_builder)],
    claim_id: Annotated[str, Body(max_length=200, embed=True)],
) -> dict:
    restore(build.conn, build.matter_id, claim_id)
    return current(cfg, build)


@router.get("/api/matters/{matter_id}/review-queue/history")
def get_history(matter_id: int, conn: Annotated[sqlite3.Connection, Depends(_db)]) -> list[dict]:
    return history(conn, matter_id)


@router.get("/api/matters/{matter_id}/review-queue/claims")
def get_claim_states(matter_id: int, conn: Annotated[sqlite3.Connection, Depends(_db)]) -> dict:
    """For any screen that shows a statement: which are retired, and the lawyer's notes."""
    return {"retired": sorted(retired(conn, matter_id)), "comments": comments(conn, matter_id)}
