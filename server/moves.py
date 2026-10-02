"""Moves: the few things that change the case now. Chosen and ranked in code.

Sources: hard deadlines, requests a party outside the firm has not answered,
contradictions the attorney has not ruled on, terms of the money that are
unknown or unconfirmed, and overdue tasks. Rank: a hard deadline within 14 days
first, then the amount the move touches, then how long it has waited. The draft
message is a template; no model writes it and it carries no figure.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

from shared import contract as c

from . import rules

if TYPE_CHECKING:
    from .case import CaseBuilder

HARD_DEADLINE_DAYS = 14


def _since_last_reply(build: "CaseBuilder", contact_id: int) -> tuple[int, date | None]:
    """(messages the firm has logged to this contact since the contact last wrote
    or called, the day the contact last did). About the contact, not about any one request."""
    last_inbound: date | None = None
    outbound: list[date] = []
    for communication in build.communications:
        day = rules.local_date(communication.get("date") or communication.get("received_at"))
        if day is None or day > build.today:
            continue
        senders = {int(p["id"]) for p in communication.get("senders") or [] if p.get("type") != "User"}
        receivers = {int(p["id"]) for p in communication.get("receivers") or [] if p.get("type") != "User"}
        if contact_id in senders:
            last_inbound = day if last_inbound is None or day > last_inbound else last_inbound
        elif contact_id in receivers:
            outbound.append(day)
    return sum(1 for day in outbound if last_inbound is None or day > last_inbound), last_inbound


def ask_after_name(title: str, patterns: list[re.Pattern[str]]) -> str | None:
    """What a task wants from the contact it names, or None when the task is the firm's own.

    Owed by the contact: "<name> - <what>" (the name, then what is wanted) and
    "<what> from <name>". Not owed by them: a name that only comes up in passing,
    as in "... with <name>'s office"."""
    for pattern in patterns:
        found = pattern.search(title or "")
        if not found:
            continue
        before, after = title[: found.start()].strip(), title[found.end():]
        if after[:1] in ("'", "\u2019"):
            continue
        after = after.strip(" -\u2013\u2014:,.")
        if len(after.split()) >= 2:
            return after
        wanted = re.sub(r"\s+from(\s+the)?$", "", before, flags=re.IGNORECASE)
        if not after and wanted != before and wanted:
            return wanted.strip(" -\u2013\u2014:,.")
    return None


def draft(build: "CaseBuilder", contact_name: str, ask: str, due: str | None, overdue: bool) -> str:
    """The message for the 'draft' action. A template: who, what was asked, by when. No figures."""
    client = build.contact_by_id.get(build.client_id) if build.client_id else None
    sender = (build.matter.get("responsible_attorney") or {}).get("name") or ""
    lines = [f"Re: {client.name}" if client else "Re: our client", "", f"Dear {contact_name},", ""]
    lines.append(f"We are following up on a request from this office: {ask}.")
    if due:
        lines.append(f"We had asked for this by {str(due)[:10]}." if overdue else f"We would be grateful to have it by {str(due)[:10]}.")
    lines += ["", "Please send it to us, or tell us what you need from this office in order to do so.", "", "Thank you,", sender]
    return "\n".join(lines).rstrip()


def build_moves(build: "CaseBuilder", case: c.CaseModel) -> list[c.Move]:
    moves: list[c.Move] = []
    nodes = {node.id: node for node in case.nodes}
    panels = {panel.contact.id: panel for panel in case.providers}
    claims = {claim.id: claim for claim in case.claims}

    # 1. Tasks: hard deadlines, things owed by someone outside the firm, and the firm's own overdue work.
    for item in case.agenda.overdue + case.agenda.waiting + case.agenda.coming:
        if item.kind != "task":
            continue
        days = item.days_from_today
        hard = item.is_limitations and days is not None and days <= HARD_DEADLINE_DAYS
        contact = build.contact_by_id.get(item.waiting_on_contact_id) if item.waiting_on_contact_id else None
        ask = ask_after_name(item.title, build.patterns.get(contact.id, [])) if contact is not None else None
        if contact is not None and ask:
            asked, last_heard = _since_last_reply(build, contact.id)
            overdue = days is not None and days < 0
            panel = panels.get(contact.id)
            late = f"{-days} days past due" if overdue else (f"due in {days} days" if days is not None else "no due date")
            heard = f"; last heard from them {last_heard.isoformat()}" if last_heard else "; nothing logged from them"
            wrote = f", {asked} message{'s' if asked != 1 else ''} logged to them since" if asked and last_heard else ""
            moves.append(
                c.Move(
                    id=f"move:{item.id}", rank=0, kind="deadline" if hard else "ask",
                    title=f"{contact.name}: {ask}",
                    reason=f"Owed by {contact.name}, {late}{heard}{wrote}",
                    owed_by=contact.name, owed_by_role=contact.role, audience_contact_id=contact.id,
                    waiting_days=-days if overdue else None, times_asked=asked or None, due=item.due,
                    amount=panel.bills.billed_total if panel else None,
                    node_id=f"medical:{contact.id}" if f"medical:{contact.id}" in nodes else None,
                    source=item.source,
                    draft_text=draft(build, contact.name, ask, item.due, overdue),
                    category=c.DisclosureCategory.asks,
                )
            )
        elif hard or (days is not None and days < 0):
            moves.append(
                c.Move(
                    id=f"move:{item.id}", rank=0, kind="deadline" if hard else "task",
                    title=item.title,
                    reason=("Hard deadline on the matter record, " if hard else "") + (f"{-days} days past due" if days < 0 else f"due in {days} days"),
                    owed_by=item.assignee or "firm", owed_by_role="firm",
                    waiting_days=-days if days < 0 else None, due=item.due, source=item.source,
                )
            )

    # 2. Contradictions the attorney has not ruled on: the first-ranked one for each part of the money.
    seen: set = set()
    for conflict in case.conflicts:
        # One move per amount behind the gate, and one per part of the money otherwise; housekeeping conflicts stay as cards.
        key = conflict.amount_at_stake or tuple(conflict.node_ids)
        if conflict.review != "unreviewed" or conflict.severity < 2 or key in seen:
            continue
        seen.add(key)
        side = next((claims[i] for i in conflict.document_claim_ids if i in claims), None)
        if side is None:
            continue
        behind = f"; {rules.usd(conflict.amount_at_stake)} sits behind the coverage gate" if conflict.amount_at_stake else ""
        moves.append(
            c.Move(
                id=f"move:{conflict.id}", rank=0, kind="conflict",
                title=f"Review: {conflict.topic}",
                reason=f"The firm's entries and a document in the file disagree{behind}",
                owed_by="firm", owed_by_role="firm", amount=conflict.amount_at_stake,
                node_id=conflict.node_ids[0] if conflict.node_ids else None, source=side.source,
            )
        )

    # 3. Terms of the money that are missing or rest on the firm's word alone.
    wanted = (
        ("value", "Record an estimated case value", "No case value is in the matter"),
        ("coverage", "Establish what coverage is behind the case", "No coverage figure is in the matter"),
    )
    for node_id, title, reason in wanted:
        node = nodes.get(node_id)
        if node is not None and node.amount is None:
            moves.append(c.Move(id=f"move:node:{node_id}", rank=0, kind="node", title=title, reason=reason,
                                owed_by="firm", owed_by_role="firm", node_id=node_id, source=case.matter.source))
    lien = nodes.get("lien")
    if lien is not None and lien.amount is not None and lien.status == c.FactStatus.assumed.value and lien.sources:
        moves.append(
            c.Move(id="move:node:lien", rank=0, kind="node", title=f"Confirm the lien figure: {lien.label}",
                   reason="Stated in an imported field; no document in the file confirms the amount",
                   owed_by="firm", owed_by_role="firm", amount=lien.amount, node_id="lien", source=lien.sources[0])
        )
    fee = nodes.get("fee")
    if fee is not None and fee.amount is None and case.river.net is not None:
        moves.append(c.Move(id="move:node:fee", rank=0, kind="node", title="Set the firm's fee percentage",
                            reason="Net to client is shown before fee until it is set", owed_by="firm",
                            owed_by_role="firm", node_id="fee", source=case.matter.source))

    moves.sort(key=lambda m: (0 if m.kind == "deadline" else 1, -(m.amount or 0.0), -(m.waiting_days or 0), m.title))
    for position, move in enumerate(moves, start=1):
        move.rank = position
    return moves
