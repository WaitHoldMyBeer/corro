"""River v2: where every dollar of the case value comes from, which coverage can
pay it, and whether the sums add up. All of it arithmetic over Clio's own
fields and expense entries; no model. An amount that is not in the matter stays
null, and a difference is shown as its own named band instead of being hidden.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from shared import contract as c

from . import rules

if TYPE_CHECKING:
    from .case import CaseBuilder

SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\n")


def _dec(amount: float | None) -> Decimal | None:
    return Decimal(str(amount)) if amount is not None else None


def _first_sentence(text: str | None) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    match = re.match(r".+?[.!?](?=\s|$)(?<!\d\.)", text, re.S)
    return (match.group(0) if match else text.split("\n")[0]).strip()[:240]


def tributaries(
    build: "CaseBuilder", slots: dict[str, c.Fact], fields: list[c.Fact], brief: c.Brief, panels: list[c.ProviderPanel]
) -> list[c.ValueNode]:
    """Children of the value node (what the case is worth and why) and of the
    coverage node (what can pay), each with what it rests on."""
    out: list[c.ValueNode] = []
    value = _dec(brief.case_value.amount) if brief.case_value else None
    itemised = Decimal("0")

    # Medical charges: the stated total, and one band per provider from the charges entered in Clio.
    stated = slots.get("specials")
    charges = [(panel.contact, panel.bills) for panel in panels if panel.bills.billed_total is not None]
    charged = sum((_dec(bills.billed_total) for _, bills in charges), Decimal("0"))
    medical = _dec(stated.amount) if stated and stated.amount is not None else (charged if charges else None)
    if medical is not None:
        itemised += medical
        out.append(
            c.ValueNode(
                id="medical",
                kind="economic",
                parent_id="value",
                label=stated.label if stated else "Medical charges",
                amount=float(medical),
                basis=f"Imported field “{stated.label}”" if stated else "Sum of the providers' charges entered on the matter record",
                status=c.FactStatus.assumed,
                derivation=c.Derivation.clio if stated else c.Derivation.computed,
                sources=stated.sources if stated else [],
            )
        )
        for contact, bills in charges:
            entries = "entry" if bills.line_count == 1 else "entries"
            out.append(
                c.ValueNode(
                    id=f"medical:{contact.id}",
                    kind="economic",
                    parent_id="medical",
                    label=contact.name,
                    amount=bills.billed_total,
                    basis=f"{bills.line_count} charge {entries} on the matter record",
                    status=c.FactStatus.assumed,
                    derivation=c.Derivation.computed,
                    sources=bills.sources,
                    owed_by_contact_id=contact.id,
                )
            )
        if charges and medical - charged > 0:
            out.append(
                c.ValueNode(
                    id="medical:unitemised",
                    kind="economic",
                    parent_id="medical",
                    label="Stated medical total with no provider charge behind it",
                    amount=float(medical - charged),
                    basis="Stated total less the charges entered per provider",
                    status=c.FactStatus.assumed,
                    derivation=c.Derivation.computed,
                )
            )

    # Lost income, from the firm's own field.
    wage = slots.get("wage_loss")
    if wage:
        figures = [_dec(wage.amount)] if wage.amount is not None else rules.money_amounts(wage.display)
        if figures:
            itemised += figures[0]
            out.append(
                c.ValueNode(
                    id="wage",
                    kind="economic",
                    parent_id="value",
                    label=wage.label,
                    amount=float(figures[0]),
                    basis=_first_sentence(wage.detail or wage.display),
                    status=c.FactStatus.assumed,
                    derivation=c.Derivation.computed,
                    sources=wage.sources,
                )
            )

    # Whatever the stated value has beyond the itemised components is named, not hidden.
    if value is not None and out and value - itemised > 0:
        out.append(
            c.ValueNode(
                id="value:unitemised",
                kind="economic",
                parent_id="value",
                label="Firm's estimate beyond the itemised components",
                amount=float(value - itemised),
                basis="Case value less the itemised components; nothing itemised behind it",
                status=c.FactStatus.assumed,
                derivation=c.Derivation.computed,
                sources=brief.case_value.sources if brief.case_value else [],
            )
        )

    out += coverage_layers(build, slots, fields, brief)
    return out


def coverage_layers(build: "CaseBuilder", slots: dict[str, c.Fact], fields: list[c.Fact], brief: c.Brief) -> list[c.ValueNode]:
    """One band per line of the firm's coverage field that carries a figure. The
    first is the one the gate uses; the others are shown with why they add
    nothing, in the firm's own words where one of its fields says so."""
    stated = slots.get("coverage")
    if not stated:
        return []
    lines = [line.strip() for line in (stated.detail or stated.display or "").splitlines() if rules.money_amounts(line)]
    layers: list[c.ValueNode] = []
    primary: Decimal | None = None
    for index, line in enumerate(lines):
        amount = rules.money_amounts(line)[0]
        label = line.split(":", 1)[0].strip() if ":" in line else line[:60]
        counted = index == 0
        sources = list(stated.sources)
        if counted:
            primary = amount
            basis = f"First layer listed in the imported field “{stated.label}”: “{line}”. The gate uses this figure."
            status = brief.coverage.status if brief.coverage else c.FactStatus.assumed
        else:
            said = _said_elsewhere(label, amount, stated, fields)
            if said:
                basis = f"Shown, not counted. The firm's field “{said[0].label}” says: “{said[1]}”"
                sources += said[0].sources
            elif primary is not None and amount <= primary:
                basis = (
                    "Shown, not counted: the gate uses the first layer only, and this figure is at or below it"
                    f" ({rules.usd(primary)})"
                )
            else:
                basis = "Shown, not counted: the gate uses the first layer only; nothing in the matter says this one can be reached"
            status = c.FactStatus.assumed
        layers.append(
            c.ValueNode(
                id=f"coverage:{index}",
                kind="coverage",
                parent_id="coverage",
                label=label,
                amount=float(amount),
                basis=basis,
                payer=label,
                counted=counted,
                status=status,
                derivation=c.Derivation.computed,
                sources=sources,
            )
        )
    return layers


def _said_elsewhere(label: str, amount: Decimal, own: c.Fact, fields: list[c.Fact]) -> tuple[c.Fact, str] | None:
    """A sentence in another of the firm's custom fields that names this layer in
    full and repeats its figure, quoted verbatim. Anything looser is not used."""
    for fact in fields:
        if fact.label == own.label:
            continue
        for sentence in SENTENCE_BREAK.split(fact.detail or fact.display or ""):
            if label.lower() in sentence.lower() and amount in rules.money_amounts(sentence):
                return fact, sentence.strip()[:300]
    return None


def annotate(river: c.River, nodes: list[c.ValueNode]) -> c.River:
    """Sum checks, the evidence-backed share and the reading line, from the nodes."""
    by_id = {node.id: node for node in nodes}
    children: dict[str, list[c.ValueNode]] = {}
    for node in nodes:
        if node.parent_id:
            children.setdefault(node.parent_id, []).append(node)

    def total(parent: str) -> Decimal:
        return sum((_dec(n.amount) for n in children.get(parent, []) if n.amount is not None and n.counted), Decimal("0"))

    for parent, label in (("medical", "Provider charges add up to the stated medical total"),
                          ("value", "Components add up to the stated case value")):
        node = by_id.get(parent)
        if node is None or node.amount is None or not children.get(parent):
            continue
        actual, expected = total(parent), _dec(node.amount)
        river.checks.append(
            c.RiverCheck(
                id=f"sum:{parent}",
                label=label,
                ok=actual == expected,
                expected=float(expected),
                actual=float(actual),
                difference=float(expected - actual),
                node_ids=[parent] + [n.id for n in children[parent]],
            )
        )
    for stage in river.stages:
        if stage.inflow is not None and stage.diverted is not None and stage.id != "value" and stage.diverted > stage.inflow:
            river.checks.append(
                c.RiverCheck(
                    id=f"exceeds:{stage.id}",
                    label=f"{stage.label} is larger than what reaches it",
                    ok=False,
                    expected=stage.inflow,
                    actual=stage.diverted,
                    difference=round(stage.diverted - stage.inflow, 2),
                    node_ids=[stage.node_id or stage.id],
                )
            )

    # Evidence-backed: the leaves under value whose status is confirmed (a document is on file for them).
    def leaves(parent: str) -> list[c.ValueNode]:
        out: list[c.ValueNode] = []
        for node in children.get(parent, []):
            out += leaves(node.id) if children.get(node.id) else [node]
        return out

    value = by_id.get("value")
    value_leaves = leaves("value")
    if value is not None and value.amount and value_leaves:
        backed = sum((_dec(n.amount) for n in value_leaves if n.amount is not None and n.status == c.FactStatus.confirmed.value), Decimal("0"))
        river.evidence_backed_amount = float(backed)
        river.evidence_backed_share = round(float(backed / _dec(value.amount)), 4)

    river.reading = _reading(river, by_id, children)
    return river


def _reading(river: c.River, by_id: dict[str, c.ValueNode], children: dict[str, list[c.ValueNode]]) -> str | None:
    """One paragraph built from a template. Every figure in it is one of the river's own."""
    value = by_id.get("value")
    if value is None or value.amount is None:
        return None
    usd = rules.usd
    parts = []
    components = [n for n in children.get("value", []) if n.amount is not None]
    named = [n for n in components if n.id != "value:unitemised"]
    remainder = by_id.get("value:unitemised")
    if named:
        listed = ", ".join(f"{usd(n.amount)} {n.label.lower()}" for n in named)
        sentence = f"{usd(value.amount)} estimated by the firm: {listed}"
        if remainder is not None and remainder.amount:
            sentence += f", and {usd(remainder.amount)} that is the firm's estimate with nothing itemised behind it"
        parts.append(sentence + ".")
    else:
        parts.append(f"{usd(value.amount)} estimated by the firm, with no components itemised in the matter.")
    providers = [n for n in children.get("medical", []) if n.owed_by_contact_id is not None]
    if providers:
        parts.append(f"The medical figure is spread over {len(providers)} providers.")
    if river.evidence_backed_amount is not None:
        parts.append(f"{usd(river.evidence_backed_amount)} of the total has a document on file behind it.")
    gate = by_id.get("gate")
    if gate is not None and gate.amount is not None:
        through = Decimal(str(value.amount)) - Decimal(str(gate.amount))
        parts.append(f"{usd(float(through))} of coverage is confirmed, so {usd(gate.amount)} is held back.")
    else:
        parts.append("No confirmed coverage figure is in the matter, so nothing is shown as held back.")
    taken = [(by_id[i].amount, word) for i, word in (("lien", "in liens"), ("cost", "in costs")) if by_id.get(i) and by_id[i].amount is not None]
    if river.net is not None:
        after = " and ".join(f"{usd(amount)} {word}" for amount, word in taken)
        fee = by_id.get("fee")
        tail = "before the firm's fee, which is not set" if fee is None or fee.amount is None else f"after a fee of {usd(fee.amount)}"
        parts.append(f"{'After ' + after + ', ' if after else ''}{usd(river.net)} is left {tail}.")
    return " ".join(parts)
