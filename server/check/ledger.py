"""The claims ledger as the checker reads it.

The digest stores what each record and page says (`item_claims`, `page_reads`)
and where the firm's entries and the documents disagree (`reconciliations`).
This module loads that, read-only, into memory with the indexes the checker
needs: by amount, by date, by word (SQLite FTS5), and by conflict.

The checker and the batch reconciliation are two call paths over this one
ledger. Nothing here is written by a model at check time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from shared import check_contract as k

from . import extract

log = logging.getLogger("check")

NEVER_SHARED = {"valuation", "strategy", "other_party", "internal"}
PROVIDER_OWN = {"bills", "records", "attendance", "asks"}  # shareable only with the provider they belong to

# Page facts carry no disclosure category from the digest (they default to
# internal). For the leak guard the checker derives one from the kind of fact.
CATEGORY_OF_PAGE_FACT = {
    "charge_or_balance": "bills",
    "treatment": "records",
    "injury_or_diagnosis": "records",
    "legal_event": "status",
    "valuation": "valuation",
    "insurance_or_coverage": "valuation",
    "liability_fact": "strategy",
}


@dataclass
class LedgerClaim:
    id: str
    origin: str
    source_kind: str
    clio_id: str
    page: int | None
    record_date: str | None
    label: str
    quote: str | None
    quote_verified: bool
    kind: str
    topic: str | None
    statement: str
    date: str | None
    amount: int | None  # cents
    party: str | None
    category: str
    issuer: str | None = None
    document_date: str | None = None
    amounts: frozenset[int] = frozenset()
    dates: frozenset[str] = frozenset()
    tokens: frozenset[str] = frozenset()
    contact_ids: frozenset[int] = frozenset()

    @property
    def when(self) -> str | None:
        """The date the claim is about, falling back to the date it was recorded."""
        printed = self.document_date if self.origin == "document" else None
        return self.date or printed or (self.record_date or "")[:10] or None


@dataclass
class LedgerContact:
    id: int
    name: str
    role: str
    patterns: list = field(default_factory=list)
    organisation: bool = False


@dataclass
class LedgerConflict:
    id: str
    topic: str
    notes_ids: list[str]
    document_ids: list[str]


def _short_name(contact: dict[str, Any]) -> list[re.Pattern[str]]:
    """An organisation is usually written by the start of its name: the first
    two words of a name of three or more also count as naming it."""
    words = (contact.get("name") or "").split()
    if contact.get("type") == "Person" or len(words) < 3:
        return []
    head = " ".join(words[:2]).strip(" ,")
    return [re.compile(r"(?<!\w)" + re.escape(head) + r"(?!\w)", re.IGNORECASE)] if len(head) >= 8 else []


class Ledger:
    def __init__(
        self,
        matter_id: int,
        raw_claims: Iterable[dict[str, Any]],
        *,
        contacts: Iterable[dict[str, Any]] = (),
        conflicts: Iterable[dict[str, Any]] = (),
        headline_ids: Iterable[str] = (),
        firm_names: Iterable[str] = (),
    ):
        """`raw_claims` are dicts as `digest.pipeline.collect_claims` returns them.
        `contacts`: Clio contact dicts plus an optional `role`. `conflicts`: dicts
        with id, topic, notes_claim_ids, document_claim_ids."""
        from ..rules import name_patterns

        self.matter_id = matter_id
        # The firm's own people, as Clio names them: a signature line is not a disclosure.
        self.firm_words: frozenset[str] = frozenset(token for name in firm_names for token in extract.content_tokens(name))
        self.contacts = [
            LedgerContact(
                int(raw["id"]), raw.get("name") or "", raw.get("role") or "other", name_patterns(raw) + _short_name(raw),
                raw.get("type") == "Company",
            )
            for raw in contacts
        ]
        self.contact_by_id = {contact.id: contact for contact in self.contacts}
        self.client_ids = {contact.id for contact in self.contacts if contact.role == "client"}
        self.provider_ids = {contact.id for contact in self.contacts if contact.role == "provider"}

        self.claims: dict[str, LedgerClaim] = {}
        for raw in raw_claims:
            claim = self._claim(raw)
            self.claims[claim.id] = claim

        self.conflicts = [
            LedgerConflict(
                str(raw["id"]),
                raw.get("topic") or "",
                [i for i in raw.get("notes_claim_ids") or [] if i in self.claims],
                [i for i in raw.get("document_claim_ids") or [] if i in self.claims],
            )
            for raw in conflicts
        ]
        self.conflicts = [conflict for conflict in self.conflicts if conflict.notes_ids and conflict.document_ids]
        # A firm entry that a document in the file disagrees with -> the document claims.
        self.shown_otherwise: dict[str, list[str]] = {}
        for conflict in self.conflicts:
            for claim_id in conflict.notes_ids:
                self.shown_otherwise.setdefault(claim_id, []).extend(conflict.document_ids)
        self.headline_ids = list(dict.fromkeys(i for i in headline_ids if i in self.claims))
        # Firm entries filed as valuation or strategy: what a provider must never be sent.
        self.firm_only_ids = [
            claim.id for claim in self.claims.values() if claim.origin != "document" and claim.category in ("valuation", "strategy")
        ]

        self.by_amount: dict[int, list[str]] = {}
        self.by_date: dict[str, list[str]] = {}
        self.by_topic: dict[str, list[str]] = {}
        self.by_source: dict[tuple[str, str], list[str]] = {}
        frequency: Counter[str] = Counter()
        for claim in self.claims.values():
            for amount in claim.amounts:
                self.by_amount.setdefault(amount, []).append(claim.id)
            for day in claim.dates:
                self.by_date.setdefault(day, []).append(claim.id)
            if claim.topic:
                self.by_topic.setdefault(claim.topic.strip().lower(), []).append(claim.id)
            self.by_source.setdefault((claim.source_kind, claim.clio_id), []).append(claim.id)
            frequency.update(claim.tokens)
        total = max(len(self.claims), 1)
        self.idf = {token: math.log(1 + total / count) for token, count in frequency.items()}
        self.idf_unseen = math.log(1 + total)

        digest = hashlib.sha256()
        for claim in sorted(self.claims.values(), key=lambda item: item.id):
            digest.update(
                json.dumps(
                    [claim.id, claim.statement, claim.amount, claim.date, claim.category, claim.quote, claim.record_date],
                    ensure_ascii=False,
                ).encode()
            )
        digest.update(json.dumps([[x.id, x.notes_ids, x.document_ids] for x in self.conflicts]).encode())
        digest.update(json.dumps(self.headline_ids).encode())
        digest.update(json.dumps(sorted((x.id, x.name, x.role) for x in self.contacts)).encode())
        self.version = digest.hexdigest()[:12]

        self._fts_lock = threading.Lock()
        self._fts = self._build_fts()

    # -- construction ----------------------------------------------------------

    def _claim(self, raw: dict[str, Any]) -> LedgerClaim:
        origin = raw["origin"]
        category = raw.get("category") or "internal"
        if origin == "document":
            category = CATEGORY_OF_PAGE_FACT.get(raw.get("kind") or "", "internal")
        statement = raw.get("statement") or ""
        amount = extract.cents(raw.get("amount_usd"))
        text = f"{statement}\n{raw.get('quote') or ''}"
        amounts = {found.cents for found in extract.find_amounts(text)}
        if amount is not None:
            amounts.add(amount)
        dates = {found.iso for found in extract.find_dates(text)}
        if raw.get("date"):
            dates.add(str(raw["date"])[:10])
        about = " ".join(str(raw.get(key) or "") for key in ("party", "issuer"))
        contact_ids = {
            contact.id
            for contact in self.contacts
            if any(pattern.search(about) or pattern.search(statement) for pattern in contact.patterns)
        }
        return LedgerClaim(
            id=raw["id"],
            origin=origin,
            source_kind=raw["source_kind"],
            clio_id=str(raw["clio_id"]),
            page=raw.get("page"),
            record_date=raw.get("record_date"),
            label=raw.get("label") or "",
            quote=raw.get("quote"),
            quote_verified=bool(raw.get("quote_verified")),
            kind=raw.get("kind") or "other",
            topic=raw.get("topic"),
            statement=statement,
            date=(str(raw["date"])[:10] if raw.get("date") else None),
            amount=amount,
            party=raw.get("party"),
            category=category,
            issuer=raw.get("issuer"),
            document_date=raw.get("document_date"),
            amounts=frozenset(amounts),
            dates=frozenset(dates),
            tokens=frozenset(extract.content_tokens(f"{statement} {raw.get('topic') or ''} {about}")),
            contact_ids=frozenset(contact_ids),
        )

    def _build_fts(self) -> sqlite3.Connection:
        fts = sqlite3.connect(":memory:", check_same_thread=False)
        fts.execute(
            "CREATE VIRTUAL TABLE claims_fts USING fts5(claim_id UNINDEXED, statement, topic, party, quote,"
            " tokenize='porter unicode61')"
        )
        fts.executemany(
            "INSERT INTO claims_fts VALUES (?,?,?,?,?)",
            [
                (claim.id, claim.statement, claim.topic or "", f"{claim.party or ''} {claim.issuer or ''}", claim.quote or "")
                for claim in self.claims.values()
            ],
        )
        fts.commit()
        return fts

    # -- lookups ---------------------------------------------------------------

    def search(self, text: str, limit: int = 12) -> list[str]:
        """Claim ids ranked by BM25 against the words of `text`."""
        words = sorted({word for word in extract.TOKEN.findall(text.lower()) if word not in extract.STOPWORDS})
        words = [word.replace('"', "").replace("’", "").replace("'", "") for word in words]
        words = [word for word in words if len(word) >= 3][:40]
        if not words:
            return []
        query = " OR ".join(f'"{word}"' for word in words)
        with self._fts_lock:
            try:
                rows = self._fts.execute(
                    "SELECT claim_id FROM claims_fts WHERE claims_fts MATCH ? ORDER BY bm25(claims_fts, 0, 4.0, 3.0, 2.0, 1.0) LIMIT ?",
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [row[0] for row in rows]

    def mentioned(self, text: str) -> set[int]:
        """Contacts named in a piece of text."""
        return {contact.id for contact in self.contacts if any(pattern.search(text) for pattern in contact.patterns)}

    def overlap(self, tokens: set[str], claim: LedgerClaim) -> tuple[int, float]:
        """(shared words, share of the sentence's subject they carry, weighted by
        rarity). A word the ledger has never seen counts fully against the
        match: it is a sign the sentence is about something else."""
        shared = tokens & claim.tokens
        if not shared:
            return 0, 0.0
        weight = sum(self.idf.get(token, self.idf_unseen) for token in tokens)
        return len(shared), (sum(self.idf[token] for token in shared) / weight if weight else 0.0)

    def disputed_entries(self, tokens: set[str], limit: int, least: float = 0.3) -> list[str]:
        """Firm entries with a stored conflict whose subject the words share, best first."""
        scored = []
        for claim_id in self.shown_otherwise:
            shared, share = self.overlap(tokens, self.claims[claim_id])
            if shared >= 2 and share >= least:
                scored.append((share, claim_id))
        return [claim_id for _, claim_id in sorted(scored, reverse=True)[:limit]]

    def is_own(self, claim: LedgerClaim, provider_id: int | None) -> bool:
        return provider_id is not None and provider_id in claim.contact_ids

    def category_for(self, claim: LedgerClaim, provider_id: int | None) -> str:
        """The claim's category as seen by one provider: its own charge or balance
        is bills whatever the entry was filed under; another provider's bill or
        record is other_party (docs/DISCLOSURE.md, rule 4)."""
        if provider_id is not None and claim.kind == "charge_or_balance" and provider_id in claim.contact_ids:
            return "bills"
        if claim.category in PROVIDER_OWN and provider_id is not None:
            others = (claim.contact_ids & self.provider_ids) - {provider_id}
            if others and provider_id not in claim.contact_ids:
                return "other_party"
        return claim.category

    def standing(self, claim: LedgerClaim, provider_id: int | None) -> tuple[str, str]:
        """(category, standing) of a claim for one provider. `never`: a category
        no provider is shown. `open`: one a provider may be shown. `unsorted`: a
        page fact the digest gave no category, which neither locks nor clears."""
        category = self.category_for(claim, provider_id)
        if category in ("valuation", "strategy", "other_party"):
            return category, "never"
        if category == "internal":
            return category, "unsorted" if claim.origin == "document" else "never"
        return category, "open"

    # -- output ----------------------------------------------------------------

    def evidence(self, claim_id: str, role: str) -> k.CheckEvidence:
        from ..case import source_href

        claim = self.claims[claim_id]
        clio_id: int | str = int(claim.clio_id) if claim.clio_id.isdigit() else claim.clio_id
        return k.CheckEvidence.model_validate(
            {
                "claim_id": claim.id,
                "role": role,
                "text": claim.statement,
                "date": claim.when,
                "origin": claim.origin,
                "category": claim.category,
                "source": {
                    "kind": claim.source_kind,
                    "clio_id": clio_id,
                    "label": (claim.label or claim.source_kind.replace("_", " ")).strip()[:140],
                    "date": claim.record_date,
                    "page": claim.page,
                    "quote": claim.quote,
                    "quote_verified": claim.quote_verified,
                    "href": source_href(self.matter_id, claim.source_kind, clio_id, claim.page),
                },
            }
        )

    def cite(self, claim_id: str) -> str:
        """'label, p. 3, 2031-01-12' for a hover line."""
        claim = self.claims[claim_id]
        parts = [claim.label.strip()[:60] or claim.source_kind.replace("_", " ")]
        if claim.page:
            parts.append(f"p. {claim.page}")
        if claim.when:
            parts.append(claim.when)
        return ", ".join(parts)


# --------------------------------------------------------------------------- loading from the database


def _stamp(conn: sqlite3.Connection, matter_id: int) -> tuple:
    """Cheap to read; changes whenever anything the ledger is built from changes."""
    one = conn.execute("SELECT COUNT(*), MAX(at) FROM item_claims WHERE matter_id=?", (matter_id,)).fetchone()
    two = conn.execute(
        "SELECT COUNT(*), MAX(r.at) FROM page_reads r JOIN document_blobs b ON b.sha256 = r.sha256 WHERE b.matter_id=?",
        (matter_id,),
    ).fetchone()
    three = conn.execute("SELECT input_hash, at FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    four = conn.execute("SELECT COUNT(*), MAX(reviewed_at) FROM conflict_reviews WHERE matter_id=?", (matter_id,)).fetchone()
    five = conn.execute("SELECT MAX(changed_at) FROM clio_items WHERE matter_id=?", (matter_id,)).fetchone()
    try:
        from .. import review_queue

        six = review_queue.stamp(conn, matter_id)  # a claim retired or restored in the review queue
    except sqlite3.Error:
        six = None
    return (tuple(one), tuple(two), tuple(three) if three else None, tuple(four), tuple(five), six)


def load(cfg, conn: sqlite3.Connection, matter_id: int) -> Ledger:
    """Build the ledger from what the digest has stored. Reads only."""
    from ..case import CaseBuilder
    from ..digest import pipeline

    raw = pipeline.collect_claims(cfg, conn, matter_id)
    try:
        # A statement the lawyer retired in the review queue is not used to check their writing.
        from .. import review_queue

        raw = review_queue.active(conn, matter_id, raw)
    except sqlite3.Error as error:  # a read-only connection before the queue's tables exist: nothing is retired yet
        log.warning("check: review decisions could not be read (%s); no claim left out", type(error).__name__)
    build = CaseBuilder(conn, matter_id)
    roles = {contact.id: contact.role for contact in build.contacts}
    contacts = [{**contact, "role": roles.get(int(contact["id"]), "other")} for contact in build.contacts_raw]
    try:
        raw = raw + _computed(build)
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as error:
        log.warning("check: the case model's computed facts could not be read (%s); left out", type(error).__name__)
    try:
        conflicts, headline = _reconciled(conn, matter_id, {claim["id"]: claim for claim in raw})
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as error:
        # The stored reconciliation is the digest's and its shape may move; the checker then
        # runs on the claims alone rather than not at all.
        log.warning("check: stored reconciliation could not be read (%s); conflicts left out", type(error).__name__)
        conflicts, headline = [], []
    return Ledger(matter_id, raw, contacts=contacts, conflicts=conflicts, headline_ids=headline, firm_names=_firm_names(build))


def _firm_names(build) -> set[str]:
    """Names of the firm's own users on this matter, from Clio: the responsible and originating
    attorneys, task assignees, and users who sent or received a logged communication."""
    names: set[str] = set()
    for key in ("responsible_attorney", "originating_attorney", "user"):
        names.add(((build.matter.get(key) or {}).get("name") or "").strip())
    for row in build.communications:
        for person in (row.get("senders") or []) + (row.get("receivers") or []):
            if person.get("type") == "User":
                names.add((person.get("name") or "").strip())
        names.add(((row.get("user") or {}).get("name") or "").strip())
    for row in build.tasks:
        names.add(((row.get("assignee") or {}).get("name") or "").strip())
    return names - {""}


def _computed(build) -> list[dict[str, Any]]:
    """The case model's own figures as claims: what the header and the river show, computed in
    code from Clio (stage, case value, coverage, firm spend, liens, net, limitations, last client
    contact, each provider's billed total) and every Clio field that carries an amount or a date.
    They exist before any digest has run, so a figure the firm holds only as a Clio field still
    meets the leak guard."""
    case = build.build(None)
    matter_id = build.matter_id

    def claim(claim_id: str, label: str, display: str, *, amount, date, category, sources, kind: str = "other", party: str | None = None) -> dict[str, Any]:
        source = sources[0] if sources else None
        return {
            "id": claim_id, "origin": "field",
            "source_kind": getattr(source.kind, "value", source.kind) if source else "matter",
            "clio_id": str(source.clio_id) if source else str(matter_id),
            "page": source.page if source else None,
            "record_date": source.date if source else None,
            "label": (source.label if source else None) or label,
            "quote": source.quote if source else None,
            "quote_verified": bool(source and source.quote_verified),
            "kind": kind, "topic": label, "statement": f"{label}: {display}",
            "date": date, "amount_usd": amount, "party": party,
            "category": getattr(category, "value", category) or "internal",
        }

    out: list[dict[str, Any]] = []
    brief = case.brief
    facts = [brief.stage, brief.alive, brief.case_value, brief.coverage, brief.firm_spend, brief.last_client_contact, brief.limitations]
    facts += [fact for fact in case.custom_fields if fact.amount is not None or fact.date]
    seen: set[str] = set()
    for fact in facts:
        if fact is None or fact.id in seen:
            continue
        seen.add(fact.id)
        out.append(claim(f"fact:{fact.id}", fact.label, fact.display, amount=fact.amount, date=fact.date,
                         category=fact.category, sources=fact.sources, kind="valuation" if fact.amount is not None else "other"))
    for node in case.nodes:
        if node.amount is None or ":" in node.id:
            continue  # a provider's own total is added below, as that provider's
        out.append(claim(f"node:{node.id}", node.label, f"${node.amount:,.2f}", amount=node.amount, date=None,
                         category=node.category, sources=node.sources, kind="valuation"))
    for panel in case.providers:
        total = panel.bills.billed_total
        if total is None:
            continue
        out.append(claim(f"provider:{panel.contact.id}:billed_total", f"Charges billed by {panel.contact.name}", f"${total:,.2f}",
                         amount=total, date=None, category="bills", sources=panel.bills.sources, kind="charge_or_balance",
                         party=panel.contact.name))
    return out


def _reconciled(conn: sqlite3.Connection, matter_id: int, by_id: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """(conflicts the attorney has not dismissed, claim ids of the matter's main facts), read
    from the stored reconciliation as plain JSON: only the claim ids and topics are used."""
    row = conn.execute("SELECT result FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    if row is None:
        return [], []
    stored = json.loads(row["result"])
    reviews = [dict(r) for r in conn.execute("SELECT * FROM conflict_reviews WHERE matter_id=?", (matter_id,))]
    conflicts: list[dict[str, Any]] = []
    for out in stored.get("conflicts") or []:
        # Same rule as the conflict cards: both sides, each really from where it claims to be.
        notes_ids = [i for i in out.get("notes_claim_ids") or [] if i in by_id and by_id[i]["origin"] != "document"]
        document_ids = [i for i in out.get("document_claim_ids") or [] if i in by_id and by_id[i]["origin"] == "document"]
        if not notes_ids or not document_ids:
            continue
        # The card's id is the two sides it rests on (as in digest/overlay.py), so a review finds it.
        conflict_id = "conflict:" + hashlib.sha256("|".join(sorted(notes_ids) + sorted(document_ids)).encode()).hexdigest()[:12]
        if _dismissed(reviews, conflict_id, notes_ids + document_ids):
            continue  # the attorney reviewed it and said the entries stand
        conflicts.append({"id": conflict_id, "topic": out.get("topic") or "", "notes_claim_ids": notes_ids, "document_claim_ids": document_ids})
    headline: list[str] = []
    for fact in stored.get("key_facts") or []:
        headline.extend(fact.get("claim_ids") or [])
    for evidence in stored.get("node_evidence") or []:
        headline.extend(evidence.get("claim_ids") or [])
    headline.extend(economic.get("claim_id") or "" for economic in stored.get("economics") or [])
    for conflict in conflicts:
        headline.extend(conflict["notes_claim_ids"] + conflict["document_claim_ids"])
    return conflicts, headline


def _dismissed(reviews: list[dict[str, Any]], conflict_id: str, claim_ids: list[str]) -> bool:
    """The attorney's review of this card, or of the card that shares at least half its claims
    (a rebuild can add or drop a claim), says dismissed."""
    for review in reviews:
        if review.get("conflict_id") == conflict_id:
            return review.get("review") == "dismissed"
    now, best, best_share = set(claim_ids), None, 0.0
    for review in reviews:
        then = set(json.loads(review["claim_ids"])) if review.get("claim_ids") else set()
        if not then:
            continue
        share = len(now & then) / len(now | then)
        if share >= 0.5 and share > best_share:
            best, best_share = review, share
    return bool(best) and best.get("review") == "dismissed"


_cache: dict[tuple[str, int], tuple[tuple, Ledger]] = {}
_cache_lock = threading.Lock()
_rebuilding: set[tuple[str, int]] = set()
_last_build: dict[tuple[str, int], float] = {}
MIN_SECONDS_BETWEEN_REBUILDS = 10.0


def current(cfg, conn: sqlite3.Connection, matter_id: int) -> Ledger:
    """The ledger for a matter, rebuilt when its inputs change. A check never
    waits for a rebuild once a ledger exists: it is served the one in hand while
    the next is built on another thread (the digest may be writing pages)."""
    key = (str(cfg.db_path), matter_id)
    stamp = _stamp(conn, matter_id)
    with _cache_lock:
        held = _cache.get(key)
    if held is None:
        ledger = load(cfg, conn, matter_id)
        with _cache_lock:
            _cache[key] = (stamp, ledger)
            _last_build[key] = time.monotonic()
        return ledger
    if held[0] == stamp:
        return held[1]
    with _cache_lock:
        due = time.monotonic() - _last_build.get(key, 0.0) >= MIN_SECONDS_BETWEEN_REBUILDS
        if key in _rebuilding or not due:
            return held[1]
        _rebuilding.add(key)

    def rebuild() -> None:
        from ..db import connect

        own = connect(cfg.db_path)
        try:
            fresh_stamp = _stamp(own, matter_id)
            ledger = load(cfg, own, matter_id)
            with _cache_lock:
                _cache[key] = (fresh_stamp, ledger)
        finally:
            own.close()
            with _cache_lock:
                _last_build[key] = time.monotonic()
                _rebuilding.discard(key)

    threading.Thread(target=rebuild, name=f"ledger-{matter_id}", daemon=True).start()
    return held[1]


def forget(cfg=None) -> None:
    """Drop cached ledgers (tests)."""
    with _cache_lock:
        _cache.clear()
        _last_build.clear()
