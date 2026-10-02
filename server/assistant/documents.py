"""Documents the assistant drafts: assembled, ordered and added up in code.

The model chooses what goes in (kinds, dates, a provider, or exact claim ids).
Every entry's wording is a statement the digest read from the file, every date
is the one printed on the page or held by the record, and every entry carries
the claims it was built from, so each one opens a page.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from shared import assistant_contract as a

from ..check.ledger import LedgerClaim
from ..db import now_iso
from .tools import MEDICAL_KINDS, Toolbox

STATEMENTS_PER_ENTRY = 6
CITES_PER_ENTRY = 10
WHAT_CHARS = 700

TITLES = {
    "medical_chronology": "Medical chronology", "records_summary": "Records summary by provider", "damages_summary": "Damages summary",
    "bills_liens_summary": "Bills and liens", "provider_requests": "Open requests to providers", "case_summary": "Case summary",
}
COLUMNS = {
    "medical_chronology": (("date", "Date"), ("provider", "Provider"), ("what", "What the record says"), ("source", "Source")),
    "records_summary": (("provider", "Provider"), ("date", "Date"), ("what", "What the record says"), ("source", "Source")),
    "damages_summary": (("group", "Part"), ("provider", "Who"), ("what", "Item"), ("amount", "Amount"), ("source", "Source")),
    "bills_liens_summary": (("provider", "Provider"), ("what", "Item"), ("amount", "Amount"), ("date", "Last service"), ("source", "Source")),
    "provider_requests": (("provider", "Provider"), ("date", "Last asked"), ("what", "What the firm needs"), ("source", "Source")),
    "case_summary": (("group", "Part"), ("what", "What the file shows"), ("date", "Date"), ("source", "Source")),
}
DESCRIPTIONS = {
    "medical_chronology": "Every dated treatment and diagnosis statement read from the documents, in date order, each with its page.",
    "records_summary": "The same statements grouped by provider, with each provider's first and last date on file.",
    "damages_summary": "Charges billed by each provider and the components of the value graph, with the amounts computed in code.",
    "bills_liens_summary": "Each provider's charges on file and each lien, with the state of the bills and where each figure comes from.",
    "provider_requests": "What the firm has asked each provider for and not yet received, with how often and when it was last asked.",
    "case_summary": "One page: the header facts, the key facts, what to do next and what is waiting for review, each with its source.",
}
PROMPTS = {
    "medical_chronology": "Build a medical chronology.",
    "records_summary": "Summarise the records we hold, provider by provider.",
    "damages_summary": "Give me a damages summary.",
    "bills_liens_summary": "Give me a summary of the bills and liens.",
    "provider_requests": "List the open requests to providers.",
    "case_summary": "Give me a one-page case summary.",
}


def kinds() -> list[a.DocumentKindInfo]:
    """The documents this server can build, for the interface to offer as starting points."""
    return [a.DocumentKindInfo(kind=kind, title=title, description=DESCRIPTIONS[kind], prompt=PROMPTS[kind]) for kind, title in TITLES.items()]


UNNAMED = "Provider not named on the page"
UNNAMED_RECORD = "No provider named in the record"


def _entries(box: Toolbox, claims: list[LedgerClaim]) -> list[a.DocumentEntry]:
    """One entry per date, provider and source document: what that record says happened that day."""
    grouped: dict[tuple[str, str, str], list[LedgerClaim]] = {}
    for claim in claims:
        provider = box.provider_of(claim) or (UNNAMED if claim.origin == "document" else UNNAMED_RECORD)
        grouped.setdefault((claim.when or "", provider, f"{claim.source_kind}:{claim.clio_id}"), []).append(claim)
    out = []
    for (when, provider, _source), members in grouped.items():
        members.sort(key=lambda claim: (claim.page or 0, claim.id))
        said: list[str] = []
        for claim in members:
            text = claim.statement.strip()
            if text and text.lower() not in {x.lower() for x in said}:
                said.append(text)
        what = " ".join(x if x.endswith((".", ";", "?")) else x + "." for x in said[:STATEMENTS_PER_ENTRY])
        if len(said) > STATEMENTS_PER_ENTRY:
            what += f" (+{len(said) - STATEMENTS_PER_ENTRY} more on these pages)"
        first = members[0]
        out.append(
            a.DocumentEntry(
                date=when or None,
                date_basis=("document" if first.origin == "document" else "record") if when else None,
                provider=provider, what=what[:WHAT_CHARS], group=provider,
                kind="treatment" if any(claim.kind == "treatment" for claim in members) else first.kind,
                source_label=first.label[:140] or None, page=first.page, cite=[claim.id for claim in members[:CITES_PER_ENTRY]],
                provider_as_printed=next((claim.issuer.strip() for claim in members if claim.issuer and claim.issuer.strip() != provider), None),
            )
        )
    return out


def _groups(entries: list[a.DocumentEntry], totals: dict[str, Decimal] | None = None) -> list[a.DocumentGroup]:
    seen: dict[str, list[a.DocumentEntry]] = {}
    for entry in entries:
        seen.setdefault(entry.group or "", []).append(entry)
    out = []
    for label, members in seen.items():
        dates = sorted(entry.date for entry in members if entry.date)
        total = totals.get(label) if totals else None
        out.append(a.DocumentGroup(label=label, entries=len(members), first=dates[0] if dates else None, last=dates[-1] if dates else None,
                                   total=float(total) if total is not None else None))
    return out


def _damages(box: Toolbox) -> tuple[list[a.DocumentEntry], dict[str, Decimal]]:
    """Money rows from the case model, whose amounts are computed in code from the file."""
    case, entries = box.shared.case, []
    billed, billed_total = "Charges billed, by provider", Decimal(0)
    for panel in case.providers:
        if panel.bills.billed_total is None:
            continue
        own = f"provider:{panel.contact.id}:billed_total"
        billed_total += Decimal(str(panel.bills.billed_total))
        entries.append(a.DocumentEntry(
            date=panel.bills.last_service_date, date_basis="document" if panel.bills.last_service_date else None,
            provider=panel.contact.name, what=f"Charges on file: {panel.bills.line_count} lines", amount=panel.bills.billed_total, group=billed,
            kind="charge_or_balance", cite=[own] if own in box.claims else box.cites(None, panel.bills.sources),
        ))
    parts = {"economic": "Components of case value", "value": "Case value", "coverage": "Coverage", "gate": "Coverage",
             "lien": "Liens", "cost": "Costs", "fee": "Fee", "net": "Net"}
    for node in case.nodes:
        if node.amount is None or node.kind not in parts:
            continue
        own = f"node:{node.id}"
        entries.append(a.DocumentEntry(
            provider=node.payer, what=node.label + (f" ({node.basis})" if node.basis else ""), amount=node.amount, group=parts[node.kind],
            kind=str(node.status), cite=[own] if own in box.claims else box.cites(node.claim_ids, node.sources),
        ))
    # Only the providers' own totals are distinct amounts that add up; the value graph's rows include parents and their parts.
    return entries, ({billed: billed_total} if billed_total else {})


def _bills_and_liens(box: Toolbox) -> tuple[list[a.DocumentEntry], dict[str, Decimal]]:
    """Each provider's charges as the case model added them up, then each lien of the value graph."""
    case, entries = box.shared.case, []
    charges, liens, total = "Charges on file, by provider", "Liens", Decimal(0)
    for panel in case.providers:
        bills, own = panel.bills, f"provider:{panel.contact.id}:billed_total"
        if bills.billed_total is None and bills.state == "unknown":
            continue
        if bills.billed_total is not None:
            total += Decimal(str(bills.billed_total))
        entries.append(a.DocumentEntry(
            date=bills.last_service_date, date_basis="document" if bills.last_service_date else None, provider=panel.contact.name,
            what=f"Bills {bills.state}: {bills.line_count} charge lines on file" if bills.line_count else f"Bills {bills.state}",
            amount=bills.billed_total, group=charges, kind="charge_or_balance",
            cite=[own] if own in box.claims else box.cites(None, bills.sources),
        ))
    for node in case.nodes:
        if node.kind != "lien":
            continue
        own = f"node:{node.id}"
        holder = box.shared.case and next((x.name for x in case.contacts if x.id == node.owed_by_contact_id), None)
        entries.append(a.DocumentEntry(
            provider=holder, what=node.label + (f" ({node.basis})" if node.basis else "") + ("" if node.amount is not None else ": amount not in the file"),
            amount=node.amount, group=liens, kind=str(node.status), cite=[own] if own in box.claims else box.cites(node.claim_ids, node.sources),
        ))
    return entries, ({charges: total} if total else {})


def _requests(box: Toolbox) -> list[a.DocumentEntry]:
    """What the firm has asked each provider for, as the case model reads it from the firm's own records."""
    entries = []
    for panel in box.shared.case.providers:
        for ask in panel.asks:
            state = "Answered" if ask.reply else ("Overdue" if ask.overdue else "Open")
            asked = f" Asked {ask.times_asked} times." if ask.times_asked and ask.times_asked > 1 else ""
            due = f" Due {ask.due}." if ask.due else ""
            entries.append(a.DocumentEntry(
                date=ask.last_asked or ask.first_asked, date_basis="record" if (ask.last_asked or ask.first_asked) else None,
                provider=panel.contact.name, what=f"{ask.text.strip()}{'' if ask.text.strip().endswith('.') else '.'}{asked}{due}",
                group=state, kind="ask", cite=box.cites(None, ask.sources),
            ))
    order = {"Overdue": 0, "Open": 1, "Answered": 2}
    entries.sort(key=lambda entry: (order.get(entry.group or "", 3), entry.provider or "", entry.date or ""))
    return entries


def _case_summary(box: Toolbox) -> list[a.DocumentEntry]:
    """The header, the key facts, the next moves and the top differences, each as the case model states it."""
    case, entries = box.shared.case, []

    def fact(item, group: str) -> None:
        if item is None:
            return
        own = f"fact:{item.id}"
        entries.append(a.DocumentEntry(
            date=item.date, date_basis="record" if item.date else None, what=f"{item.label}: {item.display}", amount=item.amount, group=group,
            kind=str(item.status), cite=[own] if own in box.claims else box.cites(None, item.sources),
        ))

    brief = case.brief
    for item in (brief.stage, brief.alive, brief.case_value, brief.coverage, brief.firm_spend, brief.last_client_contact, brief.limitations):
        fact(item, "At a glance")
    for item in case.key_facts[:10]:
        fact(item, "Key facts")
    for move in case.moves[:3]:
        entries.append(a.DocumentEntry(date=move.due, date_basis="record" if move.due else None, provider=move.owed_by,
                                       what=f"{move.title}. {move.reason}", amount=move.amount, group="What to do next", kind=move.kind,
                                       cite=box.cites(None, [move.source])))
    for card in sorted(case.conflicts, key=lambda card: card.rank or 999)[:3]:
        entries.append(a.DocumentEntry(what=f"{card.topic}: {card.summary}", amount=card.amount_at_stake, group="For review",
                                       kind=card.kind, cite=box.cites(card.document_claim_ids + card.notes_claim_ids, None, most=6)))
    return entries


def compose(
    box: Toolbox,
    kind: str,
    title: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    provider: str | None = None,
    ids: list[str] | None = None,
    exclude_ids: list[str] | None = None,
    kinds: list[str] | None = None,
    origins: list[str] | None = None,
    conversation_id: str | None = None,
) -> a.AssistantDocument:
    if kind not in TITLES:
        raise ValueError("kind")
    undated, totals = 0, None
    if kind in ("damages_summary", "bills_liens_summary"):
        entries, totals = _damages(box) if kind == "damages_summary" else _bills_and_liens(box)
        order = list(dict.fromkeys(entry.group for entry in entries))
        entries.sort(key=lambda entry: (order.index(entry.group), -(entry.amount or 0)))
    elif kind == "provider_requests":
        entries = _requests(box)
    elif kind == "case_summary":
        entries = _case_summary(box)
    else:
        if ids:
            claims = [box.claims[i] for i in dict.fromkeys(ids) if i in box.claims]
        else:
            claims = box.select(kinds or list(MEDICAL_KINDS), date_from, date_to, provider, origins or ["document"], dated_only=False)
        dropped = set(exclude_ids or [])
        claims = [claim for claim in claims if claim.id not in dropped]
        undated = sum(1 for claim in claims if not claim.when)
        entries = _entries(box, [claim for claim in claims if claim.when])
        if kind == "medical_chronology":
            entries.sort(key=lambda entry: (entry.date or "", entry.provider or "", entry.page or 0))
        else:
            entries.sort(key=lambda entry: (entry.provider or "", entry.date or "", entry.page or 0))
    for entry in entries:  # a row built from the case model says where its first reference is
        if not entry.source_label and entry.cite:
            first = box.citation(entry.cite[0])
            if first is not None:
                entry.source_label, entry.page = first.label, first.page
    amounts = [Decimal(str(total)) for total in (totals or {}).values()]
    seed = "|".join([kind, box.version, title or "", *(ref for entry in entries for ref in entry.cite)])
    document_id = "doc_" + hashlib.sha256(seed.encode()).hexdigest()[:12]
    return a.AssistantDocument(
        id=document_id, kind=kind, title=(title or TITLES[kind]).strip()[:140], created_at=now_iso(), ledger_version=box.version,
        conversation_id=conversation_id,
        columns=[a.DocumentColumn(key=key, label=label) for key, label in COLUMNS[kind]],
        entries=entries, groups=_groups(entries, totals),
        totals=a.DocumentTotals(entries=len(entries), amount=float(sum(amounts)) if amounts else None, undated=undated),
        href=f"/api/matters/{box.matter_id}/assistant/documents/{document_id}",
        pdf_href=f"/api/matters/{box.matter_id}/assistant/documents/{document_id}.pdf",
    )


def digest_of(document: a.AssistantDocument, sample: int = 12) -> dict[str, Any]:
    """What the model is told about a document it asked for: its id, its size, and a sample to describe it from."""
    step = max(len(document.entries) // sample, 1)
    return {
        "document_id": document.id, "kind": document.kind, "title": document.title, "entries": document.totals.entries,
        "undated_left_out": document.totals.undated,
        "groups": [group.model_dump(exclude_none=True) for group in document.groups[:40]],
        "sample": [entry.model_dump(exclude_none=True) | {"cite": entry.cite[:2]} for entry in document.entries[::step][:sample]],
    }
