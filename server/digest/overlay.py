"""Lays the stored digest over the case model.

The model's output is only ever a set of references: claim ids, labels, and
matches between names. Everything shown is assembled here. An id the model
invented is dropped; amounts are taken from the claims and summed in code;
`quote_verified` is whatever `pipeline.quote_in` found, never the model's word.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any
from urllib.parse import quote_plus

from shared import contract as c

from .. import rules
from ..case import CaseBuilder
from .. import review_queue
from ..config import Settings
from ..moves import build_moves
from . import pipeline, prompts, schemas

MAX_KEY_FACTS = 10
MAX_EVENTS = 12

RECORD_PAGES = {"medical_record"}
BILL_PAGES = {"bill_or_ledger"}


def digest_status(cfg: Settings, conn: sqlite3.Connection, matter_id: int, claims: list[dict[str, Any]] | None = None) -> c.DigestStatus:
    pages_total, pending = pipeline.pages_pending(cfg, conn, matter_id)
    pages_done = pages_total - sum(len(pages) for _, pages in pending)
    everything = pipeline.text_items(conn, matter_id)
    stored = {
        (row["kind"], row["clio_id"]): row
        for row in conn.execute("SELECT kind, clio_id, content_hash, prompt_version, model FROM item_claims WHERE matter_id=?", (matter_id,))
    }
    done = stale = 0
    for entry in everything:
        row = stored.get((entry["kind"], entry["clio_id"]))
        if row is None:
            continue
        if row["content_hash"] == entry["hash"] and row["prompt_version"] == prompts.CLAIMS_VERSION:
            done += 1
        else:
            stale += 1
    usage = [
        c.ModelUsage(
            model=row["model"],
            requests=row["n"],
            input_tokens=row["input_tokens"],
            cache_read_input_tokens=row["cached"],
            cache_creation_input_tokens=row["written"],
            output_tokens=row["output_tokens"],
            cost_usd=round(row["cost"] or 0.0, 4),
        )
        for row in conn.execute(
            "SELECT model, COUNT(*) AS n, SUM(input_tokens) AS input_tokens, SUM(cached_tokens) AS cached,"
            " SUM(cache_write_tokens) AS written, SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost"
            " FROM llm_calls WHERE matter_id=? AND purpose IN ('pages', 'claims', 'reconcile', 'photo') GROUP BY model",
            (matter_id,),
        )
    ]
    last = conn.execute(
        "SELECT id, state, finished_at, started_at FROM digest_runs WHERE matter_id=? ORDER BY id DESC LIMIT 1", (matter_id,)
    ).fetchone()
    checks = conn.execute(
        "SELECT COUNT(*) AS n, SUM(cost_usd) AS cost FROM llm_calls WHERE matter_id=? AND purpose='check'", (matter_id,)
    ).fetchone()
    last_cost, last_in, last_out = 0.0, 0, 0
    if last:
        row = conn.execute(
            "SELECT SUM(cost_usd) AS cost, SUM(input_tokens) AS i, SUM(output_tokens) AS o FROM llm_calls WHERE run_id=?",
            (last["id"],),
        ).fetchone()
        last_cost, last_in, last_out = round(row["cost"] or 0.0, 4), row["i"] or 0, row["o"] or 0
    if pipeline.is_running(matter_id) or (last and last["state"] == "running"):
        state = "running"
    elif not last:
        state = "not_started"
    else:
        state = last["state"]
    claims = claims if claims is not None else []
    recon = conn.execute("SELECT input_hash, at FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    stale, changed, took = False, 0, None
    if recon:
        stale = pipeline.reconcile_input(cfg, conn, matter_id)[0] != recon["input_hash"]
        if stale:
            changed = conn.execute(
                "SELECT COUNT(*) AS n FROM item_claims WHERE matter_id=? AND at>?", (matter_id, recon["at"])
            ).fetchone()["n"] + conn.execute(
                "SELECT COUNT(DISTINCT b.document_id) AS n FROM page_reads p JOIN document_blobs b ON b.sha256=p.sha256"
                " WHERE b.matter_id=? AND p.at>?",
                (matter_id, recon["at"]),
            ).fetchone()["n"]
        took = conn.execute(
            "SELECT seconds, cost_usd FROM llm_calls WHERE matter_id=? AND purpose='reconcile' AND ok=1 ORDER BY id DESC LIMIT 1",
            (matter_id,),
        ).fetchone()
    return c.DigestStatus(
        reconcile_stale=stale,
        changed_since_reconcile=max(changed, 1) if stale else 0,
        reconciled_at=recon["at"] if recon else None,
        reconcile_estimate_seconds=took["seconds"] if took else None,
        reconcile_estimate_usd=round(took["cost_usd"], 2) if took and took["cost_usd"] is not None else None,
        reconcile_href=f"/api/matters/{matter_id}/digest?reconcile=true" if recon else None,
        state=state,
        items_total=len(everything),
        items_digested=done,
        pages_total=pages_total,
        pages_digested=pages_done,
        items_stale=stale,
        last_run_at=(last["finished_at"] or last["started_at"]) if last else None,
        quotes_total=len(claims),
        quotes_verified=sum(1 for claim in claims if claim["quote_verified"]),
        quotes_uncheckable=sum(1 for claim in claims if claim.get("scanned")),
        usage=usage,
        cost_usd_total=round(sum(u.cost_usd for u in usage), 4),
        cost_usd_last_run=last_cost,
        check_calls=checks["n"] or 0,
        check_cost_usd=round(checks["cost"] or 0.0, 4),
        check_cost_usd_avg=round((checks["cost"] or 0.0) / checks["n"], 5) if checks["n"] else None,
        input_tokens_last_run=last_in,
        output_tokens_last_run=last_out,
    )


def _ref(build: CaseBuilder, claim: dict[str, Any]) -> c.SourceRef:
    ref = build.ref(
        claim["source_kind"],
        int(claim["clio_id"]) if claim["clio_id"].isdigit() else claim["clio_id"],
        claim["label"],
        claim["record_date"],
        page=claim["page"],
        quote=claim["quote"],
        quote_verified=claim["quote_verified"],
    )
    # The link names the claim, so the drawer opens on its quote and highlights it on the page.
    ref.href += ("&" if "?" in ref.href else "?") + "claim=" + quote_plus(claim["id"])
    return ref


def _claim(build: CaseBuilder, claim: dict[str, Any]) -> c.Claim:
    # A document claim is dated only by what its page prints. When the page prints no date it has none:
    # the day the PDF was filed in Clio is not the day the fact happened.
    printed = claim["origin"] == "document"
    if printed:
        when = claim["date"] or claim.get("document_date")
    else:
        when = claim["date"] or (claim["record_date"] or "")[:10] or None
    return c.Claim(
        id=claim["id"],
        text=claim["statement"],
        topic=claim["topic"],
        date=when,
        date_source=("document" if printed else "clio") if when else None,
        issuer=claim.get("issuer"),
        origin=claim["origin"],
        derivation=c.Derivation.ai,
        category=claim["category"],
        source=_ref(build, claim),
    )


def apply(cfg: Settings, build: CaseBuilder, case: c.CaseModel, all_claims: bool = False) -> c.CaseModel:
    """Overlay the digest. `case.claims` carries the claims something on screen
    points at (conflicts, key facts, nodes, events); `all_claims` returns every one."""
    case = _apply(cfg, build, case)
    case.moves = build_moves(build, case)
    if not all_claims:
        used = {i for conflict in case.conflicts for i in conflict.notes_claim_ids + conflict.document_claim_ids}
        used |= {i for node in case.nodes for i in node.claim_ids}
        used |= {i for event in case.timeline for i in event.claim_ids}
        case.claims = [claim for claim in case.claims if claim.id in used]
    return case


def _apply(cfg: Settings, build: CaseBuilder, case: c.CaseModel) -> c.CaseModel:
    conn, matter_id = build.conn, build.matter_id
    raw = pipeline.collect_claims(cfg, conn, matter_id)
    case.meta.digest = digest_status(cfg, conn, matter_id, raw)
    if not raw:
        return case
    by_id = {claim["id"]: claim for claim in raw}
    # Statements the attorney retired in the review queue stop feeding the value graph; nothing is deleted.
    retired = review_queue.retired(conn, matter_id)
    case.claims = [_claim(build, claim) for claim in raw]
    contract_claims = {claim.id: claim for claim in case.claims}
    _providers_from_pages(cfg, build, case)
    _client_photo(cfg, build, case)
    _documented_components(case)
    case.river = build.river(case.nodes)

    row = conn.execute("SELECT result, claims, input_hash FROM reconciliations WHERE matter_id=?", (matter_id,)).fetchone()
    if row is None:
        return case
    stored = json.loads(row["result"])
    for conflict in stored.get("conflicts", []):
        conflict.setdefault("entry_position", "different")  # cards built before the field existed
    result = schemas.Reconciled.model_validate(stored)
    # The cards are shown as they were built. Where a record they rest on has since changed in
    # Clio, the claim as it read then is used for the card, and the card is marked stale.
    changed_ids: set[str] = set()
    if row["claims"] is None:
        if not case.meta.digest.reconcile_stale:  # cards built before snapshots were kept: take one now
            conn.execute(
                "UPDATE reconciliations SET claims=? WHERE matter_id=?",
                (json.dumps(pipeline.referenced_claims(cfg, conn, matter_id, result)), matter_id),
            )
            conn.commit()
    elif case.meta.digest.reconcile_stale:
        for claim_id, then in json.loads(row["claims"]).items():
            now = by_id.get(claim_id)
            if now is None or (now["statement"], now["quote"]) != (then["statement"], then["quote"]):
                changed_ids.add(claim_id)
                by_id[claim_id] = then
                contract_claims[claim_id] = _claim(build, then)
        case.claims = list(contract_claims.values())

    def known(ids: list[str]) -> list[str]:
        return [claim_id for claim_id in ids if claim_id in by_id]

    def refs(ids: list[str]) -> list[c.SourceRef]:
        return [contract_claims[claim_id].source for claim_id in ids]

    nodes = {node.id: node for node in case.nodes}

    # Evidence and open question per node; a node's amount is never changed by the model.
    for evidence in result.node_evidence:
        node = nodes.get(evidence.node)
        if node is None:
            continue
        cited = known(evidence.claim_ids)
        node.claim_ids = [i for i in cited if i not in retired]
        gone = len(cited) - len(node.claim_ids)
        if gone:
            note = f"{gone} statement{'s' if gone != 1 else ''} this rested on retired in review"
            node.basis = f"{node.basis}; {note}" if node.basis else note
        if evidence.node == "gate" and evidence.open_question and node.claim_ids:
            node.label = f"Open question named in the firm's entries: {evidence.open_question}"

    # Components of the case value the model found in claims. Used only when Clio's own
    # fields and charge entries gave the river no components (see server/river.py).
    has_components = any(node.parent_id == "value" for node in case.nodes)
    for index, economic in enumerate(result.economics):
        claim = by_id.get(economic.claim_id)
        if has_components or claim is None or claim["id"] in retired or claim["amount_usd"] is None:
            continue
        case.nodes.append(
            c.ValueNode(
                id=f"economic:{index}",
                kind="economic",
                parent_id="value",
                basis=claim["statement"][:240],
                label=_without_figures(economic.label),
                amount=round(float(claim["amount_usd"]), 2),
                status=c.FactStatus.assumed,
                derivation=c.Derivation.ai,
                claim_ids=[claim["id"]],
                sources=refs([claim["id"]]),
            )
        )
        nodes["value"].depends_on.append(f"economic:{index}")

    reviews = {
        r["conflict_id"]: r
        for r in conn.execute(
            "SELECT conflict_id, review, reviewed_at, claim_ids FROM conflict_reviews WHERE matter_id=?", (matter_id,)
        )
    }
    folders = {str(d["id"]): ((d.get("parent") or {}).get("name") or "") for d in build.documents}
    affected = {"case_value": ["value"], "coverage": ["coverage", "gate"], "liability": ["gate"], "liens": ["lien"], "damages": ["value"], "none": []}
    conflicts = []
    for out in result.conflicts:
        notes_ids = [i for i in known(out.notes_claim_ids) if by_id[i]["origin"] != "document"]
        document_ids = [i for i in known(out.document_claim_ids) if by_id[i]["origin"] == "document"]
        if not notes_ids or not document_ids:
            continue  # a conflict needs both sides, each really from where it claims to be
        # The id is the two sides it rests on, so an attorney's review survives a re-digest.
        conflict_id = "conflict:" + _short_hash(sorted(notes_ids) + sorted(document_ids))
        node_ids = [node_id for node_id in affected[out.affects] if node_id in nodes]
        at_stake = None
        if "gate" in node_ids and nodes["gate"].amount is not None:
            at_stake = nodes["gate"].amount
        review = reviews.get(conflict_id) or _carried_review(reviews, notes_ids + document_ids)
        conflicts.append(
            c.Conflict(
                kind=_conflict_kind(document_ids, by_id, folders, out.entry_position),
                stale=any(i in changed_ids for i in notes_ids + document_ids),
                id=conflict_id,
                topic=out.topic,
                summary=out.summary,
                notes_claim_ids=notes_ids,
                document_claim_ids=document_ids,
                node_ids=node_ids,
                amount_at_stake=at_stake,
                severity=out.severity,
                review=review["review"] if review else "unreviewed",
                reviewed_at=review["reviewed_at"] if review else None,
                review_href=f"/api/matters/{matter_id}/conflicts/{conflict_id}/review",
            )
        )
    case.conflicts = _merge_and_rank(conflicts, by_id, folders)
    conflicts = case.conflicts
    for conflict in conflicts:
        if conflict.review == "dismissed":
            continue
        for node_id in conflict.node_ids:
            if nodes[node_id].amount is not None:
                nodes[node_id].status = c.FactStatus.contested.value
    case.river = build.river(case.nodes)
    for stage in case.river.stages:
        stage.claim_ids = nodes[stage.node_id].claim_ids if stage.node_id in nodes else []

    conflict_of: dict[str, list[str]] = {}
    for conflict in conflicts:
        for claim_id in conflict.notes_claim_ids + conflict.document_claim_ids:
            conflict_of.setdefault(claim_id, []).append(conflict.id)

    for index, fact in enumerate(result.key_facts[:MAX_KEY_FACTS]):
        ids = known(fact.claim_ids)
        if not ids:
            continue
        first = by_id[ids[0]]
        case.key_facts.append(
            c.Fact(
                id=f"key:{index}",
                label=fact.label,
                display=first["statement"],
                amount=first["amount_usd"],
                date=first["date"],
                status=_status(ids, by_id, conflict_of),
                derivation=c.Derivation.ai,
                category=_most_restrictive(by_id[i]["category"] for i in ids),
                sources=refs(ids),
                conflict_ids=sorted({cid for i in ids for cid in conflict_of.get(i, [])}),
            )
        )

    for index, sentence in enumerate(result.summary):
        ids = known(sentence.claim_ids)
        if not ids:
            continue  # an unsupported sentence is not shown
        case.brief.summary.append(
            c.Fact(
                id=f"summary:{index}",
                label="Summary",
                display=sentence.sentence,
                status=_status(ids, by_id, conflict_of, generated=True),
                derivation=c.Derivation.ai,
                category=c.DisclosureCategory.internal,
                sources=refs(ids),
                conflict_ids=sorted({cid for i in ids for cid in conflict_of.get(i, [])}),
            )
        )

    for event in result.events[:MAX_EVENTS]:
        claim = by_id.get(event.claim_id)
        contract_claim = contract_claims.get(event.claim_id)
        if claim is None or not contract_claim.date:
            continue
        case.timeline.append(
            c.TimelineEvent(
                id=f"claim:{claim['id']}",
                date=contract_claim.date[:10],
                date_source=contract_claim.date_source or "clio",
                label=_without_leading_date(event.label),
                kind=event.kind,
                importance=event.importance,
                claim_ids=[claim["id"]],
                # The label is model-written, so the event is firm-only whatever the claim's own category.
                category=c.DisclosureCategory.internal,
                derivation=c.Derivation.ai,
                sources=[contract_claim.source],
            )
        )
    case.timeline.sort(key=lambda event: event.date)
    _one_status_per_fact(case, by_id)
    case.incoming = _incoming(build)
    return case


def _incoming(build: CaseBuilder) -> list[c.IncomingCheck]:
    """Stored checker results for communications from outside the firm, as contract objects."""
    current = {message["clio_id"]: message["hash"] for message in pipeline.incoming_messages(build.conn, build.matter_id)}
    rows = {
        row["clio_id"]: json.loads(row["result"])
        for row in build.conn.execute(
            "SELECT clio_id, content_hash, ledger_version, result FROM incoming_checks"
            " WHERE matter_id=? AND kind='communication'",
            (build.matter_id,),
        )
        # A result made for other text, under an older rule, or by an older checker is not shown.
        if current.get(row["clio_id"]) == row["content_hash"]
        and row["ledger_version"].endswith("/" + pipeline.checker_version())
    }
    out = []
    for message in build.communications:
        result = rows.get(str(message["id"]))
        senders = [int(p["id"]) for p in message.get("senders") or [] if p.get("type") != "User"]
        contact = build.contact_by_id.get(senders[0]) if senders else None
        if result is None or contact is None:
            continue
        spans, counts = [], {}
        for span in result.get("spans", []):
            verdict = span.get("verdict")
            if not verdict:
                continue
            counts[verdict] = counts.get(verdict, 0) + 1
            spans.append(
                c.IncomingSpan(
                    text=span["text"],
                    verdict=verdict,
                    message=span.get("message") or "",
                    claim_ids=span.get("claim_ids") or [],
                    sources=[c.SourceRef.model_validate(evidence["source"]) for evidence in span.get("evidence") or []],
                )
            )
        out.append(
            c.IncomingCheck(
                id=f"communication:{message['id']}",
                contact_id=contact.id,
                contact_name=contact.name,
                contact_role=contact.role,
                date=message.get("date"),
                subject=message.get("subject"),
                counts=counts,
                spans=spans,
                source=build.ref("communication", message["id"], message.get("subject"), message.get("date")),
            )
        )
    return sorted(out, key=lambda check: check.date or "", reverse=True)


def _one_status_per_fact(case: c.CaseModel, by_id: dict[str, dict]) -> None:
    """A header tile and the river node built from the same Clio value show the same
    status, and a tile whose own Clio record is one side of an unreviewed conflict
    says so: the conflict is attached and the status becomes contested."""
    open_conflicts = [conflict for conflict in case.conflicts if conflict.review != "dismissed"]
    entry_sources: dict[tuple[str, str], list[str]] = {}
    for conflict in open_conflicts:
        for claim_id in conflict.notes_claim_ids:
            key = (by_id[claim_id]["source_kind"], str(by_id[claim_id]["clio_id"]))
            entry_sources.setdefault(key, [])
            if conflict.id not in entry_sources[key]:
                entry_sources[key].append(conflict.id)
    brief = case.brief
    nodes = {node.id: node for node in case.nodes}
    tiles = [brief.stage, brief.alive, brief.case_value, brief.coverage, brief.firm_spend,
             brief.last_client_contact, brief.limitations] + case.custom_fields
    for fact in [tile for tile in tiles if tile is not None]:
        found = [cid for source in fact.sources for cid in entry_sources.get((source.kind, str(source.clio_id)), [])]
        for conflict_id in found:
            if conflict_id not in fact.conflict_ids:
                fact.conflict_ids.append(conflict_id)
    for fact, node_id in ((brief.case_value, "value"), (brief.coverage, "coverage")):
        node = nodes.get(node_id)
        if fact is not None and node is not None and node.status == c.FactStatus.contested.value:
            for conflict in open_conflicts:
                if node_id in conflict.node_ids and conflict.id not in fact.conflict_ids:
                    fact.conflict_ids.append(conflict.id)
    for fact in [tile for tile in tiles if tile is not None and tile.conflict_ids]:
        state = _clio_state(fact, brief)
        fact.status = c.FactStatus.contested.value
        count = len(fact.conflict_ids)
        note = f"{state}; {count} unreviewed difference{'s' if count != 1 else ''} in the file bear{'s' if count == 1 else ''} on it."
        fact.detail = f"{note}\n{fact.detail}" if fact.detail else note


def _clio_state(fact: c.Fact, brief: c.Brief) -> str:
    """What Clio itself records for a tile, in Clio's terms. A task marked complete or a
    ticked box is not the same as the fact being confirmed, so it is not called that."""
    if fact is brief.limitations:
        done = (fact.detail or "").startswith("Marked complete")
        return "The matter record shows the limitations task marked complete" if done else "The matter record holds this limitations date on an open task"
    if fact is brief.coverage:
        ticked = fact.status == c.FactStatus.confirmed.value
        return (
            "The firm's imported fields give this limit and have the confirmed box ticked"
            if ticked
            else "The firm's imported field gives this limit; the confirmed box is not ticked"
        )
    if fact is brief.case_value:
        return "This is the firm's own estimate, entered in an imported field"
    return "This is the firm's entry on the matter record"


TRAILING_FIGURE = re.compile(r"\s*[\u2014\u2013:-]\s*\$[\d,.]+\s*$")
LEADING_DATE = re.compile(r"^(?:[A-Z][a-z]+ \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})\s*[\u2014\u2013:-]\s*")


def _without_figures(label: str) -> str:
    """A label names the term; its amount is shown from the claim, so a figure the model appended is cut."""
    return TRAILING_FIGURE.sub("", label).strip()


def _without_leading_date(label: str) -> str:
    """The event's date comes from its claim; a date the model repeated in the label is cut."""
    stripped = LEADING_DATE.sub("", label).strip()
    return stripped[:1].upper() + stripped[1:] if stripped else label


# Clio folders a firm keeps for expert material. A document there is an opinion, not a record.
EXPERT_FOLDER_WORDS = ("expert", "ime", "independent medical")
KIND_ORDER = {"answered_gap": 0, "record": 1, "expert_opinion": 2}
KIND_LABEL = {
    "answered_gap": "The entries call this outstanding; this page is in the file",
    "record": "The entries say one thing; a record in the file says another",
    "expert_opinion": "An expert's opinion in the file differs from the entries",
}


def _conflict_kind(document_ids: list[str], by_id: dict[str, dict], folders: dict[str, str], entry_position: str) -> str:
    """Expert material is told from the Clio folder the document sits in, in code (Clio
    does not record who produced a document, so nothing here says whose side it is on).
    Otherwise the card is an answered gap when the firm's entries call the thing
    outstanding, and a differing record when they state something else."""
    def in_expert_folder(claim_id: str) -> bool:
        folder = folders.get(by_id[claim_id]["clio_id"], "").lower()
        return any(re.search(rf"\b{re.escape(word)}", folder) for word in EXPERT_FOLDER_WORDS)

    if document_ids and all(in_expert_folder(i) for i in document_ids):
        return "expert_opinion"
    return "answered_gap" if entry_position == "outstanding" else "record"


def _source_keys(ids: list[str], by_id: dict[str, dict]) -> set[tuple]:
    """The distinct records and pages behind a set of claims."""
    return {(by_id[i]["source_kind"], by_id[i]["clio_id"], by_id[i]["page"]) for i in ids}


def _carried_review(reviews: dict[str, Any], claim_ids: list[str]) -> Any | None:
    """A card's id is its claim ids, so a rebuilt card with the same two sides keeps its
    review by itself. When a rebuild brings the same conflict back with some claims
    added or dropped, the review made on the card that shares at least half its claims is carried."""
    now, best, best_share = set(claim_ids), None, 0.0
    for review in reviews.values():
        then = set(json.loads(review["claim_ids"])) if review["claim_ids"] else set()
        if not then:
            continue
        share = len(now & then) / len(now | then)
        if share >= 0.5 and share > best_share:
            best, best_share = review, share
    return best


def _merge_and_rank(conflicts: list[c.Conflict], by_id: dict[str, dict], folders: dict[str, str]) -> list[c.Conflict]:
    """Fold cards that are the same subject into one, then order them, all in code.

    Two cards are the same subject when their topics read the same, or when they
    touch the same nodes and at least half of the smaller one's document claims
    are the other's. Nothing is dropped: the merged card carries every claim id.
    Order: amount behind the gate, then severity, then how many distinct records
    and pages back the two sides."""
    merged: list[c.Conflict] = []
    for conflict in sorted(conflicts, key=lambda x: (-x.severity, x.topic)):
        documents = set(conflict.document_claim_ids)
        home = None
        for kept in merged:
            shared = documents & set(kept.document_claim_ids)
            smaller = min(len(documents), len(kept.document_claim_ids))
            same_topic = kept.topic.strip().lower() == conflict.topic.strip().lower()
            overlapping = kept.node_ids == conflict.node_ids and smaller and len(shared) * 2 >= smaller
            # An answered gap and a differing record are different things even on the same pages: never merged.
            if kept.kind == conflict.kind and (same_topic or overlapping):
                home = kept
                break
        if home is None:
            merged.append(conflict)
            continue
        home.notes_claim_ids += [i for i in conflict.notes_claim_ids if i not in home.notes_claim_ids]
        home.document_claim_ids += [i for i in conflict.document_claim_ids if i not in home.document_claim_ids]
        if conflict.topic.strip().lower() != home.topic.strip().lower():
            home.merged_topics.append(conflict.topic)
    for conflict in merged:
        # Counted after merging, over the ids the card actually carries.
        conflict.source_count = len(_source_keys(conflict.notes_claim_ids + conflict.document_claim_ids, by_id))
        conflict.notes_quotes_verified = sum(1 for i in conflict.notes_claim_ids if by_id[i]["quote_verified"])
        conflict.document_quotes_verified = sum(1 for i in conflict.document_claim_ids if by_id[i]["quote_verified"])
        conflict.kind_label = KIND_LABEL[conflict.kind]
    # Answered gaps, then differing records, then expert opinions. Within each: the amount behind the
    # gate; severity; a document side whose quote code found in the page text before one it could not
    # find; then how many records and pages back it.
    merged.sort(
        key=lambda x: (
            KIND_ORDER.get(x.kind, 9),
            -(x.amount_at_stake or 0.0),
            -x.severity,
            0 if x.document_quotes_verified else 1,
            -x.source_count,
            x.topic,
        )
    )
    for position, conflict in enumerate(merged, start=1):
        conflict.rank = position
    return merged


def _short_hash(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


RESTRICTIVE_ORDER = ["internal", "strategy", "valuation", "other_party", "attendance", "coverage", "bills", "records", "asks", "status"]


def _most_restrictive(categories) -> str:
    present = set(categories)
    return next((category for category in RESTRICTIVE_ORDER if category in present), "internal")


def _status(ids: list[str], by_id: dict[str, dict], conflict_of: dict[str, list[str]], generated: bool = False) -> c.FactStatus:
    """contested: a cited claim is in a conflict. confirmed: a cited claim is a document
    page whose quote code found in the page text, or a scan the attorney can open. It
    means "a document is on file for this", not "true". A generated sentence is never confirmed."""
    if any(i in conflict_of for i in ids):
        return c.FactStatus.contested
    documented = any(by_id[i]["origin"] == "document" and (by_id[i]["quote_verified"] or by_id[i].get("scanned")) for i in ids)
    return c.FactStatus.confirmed if documented and not generated else c.FactStatus.assumed


def _documented_components(case: c.CaseModel) -> None:
    """A provider's charge becomes `confirmed` when that provider's bill is on file in
    the matter's documents; the medical total is confirmed when every part of it is."""
    nodes = {node.id: node for node in case.nodes}
    for panel in case.providers:
        node = nodes.get(f"medical:{panel.contact.id}")
        if node is not None and panel.bills.state == "received":
            node.status = c.FactStatus.confirmed.value
            node.basis = f"{node.basis}; itemised bill on file"
            node.sources = panel.bills.sources
    parts = [node for node in case.nodes if node.parent_id == "medical"]
    medical = nodes.get("medical")
    if medical is not None and parts and all(node.status == c.FactStatus.confirmed.value for node in parts):
        medical.status = c.FactStatus.confirmed.value


def _issuer_contacts(build: CaseBuilder, issuers: set[str]) -> dict[str, int]:
    """Which contact each printed issuer is: first by name in code, then by the stored model match."""
    matched: dict[str, int] = {}
    for issuer in issuers:
        for contact in build.contacts:
            if contact.id != build.client_id and rules.mentions(issuer, build.patterns.get(contact.id, [])):
                matched[issuer] = contact.id
                break
    row = build.conn.execute("SELECT result FROM reconciliations WHERE matter_id=?", (build.matter_id,)).fetchone()
    if row:
        for match in json.loads(row["result"]).get("issuers", []):
            if match["contact_id"] in build.contact_by_id and match["issuer"] in issuers:
                matched.setdefault(match["issuer"], match["contact_id"])
    return matched


def _providers_from_pages(cfg: Settings, build: CaseBuilder, case: c.CaseModel) -> None:
    """Records and bills on file per provider, counted from the pages that carry their name."""
    reads = list(pipeline.page_reads(cfg, build.conn, build.matter_id))
    issuer_of = _issuer_contacts(build, {read["issuer"] for _, _, read in reads if read.get("issuer")})
    documents = {int(d["id"]): d for d in build.documents}
    for panel in case.providers:
        record_pages, bill_pages, service_dates, charges = [], [], [], 0
        for document_id, page, read in reads:
            if issuer_of.get(read.get("issuer") or "") != panel.contact.id:
                continue
            if read["page_type"] in RECORD_PAGES:
                record_pages.append((document_id, page))
            elif read["page_type"] in BILL_PAGES:
                bill_pages.append((document_id, page))
                charges += sum(1 for fact in read["facts"] if fact["kind"] == "charge_or_balance")
            service_dates += [fact["date"] for fact in read["facts"] if fact["kind"] == "treatment" and fact["date"]]

        def page_refs(pages: list[tuple[int, int]]) -> list[c.SourceRef]:
            # First page of each run on file is enough to open the drawer at the right place.
            out, seen = [], set()
            for document_id, page in pages:
                if document_id in seen:
                    continue
                seen.add(document_id)
                document = documents.get(document_id, {})
                out.append(build.ref("document", document_id, document.get("name"), document.get("received_at"), page=page))
            return out

        def received(pages: list[tuple[int, int]]) -> str | None:
            dates = [documents[d].get("received_at") for d, _ in pages if documents.get(d, {}).get("received_at")]
            return max(dates)[:10] if dates else None

        if record_pages:
            panel.records = c.RecordsStatus(
                state="received", pages=len(record_pages), last_received=received(record_pages), sources=page_refs(record_pages)
            )
        if bill_pages:
            # Keep the total the firm recorded in Clio, if any; the pages add where the bill itself is.
            panel.bills = c.BillsStatus(
                state="received",
                billed_total=panel.bills.billed_total,
                line_count=charges or panel.bills.line_count,
                last_service_date=max(service_dates) if service_dates else panel.bills.last_service_date,
                sources=panel.bills.sources + page_refs(bill_pages),
            )


def _client_photo(cfg: Settings, build: CaseBuilder, case: c.CaseModel) -> None:
    """The first identification page that shows a face, in the matter's own documents."""
    if case.brief.client_photo is not None:
        return
    documents = {int(d["id"]): d for d in build.documents}
    for document_id, page, read in pipeline.page_reads(cfg, build.conn, build.matter_id):
        if read["shows_photo_of_a_person"] and read["page_type"] == "identification":
            document = documents.get(document_id, {})
            case.brief.client_photo = c.Photo(
                document_id=document_id,
                page=page,
                image_href=f"/api/matters/{build.matter_id}/documents/{document_id}/pages/{page}.png",
                source=build.ref("document", document_id, document.get("name"), document.get("received_at"), page=page),
            )
            return
