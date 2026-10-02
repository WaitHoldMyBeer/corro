"""Builds the contract's CaseModel from what the sync stored.

This module does the parts that need no model: custom fields, the header brief,
agenda, spend, last client contact, provider panels. Sums and date arithmetic
happen here, in code. The digest (claims, conflicts, value graph, key facts) is
layered on top by `server.digest` when it has run.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timezone
from decimal import Decimal
from typing import Any

from shared import contract as c

from . import river as river_v2
from . import rules, threads
from .moves import ask_after_name
from .db import counts, items, now_iso

# A new provider policy shares these. Attendance is the client's information, so
# the attorney switches it on deliberately; coverage is added by `policy()` unless
# the band is "not established". See docs/DISCLOSURE.md.
DEFAULT_SHARED = [
    c.DisclosureCategory.status,
    c.DisclosureCategory.bills,
    c.DisclosureCategory.records,
    c.DisclosureCategory.asks,
]

COVERAGE_NOTE = "A status note from the firm. Not a statement of the amount available and not a promise of payment."

BAND_TEXT = {
    c.CoverageBand.confirmed.value: "A source of coverage has been confirmed",
    c.CoverageBand.being_confirmed.value: "Coverage being confirmed",
    c.CoverageBand.not_established.value: "No coverage established",
}


class MatterNotSynced(LookupError):
    pass


def source_href(matter_id: int, kind: str, clio_id: Any, page: int | None = None) -> str:
    href = f"/api/matters/{matter_id}/sources/{kind}/{clio_id}"
    return f"{href}?page={page}" if page else href


class CaseBuilder:
    def __init__(self, conn: sqlite3.Connection, matter_id: int, today: date | None = None):
        self.conn = conn
        self.matter_id = matter_id
        self.today = today or rules.firm_today()
        rows = items(conn, matter_id, "matter")
        if not rows:
            raise MatterNotSynced(f"matter {matter_id} has not been imported yet")
        self.matter = rows[0]
        self.contacts_raw = items(conn, matter_id, "contact")
        self.relationships = items(conn, matter_id, "relationship")
        self.notes = items(conn, matter_id, "note")
        self.communications = items(conn, matter_id, "communication")
        self.tasks = items(conn, matter_id, "task")
        self.calendar = items(conn, matter_id, "calendar_entry")
        self.expenses = items(conn, matter_id, "expense")
        self.documents = items(conn, matter_id, "document")
        self.client_id = (self.matter.get("client") or {}).get("id")
        self.contacts = self._contacts()
        self.contact_by_id = {contact.id: contact for contact in self.contacts}
        self.patterns = {int(raw["id"]): rules.name_patterns(raw) for raw in self.contacts_raw}

    # -- sources -------------------------------------------------------------

    def ref(self, kind: str, clio_id: Any, label: str | None, when: str | None = None, **extra) -> c.SourceRef:
        return c.SourceRef(
            kind=kind,
            clio_id=clio_id,
            label=(label or kind.replace("_", " ")).strip()[:140],
            date=when,
            href=source_href(self.matter_id, kind, clio_id, extra.get("page")),
            **extra,
        )

    def matter_ref(self, when: str | None = None) -> c.SourceRef:
        label = self.matter.get("display_number") or self.matter.get("description")
        return self.ref("matter", self.matter["id"], label, when or self.matter.get("updated_at"))

    # -- people --------------------------------------------------------------

    def _contacts(self) -> list[c.Contact]:
        described: dict[int, dict[str, Any]] = {}
        for relationship in self.relationships:
            if relationship.get("contact"):
                described[int(relationship["contact"]["id"])] = relationship
        out = []
        for raw in self.contacts_raw:
            contact_id = int(raw["id"])
            relationship = described.get(contact_id)
            if contact_id == self.client_id:
                role, role_text, derivation = "client", None, c.Derivation.clio
                source = self.ref("contact", contact_id, raw.get("name"), raw.get("updated_at"))
            elif relationship:
                role_text = relationship.get("description")
                role, derivation = rules.role_from_text(role_text), c.Derivation.computed
                source = self.ref(
                    "relationship", relationship["id"], role_text, relationship.get("updated_at"), quote=role_text
                )
            else:
                role, role_text, derivation = "other", None, c.Derivation.computed
                source = self.ref("contact", contact_id, raw.get("name"), raw.get("updated_at"))
            out.append(
                c.Contact(
                    id=contact_id,
                    name=raw.get("name") or "",
                    type=raw.get("type") or "",
                    role=role,
                    role_text=role_text,
                    role_derivation=derivation,
                    email=raw.get("primary_email_address"),
                    phone=raw.get("primary_phone_number"),
                    source=source,
                )
            )
        return out

    def parties(self, communication: dict[str, Any]) -> set[int]:
        # Clio types a party as User (firm staff) or as the contact's own type (Person, Company).
        people = (communication.get("senders") or []) + (communication.get("receivers") or [])
        return {int(p["id"]) for p in people if p.get("type") != "User"}

    def named_contact(self, *texts: str | None, roles: tuple[str, ...] | None = None) -> c.Contact | None:
        """The first non-client contact whose name is written in any of `texts`."""
        for contact in self.contacts:
            if contact.id == self.client_id or (roles and contact.role not in roles):
                continue
            if any(rules.mentions(text, self.patterns.get(contact.id, [])) for text in texts):
                return contact
        return None

    # -- custom fields -------------------------------------------------------

    def custom_fields(self) -> tuple[list[c.Fact], dict[str, c.Fact]]:
        facts, slots = [], {}
        values = sorted(
            self.matter.get("custom_field_values") or [], key=lambda v: (v.get("field_display_order") or 0)
        )
        for value in values:
            field_id = (value.get("custom_field") or {}).get("id") or value.get("id")
            name, field_type, raw = value.get("field_name") or "", value.get("field_type") or "", value.get("value")
            amount = date_value = detail = None
            if field_type in ("currency", "numeric"):
                amount = rules.decimal_or_none(raw)
                display = rules.usd(amount) if field_type == "currency" and amount is not None else str(raw or "")
            elif field_type == "checkbox":
                display = "Yes" if rules.truthy(raw) else "No"
            elif field_type == "date":
                display = date_value = str(raw or "")[:10]
            else:
                display = "" if raw is None else str(raw)
                detail = display if field_type == "text_area" else None
            fact = c.Fact(
                id=f"field:{field_id}",
                label=name,
                display=display,
                amount=float(amount) if amount is not None and field_type == "currency" else None,
                date=date_value or None,
                detail=detail,
                # The firm typed it into Clio; nothing has checked it against a document yet.
                status=c.FactStatus.assumed if display else c.FactStatus.unknown,
                derivation=c.Derivation.clio,
                sources=[
                    self.ref(
                        "custom_field",
                        field_id,
                        name,
                        value.get("updated_at"),
                        quote=display if field_type in ("text_area", "text_line") and display else None,
                        quote_verified=bool(display) and field_type in ("text_area", "text_line"),
                    )
                ],
            )
            facts.append(fact)
            slot = rules.field_slot(name, field_type, set(slots))
            if slot:
                slots[slot] = fact
        return facts, slots

    # -- header brief --------------------------------------------------------

    def brief(self, slots: dict[str, c.Fact], spend: c.Spend) -> c.Brief:
        brief = c.Brief()
        stage = (self.matter.get("matter_stage") or {}).get("name")
        if stage:
            brief.stage = c.Fact(
                id="brief:stage",
                label="Stage",
                display=stage,
                # No `date`: Clio's stage timestamp says when the field was last set, not when the case reached the stage.
                detail=f"Stage set on the matter record on {(self.matter.get('matter_stage_updated_at') or '')[:10]}"
                if self.matter.get("matter_stage_updated_at")
                else None,
                status=c.FactStatus.confirmed,
                derivation=c.Derivation.clio,
                category=c.DisclosureCategory.status,
                sources=[self.matter_ref(self.matter.get("matter_stage_updated_at"))],
            )
        brief.alive = self._alive()
        if "case_value" in slots:
            brief.case_value = slots["case_value"].model_copy(
                update={"id": "brief:case_value", "category": c.DisclosureCategory.valuation}
            )
        brief.coverage = self._coverage(slots)
        brief.coverage_signal = self.coverage_signal(slots)
        if spend.lines:
            brief.firm_spend = c.Fact(
                id="brief:firm_spend",
                label="Firm spend",
                display=rules.usd(spend.total),
                amount=spend.total,
                detail=f"{len(spend.lines)} expense entries on the matter record",
                status=c.FactStatus.confirmed,
                derivation=c.Derivation.computed,
                sources=[line.source for line in spend.lines],
            )
        brief.last_client_contact = self._last_contact_with(
            self.client_id, "brief:last_client_contact", "Last client contact", spoken_first=True
        )
        brief.limitations = self._limitations()
        brief.client_photo = self._client_photo()
        return brief

    def _alive(self) -> c.Fact | None:
        status = self.matter.get("status")
        if not status:
            return None
        latest: tuple[date, c.SourceRef] | None = None
        # The dates the firm gave its own notes and logged communications. System
        # timestamps (created, completed) are left out: a bulk import sets them all to one day.
        candidates = [("note", n, n.get("subject"), n.get("date")) for n in self.notes] + [
            ("communication", m, m.get("subject"), m.get("date")) for m in self.communications
        ]
        for kind, row, label, when in candidates:
            day = rules.local_date(when)
            if day and day <= self.today and (latest is None or day > latest[0]):
                latest = (day, self.ref(kind, row["id"], label, when))
        display, sources, when = status, [self.matter_ref()], None
        if latest:
            when = latest[0].isoformat()
            display = f"{status}, last activity {when}"
            sources.append(latest[1])
        return c.Fact(
            id="brief:alive",
            label="Case status",
            display=display,
            date=when,
            detail=f"{(self.today - latest[0]).days} days since the last recorded activity" if latest else None,
            status=c.FactStatus.confirmed,
            derivation=c.Derivation.computed,
            category=c.DisclosureCategory.status,
            sources=sources,
        )

    def _coverage(self, slots: dict[str, c.Fact]) -> c.Fact | None:
        stated = slots.get("coverage")
        if not stated:
            return None
        amounts = rules.money_amounts(stated.display) if stated.amount is None else [Decimal(str(stated.amount))]
        confirmed = slots.get("coverage_confirmed")
        sources = list(stated.sources) + (list(confirmed.sources) if confirmed else [])
        return c.Fact(
            id="brief:coverage",
            label=stated.label,
            # The first figure written in the field is shown; the whole text is in `detail`.
            display=rules.usd(amounts[0]) if amounts else stated.display,
            amount=float(amounts[0]) if amounts else None,
            detail=(
                f"First dollar figure in the imported field \u201c{stated.label}\u201d. The field reads:\n"
                f"{stated.detail or stated.display}"
            )
            if amounts and stated.amount is None
            else (stated.detail or stated.display),
            status=c.FactStatus.confirmed if confirmed and confirmed.display == "Yes" else c.FactStatus.assumed,
            derivation=c.Derivation.computed if amounts and stated.amount is None else c.Derivation.clio,
            category=c.DisclosureCategory.valuation,  # limits are a figure: firm-only
            sources=sources,
        )

    def coverage_signal(self, slots: dict[str, c.Fact]) -> c.CoverageSignal:
        """The one bit a provider may be told. "Confirmed" needs the firm's logged
        confirmation in Clio; text in a limits field alone is "being confirmed"."""
        stated, confirmed = slots.get("coverage"), slots.get("coverage_confirmed")
        if confirmed and confirmed.display == "Yes":
            band, sources = c.CoverageBand.confirmed, confirmed.sources + (stated.sources if stated else [])
        elif stated and stated.display:
            band, sources = c.CoverageBand.being_confirmed, stated.sources + (confirmed.sources if confirmed else [])
        else:
            band, sources = c.CoverageBand.not_established, []
        return c.CoverageSignal(band=band, display=BAND_TEXT[band.value], note=COVERAGE_NOTE, sources=sources)

    def _last_contact_with(
        self, contact_id: int | None, fact_id: str, label: str, spoken_first: bool = False
    ) -> c.Fact | None:
        """The most recent logged communication with a contact. With `spoken_first` (the
        client: "when did anyone last talk to them") the latest call is shown if there is
        one, and the detail says when the latest written contact was."""
        if contact_id is None:
            return None
        latest: dict[bool, tuple[date, dict[str, Any]]] = {}
        for communication in self.communications:
            day = rules.local_date(communication.get("date") or communication.get("received_at"))
            if day and day <= self.today and contact_id in self.parties(communication):
                spoken = communication.get("type") == "PhoneCommunication"
                if spoken not in latest or day > latest[spoken][0]:
                    latest[spoken] = (day, communication)
        if not latest:
            return None
        if spoken_first:
            day, communication = latest.get(True) or latest[False]
        else:
            day, communication = max(latest.values(), key=lambda found: found[0])
        medium = {"PhoneCommunication": "phone call", "EmailCommunication": "email"}.get(
            communication.get("type") or "", "communication"
        )
        detail = f"{(self.today - day).days} days ago"
        sources = [self.ref("communication", communication["id"], communication.get("subject"), communication.get("date"))]
        if spoken_first and True in latest and False in latest and latest[False][0] > day:
            written_day, written = latest[False]
            detail += f"; last written contact {written_day.isoformat()}"
            sources.append(self.ref("communication", written["id"], written.get("subject"), written.get("date")))
        elif spoken_first and True not in latest:
            detail += "; no call is logged"
        return c.Fact(
            id=fact_id,
            label=label,
            display=f"{day.isoformat()}, {medium}",
            date=day.isoformat(),
            detail=detail,
            status=c.FactStatus.confirmed,
            derivation=c.Derivation.computed,
            sources=sources,
        )

    def _limitations(self) -> c.Fact | None:
        task = next((t for t in self.tasks if t.get("statute_of_limitations")), None)
        due = (task or {}).get("due_at") or (self.matter.get("statute_of_limitations") or {}).get("due_at")
        if not due:
            return None
        day = rules.local_date(due)
        done = bool(task) and task.get("status") == "complete"
        described = (task or {}).get("description")
        days = (day - self.today).days if day else None
        if done:
            detail = "Marked complete on the matter record" + (f". {described}" if described else "")
        elif days is None:
            detail = None
        else:
            detail = f"{days} days away" if days >= 0 else f"{-days} days past"
        source = (
            self.ref("task", task["id"], task.get("name"), task.get("due_at")) if task else self.matter_ref()
        )
        return c.Fact(
            id="brief:limitations",
            label="Limitations date on the matter record",
            display=str(due)[:10],
            date=str(due)[:10],
            detail=detail,
            status=c.FactStatus.confirmed if done else c.FactStatus.assumed,
            derivation=c.Derivation.clio,
            category=c.DisclosureCategory.internal,
            sources=[source],
        )

    def _client_photo(self) -> c.Photo | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (f"client_photo:{self.matter_id}",)
        ).fetchone()
        if not row:
            return None
        found = json.loads(row["value"])
        document = next((d for d in self.documents if int(d["id"]) == int(found["document_id"])), None)
        if not document:
            return None
        page = int(found.get("page") or 1)
        crop = found.get("crop")
        query = "?crop=" + ",".join(f"{value:.3f}" for value in crop) if crop else ""
        return c.Photo(
            document_id=int(document["id"]),
            page=page,
            image_href=f"/api/matters/{self.matter_id}/documents/{document['id']}/pages/{page}.png{query}",
            source=self.ref("document", document["id"], document.get("name"), document.get("received_at"), page=page),
        )

    # -- money out -----------------------------------------------------------

    def spend(self) -> c.Spend:
        """Costs the firm has advanced: Clio expense entries that are billable to the
        matter. Entries Clio flags non-billable are not firm money (see `charges_on_file`)."""
        lines, total = [], Decimal("0")
        for expense in sorted(self.expenses, key=lambda e: e.get("date") or ""):
            if expense.get("non_billable"):
                continue
            amount = rules.decimal_or_none(expense.get("total"))
            if amount is None:
                price = rules.decimal_or_none(expense.get("price"))
                if price is None:
                    continue  # no figure on the entry: left out, never counted as zero
                amount = price * (rules.decimal_or_none(expense.get("quantity")) or Decimal("1"))
            total += amount
            lines.append(
                c.ExpenseLine(
                    clio_id=int(expense["id"]),
                    date=expense.get("date"),
                    amount=float(amount),
                    note=expense.get("note"),
                    category_name=(expense.get("expense_category") or {}).get("name"),
                    source=self.ref("expense", expense["id"], expense.get("note"), expense.get("date")),
                )
            )
        return c.Spend(total=float(total), lines=lines)

    # -- value graph and river -----------------------------------------------

    def fee_percent(self) -> Decimal | None:
        """The firm's contingency percentage: a firm setting in our database, not a Clio field."""
        row = self.conn.execute("SELECT value FROM settings WHERE key='fee_percent'").fetchone()
        return rules.decimal_or_none(row["value"]) if row else None

    def fee_basis(self) -> str:
        """'gross': the percentage applies to the recovery; 'after_costs': to the
        recovery less the firm's costs. Retainers and court rules differ, so it is a setting."""
        row = self.conn.execute("SELECT value FROM settings WHERE key='fee_basis'").fetchone()
        return row["value"] if row else "gross"

    def nodes(self, slots: dict[str, c.Fact], brief: c.Brief, spend: c.Spend) -> list[c.ValueNode]:
        """The terms of net = min(value, coverage) - liens - costs - fee that Clio's
        own fields can fill. The digest adds claims, statuses and the economic terms."""

        def amount_of(fact: c.Fact | None) -> Decimal | None:
            return Decimal(str(fact.amount)) if fact and fact.amount is not None else None

        def node(node_id: str, kind: str, label: str, amount: Decimal | None, fact: c.Fact | None, **extra) -> c.ValueNode:
            return c.ValueNode(
                id=node_id,
                kind=kind,
                label=label,
                amount=float(amount) if amount is not None else None,
                status=extra.pop("status", fact.status if fact and amount is not None else c.FactStatus.unknown),
                derivation=extra.pop("derivation", fact.derivation if fact else c.Derivation.computed),
                sources=extra.pop("sources", fact.sources if fact else []),
                **extra,
            )

        value, coverage = amount_of(brief.case_value), amount_of(brief.coverage)
        # The gate closes only against coverage the firm has logged as confirmed.
        # An unconfirmed figure is shown on its own node and gates nothing.
        confirmed = coverage if brief.coverage and brief.coverage.status == c.FactStatus.confirmed.value else None
        held_back = max(value - confirmed, Decimal("0")) if value is not None and confirmed is not None else None
        through = min(value, confirmed) if value is not None and confirmed is not None else value
        lien_fact = slots.get("lien")
        lien_amounts = rules.money_amounts(lien_fact.display) if lien_fact else []
        lien = lien_amounts[0] if lien_amounts else None
        costs = Decimal(str(spend.total)) if spend.lines else None
        percent = self.fee_percent()
        fee = None
        if through is not None and percent is not None:
            base = through - (costs or Decimal("0")) if self.fee_basis() == "after_costs" else through
            fee = (base * percent / 100).quantize(Decimal("0.01"))
        net = through
        for deduction in (lien, costs, fee):
            if net is not None and deduction is not None:
                net = max(net - deduction, Decimal("0"))
        return [
            node("value", "value", brief.case_value.label if brief.case_value else "Case value", value, brief.case_value),
            node("coverage", "coverage", brief.coverage.label if brief.coverage else "Coverage", coverage, brief.coverage),
            node(
                "gate",
                "gate",
                "Held back by coverage",
                held_back,
                None,
                status=brief.coverage.status if brief.coverage and held_back is not None else c.FactStatus.unknown,
                sources=brief.coverage.sources if brief.coverage else [],
                depends_on=["value", "coverage"],
            ),
            node("lien", "lien", lien_fact.label if lien_fact else "Liens", lien, lien_fact,
                 derivation=c.Derivation.computed, depends_on=["gate"]),
            node("cost", "cost", "Costs advanced by the firm", costs, brief.firm_spend, depends_on=["gate"]),
            node(
                "fee",
                "fee",
                "Attorney fee" if percent is None else f"Attorney fee ({percent.normalize():f}%)",
                fee,
                None,
                status=c.FactStatus.assumed if fee is not None else c.FactStatus.unknown,
                depends_on=["gate"],
            ),
            node(
                "net",
                "net",
                "Net to client" if percent is not None else "Net to client, before fee",
                net,
                None,
                status=c.FactStatus.assumed if net is not None else c.FactStatus.unknown,
                depends_on=["gate", "lien", "cost", "fee"],
            ),
        ]

    def river(self, nodes: list[c.ValueNode]) -> c.River:
        """Left to right: value in, the gate holds some back, then liens, costs
        and fee come out. Arithmetic only; an unknown amount is never drawn as 0."""
        by_id = {node.id: node for node in nodes}
        percent = self.fee_percent()
        river = c.River(fee_percent=float(percent) if percent is not None else None)
        value = by_id["value"]
        flow = Decimal(str(value.amount)) if value.amount is not None else None
        river.stages.append(
            c.RiverStage(id="value", node_id="value", kind="value", label=value.label, inflow=value.amount,
                         outflow=value.amount, status=value.status, claim_ids=value.claim_ids, sources=value.sources)
        )
        if value.amount is None:
            river.unknown.append("value")
        for node_id in ("gate", "lien", "cost", "fee"):
            node = by_id[node_id]
            taken = Decimal(str(node.amount)) if node.amount is not None else None
            if taken is None:
                river.unknown.append(node_id)
            after = max(flow - taken, Decimal("0")) if flow is not None and taken is not None else flow
            river.stages.append(
                c.RiverStage(
                    id=node_id,
                    node_id=node_id,
                    kind=node.kind,
                    label=node.label,
                    inflow=float(flow) if flow is not None else None,
                    diverted=node.amount,
                    outflow=float(after) if after is not None else None,
                    status=node.status,
                    claim_ids=node.claim_ids,
                    sources=node.sources,
                )
            )
            flow = after
        river.net = float(flow) if flow is not None else None
        river.stages.append(
            c.RiverStage(id="net", node_id="net", kind="net", label=by_id["net"].label,
                         inflow=river.net, outflow=river.net, status=by_id["net"].status)
        )
        return river_v2.annotate(river, nodes)

    def charges_on_file(self, contact: c.Contact) -> c.BillsStatus | None:
        """A provider's charges as the firm recorded them in Clio: non-billable
        expense entries whose note names that provider. Summed here, in code."""
        total, sources = Decimal("0"), []
        for expense in self.expenses:
            if not expense.get("non_billable") or not rules.mentions(expense.get("note"), self.patterns.get(contact.id, [])):
                continue
            amount = rules.decimal_or_none(expense.get("total"))
            if amount is None:
                price = rules.decimal_or_none(expense.get("price"))
                if price is None:
                    continue
                amount = price * (rules.decimal_or_none(expense.get("quantity")) or Decimal("1"))
            total += amount
            sources.append(self.ref("expense", expense["id"], expense.get("note"), expense.get("date")))
        if not sources:
            return None
        # "partial": a figure the firm entered in Clio, not a bill received. The entry's date is
        # when it was entered, so no service date is claimed; the digest sets both from bill pages.
        return c.BillsStatus(state="partial", billed_total=float(total), line_count=len(sources), sources=sources)

    # -- agenda --------------------------------------------------------------

    def agenda(self) -> c.Agenda:
        agenda = c.Agenda(as_of=self.today.isoformat())
        for task in self.tasks:
            day = rules.local_date(task.get("due_at"))
            days = (day - self.today).days if day else None
            # Only the task's own name, and only when it names a party and then what is wanted from them:
            # a description that mentions a party, or "... with <name>'s office", is still the firm's task.
            waiting_on = self.named_contact(task.get("name"))
            if waiting_on and not ask_after_name(task.get("name") or "", self.patterns.get(waiting_on.id, [])):
                waiting_on = None
            if task.get("status") == "complete":
                bucket = c.AgendaBucket.done
            elif days is not None and days < 0:
                bucket = c.AgendaBucket.overdue  # past due is always overdue; `waiting_on_*` still says who owes it
            elif waiting_on:
                bucket = c.AgendaBucket.waiting
            else:
                bucket = c.AgendaBucket.coming
            item = c.AgendaItem(
                id=f"task:{task['id']}",
                kind="task",
                title=task.get("name") or "",
                detail=task.get("description"),
                due=task.get("due_at"),
                bucket=bucket,
                days_from_today=days,
                waiting_on_contact_id=waiting_on.id if waiting_on else None,
                waiting_on_name=waiting_on.name if waiting_on else None,
                assignee=(task.get("assignee") or {}).get("name"),
                is_limitations=bool(task.get("statute_of_limitations")),
                category=c.DisclosureCategory.asks if waiting_on else c.DisclosureCategory.internal,
                source=self.ref("task", task["id"], task.get("name"), task.get("due_at")),
            )
            getattr(agenda, item.bucket).append(item)
        for entry in self.calendar:
            day = rules.local_date(entry.get("start_at"))
            days = (day - self.today).days if day else None
            item = c.AgendaItem(
                id=f"calendar_entry:{entry['id']}",
                kind="calendar_entry",
                title=entry.get("summary") or "",
                detail=entry.get("description"),
                due=entry.get("start_at"),
                end=entry.get("end_at"),
                bucket=c.AgendaBucket.done if days is not None and days < 0 else c.AgendaBucket.coming,
                days_from_today=days,
                source=self.ref("calendar_entry", entry["id"], entry.get("summary"), entry.get("start_at")),
            )
            getattr(agenda, item.bucket).append(item)
        for bucket in (agenda.overdue, agenda.coming, agenda.waiting):
            bucket.sort(key=lambda i: (i.days_from_today is None, i.days_from_today or 0))
        agenda.done.sort(key=lambda i: (i.days_from_today is None, -(i.days_from_today or 0)))
        return agenda

    # -- timeline (Clio-dated part) ------------------------------------------

    def timeline(self) -> list[c.TimelineEvent]:
        events = []
        if self.matter.get("open_date"):
            created_here = self.matter.get("origin") == "upload"
            events.append(
                c.TimelineEvent(
                    id="matter:opened",
                    date=self.matter["open_date"],
                    # A case created here says so: the date is when the firm created it in this app.
                    date_source="firm" if created_here else "clio",
                    label="Case created" if created_here else "Matter opened",
                    kind="legal",
                    importance=2,
                    category=c.DisclosureCategory.status,
                    derivation=c.Derivation.computed if created_here else c.Derivation.clio,
                    sources=[self.matter_ref(self.matter["open_date"])],
                )
            )
        for entry in self.calendar:
            day = rules.local_date(entry.get("start_at"))
            if not day:
                continue
            provider = self.named_contact(entry.get("summary"), entry.get("location"), roles=("provider",))
            events.append(
                c.TimelineEvent(
                    id=f"calendar_entry:{entry['id']}",
                    date=day.isoformat(),
                    label=entry.get("summary") or "",
                    kind="treatment" if provider else "other",
                    contact_id=provider.id if provider else None,
                    category=c.DisclosureCategory.attendance if provider else c.DisclosureCategory.internal,
                    sources=[self.ref("calendar_entry", entry["id"], entry.get("summary"), entry.get("start_at"))],
                )
            )
        return sorted(events, key=lambda e: e.date)

    # -- what changed --------------------------------------------------------

    TITLE_KEYS = {
        "note": "subject",
        "communication": "subject",
        "task": "name",
        "calendar_entry": "summary",
        "document": "name",
        "expense": "note",
        "contact": "name",
        "relationship": "description",
    }

    def changes(self, since: str | None) -> c.Changes:
        changes = c.Changes(since=since)
        cutoff = rules.parse_instant(since)
        if cutoff is None:
            return changes
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

        def after(value: str | None) -> bool:
            moment = rules.parse_instant(value)
            if moment is None:
                return False
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment > cutoff

        for kind, key in self.TITLE_KEYS.items():
            for row in items(self.conn, self.matter_id, kind):
                if after(row.get("created_at")):
                    change, at = c.ChangeKind.new, row["created_at"]
                elif after(row.get("updated_at")):
                    change, at = c.ChangeKind.updated, row["updated_at"]
                else:
                    continue
                title = row.get(key) or kind.replace("_", " ")
                changes.items.append(
                    c.ChangeItem(change=change, at=at, title=title, source=self.ref(kind, row["id"], title, at))
                )
        for value in self.matter.get("custom_field_values") or []:
            if after(value.get("updated_at")):
                field_id = (value.get("custom_field") or {}).get("id") or value.get("id")
                changes.items.append(
                    c.ChangeItem(
                        change=c.ChangeKind.new if after(value.get("created_at")) else c.ChangeKind.updated,
                        at=value["updated_at"],
                        title=value.get("field_name") or "custom field",
                        source=self.ref("custom_field", field_id, value.get("field_name"), value["updated_at"]),
                    )
                )
        removed = self.conn.execute(
            "SELECT kind, clio_id, at FROM item_changes WHERE matter_id=? AND change='removed' AND at>?",
            (self.matter_id, cutoff.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchall()
        for row in removed:
            if row["kind"] in c.SourceKind._value2member_map_:
                label = f"{row['kind'].replace('_', ' ')} removed from the matter record"
                changes.items.append(
                    c.ChangeItem(
                        change=c.ChangeKind.removed,
                        at=row["at"],
                        title=label,
                        source=self.ref(row["kind"], row["clio_id"], label, row["at"]),
                    )
                )
        changes.items.sort(key=lambda i: i.at, reverse=True)
        for item in changes.items:
            changes.counts[item.source.kind] = changes.counts.get(item.source.kind, 0) + 1
        return changes

    # -- documents -----------------------------------------------------------

    def document_list(self) -> list[c.DocumentInfo]:
        blobs = {
            row["document_id"]: row
            for row in self.conn.execute(
                "SELECT document_id, size_bytes, page_count, text_pages FROM document_blobs WHERE matter_id=?",
                (self.matter_id,),
            )
        }
        out = []
        for document in self.documents:
            document_id = int(document["id"])
            blob = blobs.get(document_id)
            out.append(
                c.DocumentInfo(
                    id=document_id,
                    name=document.get("name") or document.get("filename") or "",
                    folder=(document.get("parent") or {}).get("name"),
                    received_at=document.get("received_at"),
                    size_bytes=blob["size_bytes"] if blob else document.get("size"),
                    page_count=blob["page_count"] if blob else None,
                    has_text_layer=bool(blob["text_pages"]) if blob and blob["text_pages"] is not None else None,
                    file_href=f"/api/matters/{self.matter_id}/documents/{document_id}/file" if blob else None,
                    source=self.ref("document", document_id, document.get("name"), document.get("received_at")),
                )
            )
        return out

    # -- providers -----------------------------------------------------------

    def providers(self, agenda: c.Agenda) -> list[c.ProviderPanel]:
        panels = []
        for contact in self.contacts:
            if contact.role != c.ContactRole.provider.value:
                continue
            asks = [
                c.Ask(
                    id=f"ask:{item.id}",
                    contact_id=contact.id,
                    # What is wanted, without the firm's own label and the provider's name in front of it.
                    text=ask_after_name(item.title, self.patterns.get(contact.id, [])) or item.title,
                    due=item.due,
                    overdue=item.days_from_today is not None and item.days_from_today < 0,
                    sources=[item.source],
                )
                for item in agenda.overdue + agenda.waiting
                if item.kind == "task" and item.waiting_on_contact_id == contact.id
            ]
            policy = self.policy(contact.id)
            panels.append(
                c.ProviderPanel(
                    contact=contact,
                    bills=self.charges_on_file(contact) or c.BillsStatus(),
                    attendance=self._attendance(contact),
                    asks=self._with_replies(asks),
                    last_contact=self._last_contact_with(contact.id, f"provider:{contact.id}:last_contact", "Last contact"),
                    policy=policy,
                    preview_href=f"/api/matters/{self.matter_id}/providers/{contact.id}/preview",
                    shares=self.share_log(contact.id),
                    requests=threads.requests(self.conn, self.matter_id, contact.id, list(policy.allowed_categories)),
                    thread=threads.messages(self.conn, self.matter_id, contact.id),
                )
            )
        return panels

    def _attendance(self, contact: c.Contact) -> c.Attendance:
        """Appointments on the firm's calendar whose title or location names this office.
        A description that mentions the office in passing does not count."""
        past, future, sources = [], [], []
        for entry in self.calendar:
            if not any(
                rules.mentions(text, self.patterns.get(contact.id, []))
                for text in (entry.get("summary"), entry.get("location"))
            ):
                continue
            day = rules.local_date(entry.get("start_at"))
            if not day:
                continue
            (past if day < self.today else future).append(day)
            sources.append(self.ref("calendar_entry", entry["id"], entry.get("summary"), entry.get("start_at")))
        attendance = c.Attendance(visits=len(past), sources=sources)
        if past:
            attendance.last_visit = max(past).isoformat()
            attendance.days_since_last_visit = (self.today - max(past)).days
            attendance.signal = "recorded"
        if future:
            attendance.next_visit = min(future).isoformat()
            attendance.signal = "scheduled"
        return attendance

    # -- sharing state (ours, never Clio's) ----------------------------------

    def policy(self, contact_id: int) -> c.SharePolicy:
        row = self.conn.execute(
            "SELECT policy, updated_at FROM share_policies WHERE matter_id=? AND contact_id=?",
            (self.matter_id, contact_id),
        ).fetchone()
        if row:
            return c.SharePolicy(**{**json.loads(row["policy"]), "contact_id": contact_id, "updated_at": row["updated_at"]})
        _, slots = self.custom_fields()
        return default_policy(contact_id, self.coverage_signal(slots).band)

    def share_log(self, contact_id: int) -> list[c.ShareLogEntry]:
        rows = self.conn.execute(
            "SELECT * FROM shares WHERE matter_id=? AND contact_id=? ORDER BY sent_at DESC",
            (self.matter_id, contact_id),
        ).fetchall()
        return [
            share_entry(row) for row in rows
        ]

    def _with_replies(self, asks: list[c.Ask]) -> list[c.Ask]:
        for ask in asks:
            row = self.conn.execute(
                "SELECT reply, replied_at FROM ask_replies WHERE matter_id=? AND ask_id=? ORDER BY replied_at DESC LIMIT 1",
                (self.matter_id, ask.id),
            ).fetchone()
            if row:
                ask.reply, ask.replied_at = row["reply"], row["replied_at"]
        return asks

    # -- assembly ------------------------------------------------------------

    def meta(self) -> c.Meta:
        run = self.conn.execute(
            "SELECT finished_at FROM sync_runs WHERE matter_id=? AND error IS NULL AND finished_at IS NOT NULL"
            " ORDER BY id DESC LIMIT 1",
            (self.matter_id,),
        ).fetchone()
        return c.Meta(
            matter_id=self.matter_id,
            generated_at=now_iso(),
            synced_at=run["finished_at"] if run else None,
            clio_counts=counts(self.conn, self.matter_id),
        )

    def build(self, since: str | None = None) -> c.CaseModel:
        fields, slots = self.custom_fields()
        spend = self.spend()
        agenda = self.agenda()
        brief = self.brief(slots, spend)
        panels = self.providers(agenda)
        nodes = self.nodes(slots, brief, spend) + river_v2.tributaries(self, slots, fields, brief, panels)
        client = self.contact_by_id.get(self.client_id) if self.client_id else None
        return c.CaseModel(
            meta=self.meta(),
            matter=c.Matter(
                id=int(self.matter["id"]),
                display_number=self.matter.get("display_number"),
                description=self.matter.get("description"),
                status=self.matter.get("status"),
                practice_area=(self.matter.get("practice_area") or {}).get("name"),
                open_date=self.matter.get("open_date"),
                client=client,
                responsible_attorney=(self.matter.get("responsible_attorney") or {}).get("name"),
                source=self.matter_ref(),
            ),
            brief=brief,
            custom_fields=fields,
            contacts=self.contacts,
            nodes=nodes,
            river=self.river(nodes),
            changes=self.changes(since),
            agenda=agenda,
            timeline=self.timeline(),
            spend=spend,
            documents=self.document_list(),
            providers=panels,
        )


def default_policy(contact_id: int, band: str | None) -> c.SharePolicy:
    """What a provider is shown before the attorney has touched anything.

    On: status, bills, records, asks. Off: attendance (the client's information;
    the attorney switches it on deliberately). Coverage: on when the band is
    confirmed or being_confirmed; off when it is not_established, because telling
    a provider that no coverage exists can change how the patient is treated."""
    allowed = list(DEFAULT_SHARED)
    if band in (c.CoverageBand.confirmed.value, c.CoverageBand.being_confirmed.value):
        allowed.append(c.DisclosureCategory.coverage)
    return c.SharePolicy(contact_id=contact_id, allowed_categories=allowed)


def share_entry(row: sqlite3.Row) -> c.ShareLogEntry:
    state = "active"
    if ":superseded:" in row["token"]:
        state = "superseded"
    elif row["revoked_at"]:
        state = "revoked"
    elif row["expires_at"] and row["expires_at"] <= now_iso():
        state = "expired"
    base = f"/api/matters/{row['matter_id']}/shares/{row['id']}"
    return c.ShareLogEntry(
        id=str(row["id"]),
        contact_id=row["contact_id"],
        link_href=f"/provider.html?token={row['token']}",
        sent_at=row["sent_at"],
        first_opened_at=row["first_opened_at"],
        last_opened_at=row["last_opened_at"],
        open_count=row["open_count"],
        categories=json.loads(row["categories"]),
        item_count=row["item_count"],
        state=state,
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        snapshot_href=f"{base}/snapshot",
        revoke_href=f"{base}/revoke",
    )


def build_case(conn: sqlite3.Connection, matter_id: int, since: str | None = None, today: date | None = None) -> c.CaseModel:
    return CaseBuilder(conn, matter_id, today).build(since)
