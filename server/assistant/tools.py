"""The assistant's tools: read-only views of one matter's stored data.

Every tool returns ids and source references, never prose the model could take
for a fact of its own. Claims come from the claims ledger (`check/ledger.py`),
records from the Clio items as synced, figures from the case model (computed in
code), links from the graph payload. Nothing here calls a model or Clio.

What is expensive to derive is built once per ledger version and shared between
requests; tool results are cached under the same version.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, urlsplit

from shared import assistant_contract as a
from shared import contract as c

from .. import review_queue
from ..case import CaseBuilder, source_href
from ..check import extract
from ..check import ledger as ledgers
from ..check.ledger import Ledger, LedgerClaim
from ..config import Settings
from ..digest import overlay, pipeline

MEDICAL_KINDS = ("treatment", "injury_or_diagnosis")
RECORD_KINDS = ("note", "communication", "task", "calendar_entry", "expense", "document", "contact", "custom_field")
PAGE_TEXT_CHARS = 1800
PAGES_PER_CALL = 4
SNIPPET_CHARS = 220
CLAIM_REF = re.compile(r"^(?P<kind>[a-z_]+):(?P<id>[^:#]+)(?::p(?P<page>\d+))?$")


def _day(value: Any) -> str | None:
    return str(value)[:10] if value else None


def _names(people: list[dict[str, Any]] | None) -> list[str]:
    return [person.get("name") or "" for person in people or [] if person.get("name")]


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """Drop empty members: fewer tokens, and nothing empty to misread."""
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


# Words that do not tell one organisation or person from another: legal forms, degrees, a title, joiners.
NAME_NOISE = frozenset("llc inc pc pllc pa corp corporation ltd lp llp co company md do dc dpm phd np rn dr the of and".split())


DOTTED = re.compile(r"\b(?:[A-Za-z]\.\s?){2,}")  # degrees and legal forms written with full stops


def name_key(name: str | None) -> tuple[str, ...]:
    """A name reduced to the words that identify it: case, accents, punctuation, initials, degrees, legal
    suffixes and a leading title dropped."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", DOTTED.sub(" ", text).lower())
    return tuple(word for word in words if len(word) > 1 and word not in NAME_NOISE)


def same_name(one: tuple[str, ...], other: tuple[str, ...]) -> int:
    """How strongly two reduced names agree, 0 when they do not: the same words; the same letters run
    together or spaced apart; one name's words all inside the other's; or all but one word shared."""
    if not one or not other:
        return 0
    if one == other:
        return 100
    first, second = "".join(one), "".join(other)
    if min(len(first), len(second)) >= 10 and (first.startswith(second) or second.startswith(first)):
        return 90
    shared = set(one) & set(other)
    if len(shared) >= 2 and (shared == set(one) or shared == set(other)):
        return 50 + len(shared)
    if len(shared) >= 2 and len(shared) / len(set(one) | set(other)) >= 0.6:
        return 40 + len(shared)
    return 0


def match_contact(key: tuple[str, ...], contacts: dict[tuple[str, ...], str]) -> str | None:
    """The contact a printed name is, when exactly one contact agrees with it best."""
    best, tied = (0, None), False
    for other, label in contacts.items():
        score = same_name(key, other)
        if score > best[0]:
            best, tied = (score, label), False
        elif score and score == best[0] and label != best[1]:
            tied = True
    return best[1] if best[0] and not tied else None


@dataclass
class Shared:
    """What the tools read, derived once per ledger version. Holds no connection."""

    version: str
    case: c.CaseModel
    records: dict[str, dict[str, Any]]
    reads: dict[int, dict[int, dict[str, Any]]]
    fts: sqlite3.Connection
    fts_lock: threading.Lock = field(default_factory=threading.Lock)
    graph: dict[str, Any] | None = None
    issuer_names: dict[str, str] = field(default_factory=dict)  # a name as printed on a page -> the label it is grouped under
    contact_keys: dict[tuple[str, ...], str] = field(default_factory=dict)  # identifying words of each contact -> its name
    comments: dict[str, str] = field(default_factory=dict)  # claim id -> the lawyer's note on how to read it
    printed_keys: dict[tuple[str, ...], str] = field(default_factory=dict)  # identifying words of a name no contact has -> its label
    client_key: tuple[str, ...] = ()
    source_refs: dict[str, c.SourceRef] = field(default_factory=dict)
    results: dict[str, tuple[Any, int, str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


_shared: dict[tuple[str, int], Shared] = {}
_shared_lock = threading.Lock()


def _records(build: CaseBuilder, pages: dict[int, int]) -> dict[str, dict[str, Any]]:
    """Every Clio record the graph draws, keyed by the graph's node id."""
    out: dict[str, dict[str, Any]] = {}

    def add(kind: str, clio_id: Any, title: str | None, date: Any, text: str | None, **extra: Any) -> None:
        out[f"{kind}:{clio_id}"] = _clean(
            {"id": f"{kind}:{clio_id}", "kind": kind, "clio_id": clio_id, "title": (title or kind.replace("_", " ")).strip()[:140],
             "date": _day(date), "text": text or "", **extra}
        ) | {"text": text or ""}

    for row in build.notes:
        add("note", row["id"], row.get("subject"), row.get("date"), row.get("detail"), author=(row.get("author") or {}).get("name"))
    for row in build.communications:
        add("communication", row["id"], row.get("subject"), row.get("date"), row.get("body"),
            sender=", ".join(_names(row.get("senders"))), receiver=", ".join(_names(row.get("receivers"))),
            medium={"EmailCommunication": "email", "PhoneCommunication": "phone call"}.get(row.get("type") or ""))
    for row in build.tasks:
        add("task", row["id"], row.get("name"), row.get("due_at"), row.get("description"), status=row.get("status"),
            assignee=(row.get("assignee") or {}).get("name"))
    for row in build.calendar:
        add("calendar_entry", row["id"], row.get("summary"), row.get("start_at"), row.get("description"), location=row.get("location"))
    for row in build.expenses:
        add("expense", row["id"], row.get("note") or (row.get("expense_category") or {}).get("name"), row.get("date"), None,
            amount=row.get("total"), category=(row.get("expense_category") or {}).get("name"))
    for row in build.documents:
        add("document", row["id"], row.get("name") or row.get("filename"), row.get("received_at") or row.get("created_at"), None,
            folder=(row.get("parent") or {}).get("name"), pages=pages.get(int(row["id"])))
    for contact in build.contacts:
        add("contact", contact.id, contact.name, None, None, role=contact.role, role_text=contact.role_text)
    for value in build.matter.get("custom_field_values") or []:
        if value.get("value") in (None, ""):
            continue
        field_id = (value.get("custom_field") or {}).get("id") or value.get("id")
        add("custom_field", field_id, value.get("field_name"), None, str(value["value"]))
    return out


def _records_fts(records: dict[str, dict[str, Any]]) -> sqlite3.Connection:
    fts = sqlite3.connect(":memory:", check_same_thread=False)
    fts.execute("CREATE VIRTUAL TABLE records_fts USING fts5(record_id UNINDEXED, title, body, tokenize='porter unicode61')")
    fts.executemany(
        "INSERT INTO records_fts VALUES (?,?,?)",
        [
            (key, row["title"], " ".join(str(row.get(part) or "") for part in ("text", "sender", "receiver", "author", "folder", "role_text", "category")))
            for key, row in records.items()
        ],
    )
    fts.commit()
    return fts


def shared_for(cfg: Settings, conn: sqlite3.Connection, matter_id: int, ledger: Ledger, version: str) -> Shared:
    key = (str(cfg.db_path), matter_id)
    with _shared_lock:
        held = _shared.get(key)
        if held is not None and held.version == version:
            return held
    build = CaseBuilder(conn, matter_id)
    case = overlay.apply(cfg, build, build.build(None), False)
    pages = {
        row["document_id"]: row["page_count"] or 0
        for row in conn.execute("SELECT document_id, page_count FROM document_blobs WHERE matter_id=?", (matter_id,))
    }
    reads: dict[int, dict[int, dict[str, Any]]] = {}
    for document_id, page, read in pipeline.page_reads(cfg, conn, matter_id):
        reads.setdefault(int(document_id), {})[int(page)] = read
    records = _records(build, pages)
    graph = None
    try:
        from ..graph import api as graph_api

        graph = json.loads(gzip.decompress(graph_api.stored(cfg, conn, matter_id)[1]))
    except Exception:  # the graph is another module's; without it the neighbours tool says so
        graph = None
    # Provider names: one label per provider, however a page spells it. A contact's own name when the printed
    # name matches one (the stored match the provider panels use, then identifying words); otherwise the most
    # frequent spelling among the printed names that share the same identifying words.
    contact_keys = {name_key(contact.name): contact.name for contact in build.contacts if contact.id != build.client_id and name_key(contact.name)}
    printed: dict[str, int] = {}
    for claim in ledger.claims.values():
        if claim.issuer and claim.issuer.strip():
            printed[claim.issuer] = printed.get(claim.issuer, 0) + 1
    stored: dict[str, str] = {}
    try:
        stored = {
            issuer: build.contact_by_id[contact_id].name
            for issuer, contact_id in overlay._issuer_contacts(build, set(printed)).items() if contact_id in build.contact_by_id
        }
    except Exception:
        stored = {}
    issuer_names: dict[str, str] = {}
    spellings: dict[tuple[str, ...], list[str]] = {}
    for issuer in sorted(printed, key=lambda name: (-printed[name], name)):
        label = stored.get(issuer) or match_contact(name_key(issuer), contact_keys)
        if label:
            issuer_names[issuer] = label
            continue
        key = name_key(issuer) or (issuer.strip().lower(),)
        known = next((seen for seen in spellings if same_name(key, seen)), key)  # a spelling of a name already seen
        spellings.setdefault(known, []).append(issuer)
    printed_keys: dict[tuple[str, ...], str] = {}
    for key, variants in spellings.items():
        label = max(variants, key=lambda variant: (printed[variant], variant)).strip()
        printed_keys[key] = label
        for variant in variants:
            issuer_names[variant] = label
    client = build.contact_by_id.get(build.client_id)
    try:
        comments = review_queue.comments(conn, matter_id)
    except Exception:
        comments = {}
    made = Shared(version=version, case=case, records=records, reads=reads, fts=_records_fts(records), graph=graph,
                  issuer_names=issuer_names, contact_keys=contact_keys, comments=comments, printed_keys=printed_keys,
                  client_key=name_key(client.name) if client else ())
    with _shared_lock:
        _shared[key] = made
    return made


class Toolbox:
    """One request's view of the file. `refs` collects every reference a result offered."""

    def __init__(self, cfg: Settings, conn: sqlite3.Connection, matter_id: int):
        self.cfg, self.conn, self.matter_id = cfg, conn, matter_id
        self.conn_lock = threading.Lock()  # tools of one round run on threads; the connection is used one at a time
        self.ledger: Ledger = ledgers.current(cfg, conn, matter_id)
        # A statement the lawyer retired in the review queue never comes back from a tool. The ledger drops
        # them when it is rebuilt; this also covers the moment between a decision and that rebuild.
        try:
            self.claims: dict[str, LedgerClaim] = {claim.id: claim for claim in review_queue.active(conn, matter_id, self.ledger.claims.values())}
            decided = review_queue.stamp(conn, matter_id)
        except Exception:
            self.claims, decided = dict(self.ledger.claims), ()
        self.version = hashlib.sha256(f"{self.ledger.version}|{decided}".encode()).hexdigest()[:12]
        self.shared = shared_for(cfg, conn, matter_id, self.ledger, self.version)
        self.tools: dict[str, Callable[..., tuple[Any, int, str]]] = {
            "file_overview": self.file_overview,
            "search_file": self.search_file,
            "get_claims": self.get_claims,
            "get_record": self.get_record,
            "get_document": self.get_document,
            "list_records": self.list_records,
            "case_figures": self.case_figures,
            "differences": self.differences,
            "agenda": self.agenda,
            "providers": self.providers,
            "graph_neighbours": self.graph_neighbours,
            "timeline": self.timeline,
            "app_guide": self.app_guide,
        }

    # -- running ---------------------------------------------------------------

    def run(self, tool: str, arguments: dict[str, Any], n: int, round_no: int) -> tuple[Any, a.TraceStep]:
        """Run one tool. The result is served from the version's cache when the same call was made before."""
        arguments = _clean(arguments)
        key = tool + "\x1f" + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        started = time.perf_counter()
        with self.shared.lock:
            held = self.shared.results.get(key)
        cached = held is not None
        error = None
        if held is None:
            try:
                held = self.tools[tool](**arguments)
                with self.shared.lock:
                    self.shared.results[key] = held
            except KeyError:
                held, error = ({"error": "no such tool"}, 0, "unknown tool"), "unknown tool"
            except (TypeError, ValueError) as problem:
                # Bad arguments from the model: say which kind of problem, never echo values.
                held, error = ({"error": f"bad arguments ({type(problem).__name__})"}, 0, "bad arguments"), "bad arguments"
            except Exception as problem:  # one tool failing must not sink the turn; the model is told, and so is the trace
                held, error = ({"error": f"the tool failed ({type(problem).__name__})"}, 0, "failed"), "failed"
        result, count, summary = held
        step = a.TraceStep(
            n=n, round=round_no, tool=tool, arguments=arguments, items=count,
            ms=round((time.perf_counter() - started) * 1000, 2), cached=cached, ok=error is None, error=error, summary=summary,
        )
        return result, step

    # -- references ------------------------------------------------------------

    def provider_of(self, claim: LedgerClaim) -> str | None:
        """Whose page or statement it is, under one label however it is spelled: the matter's own contact
        when the printed name matches one, else the commonest spelling of that name in the file. The person
        a statement is about (often the client) is never taken for its provider."""
        if claim.issuer and claim.issuer.strip():
            return self.shared.issuer_names.get(claim.issuer) or claim.issuer.strip()
        own = sorted(claim.contact_ids & self.ledger.provider_ids)
        if len(own) == 1:
            return self.ledger.contact_by_id[own[0]].name
        # No issuer on the page: the person the statement names, unless that is the client or a bare role word.
        key = name_key(claim.party)
        if len(key) < 2 or same_name(key, self.shared.client_key):
            return None
        label = match_contact(key, self.shared.contact_keys)
        if label:
            return label
        with self.shared.lock:
            for seen, known in self.shared.printed_keys.items():
                if same_name(key, seen):
                    return known
            self.shared.printed_keys[key] = (claim.party or "").strip()
            return self.shared.printed_keys[key]

    def claim_row(self, claim: LedgerClaim, quote: bool = False) -> dict[str, Any]:
        row = {
            "id": claim.id, "kind": claim.kind, "date": claim.when, "who": self.provider_of(claim), "text": claim.statement,
            "amount": claim.amount / 100 if claim.amount is not None else None, "origin": claim.origin,
            "source": f"{claim.source_kind}:{claim.clio_id}", "source_label": claim.label[:40], "page": claim.page,
        }
        note = self.shared.comments.get(claim.id)
        if note:
            row["lawyer_note"] = note[:400]  # the lawyer's own note on how to read this statement
        if quote:
            row |= {"quote": claim.quote, "quote_verified": claim.quote_verified, "topic": claim.topic, "category": claim.category}
        return _clean(row)

    def cites(self, claim_ids: list[str] | None, sources: list[c.SourceRef] | None, most: int = 4) -> list[str]:
        """Reference ids for a figure of the case model: its claims when the ledger holds them, else its sources."""
        out = [claim_id for claim_id in claim_ids or [] if claim_id in self.claims][:most]
        if out:
            return out
        for ref in (sources or [])[:most]:
            # A source that was built from a claim names it in its link: cite the claim, so its quote opens.
            named = parse_qs(urlsplit(ref.href).query).get("claim", [""])[0]
            if named in self.claims:
                out.append(named)
                continue
            kind = getattr(ref.kind, "value", ref.kind)
            key = f"{kind}:{ref.clio_id}" + (f":p{ref.page}" if ref.page else "")
            with self.shared.lock:
                self.shared.source_refs.setdefault(key, ref)
            out.append(key)
        return list(dict.fromkeys(out))

    def citation(self, ref_id: str) -> a.Citation | None:
        """Resolve a reference in code, or None when the file holds no such thing."""
        claim = self.claims.get(ref_id)
        if claim is not None:
            clio_id: int | str = int(claim.clio_id) if claim.clio_id.isdigit() else claim.clio_id
            href = source_href(self.matter_id, claim.source_kind, clio_id, claim.page)
            if "#" in claim.id:  # a digest claim: the drawer opens on its quote
                href += ("&" if "?" in href else "?") + "claim=" + quote_plus(claim.id)
            return a.Citation(
                id=ref_id, claim_id=claim.id, node_id=f"{claim.source_kind}:{claim.clio_id}", kind=claim.source_kind, clio_id=clio_id,
                page=claim.page, label=(claim.label or claim.source_kind.replace("_", " ")).strip()[:140], date=claim.record_date,
                text=claim.statement, quote=claim.quote, quote_verified=claim.quote_verified, href=href,
            )
        held = self.shared.source_refs.get(ref_id)
        if held is not None:
            kind = getattr(held.kind, "value", held.kind)
            return a.Citation(
                id=ref_id, node_id=f"{kind}:{held.clio_id}", kind=kind, clio_id=held.clio_id, page=held.page, label=held.label,
                date=held.date, text=held.label, quote=held.quote, quote_verified=held.quote_verified, href=held.href,
            )
        match = CLAIM_REF.match(ref_id)
        if match is None:
            return None
        record = self.shared.records.get(f"{match['kind']}:{match['id']}")
        if record is None:
            return None
        page = int(match["page"]) if match["page"] else None
        if page is not None and (record["kind"] != "document" or not 1 <= page <= (record.get("pages") or 0)):
            return None
        return a.Citation(
            id=ref_id, node_id=record["id"], kind=record["kind"], clio_id=record["clio_id"], page=page, label=record["title"],
            date=record.get("date"), text=record["title"], href=source_href(self.matter_id, record["kind"], record["clio_id"], page),
        )

    def reference_text(self, ref_id: str) -> str:
        """The words behind a reference, for checking a sentence's figures against it."""
        claim = self.claims.get(ref_id)
        if claim is not None:
            return f"{claim.statement}\n{claim.quote or ''}\n{claim.when or ''}"
        match = CLAIM_REF.match(ref_id)
        record = self.shared.records.get(f"{match['kind']}:{match['id']}") if match else None
        if record is None:
            return ""
        return "\n".join(str(record.get(part) or "") for part in ("title", "date", "text", "amount"))

    # -- tools -----------------------------------------------------------------

    def app_guide(self, topic: str = "") -> tuple[Any, int, str]:
        """How to reach a section of the application, from the checked guide. Nothing here is about the case."""
        from . import guide

        result = guide.find(topic)
        return result, len(result["matches"]), f"{len(result['matches'])} guide entries"

    def file_overview(self) -> tuple[Any, int, str]:
        """What the file holds, as counts and ids: the map to plan from."""
        kinds: dict[str, int] = {}
        for record in self.shared.records.values():
            kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
        claims: dict[str, int] = {}
        dates = []
        for claim in self.claims.values():
            claims[claim.kind] = claims.get(claim.kind, 0) + 1
            if claim.kind in MEDICAL_KINDS and claim.origin == "document" and claim.when:
                dates.append(claim.when)
        documents = [
            _clean({"id": row["id"], "title": row["title"], "folder": row.get("folder"), "pages": row.get("pages"),
                    "claims": sum(1 for i in self.ledger.by_source.get(("document", str(row["clio_id"])), []) if i in self.claims)})
            for row in self.shared.records.values() if row["kind"] == "document"
        ]
        matter = self.shared.case.matter
        result = {
            "matter": _clean({"id": f"matter:{matter.id}", "number": matter.display_number, "description": matter.description,
                              "status": matter.status, "client": matter.client.name if matter.client else None}),
            "records": kinds,
            "claims_by_kind": claims,
            "medical_claims_dated": {"count": len(dates), "first": min(dates, default=None), "last": max(dates, default=None)},
            "documents": documents,
            "providers": [{"id": f"contact:{panel.contact.id}", "name": panel.contact.name} for panel in self.shared.case.providers],
            "differences": len(self.shared.case.conflicts),
        }
        return result, len(documents), f"{len(self.claims)} claims, {len(self.shared.records)} records"

    def search_file(self, query: str, kinds: list[str] | None = None, limit: int = 20) -> tuple[Any, int, str]:
        """Ranked claims and records for the words, amounts and dates of `query`."""
        limit = max(1, min(int(limit or 20), 60))
        wanted = set(kinds or [])
        claim_ids: list[str] = []
        for amount in extract.find_amounts(query, bare=True):
            claim_ids += self.ledger.by_amount.get(amount.cents, [])
        for mention in extract.find_dates(query):
            claim_ids += self.ledger.by_date.get(mention.iso, [])
        records: list[dict[str, Any]] = []
        ranked: list[dict[str, Any]] | None = None
        try:  # the same index and scoring as the search bar, so the assistant and the lawyer find the same things
            from ..graph import query as graph_query

            with self.conn_lock:
                ranked = graph_query.search_file(self.cfg, self.conn, self.matter_id, query, limit=limit * (8 if wanted else 3))
        except Exception:
            ranked = None
        relevance: dict[str, float] = {}
        if ranked is None:
            claim_ids += self.ledger.search(query, limit * (6 if wanted else 2))
            records = self._records_by_words(query, wanted, limit)
        else:
            for hit in ranked:
                if hit.get("claim_id"):
                    claim_ids.append(hit["claim_id"])
                    relevance.setdefault(hit["claim_id"], hit.get("relevance") or 0.0)
                    continue
                record = self.shared.records.get(hit.get("node_id") or "")
                if record is None or (wanted and record["kind"] not in wanted) or len(records) >= max(limit // 2, 5):
                    continue
                records.append(_clean({"id": record["id"], "kind": record["kind"], "date": record.get("date"), "title": record["title"],
                                       "snippet": (record["text"] or hit.get("text") or "").strip()[:SNIPPET_CHARS],
                                       "relevance": round(hit.get("relevance") or 0.0, 2)}))
        claims = []
        for claim_id in dict.fromkeys(claim_ids):
            claim = self.claims.get(claim_id)
            if claim is None or (wanted and not ({claim.kind, claim.origin, claim.source_kind} & wanted)):
                continue
            row = self.claim_row(claim)
            if claim_id in relevance:
                row["relevance"] = round(relevance[claim_id], 2)
            claims.append(row)
            if len(claims) >= limit:
                break
        return {"claims": claims, "records": records}, len(claims) + len(records), f"{len(claims)} claims, {len(records)} records"

    def _records_by_words(self, query: str, wanted: set[str], limit: int) -> list[dict[str, Any]]:
        """Records ranked by our own word index: used only when the graph's search is not available."""
        records: list[dict[str, Any]] = []
        words = [word for word in dict.fromkeys(extract.TOKEN.findall(query.lower())) if word not in extract.STOPWORDS and len(word) >= 3][:30]
        words = [word.replace('"', "").replace("’", "").replace("'", "") for word in words]
        if not words:
            return records
        with self.shared.fts_lock:
            try:
                rows = self.shared.fts.execute(
                    "SELECT record_id, snippet(records_fts, 2, '', '', ' … ', 28) FROM records_fts WHERE records_fts MATCH ?"
                    " ORDER BY bm25(records_fts, 0, 3.0, 1.0) LIMIT ?",
                    (" OR ".join(f'"{word}"' for word in words), limit * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        for record_id, snippet in rows:
            record = self.shared.records[record_id]
            if wanted and record["kind"] not in wanted:
                continue
            records.append(_clean({"id": record_id, "kind": record["kind"], "date": record.get("date"), "title": record["title"],
                                   "snippet": (snippet or "").strip()[:SNIPPET_CHARS]}))
            if len(records) >= max(limit // 2, 5):
                break
        return records

    def get_claims(self, ids: list[str]) -> tuple[Any, int, str]:
        found = [self.claim_row(self.claims[i], quote=True) for i in list(dict.fromkeys(ids))[:80] if i in self.claims]
        missing = [i for i in ids if i not in self.claims][:20]
        return _clean({"claims": found, "not_in_file": missing}), len(found), f"{len(found)} claims"

    def get_record(self, ids: list[str]) -> tuple[Any, int, str]:
        """The full text of notes, emails, tasks and other records, with the claims read from each."""
        out = []
        for record_id in list(dict.fromkeys(ids))[:12]:
            record = self.shared.records.get(record_id)
            if record is None:
                continue
            claims = self.ledger.by_source.get((record["kind"], str(record["clio_id"])), [])
            out.append(_clean({**record, "text": record["text"][:6000],
                               "claims": [self.claim_row(self.claims[i]) for i in claims if i in self.claims][:30]}))
        return {"records": out}, len(out), f"{len(out)} records"

    def get_document(self, id: str, pages: list[int] | None = None) -> tuple[Any, int, str]:
        """Without `pages`: the outline of every page read. With `pages`: each page's text layer and stored read."""
        from .. import pages as page_files

        document_id = int(str(id).split(":")[-1])
        record = self.shared.records.get(f"document:{document_id}")
        if record is None:
            return {"error": "no such document in the file"}, 0, "not found"
        reads = self.shared.reads.get(document_id, {})
        head = _clean({"id": record["id"], "title": record["title"], "folder": record.get("folder"), "pages": record.get("pages"),
                       "date": record.get("date")})
        if not pages:
            outline = [
                _clean({"page": number, "cite": f"document:{document_id}:p{number}", "type": read.get("page_type"), "title": read.get("title"),
                        "issuer": read.get("issuer"), "date": read.get("document_date"), "facts": len(read.get("facts") or [])})
                for number, read in sorted(reads.items())
            ]
            return {**head, "outline": outline}, len(outline), f"outline of {len(outline)} pages"
        with self.conn_lock:
            path = page_files.blob_path(self.cfg, self.conn, self.matter_id, document_id)
        out = []
        for number in list(dict.fromkeys(int(page) for page in pages))[:PAGES_PER_CALL]:
            read = reads.get(number) or {}
            text = (page_files.page_text(path, number) or "") if path is not None else ""
            facts = [
                self.claim_row(self.claims[claim_id])
                for claim_id in (f"document:{document_id}:p{number}#{index}" for index in range(len(read.get("facts") or [])))
                if claim_id in self.claims
            ]
            out.append(_clean({"page": number, "cite": f"document:{document_id}:p{number}", "type": read.get("page_type"),
                               "title": read.get("title"), "issuer": read.get("issuer"), "date": read.get("document_date"),
                               "text_layer": text.strip()[:PAGE_TEXT_CHARS], "scanned": not text.strip(), "claims": facts}))
        return {**head, "page_reads": out}, len(out), f"{len(out)} pages"

    def list_records(
        self, kind: str, date_from: str | None = None, date_to: str | None = None, contains: str | None = None, limit: int = 40
    ) -> tuple[Any, int, str]:
        """Records of one kind, oldest first, each with its id and the start of its text."""
        if kind not in RECORD_KINDS:
            raise ValueError("kind")
        limit = max(1, min(int(limit or 40), 120))
        needle = (contains or "").lower().strip()
        rows = []
        for record in self.shared.records.values():
            if record["kind"] != kind:
                continue
            day = record.get("date") or ""
            if (date_from and day < date_from) or (date_to and day and day > date_to):
                continue
            if needle and needle not in json.dumps(record, ensure_ascii=False).lower():
                continue
            rows.append(record)
        rows.sort(key=lambda row: (row.get("date") or "", row["title"]))
        listed = [_clean({**row, "text": row["text"][:SNIPPET_CHARS], "clio_id": None}) for row in rows[:limit]]
        return {"total": len(rows), "records": listed}, len(listed), f"{len(listed)} of {len(rows)} {kind} records"

    def case_figures(self) -> tuple[Any, int, str]:
        """Header facts, the value graph and the river: every figure computed in code, with what to cite for it."""
        case = self.shared.case

        def fact(item: c.Fact | None) -> dict[str, Any] | None:
            if item is None:
                return None
            own = f"fact:{item.id}"
            cite = [own] if own in self.claims else self.cites(None, item.sources)
            return _clean({"label": item.label, "value": item.display, "amount": item.amount, "date": item.date, "status": item.status,
                           "detail": item.detail, "cite": cite})

        brief = case.brief
        header = [fact(x) for x in (brief.stage, brief.alive, brief.case_value, brief.coverage, brief.firm_spend,
                                    brief.last_client_contact, brief.limitations)]
        nodes = []
        for node in case.nodes:
            own = f"node:{node.id}"
            cite = [own] if own in self.claims else self.cites(node.claim_ids, node.sources)
            nodes.append(_clean({"node": node.id, "kind": node.kind, "label": node.label, "amount": node.amount, "status": node.status,
                                 "part_of": node.parent_id, "basis": node.basis, "payer": node.payer, "counted": node.counted, "cite": cite}))
        stages = [
            _clean({"stage": stage.id, "label": stage.label, "inflow": stage.inflow, "diverted": stage.diverted, "outflow": stage.outflow,
                    "status": stage.status, "cite": self.cites(stage.claim_ids, stage.sources)})
            for stage in case.river.stages
        ]
        river = _clean({"net": case.river.net, "unknown": case.river.unknown, "fee_percent": case.river.fee_percent,
                        "evidence_backed_amount": case.river.evidence_backed_amount, "stages": stages,
                        "checks": [_clean(check.model_dump()) for check in case.river.checks]})
        result = {
            "header": [row for row in header if row],
            "key_facts": [fact(x) for x in case.key_facts],
            "custom_fields": [fact(x) for x in case.custom_fields],
            "value_nodes": nodes,
            "river": river,
            "firm_spend": _clean({"total": case.spend.total, "lines": [
                _clean({"date": line.date, "amount": line.amount, "note": line.note, "category": line.category_name,
                        "cite": [f"expense:{line.clio_id}"]}) for line in case.spend.lines]}),
        }
        count = len(result["header"]) + len(result["key_facts"]) + len(result["custom_fields"]) + len(nodes) + len(stages)
        return result, count, f"{count} figures"

    def differences(self) -> tuple[Any, int, str]:
        """The review cards: where the firm's entries and a document in the file disagree."""
        cards = [
            _clean({"id": card.id, "rank": card.rank, "kind": card.kind, "kind_label": card.kind_label, "topic": card.topic,
                    "summary": card.summary, "amount_at_stake": card.amount_at_stake, "severity": card.severity, "review": card.review,
                    "entries_say": [self.claim_row(self.claims[i]) for i in card.notes_claim_ids if i in self.claims],
                    "documents_show": [self.claim_row(self.claims[i]) for i in card.document_claim_ids if i in self.claims]})
            for card in sorted(self.shared.case.conflicts, key=lambda card: card.rank or 999)
        ]
        return {"differences": cards}, len(cards), f"{len(cards)} differences"

    def agenda(self) -> tuple[Any, int, str]:
        book = self.shared.case.agenda

        def rows(items: list[c.AgendaItem]) -> list[dict[str, Any]]:
            return [
                _clean({"title": item.title, "detail": (item.detail or "")[:SNIPPET_CHARS], "due": item.due, "days_from_today": item.days_from_today,
                        "waiting_on": item.waiting_on_name, "assignee": item.assignee, "is_limitations": item.is_limitations or None,
                        "cite": self.cites(None, [item.source])})
                for item in items
            ]

        moves = [
            _clean({"rank": move.rank, "kind": move.kind, "title": move.title, "reason": move.reason, "owed_by": move.owed_by, "due": move.due,
                    "amount": move.amount, "waiting_days": move.waiting_days, "cite": self.cites(None, [move.source])})
            for move in self.shared.case.moves
        ]
        result = {"as_of": book.as_of, "overdue": rows(book.overdue), "coming": rows(book.coming), "waiting": rows(book.waiting),
                  "done_count": len(book.done), "next_moves": moves}
        count = len(book.overdue) + len(book.coming) + len(book.waiting) + len(moves)
        return result, count, f"{len(book.overdue)} overdue, {len(book.coming)} coming, {len(book.waiting)} waiting"

    def providers(self) -> tuple[Any, int, str]:
        panels = []
        for panel in self.shared.case.providers:
            total = f"provider:{panel.contact.id}:billed_total"
            dated = sorted(
                claim.when for claim in self.claims.values()
                if panel.contact.id in claim.contact_ids and claim.kind in MEDICAL_KINDS and claim.origin == "document" and claim.when
            )
            panels.append(_clean({
                "id": f"contact:{panel.contact.id}", "name": panel.contact.name, "role": panel.contact.role_text,
                "records": _clean({"state": panel.records.state, "pages": panel.records.pages, "first_requested": panel.records.first_requested,
                                   "last_received": panel.records.last_received, "cite": self.cites(None, panel.records.sources)}),
                "bills": _clean({"state": panel.bills.state, "billed_total": panel.bills.billed_total, "lines": panel.bills.line_count,
                                 "last_service_date": panel.bills.last_service_date,
                                 "cite": [total] if total in self.claims else self.cites(None, panel.bills.sources)}),
                "attendance": _clean({"signal": panel.attendance.signal, "visits": panel.attendance.visits, "last_visit": panel.attendance.last_visit,
                                      "next_visit": panel.attendance.next_visit, "cite": self.cites(None, panel.attendance.sources)}),
                "treatment_claims": _clean({"count": len(dated), "first": dated[0] if dated else None, "last": dated[-1] if dated else None}),
                "asks": [_clean({"text": ask.text, "times_asked": ask.times_asked, "due": ask.due, "overdue": ask.overdue or None,
                                 "reply": ask.reply, "cite": self.cites(None, ask.sources)}) for ask in panel.asks],
                "shared_with_provider": len(panel.shares),
            }))
        return {"providers": panels}, len(panels), f"{len(panels)} providers"

    def graph_neighbours(self, node_id: str) -> tuple[Any, int, str]:
        """What a node of the graph is linked to, and the claims read from it."""
        graph = self.shared.graph
        if graph is None:
            return {"error": "the graph is not built for this matter"}, 0, "no graph"
        index = next((i for i, node in enumerate(graph["nodes"]) if node["id"] == node_id), None)
        if index is None:
            return {"error": "no such node in the graph"}, 0, "not found"
        kinds = graph["edge_kinds"]
        linked = []
        for first, second, kind in graph["edges"]:
            if index in (first, second):
                other = graph["nodes"][second if first == index else first]
                linked.append(_clean({"id": other["id"], "kind": other["kind"], "label": other["label"], "date": other.get("date"),
                                      "link": kinds[kind], "claims": other.get("claims")}))
        node = graph["nodes"][index]
        kind, _, clio_id = node_id.partition(":")
        claims = [self.claim_row(self.claims[i]) for i in self.ledger.by_source.get((kind, clio_id), []) if i in self.claims][:25]
        result = {"node": _clean({"id": node["id"], "kind": node["kind"], "label": node["label"], "sub": node.get("sub"), "date": node.get("date"),
                                  "claims": node.get("claims")}), "linked": linked[:80], "claims_read_from_it": claims}
        return result, len(linked) + len(claims), f"{len(linked)} links, {len(claims)} claims"

    def select(
        self, kinds: list[str] | None, date_from: str | None, date_to: str | None, provider: str | None, origins: list[str] | None,
        dated_only: bool = True,
    ) -> list[LedgerClaim]:
        """Claims by kind, origin, date range and provider, in date order. Used by the timeline and the documents."""
        wanted, sources = set(kinds or []), set(origins or [])
        needle = (provider or "").lower().strip()
        if needle.startswith("contact:"):
            contact_id = int(needle.split(":", 1)[1]) if needle.split(":", 1)[1].isdigit() else -1
        else:
            contact_id = next((x.id for x in self.ledger.contacts if needle and needle in x.name.lower()), None)
        out = []
        for claim in self.claims.values():
            if ":" in claim.id and "#" not in claim.id:
                continue  # a computed figure of the case model, not a statement of the file
            if wanted and claim.kind not in wanted:
                continue
            if sources and claim.origin not in sources:
                continue
            when = claim.when
            if dated_only and not when:
                continue
            if when and ((date_from and when < date_from) or (date_to and when > date_to)):
                continue
            if needle and not (
                contact_id in claim.contact_ids
                or needle in f"{claim.issuer or ''} {claim.party or ''} {claim.label} {self.provider_of(claim) or ''}".lower()
            ):
                continue
            out.append(claim)
        out.sort(key=lambda claim: (claim.when or "9999", claim.clio_id, claim.page or 0, claim.id))
        return out

    def timeline(
        self, date_from: str | None = None, date_to: str | None = None, kinds: list[str] | None = None, provider: str | None = None,
        origins: list[str] | None = None, limit: int = 80, offset: int = 0, newest_first: bool = False,
    ) -> tuple[Any, int, str]:
        """Dated claims in order. `total` says how many match; page through with `offset`."""
        limit = max(1, min(int(limit or 80), 200))
        found = self.select(kinds, date_from, date_to, provider, origins)
        if newest_first:
            found.reverse()
        rows = [self.claim_row(claim) for claim in found[int(offset or 0): int(offset or 0) + limit]]
        days = [claim.when for claim in found]
        result = {"total": len(found), "first": min(days, default=None), "last": max(days, default=None), "claims": rows}
        return result, len(rows), f"{len(rows)} of {len(found)} dated claims"
