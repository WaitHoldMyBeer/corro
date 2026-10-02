"""The bargaining arithmetic. No model call, no figure that is not either in the
case model or set by the attorney.

From the case model: the firm's estimate and the part of it with a document
behind it, each coverage layer, liens, costs, the fee setting, and the review
cards with the node each one bears on. From the attorney: the probabilities,
rates and costs the file does not hold. An input that has not been set is never
replaced with a guess: a probability is swept across its whole span (0 to 1) and
every figure that depends on it is returned as a range; a cost or a rate that
has not been set is left out, and the response says so and in which direction
that moves the result.

Terms (see docs/NEGOTIATION.md for the sources):

    J(q)      = E + q * (V - E)            judgment if the client prevails
    net(x)    = x - fee(x) - liens - costs client's net from a gross recovery x
    W(s)      = d * p * net(min(J, ceiling_s) ; costs + further cost to try)
                                           expected net from trying the case
    walk(s)   = the gross x at which net(x) = W(s)
    top(s)    = min(ceiling_s, p * min(J, ceiling_s) + other side's cost to try)
    target(s) = walk + w * (top - walk),   w = r_other / (r_client + r_other)

`w` is the limit of the alternating-offers split as the time between offers goes
to zero. With either rate unset, w is swept from 0 to 1, so the target is the
whole zone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from shared import contract as c

from .. import rules

D = Decimal
ZERO, ONE, CENT = D(0), D(1), D("0.01")

# The largest dollar input accepted: a bound on the field, so one absurd entry cannot break the arithmetic.
MAX_USD = 1e12

STANDING_LINE = "An analysis of this file under the assumptions you set. Not legal advice and not a prediction."


# --------------------------------------------------------------------------- inputs the attorney sets


class Inputs(BaseModel):
    """Everything the file does not hold. Null means not set; nothing here has a default."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    p_win: float | None = Field(None, ge=0, le=100, description="Percent.")
    value_share: float | None = Field(None, ge=0, le=100, description="Percent.")
    p_gate: float | None = Field(None, ge=0, le=100, description="Percent.")
    rate_client: float | None = Field(None, ge=0, le=100, description="Percent per year.")
    rate_other: float | None = Field(None, ge=0, le=100, description="Percent per year.")
    months_to_trial: float | None = Field(None, ge=0, le=120)
    cost_client: float | None = Field(None, ge=0, le=MAX_USD, description="USD.")
    cost_other: float | None = Field(None, ge=0, le=MAX_USD, description="USD.")
    offer: float | None = Field(None, ge=0, le=MAX_USD, description="USD.")
    concession_ratio: float | None = Field(None, gt=0, lt=100, description="Percent.")


# key, label, unit, slider max, slider step, what an unset value does to the result
INPUT_SPECS: tuple[tuple[str, str, str, float | None, float, str], ...] = (
    ("p_win", "Chance of a finding for the client if the case is tried", "percent", 100, 1,
     "Swept from 0 to 100%: every figure that depends on it is a range."),
    ("value_share", "Share of the estimate with no document behind it that holds up", "percent", 100, 1,
     "Swept from 0 to 100%: the judgment runs from the documented components to the firm's full estimate."),
    ("p_gate", "Chance that recovery is not limited to the counted coverage", "percent", 100, 1,
     "Swept from 0 to 100%: the coverage scenarios are shown side by side and not weighted."),
    ("rate_client", "Client's cost of delay, per year", "percent", 100, 1,
     "The split of the zone is swept end to end, and the wait for trial is not discounted."),
    ("rate_other", "Other side's cost of delay, per year", "percent", 100, 1,
     "The split of the zone is swept end to end."),
    ("months_to_trial", "Months until a trial would be heard", "months", 120, 1,
     "The wait is not discounted, which makes the walk-away figure higher than it would be."),
    ("cost_client", "Further cost to try the case, client's side", "usd", None, 500,
     "Left out, which makes the walk-away figure higher than it would be."),
    ("cost_other", "Further cost to try the case, other side", "usd", None, 500,
     "Left out: the top of the zone is then the coverage ceiling alone."),
    ("offer", "Latest offer from the other side, if one has been made", "usd", None, 500,
     "The midpoint check against the opening is not run."),
    ("concession_ratio", "Each concession as a share of the one before it", "percent", 99, 1,
     "The concession path is given in words, without figures."),
)


class InputSpec(BaseModel):
    key: str
    label: str
    unit: str = Field(description="percent | usd | months")
    max: float | None = None
    step: float = 1
    value: float | None = None
    when_unset: str


# --------------------------------------------------------------------------- what the endpoint returns


class Range(BaseModel):
    """A figure, or the span it covers while an input is unset. USD. `lo == hi` when it is one number."""

    lo: float
    hi: float
    display: str
    exact: bool


class TraceLine(BaseModel):
    """One input a figure was computed from, with the place in the file it came from."""

    label: str
    display: str
    amount: float | None = None
    origin: str = Field(description="file | setting | input | computed")
    sources: list[c.SourceRef] = Field(default_factory=list)


class Figure(BaseModel):
    id: str
    label: str
    value: Range | None = None
    status: str = Field(description="One word for the figure's standing.")
    formula: str
    trace: list[TraceLine] = Field(default_factory=list)


class Scenario(BaseModel):
    """One coverage scenario: what could pay, and the zone of agreement under it."""

    id: str
    label: str
    basis: str
    ceiling: float
    ceiling_display: str
    status: str
    walk_away: Range
    top: Range
    target: Range
    opening: float
    opening_display: str
    zone: str = Field(description="open | none | partial: whether the walk-away figure sits below the top of the zone.")
    path: list[Range] = Field(default_factory=list, description="Positions after each concession, when a ratio is set.")
    midpoint_check: str | None = None
    sources: list[c.SourceRef] = Field(default_factory=list)


class CardRef(BaseModel):
    id: str
    topic: str
    kind_label: str = ""
    review: str = "unreviewed"
    sources: list[c.SourceRef] = Field(default_factory=list)


class EvidenceSide(BaseModel):
    text: str
    claim_ids: list[str] = Field(default_factory=list)
    sources: list[c.SourceRef] = Field(default_factory=list)


class OpenFact(BaseModel):
    """Something the file leaves open, and how far the expected outcome moves with it."""

    id: str
    label: str
    node_ids: list[str] = Field(default_factory=list)
    swing: Range
    swing_note: str
    by_scenario: dict[str, Range] = Field(default_factory=dict)
    cards: list[CardRef] = Field(default_factory=list)
    sources: list[c.SourceRef] = Field(default_factory=list)
    higher: EvidenceSide | None = Field(None, description="What in the file supports the higher outcome.")
    lower: EvidenceSide | None = Field(None, description="What in the file supports the lower outcome.")


class PlanStep(BaseModel):
    title: str
    text: str
    sources: list[c.SourceRef] = Field(default_factory=list)


class Analysis(BaseModel):
    """Response of GET /api/matters/{matter_id}/negotiation. Firm side only."""

    matter_id: int
    generated_at: str
    category: str = c.DisclosureCategory.strategy.value
    standing_line: str = STANDING_LINE
    ready: bool = Field(description="False when the matter holds no value estimate to work from.")
    reason: str | None = None
    figures: list[Figure] = Field(default_factory=list, description="walk_away, target, ceiling: for the first scenario.")
    scenarios: list[Scenario] = Field(default_factory=list)
    scale_max: float = Field(0, description="The largest figure on the bars, for the axis.")
    expected_net: Range | None = Field(None, description="The client's expected net from trying the case, across the coverage scenarios.")
    walk_away_weighted: Range | None = Field(None, description="The gross settlement that nets the client `expected_net`.")
    open_facts: list[OpenFact] = Field(default_factory=list, description="Ranked by swing, largest first.")
    plan: list[PlanStep] = Field(default_factory=list)
    inputs: list[InputSpec] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="What an unset input or a missing term did to the result.")
    evidence_state: str = Field("none", description="none | missing | ready")
    evidence_href: str | None = None
    inputs_href: str | None = None


# --------------------------------------------------------------------------- terms read from the case model


@dataclass
class Layer:
    label: str
    amount: D
    counted: bool
    status: str
    sources: list[c.SourceRef]


@dataclass
class Terms:
    value: D
    evidenced: D
    liens: D
    costs: D
    fee: D | None  # a fraction, 0-1
    fee_after_costs: bool
    liens_known: bool = True  # False when the matter holds no lien total: `liens` is then 0 and the lien is swept
    costs_known: bool = True
    layers: list[Layer] = field(default_factory=list)

    def net(self, gross: D, further_costs: D = ZERO, liens: D | None = None) -> D:
        """The client's net from a gross recovery: fee, liens and costs out, floored at zero."""
        liens = self.liens if liens is None else liens
        costs = self.costs + further_costs
        base = max(gross - costs, ZERO) if self.fee_after_costs else gross
        return max(gross - (self.fee or ZERO) * base - liens - costs, ZERO)

    def gross_for(self, net: D, liens: D | None = None) -> D:
        """The gross settlement at which the client nets `net`: the inverse of `net` above its floor. Never below zero."""
        liens = self.liens if liens is None else liens
        keep = ONE - (self.fee or ZERO)
        if keep <= 0:
            return self.value
        if self.fee_after_costs:
            return max((net + liens) / keep + self.costs, ZERO)
        return max((net + liens + self.costs) / keep, ZERO)


def _dec(amount: float | None) -> D | None:
    return D(str(amount)) if amount is not None else None


def _money(amount: D) -> str:
    whole = amount.quantize(D(1))
    return f"-{rules.usd(-whole)}" if whole < 0 else rules.usd(whole)


def _range(lo: D, hi: D) -> Range:
    lo, hi = min(lo, hi).quantize(D(1)), max(lo, hi).quantize(D(1))
    exact = lo == hi
    return Range(lo=float(lo), hi=float(hi), exact=exact, display=_money(lo) if exact else f"{_money(lo)} to {_money(hi)}")


def read_terms(case: c.CaseModel, fee_basis: str) -> Terms | None:
    by_id = {node.id: node for node in case.nodes}
    value = _dec(by_id["value"].amount) if "value" in by_id else None
    if value is None or value <= 0:
        return None
    layers = [
        Layer(node.label, _dec(node.amount), node.counted, str(node.status), node.sources)
        for node in case.nodes
        if node.kind == c.NodeKind.coverage.value and node.parent_id == "coverage" and node.amount is not None
    ]
    whole = by_id.get("coverage")
    if not layers and whole is not None and whole.amount is not None:
        layers = [Layer(whole.label, _dec(whole.amount), True, str(whole.status), whole.sources)]
    percent = case.river.fee_percent
    return Terms(
        value=value,
        evidenced=min(_dec(case.river.evidence_backed_amount) or ZERO, value),
        liens=_dec(by_id["lien"].amount) if "lien" in by_id and by_id["lien"].amount is not None else ZERO,
        costs=_dec(by_id["cost"].amount) if "cost" in by_id and by_id["cost"].amount is not None else ZERO,
        liens_known="lien" in by_id and by_id["lien"].amount is not None,
        costs_known="cost" in by_id and by_id["cost"].amount is not None,
        fee=D(str(percent)) / 100 if percent is not None else None,
        fee_after_costs=fee_basis == "after_costs",
        layers=layers,
    )


# --------------------------------------------------------------------------- the arithmetic


@dataclass
class Setting:
    """The attorney's inputs as fractions and decimals, with the span of each unset probability."""

    p: list[D]
    q: list[D]
    g: list[D]
    w: list[D]
    discount: D
    cost_client: D
    cost_other: D | None
    offer: D | None
    ratio: D | None


def _span(percent: float | None) -> list[D]:
    return [ZERO, ONE] if percent is None else [D(str(percent)) / 100]


def read_inputs(inputs: Inputs) -> Setting:
    rate_client, rate_other = inputs.rate_client, inputs.rate_other
    weight = [ZERO, ONE]
    if rate_client is not None and rate_other is not None and rate_client + rate_other > 0:
        weight = [D(str(rate_other)) / (D(str(rate_client)) + D(str(rate_other)))]
    discount = ONE
    if rate_client is not None and inputs.months_to_trial is not None:
        discount = D(str(math.exp(-(rate_client / 100) * (inputs.months_to_trial / 12))))
    return Setting(
        p=_span(inputs.p_win),
        q=_span(inputs.value_share),
        g=_span(inputs.p_gate),
        w=weight,
        discount=discount,
        cost_client=_dec(inputs.cost_client) or ZERO,
        cost_other=_dec(inputs.cost_other),
        offer=_dec(inputs.offer),
        ratio=D(str(inputs.concession_ratio)) / 100 if inputs.concession_ratio is not None else None,
    )


def judgment(terms: Terms, q: D) -> D:
    return terms.evidenced + q * (terms.value - terms.evidenced)


def trial_net(terms: Terms, s: Setting, ceiling: D, p: D, q: D, liens: D | None = None) -> D:
    """The client's expected net from trying the case under one ceiling. A loss nets nothing."""
    return s.discount * p * terms.net(min(judgment(terms, q), ceiling), s.cost_client, liens)


def unknown_lien(terms: Terms, s: Setting, q: D, share: D | None) -> D | None:
    """With no lien total in the file the lien is not taken as zero: it is swept from nothing (share 0) to the
    most a lien could take, the client's whole net at the judgment (share 1). None when the file holds the total."""
    if terms.liens_known or share is None:
        return None
    return share * terms.net(judgment(terms, q), s.cost_client, ZERO)


def lien_span(terms: Terms) -> list[D | None]:
    return [None] if terms.liens_known else [ZERO, ONE]


def zone(terms: Terms, s: Setting, ceiling: D, p: D, q: D, w: D, liens: D | None = None) -> tuple[D, D, D]:
    """(walk-away, top of the zone, target), all gross."""
    walk = terms.gross_for(trial_net(terms, s, ceiling, p, q, liens), liens)
    top = ceiling
    if s.cost_other is not None:
        top = min(ceiling, p * min(judgment(terms, q), ceiling) + s.cost_other)
    target = walk + w * (top - walk) if top >= walk else walk
    return walk, top, target


def tree_net(terms: Terms, s: Setting, base: D, p: D, q: D, g: D, liens: D | None = None) -> D:
    """The decision tree: the gate opens or stays shut, the client prevails or not, the estimate holds or not."""
    shut = trial_net(terms, s, base, p, q, liens)
    opened = trial_net(terms, s, terms.value, p, q, liens)
    return (ONE - g) * shut + g * opened


def scenarios_of(terms: Terms) -> list[tuple[str, str, str, D, str, list[c.SourceRef]]]:
    """(id, label, basis, ceiling, status, sources). Built from the coverage layers in the matter."""
    out = []
    counted = [layer for layer in terms.layers if layer.counted]
    if counted:
        total = min(sum((layer.amount for layer in counted), ZERO), terms.value)
        names = ", ".join(layer.label for layer in counted)
        out.append(("counted", "Counted coverage only", f"The layer the file counts: {names}.", total,
                    counted[0].status, [ref for layer in counted for ref in layer.sources]))
    if counted and len(terms.layers) > len(counted):
        total = min(sum((layer.amount for layer in terms.layers), ZERO), terms.value)
        names = ", ".join(layer.label for layer in terms.layers if not layer.counted)
        out.append(("all_layers", "Every listed layer pays",
                    f"Adds the layers the file lists but does not count: {names}. Nothing in the file says they can be added together.",
                    total, c.FactStatus.assumed.value, [ref for layer in terms.layers for ref in layer.sources]))
    out.append(("open", "Recovery not limited by the listed coverage",
                "The firm's full estimate is collectible. Holds only if the open coverage question is answered that way.",
                terms.value, c.FactStatus.unknown.value if counted else c.FactStatus.assumed.value, []))
    return out


def sweep(values) -> Range:
    values = list(values)
    return _range(min(values), max(values))
