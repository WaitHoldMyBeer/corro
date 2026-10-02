"""The provider lens: what leaves the firm, and the log of what did.

A ProviderView is built up from an allowlist. It starts empty and each allowed
category adds its own piece, so firm-only material (valuation, strategy, other
parties) has no code path into it. Each piece is a new object carrying named
fields only: values Clio holds or code computed, with wording chosen here.
Source links, the firm's free text and anything the model wrote do not cross.

Sending freezes the view. The link serves that snapshot, so what a provider sees
is exactly what the attorney previewed; only the provider's own replies are live.
The send carries the fingerprint of the preview and is refused if it no longer matches.

Free text the firm writes for a provider (cover note, request wording, thread
messages) is read by the checker as that provider would receive it before it
goes. A sentence it locks, or cannot assess, needs a reason, and the reason is
logged. What a provider writes back is checked against the file for the firm only.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from shared import check_contract as k
from shared import contract as c

from . import threads
from .case import BAND_TEXT, COVERAGE_NOTE, CaseBuilder, MatterNotSynced, share_entry
from .check import check_text
from .db import connect, content_hash, now_iso

SHAREABLE = {category.value for category in c.SHAREABLE_CATEGORIES}

# How long a sent link stays valid. A product default, not a legal requirement.
LINK_LIFETIME_DAYS = 30

NOT_SHARED = "Not shared by the firm"

# Longest wording the attorney may approve for one request. A product default.
MAX_ASK_CHARS = 500

# Marks the token of a frozen view that a later send replaced. A link token never contains it.
SUPERSEDED = ":superseded:"

# What a provider may be shown was read from a Clio field or computed in code over
# Clio data. Anything the model wrote stays in the firm.
FROM_CLIO = {c.Derivation.clio.value, c.Derivation.computed.value}

# The wording of an update is chosen here, by what the event is. An event's own
# label is the firm's calendar title or the model's sentence; neither leaves.
STATUS_LABELS = {"matter:opened": "Matter opened"}
STATUS_LABEL = "Case status updated"
APPOINTMENT_LABEL = "Appointment on the firm's calendar"


# Fields of a ProviderView that are not part of what the attorney approves: timestamps,
# the preview-only notes, the fingerprint itself, and what the provider adds afterwards.
NOT_CONTENT = {"generated_at", "expires_at", "withheld_counts", "warnings", "content_hash", "requests", "thread"}

# Reads one text as a given provider would receive it.
Checker = Callable[[str], k.CheckResult]

# Why a sentence the firm wrote is held: it discloses a never-shared category, the
# model tier has not read it, or the checker could not be run at all.
DISCLOSES, UNCHECKED, UNAVAILABLE = "dont_send", "unchecked", "unavailable"

NEVER_SHOWN = "The checker's model places this sentence in a category this provider is never shown."
NOT_READ = "The checker did not read this part of the text."

# The line a provider reads when a send answers a request they made.
ANSWERED_BY_VIEW = "The firm has updated what it shares with you; your page now shows it."

# A provider's text whose check is incomplete (the model has not read it all) is tried again no sooner than this.
RECHECK_SECONDS = 60

# How many of a provider's texts are checked in one pass; the rest wait for the next read.
MAX_INCOMING_CHECKS = 20

# The attorney's call on a checked text from a provider; the same three states as a conflict review.
REVIEWS = ("unreviewed", "confirmed", "dismissed")


class NotShareable(ValueError):
    pass


class PreviewChanged(ValueError):
    """What would be sent is no longer what the attorney was shown."""


class LockedText(ValueError):
    """Text the firm wrote for a provider is held, and no reason to send it anyway came with it."""

    def __init__(self, locked: list[dict]) -> None:
        self.locked = locked
        discloses = sum(1 for entry in locked if entry["state"] == DISCLOSES)
        unchecked = len(locked) - discloses
        parts = []
        if discloses:
            parts.append(
                f"{discloses} sentence{'' if discloses == 1 else 's'} disclose{'s' if discloses == 1 else ''}"
                " something this provider is never shown."
            )
        if unchecked:
            parts.append(
                f"{unchecked} sentence{'' if unchecked == 1 else 's'} {'has' if unchecked == 1 else 'have'} not been"
                " read by the checker's model; sending again in a moment may clear it."
            )
        super().__init__(" ".join(parts) + " Edit the text, or give a reason to send it anyway.")

    def detail(self) -> dict:
        """The body of the refusal: the sentences held and why, for the attorney to act on."""
        return {"code": "locked_text", "message": str(self), "locked": self.locked}


def own_item_ids(case: c.CaseModel, contact_id: int) -> tuple[set[str], set[str]]:
    """The ids a policy for this provider may name: its own requests, and with them the
    updates that could appear in its view. Anything else is another party's or the firm's."""
    asks = {ask.id for panel in case.providers if panel.contact.id == contact_id for ask in panel.asks}
    updates = {event.id for event in case.timeline if _outbound_event(event, contact_id) is not None}
    return asks, asks | updates


def save_policy(conn: sqlite3.Connection, matter_id: int, policy: c.SharePolicy) -> c.SharePolicy:
    """Store what one provider may see. This is the gate every change to a draft passes,
    whoever made it: a never-shared category is refused outright, and an item id that is
    not this provider's own is not stored."""
    refused = [category for category in policy.allowed_categories if category not in SHAREABLE]
    if refused:
        raise NotShareable(f"categories that can never be shared with a provider: {', '.join(refused)}")
    # Wording is stored as the attorney approved it, whitespace collapsed. Blank wording is no approval.
    policy.approved_asks = {
        ask_id: " ".join(text.split())[:MAX_ASK_CHARS] for ask_id, text in policy.approved_asks.items() if text.strip()
    }
    try:
        case = CaseBuilder(conn, matter_id).build()
    except MatterNotSynced:
        case = None  # nothing to check the ids against; the projection ignores an id that is not the provider's
    if case is not None:
        asks, items = own_item_ids(case, policy.contact_id)
        policy.approved_asks = {ask_id: text for ask_id, text in policy.approved_asks.items() if ask_id in asks}
        policy.hidden_item_ids = [item_id for item_id in policy.hidden_item_ids if item_id in items]
    policy.updated_at = now_iso()
    conn.execute(
        "INSERT INTO share_policies (matter_id, contact_id, policy, updated_at) VALUES (?,?,?,?)"
        " ON CONFLICT(matter_id, contact_id) DO UPDATE SET policy=excluded.policy, updated_at=excluded.updated_at",
        (matter_id, policy.contact_id, policy.model_dump_json(), policy.updated_at),
    )
    conn.commit()
    return policy


def _outbound(fact: c.Fact | None) -> c.Fact | None:
    """A status fact as a provider may see it: its name, value and date, copied field
    by field. Detail, amount, the firm's assessment, sources and conflict links have no way across."""
    if fact is None or fact.category != "status" or fact.derivation not in FROM_CLIO:
        return None
    return c.Fact(
        id=fact.id, label=fact.label, display=fact.display, date=fact.date, derivation=fact.derivation, category=fact.category
    )


def _outbound_contact(contact: c.Contact) -> c.Contact:
    """The provider as named to itself: name, type and role. The firm's own wording
    of the relationship, its contact details on file and its source link stay inside."""
    return c.Contact(
        id=contact.id,
        name=contact.name,
        type=contact.type,
        role=contact.role,
        source=c.SourceRef(kind="contact", clio_id=contact.id, label=contact.name, href=""),
    )


def _outbound_event(event: c.TimelineEvent, contact_id: int) -> c.TimelineEvent | None:
    """An update as a provider may see it, or None if it may not leave.

    Two shapes can leave: a status event about the matter as a whole or about this
    provider, and an appointment with this provider. Both must come from Clio's own
    data. The wording is chosen here; the event's own label is never copied."""
    if event.derivation not in FROM_CLIO:
        return None
    if event.category == "status" and event.contact_id in (None, contact_id):
        label = STATUS_LABELS.get(event.id, STATUS_LABEL)
    elif event.category == "attendance" and event.contact_id == contact_id:
        label = APPOINTMENT_LABEL
    else:
        return None
    return c.TimelineEvent(
        id=event.id,
        date=event.date,
        label=label,
        kind=event.kind,
        contact_id=event.contact_id,
        category=event.category,
        derivation=event.derivation,
    )


def _outbound_ask(ask: c.Ask, wording: str) -> c.Ask:
    """A request as the provider sees it, in the wording the attorney approved. The
    firm's own text for it (the Clio task title) is never copied. Replies are laid
    over the frozen view from this provider's own link."""
    return c.Ask(
        id=ask.id,
        contact_id=ask.contact_id,
        text=wording,
        first_asked=ask.first_asked,
        last_asked=ask.last_asked,
        times_asked=ask.times_asked,
        due=ask.due,
        overdue=ask.overdue,
    )


def _project(
    contact_id: int,
    allowed: list[str],
    hidden: set[str],
    *,
    provider: c.Contact,
    client_name: str,
    from_name: str | None,
    message: str | None,
    stage: c.Fact | None,
    alive: c.Fact | None,
    band: str | None,
    records: c.RecordsStatus | None,
    bills: c.BillsStatus | None,
    attendance: c.Attendance | None,
    events: list[c.TimelineEvent],
    asks: list[c.Ask],
    approved: dict[str, str],
) -> c.ProviderView:
    """The one place a ProviderView is made. A new view from the case and a stored
    view on its way back out both pass through here, so both obey the same rules."""
    view = c.ProviderView(
        generated_at=now_iso(),
        provider=_outbound_contact(provider),
        client_name=client_name,
        from_name=from_name,
        message=message,
        shared_categories=allowed,
        coverage=c.CoverageSignal(shared=False, display=NOT_SHARED),
        request_kinds={kind: question for kind, (question, _) in threads.REQUEST_KINDS.items()},
    )
    if "status" in allowed:
        view.stage = _outbound(stage)
        view.alive = _outbound(alive)
    if "coverage" in allowed and band in BAND_TEXT:
        # A band, never a number: no limits, no carrier, none of the firm's sources. The words are fixed per band.
        view.coverage = c.CoverageSignal(band=band, display=BAND_TEXT[band], note=COVERAGE_NOTE)
    if "records" in allowed and records is not None:
        view.records = c.RecordsStatus(
            state=records.state, pages=records.pages, first_requested=records.first_requested, last_received=records.last_received
        )
    if "bills" in allowed and bills is not None:
        view.bills = c.BillsStatus(
            state=bills.state, billed_total=bills.billed_total, line_count=bills.line_count, last_service_date=bills.last_service_date
        )
    if "attendance" in allowed and attendance is not None:
        view.attendance = c.Attendance(
            signal=attendance.signal,
            visits=attendance.visits,
            last_visit=attendance.last_visit,
            next_visit=attendance.next_visit,
            days_since_last_visit=attendance.days_since_last_visit,
        )
    for event in sorted(events, key=lambda event: event.date):
        if event.category in allowed and event.id not in hidden:
            outbound = _outbound_event(event, contact_id)
            if outbound is not None:
                view.updates.append(outbound)
    if "asks" in allowed:
        # Off until approved: a request leaves only with wording the attorney approved for this provider.
        view.asks = [
            _outbound_ask(ask, approved[ask.id])
            for ask in asks
            if ask.contact_id == contact_id and ask.id not in hidden and approved.get(ask.id, "").strip()
        ]
    view.content_hash = fingerprint(view)
    return view


def fingerprint(view: c.ProviderView) -> str:
    """SHA-256 over every field of the view the attorney approves. A preview and the
    snapshot frozen from it carry the same value when nothing changed in between."""
    return content_hash(view.model_dump(mode="json", exclude=NOT_CONTENT))


# -- text the firm writes for a provider ----------------------------------------


def provider_checker(cfg, conn: sqlite3.Connection, matter_id: int, contact_id: int) -> Checker:
    """The live checker, reading a text as this provider would receive it. It waits for the
    model tier up to the checker's own deadline; a sentence not read by then comes back pending."""
    return lambda text: check_text(
        cfg, conn, matter_id, text, audience="provider", audience_contact_id=contact_id, wait=False
    )


def locked_text(written: list[tuple[str, str]], checker: Checker) -> list[dict]:
    """The sentences in `written` (pairs of what the text is and the text) that are not
    cleared for a provider. Fails closed. A sentence is cleared only when the model tier
    read it, did not lock it, and did not place it in a category outside the ones a
    provider may be shown; what a sentence gives away is judged there, and the code tier
    alone can call a figure "not in the file" without having looked. A sentence still
    pending, or not read, is held as unchecked, and so is any part of the text the checker
    returned no sentence for. If the checker cannot be run, the whole text is held."""
    locked = []
    for item, text in written:
        try:
            result = checker(text)
        except Exception as error:  # any failure of the checker means nothing was cleared
            locked.append({"item": item, "text": "", "category": None, "state": UNAVAILABLE, "note": type(error).__name__})
            continue
        for span in result.spans:
            if span.verdict == k.Verdict.dont_send.value:
                locked.append({"item": item, "text": span.text, "category": span.category, "state": DISCLOSES, "note": span.message})
            elif span.pending or not _model_read(result, span):
                note = "The checker's model had not answered yet." if span.pending else span.message
                locked.append({"item": item, "text": span.text, "category": None, "state": UNCHECKED, "note": note})
            elif span.category is not None and span.category not in SHAREABLE:
                # The provider view is an allowlist and so is this: a sentence the model places in a
                # category no provider is shown is held, whether or not the checker went on to lock it.
                locked.append({"item": item, "text": span.text, "category": span.category, "state": DISCLOSES, "note": NEVER_SHOWN})
        for part in _unread(text, result):
            locked.append({"item": item, "text": part, "category": None, "state": UNCHECKED, "note": NOT_READ})
    return locked


def _unread(text: str, result: k.CheckResult) -> list[str]:
    """Parts of the text the checker returned no sentence for and that carry a letter or a
    digit: a line that is only a figure, for one. Nobody read them, so they are not cleared."""
    rest = text
    for span in result.spans:
        rest = rest.replace(span.text, " ", 1)
    return [part.strip() for part in rest.splitlines() if any(character.isalnum() for character in part)]


def _model_read(result: k.CheckResult, span: k.CheckSpan) -> bool:
    """Whether the model tier read this sentence. The checker says so on the span
    (`checked`). Where a checker build does not carry that field yet, it is taken from
    what the result implies: the model tier was answering, and it either decided this
    sentence or left standing a verdict code had already reached."""
    checked = getattr(span, "checked", None)
    if checked is not None:
        return checked
    return result.tier2_available and (span.tier == 2 or span.verdict is not None)


def reason_for(locked: list[dict], override_reason: str | None) -> str:
    """The attorney's reason for sending held text. Without one, nothing is sent."""
    reason = " ".join((override_reason or "").split())
    if locked and not reason:
        raise LockedText(locked)
    return reason


def log_overrides(
    conn: sqlite3.Connection, matter_id: int, contact_id: int, share_id: int | None, locked: list[dict], reason: str
) -> None:
    """Record that held text went to a provider anyway: the held sentence and no more of
    the text, why it was held, the reason given and the time. The caller commits."""
    at = now_iso()
    conn.executemany(
        "INSERT INTO send_overrides (matter_id, contact_id, share_id, item, text, category, state, reason, at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (matter_id, contact_id, share_id, entry["item"], entry["text"], entry["category"], entry["state"], reason, at)
            for entry in locked
        ],
    )


def release(
    conn: sqlite3.Connection,
    checker: Checker,
    matter_id: int,
    contact_id: int,
    text: str,
    override_reason: str | None,
    store: Callable[[], object],
) -> None:
    """Put a message the firm wrote into a provider's thread, if it may go. `store` is what
    writes it there. If a sentence is held and no reason was given, LockedText is raised
    and nothing is stored; with a reason, the message is stored and the override is logged
    against it."""
    locked = locked_text([("message", text)], checker)
    reason = reason_for(locked, override_reason)
    store()
    if locked:
        sent = conn.execute(
            "SELECT MAX(id) FROM provider_messages WHERE matter_id=? AND contact_id=? AND direction='from_firm'",
            (matter_id, contact_id),
        ).fetchone()[0]
        for entry in locked:
            entry["item"] = f"message:{sent}"
        log_overrides(conn, matter_id, contact_id, None, locked, reason)
        conn.commit()


def overrides(conn: sqlite3.Connection, matter_id: int, contact_id: int) -> list[dict]:
    """Every sentence that went to this provider over a hold, with the reason given: the
    answer to "can I defend having sent this". `share_id` names the share it went out
    with, or `item` is `message:<id>` for a message in the thread. Firm side only."""
    rows = conn.execute(
        "SELECT share_id, item, text, category, state, reason, at FROM send_overrides WHERE matter_id=? AND contact_id=?"
        " ORDER BY id",
        (matter_id, contact_id),
    ).fetchall()
    return [{**dict(row), "share_id": str(row["share_id"]) if row["share_id"] is not None else None} for row in rows]


# -- what a provider writes back, checked against the file ------------------------


def _incoming(conn: sqlite3.Connection, matter_id: int, contact_id: int) -> dict[str, tuple[str, str, str]]:
    """Everything this provider wrote through its page: the key the firm's page uses,
    then how it is stored (kind, id) and the text."""
    scope = (matter_id, contact_id)
    written = {
        f"message:{row['id']}": ("provider_message", str(row["id"]), row["text"])
        for row in conn.execute(
            "SELECT id, text FROM provider_messages WHERE matter_id=? AND contact_id=? AND direction='from_provider'", scope
        )
    }
    written |= {
        f"request:{row['id']}": ("provider_request", str(row["id"]), row["text"])
        for row in conn.execute(
            "SELECT id, text FROM provider_requests WHERE matter_id=? AND contact_id=? AND text IS NOT NULL", scope
        )
    }
    # The latest reply to each ask is the one the firm is shown.
    written |= {
        f"reply:{row['ask_id']}": ("provider_reply", f"{contact_id}:{row['ask_id']}", row["reply"])
        for row in conn.execute("SELECT ask_id, reply FROM ask_replies WHERE matter_id=? AND contact_id=? ORDER BY id", scope)
    }
    return written


def _complete(result: k.CheckResult) -> bool:
    """A check in which the model tier read every sentence."""
    return all(_model_read(result, span) and not span.pending for span in result.spans)


def incoming_checks(
    cfg, conn: sqlite3.Connection, matter_id: int, contact_id: int, wait: bool = False
) -> dict[str, k.CheckResult]:
    """The checker's reading of what this provider wrote ("their statement / your file"),
    keyed `message:<id>`, `request:<id>` or `reply:<ask id>`. Stored beside the checks of
    Clio communications and reused; run again only if the text changed or the check
    is incomplete (the model tier has not read every sentence). With `wait` the model takes as
    long as it needs; without it the checker's own deadline applies. For the firm only: no model a provider can fetch has a field for this."""
    stored = {
        (row["kind"], row["clio_id"]): row
        for row in conn.execute("SELECT kind, clio_id, content_hash, result, at FROM incoming_checks WHERE matter_id=?", (matter_id,))
    }
    now = datetime.now(UTC)
    results: dict[str, k.CheckResult] = {}
    checked_now = 0
    for item, (kind, item_id, text) in _incoming(conn, matter_id, contact_id).items():
        row, text_hash = stored.get((kind, item_id)), content_hash(["incoming", text])
        kept = k.CheckResult.model_validate_json(row["result"]) if row is not None and row["content_hash"] == text_hash else None
        if kept is not None:
            age = (now - datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds()
            if _complete(kept) or age < RECHECK_SECONDS:
                results[item] = kept
                continue
        if checked_now >= MAX_INCOMING_CHECKS:  # a link that posts without end cannot run up checks without end
            if kept is not None:
                results[item] = kept
            continue
        checked_now += 1
        try:
            result = check_text(cfg, conn, matter_id, text, mode="incoming", author_contact_id=contact_id, wait=wait)
        except Exception:  # a reply is never lost or hidden because the checker failed; it shows as not checked
            if kept is not None:
                results[item] = kept
            continue
        conn.execute(
            "INSERT OR REPLACE INTO incoming_checks (matter_id, kind, clio_id, content_hash, ledger_version, result, at)"
            " VALUES (?,?,?,?,?,?,?)",
            (matter_id, kind, item_id, text_hash, result.ledger_version, result.model_dump_json(), now_iso()),
        )
        conn.commit()
        results[item] = result
    return results


def _settled(conn: sqlite3.Connection, matter_id: int, contact_id: int) -> tuple[dict[str, k.CheckResult], dict[str, str]]:
    """What is already stored for this provider's texts, without running the checker:
    each stored result that was made for the text as it stands, and each review of it."""
    written = _incoming(conn, matter_id, contact_id)
    checked = {
        (row["kind"], row["clio_id"]): row
        for row in conn.execute("SELECT kind, clio_id, content_hash, result FROM incoming_checks WHERE matter_id=?", (matter_id,))
    }
    reviewed = {
        row["item"]: row
        for row in conn.execute(
            "SELECT item, text_hash, review FROM incoming_reviews WHERE matter_id=? AND contact_id=?", (matter_id, contact_id)
        )
    }
    results, reviews = {}, {}
    for item, (kind, item_id, text) in written.items():
        text_hash = content_hash(["incoming", text])
        row = checked.get((kind, item_id))
        if row is not None and row["content_hash"] == text_hash:
            results[item] = k.CheckResult.model_validate_json(row["result"])
        # A review is of one text: if the provider wrote again, the new text is unreviewed.
        review = reviewed.get(item)
        if review is not None and review["text_hash"] == text_hash:
            reviews[item] = review["review"]
    return results, reviews


def _contradicted(result: k.CheckResult) -> bool:
    return any(span.verdict == k.Verdict.contradicted.value for span in result.spans)


def provider_incoming(cfg, conn: sqlite3.Connection, matter_id: int, contact_id: int) -> c.ProviderIncoming:
    """Everything the firm's page needs about what one provider wrote: the checks, the
    attorney's reviews of them, and how many contradictions are still unreviewed."""
    checks = incoming_checks(cfg, conn, matter_id, contact_id)
    _, reviews = _settled(conn, matter_id, contact_id)
    return c.ProviderIncoming(
        checks={item: result.model_dump(mode="json") for item, result in checks.items()},
        reviews=reviews,
        unreviewed_contradictions=sum(
            1 for item, result in checks.items() if _contradicted(result) and reviews.get(item, "unreviewed") == "unreviewed"
        ),
    )


def unreviewed_contradictions(conn: sqlite3.Connection, matter_id: int) -> dict[int, int]:
    """Per provider, how many of its texts the file contradicts and nobody has reviewed.
    Reads stored results only, so a list of providers can show a badge without a check being run."""
    contacts = {
        row[0]
        for table in ("provider_messages", "provider_requests", "ask_replies")
        for row in conn.execute(f"SELECT DISTINCT contact_id FROM {table} WHERE matter_id=?", (matter_id,))
    }
    counts = {}
    for contact_id in sorted(contacts):
        results, reviews = _settled(conn, matter_id, contact_id)
        n = sum(1 for item, result in results.items() if _contradicted(result) and reviews.get(item, "unreviewed") == "unreviewed")
        if n:
            counts[contact_id] = n
    return counts


def review_incoming(conn: sqlite3.Connection, matter_id: int, contact_id: int, item: str, review: str) -> None:
    """The attorney's call on one checked text from a provider: unreviewed, confirmed or dismissed."""
    if review not in REVIEWS:
        raise ValueError(f"review must be one of: {', '.join(REVIEWS)}")
    written = _incoming(conn, matter_id, contact_id)
    if item not in written:
        raise LookupError("nothing this provider wrote has that key")
    conn.execute(
        "INSERT INTO incoming_reviews (matter_id, contact_id, item, text_hash, review, reviewed_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(matter_id, contact_id, item) DO UPDATE SET text_hash=excluded.text_hash, review=excluded.review,"
        " reviewed_at=excluded.reviewed_at",
        (matter_id, contact_id, item, content_hash(["incoming", written[item][2]]), review, now_iso()),
    )
    conn.commit()


def check_incoming(cfg, matter_id: int, contact_id: int) -> None:
    """Run after a provider posts something, off the request: checks what is not yet
    checked and stores it. Opens its own connection, and never raises."""
    conn = connect(cfg.db_path)
    try:
        incoming_checks(cfg, conn, matter_id, contact_id, wait=True)
    except Exception:  # the firm's next read tries again
        pass
    finally:
        conn.close()


def provider_view(
    case: c.CaseModel, contact_id: int, policy: c.SharePolicy, preview: bool = False
) -> c.ProviderView:
    panel = next((p for p in case.providers if p.contact.id == contact_id), None)
    if panel is None:
        raise LookupError(f"contact {contact_id} is not a treating provider on this matter")
    allowed = [category for category in policy.allowed_categories if category in SHAREABLE]
    hidden = set(policy.hidden_item_ids)
    signal = case.brief.coverage_signal
    view = _project(
        contact_id,
        allowed,
        hidden,
        provider=panel.contact,
        client_name=case.matter.client.name if case.matter.client else "",
        from_name=case.matter.responsible_attorney,
        message=policy.message,
        stage=case.brief.stage,
        alive=case.brief.alive,
        band=signal.band if signal is not None else None,
        records=panel.records,
        bills=panel.bills,
        attendance=panel.attendance,
        events=case.timeline,
        asks=panel.asks,
        approved=policy.approved_asks,
    )
    if preview:
        if view.coverage.band == c.CoverageBand.not_established.value:
            view.warnings.append(
                "This provider will be told no coverage has been established."
                " That can change how a provider treats the patient; share it deliberately."
            )
        if view.attendance is not None:
            view.warnings.append("Attendance is the client's information; it is shared only because you switched it on.")
        held_back = sum(
            1
            for event in case.timeline
            if event.category in allowed
            and event.id not in hidden
            and event.contact_id in (None, contact_id)
            and _outbound_event(event, contact_id) is None
        )
        if held_back:
            view.warnings.append(
                f"{held_back} timeline {'entry' if held_back == 1 else 'entries'} written by the digest"
                f" {'is' if held_back == 1 else 'are'} held back: model-written wording does not go to a provider."
            )
        waiting = sum(1 for ask in panel.asks if not policy.approved_asks.get(ask.id, "").strip())
        if "asks" in allowed and waiting:
            view.warnings.append(
                f"{waiting} request{'' if waiting == 1 else 's'} for this office"
                f" {'is' if waiting == 1 else 'are'} not approved and will not be sent."
            )
        view.withheld_counts = withheld_counts(case, contact_id, allowed, hidden)
    return view


def withheld_counts(case: c.CaseModel, contact_id: int, allowed: list[str], hidden: set[str]) -> dict[str, int]:
    """For the attorney's preview: how much stays inside the firm, per category."""
    counts: dict[str, int] = {}

    def withhold(category: str, n: int = 1) -> None:
        counts[category] = counts.get(category, 0) + n

    for group in (case.key_facts, case.custom_fields, case.claims, case.nodes, case.brief.summary):
        for entry in group:
            if entry.category not in allowed:
                withhold(entry.category)
    for event in case.timeline:
        if event.contact_id not in (None, contact_id):
            withhold("other_party")
        elif event.category not in allowed or event.id in hidden or _outbound_event(event, contact_id) is None:
            withhold(event.category)
    for other in case.providers:
        if other.contact.id != contact_id:
            withhold("other_party", len(other.asks) + 1)
    withhold("strategy", len(case.conflicts))
    return {category: n for category, n in counts.items() if n}


def item_count(view: c.ProviderView) -> int:
    singles = (view.stage, view.alive, view.records, view.bills, view.attendance)
    shown = sum(1 for piece in singles if piece is not None) + (1 if view.coverage.shared else 0)
    return shown + len(view.updates) + len(view.asks)


def send(
    conn: sqlite3.Connection,
    builder: CaseBuilder,
    case: c.CaseModel,
    contact_id: int,
    *,
    previewed: str | None = None,
    checker: Checker | None = None,
    override_reason: str | None = None,
) -> c.ShareLogEntry:
    """Freeze the current view into a link. Nothing is emailed; the attorney passes the link on.

    `previewed` is the fingerprint of the preview the attorney confirmed: if the case
    or the policy moved since, nothing is sent. With a `checker`, the text the attorney
    wrote into the view (the cover note, the wording of each request) is read as the
    provider would receive it; a held sentence stops the send unless a reason is
    given, and the reason is logged with the share."""
    policy = builder.policy(contact_id)
    view = provider_view(case, contact_id, policy)
    if previewed is not None and not hmac.compare_digest(previewed.encode(), view.content_hash.encode()):
        raise PreviewChanged(
            "The case or the sharing settings changed after this preview was shown. Review the preview again, then send."
        )
    written = ([("cover_note", view.message)] if view.message else []) + [(ask.id, ask.text) for ask in view.asks]
    locked = locked_text(written, checker) if checker is not None else []
    reason = reason_for(locked, override_reason)
    sent = datetime.now(UTC)
    view.expires_at = (sent + timedelta(days=LINK_LIFETIME_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A provider keeps one link. Sending again puts the new frozen view on it; the
    # view it replaces stays in the log as "superseded", so what left the firm and when is all still there.
    token = secrets.token_urlsafe(24)
    live = conn.execute(
        "SELECT id, token FROM shares WHERE matter_id=? AND contact_id=? AND revoked_at IS NULL"
        " AND instr(token, ?) = 0 AND (expires_at IS NULL OR expires_at > ?) ORDER BY id DESC LIMIT 1",
        (builder.matter_id, contact_id, SUPERSEDED, now_iso()),
    ).fetchone()
    if live:
        token = live["token"]
        conn.execute("UPDATE shares SET token=? WHERE id=?", (f"{token}{SUPERSEDED}{live['id']}", live["id"]))
    row_id = conn.execute(
        "INSERT INTO shares (token, matter_id, contact_id, policy, categories, item_count, sent_at, snapshot,"
        " expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            token,
            builder.matter_id,
            contact_id,
            policy.model_dump_json(),
            json.dumps(view.shared_categories),
            item_count(view),
            sent.strftime("%Y-%m-%dT%H:%M:%SZ"),
            view.model_dump_json(),
            view.expires_at,
        ),
    ).lastrowid
    log_overrides(conn, builder.matter_id, contact_id, row_id, locked, reason)
    conn.commit()
    # A request the provider made is answered by this send if the view now carries its category.
    for request in threads.requests(conn, builder.matter_id, contact_id, allowed=None):
        category = threads.REQUEST_KINDS.get(request.kind, threads.REQUEST_KINDS["other"])[1]
        if request.state == "open" and category is not None and category.value in view.shared_categories:
            threads.answer(conn, builder.matter_id, contact_id, int(request.id), "answered", ANSWERED_BY_VIEW)
    return share_entry(conn.execute("SELECT * FROM shares WHERE id=?", (row_id,)).fetchone())


def snapshot(conn: sqlite3.Connection, row: sqlite3.Row) -> c.ProviderView:
    """The frozen view, with this provider's replies to its asks laid over it.

    What was stored is projected again on the way out, so a link sent under older
    rules serves no more than a link sent now would. A request is served only if
    the policy frozen with the link approved it."""
    stored = c.ProviderView.model_validate_json(row["snapshot"])
    view = _project(
        row["contact_id"],
        [category for category in stored.shared_categories if category in SHAREABLE],
        set(),
        provider=stored.provider,
        client_name=stored.client_name,
        from_name=stored.from_name,
        message=stored.message,
        stage=stored.stage,
        alive=stored.alive,
        band=stored.coverage.band if stored.coverage.shared else None,
        records=stored.records,
        bills=stored.bills,
        attendance=stored.attendance,
        events=stored.updates,
        asks=stored.asks,
        approved=json.loads(row["policy"]).get("approved_asks") or {},
    )
    view.generated_at, view.expires_at = stored.generated_at, stored.expires_at
    for ask in view.asks:
        reply = conn.execute(
            "SELECT reply, replied_at FROM ask_replies WHERE matter_id=? AND contact_id=? AND ask_id=?"
            " ORDER BY id DESC LIMIT 1",
            (row["matter_id"], row["contact_id"], ask.id),
        ).fetchone()
        if reply:
            ask.reply, ask.replied_at = reply["reply"], reply["replied_at"]
    # The provider's own requests and the thread with the firm: live, and theirs alone.
    view.requests = threads.requests(conn, row["matter_id"], row["contact_id"], allowed=None)
    view.thread = threads.messages(conn, row["matter_id"], row["contact_id"])
    return view


def find_live(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """A link that exists, is not revoked, has not expired and has not been replaced.

    A view replaced by a later send keeps its row for the log under a marked token.
    That marked token is not a link: asking for it by name finds nothing."""
    if SUPERSEDED in token:
        return None
    row = conn.execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
    if row is None or row["revoked_at"] or (row["expires_at"] and row["expires_at"] <= now_iso()):
        return None
    return row


def record_open(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    at = now_iso()
    conn.execute(
        "UPDATE shares SET open_count=open_count+1, last_opened_at=?,"
        " first_opened_at=COALESCE(first_opened_at, ?) WHERE id=?",
        (at, at, row["id"]),
    )
    conn.commit()


def revoke(conn: sqlite3.Connection, matter_id: int, share_id: int) -> sqlite3.Row | None:
    conn.execute(
        "UPDATE shares SET revoked_at=COALESCE(revoked_at, ?) WHERE id=? AND matter_id=?",
        (now_iso(), share_id, matter_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM shares WHERE id=? AND matter_id=?", (share_id, matter_id)).fetchone()


def reply_to_ask(conn: sqlite3.Connection, row: sqlite3.Row, ask_id: str, text: str) -> None:
    conn.execute(
        "INSERT INTO ask_replies (matter_id, ask_id, contact_id, reply, replied_at) VALUES (?,?,?,?,?)",
        (row["matter_id"], ask_id, row["contact_id"], text, now_iso()),
    )
    conn.commit()
