"""The firm's dashboard shell: its layout, the ten important documents and the record lists.

Three things the dashboard's own pages need that the case model does not carry.
All of it is the firm's side: every route is under /api/matters/ and so behind
the firm session guard, and nothing here is part of anything a provider can fetch.

What the lawyer arranged or marked is kept in our database (the settings table,
one JSON value per matter). Nothing is written to Clio.

Mounted by `server/app.py`, before the static files:

    from .dashboard import router as dashboard_router
    app.include_router(dashboard_router)
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator

from shared import contract as c

from . import review_queue, rules
from .case import CaseBuilder, MatterNotSynced
from .config import Settings, get_settings
from .db import connect, get_setting, items, now_iso, set_setting
from .digest import overlay

router = APIRouter()

# How many documents the "important documents" card holds. A product default.
SLOTS = 10

# Limits on the stored dashboard document, so one bad request cannot fill the database.
MAX_CARDS = 60
MAX_SAVED = 100
MAX_DASHBOARDS = 20
MAX_LAYOUT_BYTES = 256_000

# The list a matter starts with before the lawyer has arranged anything. Empty means
# "the page's own default": the response says `stored: false` and the page lays out its cards.
DEFAULT_CARDS: list[dict[str, Any]] = []

PAGE_DEFAULT, PAGE_MAX = 50, 200
SNIPPET_CHARS = 240


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


Config = Annotated[Settings, Depends(_settings)]
Database = Annotated[sqlite3.Connection, Depends(_db)]


def _builder(matter_id: int, conn: Database) -> CaseBuilder:
    try:
        return CaseBuilder(conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error


Builder = Annotated[CaseBuilder, Depends(_builder)]


def _origin(record_id: Any) -> str:
    """Where a record came from. Records added in this app (an uploaded document) are stored
    with an id below zero; everything else was imported."""
    return "uploaded" if str(record_id).startswith("-") else "imported"


def _document_id(ref: c.SourceRef) -> int | None:
    """The document a source reference points at, imported or uploaded, or None."""
    if ref.kind != "document":
        return None
    try:
        return int(str(ref.clio_id))
    except ValueError:
        return None


def _stored(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    value = get_setting(conn, key)
    return json.loads(value) if value else None


# --------------------------------------------------------------------------- layout


class Card(c.Model):
    """One card on the dashboard, in the order the lawyer put it."""

    id: str = Field(min_length=1, max_length=80, description="Which card: a built-in card's name, or the lawyer's own id for a custom one.")
    size: str | None = Field(None, max_length=20, description="The page's own size name for the card.")
    spec: dict[str, Any] | None = Field(None, description="For a custom card: what it shows. Stored as given, never interpreted here.")


class SavedCard(c.Model):
    """A card the lawyer described or configured, kept in their library whether or not it is
    on the dashboard now."""

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    prompt: str | None = Field(None, max_length=2000, description="The lawyer's own words the card was made from, if it was described.")
    spec: dict[str, Any] | None = Field(None, description="For a described card: what it shows. Stored as given.")
    base: str | None = Field(None, max_length=80, description="For a configured template: the card it is an instance of.")
    settings: dict[str, Any] | None = Field(None, description="For a configured template: its settings. Stored as given.")
    created_at: str | None = Field(None, description="Set here when the card is first saved; kept afterwards.")

    @model_validator(mode="after")
    def _one_form(self) -> SavedCard:
        """A saved card is a described card (`spec`) or an instance of a template (`base`), not both and not neither."""
        if (self.spec is None) == (self.base is None):
            raise ValueError("a saved card carries either `spec` or `base` (with optional `settings`)")
        if self.spec is not None and self.settings is not None:
            raise ValueError("`settings` belongs to a template instance (`base`), not to a described card")
        return self


class NamedDashboard(c.Model):
    """A layout the lawyer saved under a name, to switch back to."""

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    cards: list[Card] = Field(default_factory=list, max_length=MAX_CARDS)


class DashboardLayout(c.Model):
    """Response of GET /api/matters/{id}/dashboard: three lists that are stored together
    and changed independently. Taking a card off `cards` never removes it from `saved`."""

    cards: list[Card] = Field(default_factory=list, max_length=MAX_CARDS, description="What is on the dashboard now, in order.")
    saved: list[SavedCard] = Field(default_factory=list, max_length=MAX_SAVED, description="The lawyer's library of their own cards.")
    dashboards: list[NamedDashboard] = Field(default_factory=list, max_length=MAX_DASHBOARDS, description="Layouts saved under a name.")
    stored: bool = Field(False, description="False when the lawyer has arranged nothing yet and `cards` is the default.")
    updated_at: str | None = None


class DashboardChange(c.Model):
    """Body of PUT /api/matters/{id}/dashboard. Each list that is present replaces the stored
    one; a list that is left out is kept as it is."""

    cards: list[Card] | None = Field(None, max_length=MAX_CARDS)
    saved: list[SavedCard] | None = Field(None, max_length=MAX_SAVED)
    dashboards: list[NamedDashboard] | None = Field(None, max_length=MAX_DASHBOARDS)
    stored: bool | None = Field(None, description="Accepted so a GET response can be sent back; ignored.")
    updated_at: str | None = Field(None, description="Accepted so a GET response can be sent back; ignored.")


def _unique(ids: list[str], what: str) -> None:
    if len(ids) != len(set(ids)):
        raise HTTPException(422, f"an id appears twice in {what}")


@router.get("/api/matters/{matter_id}/dashboard", response_model=DashboardLayout)
def get_dashboard(build: Builder) -> DashboardLayout:
    """The lawyer's arrangement of cards for this matter (or the default if there is none),
    their library of saved cards, and their named layouts. 404 for a matter that is not held."""
    return _layout(build.conn, build.matter_id)


def _layout(conn: sqlite3.Connection, matter_id: int) -> DashboardLayout:
    stored = _stored(conn, f"dashboard:{matter_id}") or {}
    arranged = stored.get("cards")
    return DashboardLayout(
        cards=arranged if arranged is not None else [Card(**card) for card in DEFAULT_CARDS],
        saved=stored.get("saved") or [],
        dashboards=stored.get("dashboards") or [],
        stored=arranged is not None,
        updated_at=stored.get("updated_at"),
    )


@router.put("/api/matters/{matter_id}/dashboard", response_model=DashboardLayout)
def put_dashboard(change: DashboardChange, build: Builder) -> DashboardLayout:
    """Store the arrangement, the library of saved cards, the named layouts, or any of the three.
    404 for a matter that is not held, so nothing is stored under an id we do not have."""
    conn, matter_id = build.conn, build.matter_id
    stored = _stored(conn, f"dashboard:{matter_id}") or {}
    if change.cards is not None:
        _unique([card.id for card in change.cards], "the layout")
        stored["cards"] = [card.model_dump() for card in change.cards]
    if change.saved is not None:
        _unique([card.id for card in change.saved], "the saved cards")
        first_saved = {card["id"]: card.get("created_at") for card in stored.get("saved") or []}
        at = now_iso()
        stored["saved"] = [
            {**card.model_dump(exclude_none=True), "created_at": first_saved.get(card.id) or card.created_at or at}
            for card in change.saved
        ]
    if change.dashboards is not None:
        _unique([dashboard.id for dashboard in change.dashboards], "the named layouts")
        for dashboard in change.dashboards:
            _unique([card.id for card in dashboard.cards], f"the layout named {dashboard.id}")
        stored["dashboards"] = [dashboard.model_dump() for dashboard in change.dashboards]
    stored["updated_at"] = now_iso()
    value = json.dumps(stored, ensure_ascii=False)
    if len(value.encode()) > MAX_LAYOUT_BYTES:
        raise HTTPException(422, f"the dashboard document is larger than {MAX_LAYOUT_BYTES} bytes")
    set_setting(conn, f"dashboard:{matter_id}", value)
    return _layout(conn, matter_id)


# --------------------------------------------------------------------------- important documents


class ImportantDocument(c.Model):
    """One of the ten slots. `chosen_by` says whose choice it is and is never blurred:
    a system pick stays "ai" until the lawyer accepts it."""

    document: c.DocumentInfo
    document_origin: str = Field("imported", description="imported | uploaded (added in this app by the firm).")
    chosen_by: str = Field(description="lawyer | ai")
    origin: str | None = Field(None, description="For the lawyer's: marked (their own pick) | accepted (a suggestion they accepted).")
    why: str | None = Field(None, description="For a system pick: one line built in code from the digest. Null for the lawyer's.")
    review_cards: int = Field(0, description="Open review cards with a side that rests on this document.")
    key_facts: int = Field(0, description="Key facts that cite this document.")
    verified_claims: int = Field(0, description="Claims read from this document whose quote was found on its page.")


class ImportantDocuments(c.Model):
    """Response of GET and PUT /api/matters/{id}/important-documents."""

    slots: int = SLOTS
    items: list[ImportantDocument] = Field(
        default_factory=list, description="At most `slots`: the lawyer's, in the lawyer's order, then system picks."
    )
    rejected: list[int] = Field(default_factory=list, description="Document ids the lawyer rejected as suggestions; not offered again.")


class ImportantChange(c.Model):
    """Body of PUT /api/matters/{id}/important-documents: one change at a time."""

    action: str = Field(pattern="^(mark|unmark|reorder|accept|reject|restore)$")
    document_id: int | None = Field(None, description="For every action but reorder.")
    order: list[int] | None = Field(None, description="For reorder: the lawyer's document ids in their new order.")


def _claim_id(ref: c.SourceRef) -> str | None:
    """The claim a source link stands for: the overlay puts it in the link's query."""
    return (parse_qs(urlsplit(ref.href).query).get("claim") or [None])[0]


def _counts(case: c.CaseModel, retired: set[str]) -> dict[int, dict[str, int]]:
    """Per document, what in the digest rests on it. Counted in code from the case model.
    A claim the lawyer retired counts for nothing: not as a verified claim, not as the side
    of a review card, not as the support of a key fact."""
    counts: dict[int, dict[str, int]] = {}

    def count(document_id: int, what: str) -> None:
        counts.setdefault(document_id, {"review_cards": 0, "key_facts": 0, "verified_claims": 0})[what] += 1

    document_of = {}
    for claim in case.claims:
        document_id = _document_id(claim.source)
        if document_id is not None and claim.id not in retired:
            document_of[claim.id] = document_id
            if claim.source.quote_verified:
                count(document_id, "verified_claims")
    for conflict in case.conflicts:
        if conflict.review == "dismissed":
            continue
        sides = conflict.notes_claim_ids + conflict.document_claim_ids
        for document_id in {document_of[claim_id] for claim_id in sides if claim_id in document_of}:
            count(document_id, "review_cards")
    for fact in case.key_facts:
        cited = {_document_id(source) for source in fact.sources if _claim_id(source) not in retired}
        for document_id in cited - {None}:
            count(document_id, "key_facts")
    return counts


def _why(counted: dict[str, int]) -> str:
    """The reason a document is suggested, in words, from its counts."""

    def some(n: int, one: str, many: str) -> str | None:
        return f"{n} {one if n == 1 else many}" if n else None

    parts = [
        part
        for part in (
            some(counted["review_cards"], "review card", "review cards"),
            some(counted["key_facts"], "key fact", "key facts"),
            some(counted["verified_claims"], "verified claim", "verified claims"),
        )
        if part
    ]
    if not parts:
        return "Suggested by date received: nothing in the digest rests on it yet."
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Suggested: {listed} {'rests' if sum(counted.values()) == 1 else 'rest'} on it."


def _important(cfg: Settings, build: CaseBuilder) -> ImportantDocuments:
    case = overlay.apply(cfg, build, build.build(), all_claims=True)
    documents = {document.id: document for document in case.documents}
    counts = _counts(case, review_queue.retired(build.conn, build.matter_id))
    blank = {"review_cards": 0, "key_facts": 0, "verified_claims": 0}
    state = _stored(build.conn, f"important_documents:{build.matter_id}") or {"marked": [], "rejected": []}

    chosen = [
        ImportantDocument(
            document=documents[entry["id"]],
            document_origin=_origin(entry["id"]),
            chosen_by="lawyer",
            origin=entry["origin"],
            **counts.get(entry["id"], blank),
        )
        for entry in state["marked"]
        if entry["id"] in documents  # a document since removed from the matter keeps its place in the stored list
    ][:SLOTS]
    taken = {entry["id"] for entry in state["marked"]} | set(state["rejected"])
    # Most rests on it first; among equals, the most recently received.
    ranked = sorted(
        (document for document in documents.values() if document.id not in taken),
        key=lambda document: document.received_at or "",
        reverse=True,
    )
    ranked.sort(key=lambda document: [-counts.get(document.id, blank)[what] for what in ("review_cards", "key_facts", "verified_claims")])
    for document in ranked[: SLOTS - len(chosen)]:
        counted = counts.get(document.id, blank)
        chosen.append(
            ImportantDocument(
                document=document, document_origin=_origin(document.id), chosen_by="ai", why=_why(counted), **counted
            )
        )
    return ImportantDocuments(items=chosen, rejected=[i for i in state["rejected"] if i in documents])


@router.get("/api/matters/{matter_id}/important-documents", response_model=ImportantDocuments)
def important_documents(cfg: Config, build: Builder) -> ImportantDocuments:
    """Ten slots: the documents the lawyer marked, in the lawyer's order, then the system's
    suggestions for what is left, each with the reason it is suggested."""
    return _important(cfg, build)


@router.put("/api/matters/{matter_id}/important-documents", response_model=ImportantDocuments)
def change_important_documents(change: ImportantChange, cfg: Config, build: Builder) -> ImportantDocuments:
    """Mark, unmark or reorder the lawyer's documents; accept or reject a suggestion (a rejected
    one is not offered again; restore undoes a rejection). Stored in our database."""
    key = f"important_documents:{build.matter_id}"
    state = _stored(build.conn, key) or {"marked": [], "rejected": []}
    marked = [entry["id"] for entry in state["marked"]]
    known = {int(document["id"]) for document in build.documents}

    if change.action == "reorder":
        order = change.order or []
        if sorted(order) != sorted(marked) or len(order) != len(set(order)):
            raise HTTPException(422, "the order must list each of the lawyer's documents exactly once")
        by_id = {entry["id"]: entry for entry in state["marked"]}
        state["marked"] = [by_id[document_id] for document_id in order]
    else:
        document_id = change.document_id
        if document_id is None or document_id not in known:
            raise HTTPException(404, "no such document on this matter")
        if change.action in ("mark", "accept"):
            if document_id in marked:
                raise HTTPException(409, "that document is already one of the lawyer's")
            if len(marked) >= SLOTS:
                raise HTTPException(422, f"all {SLOTS} slots are the lawyer's already; unmark one first")
            if change.action == "accept":
                suggested = {item.document.id for item in _important(cfg, build).items if item.chosen_by == "ai"}
                if document_id not in suggested:
                    raise HTTPException(409, "that document is not a current suggestion")
            state["marked"].append({"id": document_id, "origin": "marked" if change.action == "mark" else "accepted", "at": now_iso()})
            state["rejected"] = [i for i in state["rejected"] if i != document_id]
        elif change.action == "unmark":
            state["marked"] = [entry for entry in state["marked"] if entry["id"] != document_id]
        elif change.action == "reject":
            if document_id in marked:
                raise HTTPException(409, "that document is the lawyer's own; unmark it instead")
            if document_id not in state["rejected"]:
                state["rejected"].append(document_id)
        else:  # restore
            state["rejected"] = [i for i in state["rejected"] if i != document_id]
    set_setting(build.conn, key, json.dumps(state))
    return _important(cfg, build)


# --------------------------------------------------------------------------- record lists


class RecordItem(c.Model):
    id: str = Field(description="<kind>:<id>, unique across tabs.")
    kind: str = Field(description="The kind of record, as the source reference names it.")
    title: str
    origin: str = Field("imported", description="imported | uploaded (added in this app by the firm).")
    date: str | None = Field(None, description="The record's own date as it was imported.")
    date_is: str | None = Field(None, description="What `date` is for this kind of record, in words.")
    who: str | None = Field(None, description="The person on the record: its author, sender, assignee or the user who entered it.")
    snippet: str | None = Field(None, description=f"The start of the record's text, at most {SNIPPET_CHARS} characters.")
    meta: dict[str, str] = Field(default_factory=dict, description="A few named values worth a column: status, type, amounts.")
    source: c.SourceRef | None = Field(None, description="Opens the record in the source drawer. Null where the drawer has no view of this kind.")


class RecordList(c.Model):
    """Response of GET /api/matters/{id}/records/{tab}."""

    tab: str
    available: bool = Field(True, description="False when this kind of record is not imported; `items` is then empty.")
    note: str | None = Field(None, description="What the tab holds, or why it is not available.")
    total: int = 0
    offset: int = 0
    limit: int = PAGE_DEFAULT
    items: list[RecordItem] = Field(default_factory=list)


TAGS = re.compile(r"<[^>]+>")

# tab -> (what it holds, the kinds of imported record behind it). "fields" reads the matter itself.
TABS: dict[str, tuple[str, tuple[str, ...]]] = {
    "notes": ("Notes on the matter.", ("note",)),
    "communications": ("Logged emails and calls.", ("communication",)),
    "tasks": ("Tasks, by due date.", ("task",)),
    "calendar": ("Calendar entries, by start.", ("calendar_entry",)),
    "documents": ("Documents in the matter's folders.", ("document",)),
    "activities": ("Time and expense entries.", ("expense", "time_entry")),
    "fields": ("The matter's custom fields.", ()),
    "bills": ("Bills issued on the matter.", ("bill",)),
    "transactions": ("Trust and operating account transactions.", ("bank_transaction",)),
}
# There is no co-counsel record as such: the tab lists the matter's relationships whose own
# description says co-counsel. The page calls the tab by either spelling.
CO_COUNSEL = re.compile(r"\bco[\s-]?counsel\b", re.IGNORECASE)
CO_COUNSEL_TABS = {"co-counsel", "cocounsel"}

# Kinds the source drawer can open; a row of any other kind carries no source reference.
OPENABLE = {kind.value for kind in c.SourceKind}


def _snippet(text: Any) -> str | None:
    plain = " ".join(TAGS.sub(" ", str(text or "")).split())
    if not plain:
        return None
    return plain if len(plain) <= SNIPPET_CHARS else plain[: SNIPPET_CHARS - 1].rstrip() + "…"


def _names(parties: Any) -> str | None:
    return ", ".join(str(party.get("name")) for party in parties or [] if party.get("name")) or None


def _name(person: Any) -> str | None:
    return (person or {}).get("name") or None


def _usd(value: Any) -> str | None:
    """An amount as dollars, or nothing when the record's value is not a number."""
    return rules.usd(rules.decimal_or_none(value)) or None


def _meta(**values: Any) -> dict[str, str]:
    return {name: str(value) for name, value in values.items() if value not in (None, "", [])}


def _imported(conn: sqlite3.Connection, matter_id: int) -> set[str]:
    """The kinds the last completed import asked for and was given, even if none came back."""
    row = conn.execute(
        "SELECT report FROM sync_runs WHERE matter_id=? AND error IS NULL AND report IS NOT NULL ORDER BY id DESC LIMIT 1",
        (matter_id,),
    ).fetchone()
    return set(json.loads(row["report"]).get("kinds") or {}) if row else set()


def _row(build: CaseBuilder, kind: str, record: dict[str, Any]) -> RecordItem:
    """One imported record as a list row. Titles, dates and names are the record's own."""
    get = record.get
    if kind == "note":
        title, when, date_is, who, text, meta = get("subject"), get("date"), "date of the note", _name(get("author")), get("detail"), _meta(type=get("type"))
    elif kind == "communication":
        title, when, date_is, text = get("subject"), get("date") or get("received_at"), "date sent or logged", get("body")
        # A sender of type "User" is one of the firm's own people: the firm sent it. Otherwise it came in.
        sent = any((party or {}).get("type") == "User" for party in get("senders") or [])
        direction = ("sent" if sent else "received") if get("senders") else None
        who, meta = _names(get("senders")), _meta(type=get("type"), to=_names(get("receivers")), direction=direction)
    elif kind == "task":
        title, when, date_is, who, text = get("name"), get("due_at"), "due date", _name(get("assignee")), get("description")
        meta = _meta(status=get("status"), priority=get("priority"), completed=get("completed_at"))
    elif kind == "calendar_entry":
        title, when, date_is, text = get("summary"), get("start_at"), "start", get("description")
        who = _names(get("attendees")) or _name(get("calendar_owner"))
        meta = _meta(end=get("end_at"), location=get("location"), all_day="yes" if get("all_day") else None)
    elif kind == "document":
        title, when, date_is, who, text = get("name") or get("filename"), get("received_at") or get("created_at"), "date received", None, None
        meta = _meta(folder=_name(get("parent")), type=get("content_type"), size_bytes=get("size"))
    elif kind in ("expense", "time_entry"):
        category = _name(get("expense_category")) or _name(get("activity_description"))
        title, when, date_is, who, text = get("note") or category, get("date"), "date of the entry", _name(get("user")), get("note")
        meta = _meta(entry="expense" if kind == "expense" else "time", total=_usd(get("total")), category=category, hours=get("quantity_in_hours"))
    elif kind == "bill":
        title, when, date_is, who, text = get("subject") or get("number"), get("issued_at"), "date issued", None, None
        meta = _meta(
            number=get("number"), state=get("state"), due=get("due_at"), paid_on=get("paid_at"),
            total=_usd(get("total")), paid=_usd(get("paid")), balance=_usd(get("balance")),
        )
    else:  # bank_transaction
        account = get("bank_account") or {}
        title, when, date_is, who, text = get("description") or get("transaction_type"), get("date"), "transaction date", None, get("description")
        meta = _meta(
            type=get("transaction_type") or get("type"), amount=_usd(get("amount")), funds_in=_usd(get("funds_in")),
            funds_out=_usd(get("funds_out")), account=account.get("name"), account_type=account.get("type"),
        )
    title = str(title or kind.replace("_", " "))
    return RecordItem(
        id=f"{kind}:{record['id']}",
        kind=kind,
        title=title,
        origin=_origin(record["id"]),
        date=when,
        date_is=date_is,
        who=who,
        snippet=_snippet(text),
        meta=meta,
        source=build.ref(kind, record["id"], title, when) if kind in OPENABLE else None,
    )


@router.get("/api/matters/{matter_id}/records/{tab}", response_model=RecordList)
def records(
    tab: str,
    build: Builder,
    offset: int = Query(0, ge=0),
    limit: int = Query(PAGE_DEFAULT, ge=1, le=PAGE_MAX),
) -> RecordList:
    """One navigation tab's records from what was imported, a page at a time, newest first.
    Each row carries the source reference the drawer opens."""
    if tab not in TABS and tab not in CO_COUNSEL_TABS:
        raise HTTPException(404, f"no such tab; known: {', '.join([*TABS, 'co-counsel'])}")
    note, kinds = TABS.get(tab, ("Contacts related to the matter as co-counsel.", ()))
    if tab in CO_COUNSEL_TABS:
        rows = [
            RecordItem(
                id=f"relationship:{relationship['id']}",
                kind="relationship",
                title=str(_name(relationship.get("contact")) or "relationship"),
                snippet=_snippet(relationship.get("description")),
                source=build.ref("relationship", relationship["id"], relationship.get("description"), relationship.get("updated_at")),
            )
            for relationship in build.relationships
            if CO_COUNSEL.search(relationship.get("description") or "")
        ]
        if not rows:
            note = f"{note} None on this matter."
    elif tab == "fields":  # the matter's custom fields, in their display order
        facts, _ = build.custom_fields()
        rows = [
            RecordItem(id=fact.id, kind="custom_field", title=fact.label, date=fact.date, snippet=_snippet(fact.display), source=fact.sources[0])
            for fact in facts
            if fact.sources
        ]
    else:
        imported = _imported(build.conn, build.matter_id)
        held = [kind for kind in kinds if kind in imported or items(build.conn, build.matter_id, kind)]
        if not held:
            return RecordList(tab=tab, available=False, note=f"{note} Not imported for this matter.", offset=offset, limit=limit)
        rows = [_row(build, kind, record) for kind in held for record in items(build.conn, build.matter_id, kind)]
        rows.sort(key=lambda row: row.date or "", reverse=True)
        if not rows:
            note = f"{note} None on this matter."
    return RecordList(tab=tab, note=note, total=len(rows), offset=offset, limit=limit, items=rows[offset : offset + limit])
