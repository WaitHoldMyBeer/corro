"""Builds the Analysis from the case model, the attorney's inputs and the stored
evidence summaries. Every sentence is a template filled with figures computed in
`model.py`; nothing here names a party, a topic or an amount of its own.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import product
from typing import Any

from shared import contract as c

from .. import rules
from ..db import now_iso
from . import model as m

D = Decimal
MAX_CARDS_PER_FACT = 12
MAX_SOURCES = 6
PATH_ROUNDS = 3  # how many concessions the path lists; the size of each comes from the attorney's ratio

GATE_NODES = {"gate", "coverage"}


def _under(case: c.CaseModel, root: str) -> set[str]:
    """Ids of `root` and every node below it."""
    out, grew = {root}, True
    while grew:
        more = {node.id for node in case.nodes if node.parent_id in out} - out
        out |= more
        grew = bool(more)
    return out


def _dedupe(refs: list[c.SourceRef], limit: int = MAX_SOURCES) -> list[c.SourceRef]:
    seen, out = set(), []
    for ref in refs:
        key = (ref.kind, ref.clio_id, ref.page)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out[:limit]


def _cards(case: c.CaseModel, node_ids: set[str], claims: dict[str, c.Claim]) -> list[m.CardRef]:
    """Review cards that bear on these nodes and have not been dismissed, in the order the file ranks them."""
    out = []
    for card in sorted(case.conflicts, key=lambda k: k.rank or 10**6):
        if card.review == "dismissed" or not node_ids & set(card.node_ids):
            continue
        ids = card.document_claim_ids + card.notes_claim_ids
        out.append(
            m.CardRef(
                id=card.id, topic=card.topic, kind_label=card.kind_label, review=card.review,
                sources=_dedupe([claims[i].source for i in ids if i in claims], 4),
            )
        )
    return out[:MAX_CARDS_PER_FACT]


def card_claim_ids(case: c.CaseModel, fact: m.OpenFact) -> list[str]:
    wanted = {card.id for card in fact.cards}
    return [i for card in case.conflicts if card.id in wanted for i in card.notes_claim_ids + card.document_claim_ids]


def build(case: c.CaseModel, inputs: m.Inputs, fee_basis: str, evidence: dict[str, Any] | None = None) -> m.Analysis:
    base_href = f"/api/matters/{case.meta.matter_id}/negotiation"
    specs = [
        m.InputSpec(key=key, label=label, unit=unit, max=top, step=step, value=getattr(inputs, key), when_unset=unset)
        for key, label, unit, top, step, unset in m.INPUT_SPECS
    ]
    out = m.Analysis(
        matter_id=case.meta.matter_id, generated_at=now_iso(), ready=False, inputs=specs,
        inputs_href=f"{base_href}/inputs", evidence_href=f"{base_href}/evidence",
    )
    terms = m.read_terms(case, fee_basis)
    if terms is None:
        out.reason = "The matter holds no estimate of the case's value, so there is nothing to bargain over yet."
        return out
    out.ready = True
    s = m.read_inputs(inputs)
    by_id = {node.id: node for node in case.nodes}
    claims = {claim.id: claim for claim in case.claims}
    usd = m._money
    spans = m.lien_span(terms)  # one entry when the lien total is in the file, the two ends of its span when it is not
    lien_at = lambda q, share: m.unknown_lien(terms, s, q, share)  # noqa: E731

    # ---- one row per coverage scenario
    rows = m.scenarios_of(terms)
    for sid, label, basis, ceiling, status, sources in rows:
        corners = [m.zone(terms, s, ceiling, p, q, w, lien_at(q, share)) for p, q, w, share in product(s.p, s.q, s.w, spans)]
        walk, top, target = (m.sweep(corner[i] for corner in corners) for i in range(3))
        opens = [corner[1] >= corner[0] for corner in corners]
        scenario = m.Scenario(
            id=sid, label=label, basis=basis, ceiling=float(ceiling), ceiling_display=usd(ceiling), status=status,
            walk_away=walk, top=top, target=target, opening=float(ceiling), opening_display=usd(ceiling),
            zone="open" if all(opens) else "partial" if any(opens) else "none", sources=_dedupe(sources),
        )
        if s.ratio is not None:
            for k in range(1, PATH_ROUNDS + 1):
                scenario.path.append(m.sweep(max(c_[2] + (ceiling - c_[2]) * s.ratio**k, c_[0]) for c_ in corners))
        if s.offer is not None:
            middle = (ceiling + s.offer) / 2
            side = "at or above" if D(str(target.lo)) <= middle else "below"
            scenario.midpoint_check = (
                f"The midpoint of an opening at {usd(ceiling)} and the offer of {usd(s.offer)} is {usd(middle)},"
                f" {side} the low end of the target."
            )
        out.scenarios.append(scenario)
    out.scale_max = float(terms.value)
    first = out.scenarios[0]
    base = D(str(first.ceiling))

    # ---- the decision tree: expected net from trying the case
    tree = lambda p, q, g, liens=None: m.tree_net(terms, s, base, p, q, g, liens)  # noqa: E731
    out.expected_net = m.sweep(tree(p, q, g, lien_at(q, share)) for p, q, g, share in product(s.p, s.q, s.g, spans))
    out.walk_away_weighted = m.sweep(
        terms.gross_for(tree(p, q, g, lien_at(q, share)), lien_at(q, share)) for p, q, g, share in product(s.p, s.q, s.g, spans)
    )

    # ---- what moves the number: the swing of each open fact, across the span of every unset input
    top_p = [max(s.p)]  # the per-scenario note is taken where the client prevails, so it is not hidden by p = 0

    def fact(fid: str, label: str, nodes: set[str], swing, per_scenario, sources: list[c.SourceRef]) -> m.OpenFact:
        by_scenario = {sid: per_scenario(ceiling) for sid, _, _, ceiling, _, _ in rows} if per_scenario else {}
        quiet = [sc.label.lower() for sc in out.scenarios if sc.id in by_scenario and by_scenario[sc.id].hi == 0]
        note = f"Moves the client's expected net by up to {usd(D(str(swing.hi)))}."
        if swing.exact:
            note = f"Moves the client's expected net by {swing.display}."
        if quiet and swing.hi > 0:
            note += f" Moves nothing under “{quiet[0]}”."
        cards = _cards(case, nodes, claims)
        return m.OpenFact(
            id=fid, label=label, node_ids=sorted(nodes & set(by_id)), swing=swing, swing_note=note, by_scenario=by_scenario,
            cards=cards, sources=_dedupe(sources + [ref for card in cards for ref in card.sources]),
        )

    facts: list[m.OpenFact] = []
    if base < terms.value:
        coverage = by_id.get("coverage")
        facts.append(fact(
            "gate", "Whether recovery is limited to the counted coverage", GATE_NODES | _under(case, "coverage"),
            m.sweep(tree(p, q, m.ONE) - tree(p, q, m.ZERO) for p, q in product(s.p, s.q)),
            None,  # the scenarios are the two answers to this question, so there is no per-scenario swing
            coverage.sources if coverage else [],
        ))
    if terms.evidenced < terms.value:
        facts.append(fact(
            "value", "How much of the estimate beyond the documented components holds up", _under(case, "value"),
            m.sweep(tree(p, m.ONE, g) - tree(p, m.ZERO, g) for p, g in product(s.p, s.g)),
            lambda ceiling: m.sweep(
                m.trial_net(terms, s, ceiling, p, m.ONE) - m.trial_net(terms, s, ceiling, p, m.ZERO) for p in top_p
            ),
            by_id["value"].sources,
        ))
    lien = by_id.get("lien")
    if not terms.liens_known:
        facts.append(fact(
            "lien", "What the liens come to: no total is in the file", {"lien"},
            m.sweep(tree(p, q, g, m.ZERO) - tree(p, q, g, lien_at(q, m.ONE)) for p, q, g in product(s.p, s.q, s.g)),
            None, lien.sources if lien else [],
        ))
    elif lien is not None and terms.liens > 0 and lien.status != c.FactStatus.confirmed.value:
        facts.append(fact(
            "lien", "Whether the lien figure stands as stated", {"lien"},
            m.sweep(tree(p, q, g, m.ZERO) - tree(p, q, g) for p, q, g in product(s.p, s.q, s.g)),
            lambda ceiling: m.sweep(
                m.trial_net(terms, s, ceiling, p, q, m.ZERO) - m.trial_net(terms, s, ceiling, p, q) for p, q in product(top_p, s.q)
            ),
            lien.sources,
        ))
    facts.sort(key=lambda f: (-f.swing.hi, -f.swing.lo))
    for item in facts:
        stored = (evidence or {}).get(item.id) or {}
        for side in ("higher", "lower"):
            ids = [i for i in (stored.get(side) or {}).get("claim_ids", []) if i in claims]
            if ids and (stored[side].get("text") or "").strip():
                setattr(item, side, m.EvidenceSide(
                    text=stored[side]["text"].strip(), claim_ids=ids, sources=_dedupe([claims[i].source for i in ids]),
                ))
    out.open_facts = facts
    with_cards = [f for f in facts if f.cards]
    out.evidence_state = "none" if not with_cards else "ready" if evidence is not None else "missing"

    # ---- the three figures, for the first scenario, each with what it was computed from
    value_node, cost_node = by_id["value"], by_id.get("cost")
    fee_line = m.TraceLine(
        label="Attorney fee", origin="setting",
        display=f"{rules.usd(terms.fee * 100)[1:]}% of the recovery{' after costs' if terms.fee_after_costs else ''}" if terms.fee is not None else "not set: figures are before fee",
    )
    money_lines = [
        m.TraceLine(label=value_node.label, display=usd(terms.value), amount=float(terms.value), origin="file", sources=value_node.sources),
        m.TraceLine(label="Components with a document behind them", display=usd(terms.evidenced), amount=float(terms.evidenced), origin="computed"),
        m.TraceLine(label=lien.label if lien else "Liens", origin="file", sources=lien.sources if lien else [],
                    display=usd(terms.liens) if terms.liens_known else "no total in the file: swept from nothing to the whole net",
                    amount=float(terms.liens) if terms.liens_known else None),
        m.TraceLine(label=cost_node.label if cost_node else "Costs", origin="file", sources=cost_node.sources if cost_node else [],
                    display=usd(terms.costs) if terms.costs_known else "none recorded on the matter: left out",
                    amount=float(terms.costs) if terms.costs_known else None),
        fee_line,
    ]
    set_lines = [
        m.TraceLine(label=spec.label, origin="input",
                    display="not set" if spec.value is None else f"{spec.value:g}%" if spec.unit == "percent" else f"{spec.value:g} months" if spec.unit == "months" else rules.usd(spec.value))
        for spec in specs
    ]
    exact = lambda r: "set" if r.exact else "range"  # noqa: E731
    out.figures = [
        m.Figure(
            id="walk_away", label="Walk-away", value=first.walk_away, status=exact(first.walk_away),
            formula="The gross settlement at which the client nets the same as the expected result of trying the case,"
                    " after liens, costs and fee. Never below the figure at which the client nets nothing.",
            trace=money_lines + set_lines,
        ),
        m.Figure(
            id="target", label="Target", value=first.target, status=exact(first.target) if first.zone != "none" else "no zone",
            formula="Walk-away plus the client's share of the zone. The share is the other side's cost of delay divided by"
                    " the sum of both sides' costs of delay.",
            trace=[line for line in set_lines if "delay" in line.label] + money_lines[:1],
        ),
        m.Figure(
            id="ceiling", label="Ceiling", value=m._range(base, base), status=str(first.status),
            formula=f"{first.label}. {first.basis}",
            trace=[m.TraceLine(label=layer.label, display=usd(layer.amount), amount=float(layer.amount), origin="file", sources=layer.sources)
                   for layer in terms.layers if layer.counted],
        ),
    ]

    # ---- notes on what is missing, and the plan
    if not terms.liens_known:
        out.notes.append("No lien total is in the file. It is not counted as zero: every net figure runs from no lien to a lien that takes the whole net.")
    if not terms.costs_known:
        out.notes.append("No costs are recorded on the matter, so none are deducted; the net figures are higher than they will be if costs exist.")
    if terms.fee is None:
        out.notes.append("The fee is not set in Settings, so every net figure is before fee and the walk-away figure is lower than it will be.")
    out.notes += [f"{spec.label}: not set. {spec.when_unset}" for spec in specs if spec.value is None]
    out.plan = _plan(out, terms, s, specs, _dedupe([ref for line in money_lines for ref in line.sources]))
    return out


def _plan(out: m.Analysis, terms: m.Terms, s: m.Setting, specs: list[m.InputSpec], money_sources: list[c.SourceRef]) -> list[m.PlanStep]:
    usd = m._money
    first = out.scenarios[0]
    steps: list[m.PlanStep] = []
    if out.open_facts:
        lead = out.open_facts[0]
        count = f" {len(lead.cards)} review card{'s' if len(lead.cards) != 1 else ''} bear on it." if lead.cards else ""
        steps.append(m.PlanStep(
            title="The largest open question",
            text=f"{lead.label}. {lead.swing_note}{count} The figures below depend on it more than on anything else in the file.",
            sources=lead.sources,
        ))
        waiting = [f for f in out.open_facts[1:] if f.by_scenario.get(first.id) and f.by_scenario[first.id].hi == 0 and f.swing.hi > 0]
        for item in waiting[:1]:
            steps.append(m.PlanStep(
                title="Moves nothing until that is answered",
                text=f"{item.label}. While recovery stays within “{first.label.lower()}” it moves nothing, because the"
                     f" documented components ({usd(terms.evidenced)}) already reach the ceiling ({first.ceiling_display})."
                     " It matters only if the first question is answered the other way.",
                sources=item.sources,
            ))
    unset = [spec.label for spec in specs if spec.value is None and spec.key in ("p_win", "value_share", "rate_client", "rate_other")]
    if unset or terms.fee is None:
        missing = (["the fee (in Settings)"] if terms.fee is None else []) + [label[0].lower() + label[1:] for label in unset]
        steps.append(m.PlanStep(
            title="Assumptions not yet set",
            text="The figures are ranges until these are set under Assumptions: " + "; ".join(missing) + ".",
        ))
    steps.append(m.PlanStep(
        title="Opening the file supports",
        text=f"Under “{first.label.lower()}” the highest figure the file gives a basis for is {first.opening_display}; the analysis takes that as the opening."
             + (f" {first.midpoint_check}" if first.midpoint_check else ""),
        sources=first.sources,
    ))
    if first.zone == "none":
        steps.append(m.PlanStep(
            title="No zone under these assumptions",
            text="Under the assumptions set, the expected net from trying the case is higher than the most this scenario can pay"
                 f" ({first.top.display}). The zone opens only if the coverage question is answered the other way or the liens come down.",
        ))
    else:
        path = ", then ".join(step.display for step in first.path)
        steps.append(m.PlanStep(
            title="Path from the opening to the target",
            text=f"Target: {first.target.display}. "
                 + (f"At the ratio set, the path runs {path}: each step smaller than the one before, and none below the walk-away."
                    if path else "The path runs from the opening to the target in steps that each get smaller; it has figures once the ratio is set under Assumptions."),
        ))
    steps.append(m.PlanStep(
        title="Walk-away under these assumptions",
        text=f"{first.walk_away.display} gross under “{first.label.lower()}”. At that figure the client nets what trying the case"
             " is expected to net under the assumptions set; below it, the analysis shows trying the case as the better expected result."
             + (f" Across the coverage scenarios, weighted by the chance you set, trying the case is expected to net"
                f" {out.expected_net.display}, which is a walk-away of {out.walk_away_weighted.display} gross."
                if out.expected_net and out.walk_away_weighted and len(s.g) == 1 else ""),
        sources=money_sources,
    ))
    return steps
