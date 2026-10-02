"""Evaluation harness for the live checker: sentences with known verdicts in,
accuracy per verdict and latency out. Used by the synthetic evaluation and by
the run-time evaluation on whatever ledger is in the local database.

A report never contains sentence text from a real ledger: it carries group
names, counts, verdicts and timings only.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from shared.check_contract import CheckRequest

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()


def in_words(number: int) -> str:
    """A whole number below one billion, spelled out."""
    if number < 20:
        return ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return TENS[tens] + (f"-{ONES[ones]}" if ones else "")
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        return f"{ONES[hundreds]} hundred" + (f" {in_words(rest)}" if rest else "")
    for size, name in ((1_000_000, "million"), (1000, "thousand")):
        if number >= size:
            head, rest = divmod(number, size)
            return f"{in_words(head)} {name}" + (f" {in_words(rest)}" if rest else "")
    raise ValueError(number)


def usd(value: float) -> str:
    return f"${value:,.2f}"


def shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


@dataclass(repr=False)
class Case:
    group: str  # what is being measured; the report is keyed on this
    text: str
    expected: str | None  # the verdict a correct checker returns; None = no verdict (not a statement of fact)
    audience: str = "internal"
    contact_id: int | None = None
    replacement: str | None = None  # when set, the one-click fix must be exactly this
    replacement_ok: Callable[[str | None], bool] | None = None  # or must satisfy this (compares values, not spelling)
    ref: str | None = None  # the ledger claim the sentence was built from: an id, never text

    def __repr__(self) -> str:
        # Never the sentence: on a real ledger it is case text, and a failing test prints its arguments.
        return f"Case({self.group!r}, expected={self.expected!r}, {len(self.text)} chars)"


@dataclass(repr=False)
class Outcome:
    case: Case
    verdict: str | None
    pending: bool
    replacement: str | None
    message: str
    tier: int
    ms: float
    problems: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Outcome({self.case!r}, verdict={self.verdict!r}, pending={self.pending}, tier={self.tier})"

    @property
    def correct(self) -> bool:
        if self.verdict != self.case.expected or self.pending:
            return False
        if self.case.replacement_ok is not None:
            return self.case.replacement_ok(self.replacement)
        return self.case.replacement is None or self.replacement == self.case.replacement


def run(checker, ledger, cases: list[Case]) -> list[Outcome]:
    outcomes = []
    for case in cases:
        request = CheckRequest(text=case.text, audience=case.audience, audience_contact_id=case.contact_id, complete=True)
        clock = time.perf_counter()
        result = checker.run(request)
        ms = (time.perf_counter() - clock) * 1000
        problems = invariants(ledger, case, result)
        # A sentence is judged by its most severe span; the cases here are one sentence each.
        span = result.spans[0] if result.spans else None
        outcomes.append(
            Outcome(
                case=case,
                verdict=span.verdict if span else None,
                pending=bool(span and span.pending),
                replacement=span.replacement.text if span and span.replacement else None,
                message=span.message if span else "",
                tier=span.tier if span else 0,
                ms=ms,
                problems=problems,
            )
        )
    return outcomes


def invariants(ledger, case: Case, result) -> list[str]:
    """What must hold whatever the verdict. Each entry is a defect, not a miss."""
    problems = []
    if len(result.spans) != 1:
        problems.append(f"{len(result.spans)} spans for one sentence")
    if result.blocked != any(span.verdict == "dont_send" for span in result.spans):
        problems.append("`blocked` disagrees with the spans")
    for span in result.spans:
        if result_text(case, span) != span.text:
            problems.append("span offsets do not slice back to the span text")
        for claim_id in span.claim_ids:
            if claim_id not in ledger.claims:
                problems.append("a claim id that is not in the ledger")
        if span.replacement is not None:
            claim = ledger.claims.get(span.replacement.claim_id)
            if claim is None:
                problems.append("replacement cites a claim that is not in the ledger")
            if span.verdict == "dont_send":
                problems.append("a locked sentence offers a replacement")
        if span.verdict == "not_in_file" and any(word in span.message.lower() for word in ("false", "wrong", "incorrect", "untrue")):
            problems.append("'not in file' is worded as false")
        if span.verdict == "supported" and any(word in span.message.lower() for word in ("true", "correct", "verified as")):
            problems.append("'supported' is worded as true")
        if span.verdict == "contradicted" and any(word in span.message.lower() for word in ("you are wrong", "is false", "incorrect")):
            problems.append("a contradiction says who is right")
    return problems


def result_text(case: Case, span) -> str:
    # Offsets are UTF-16 code units, which are str indexes unless the text has characters outside the BMP.
    if any(ord(char) > 0xFFFF for char in case.text):
        return span.text
    return case.text[span.start : span.end]


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(round(share * (len(ordered) - 1))), len(ordered) - 1)]


def summarise(outcomes: list[Outcome]) -> dict:
    groups: dict[str, dict] = {}
    for outcome in outcomes:
        row = groups.setdefault(outcome.case.group, {"n": 0, "correct": 0, "got": {}})
        row["n"] += 1
        row["correct"] += outcome.correct
        row.setdefault("tiers", {})[outcome.tier] = row.get("tiers", {}).get(outcome.tier, 0) + 1
        got = "pending" if outcome.pending else str(outcome.verdict)
        if outcome.verdict == outcome.case.expected and not outcome.correct and not outcome.pending:
            got += " (wrong replacement)"
        row["got"][got] = row["got"].get(got, 0) + 1
    times = [outcome.ms for outcome in outcomes]
    return {
        "sentences": len(outcomes),
        "correct": sum(outcome.correct for outcome in outcomes),
        "groups": groups,
        "latency_ms": {"p50": statistics.median(times) if times else None, "p95": percentile(times, 0.95), "max": max(times, default=None)},
        "defects": sorted({problem for outcome in outcomes for problem in outcome.problems}),
    }


def render(title: str, summary: dict) -> str:
    lines = [f"\n{title}", f"  {'group':44} {'n':>3} {'right':>5} {'acc':>5}   verdicts returned"]
    for name, row in summary["groups"].items():
        accuracy = row["correct"] / row["n"] if row["n"] else 0.0
        returned = ", ".join(f"{verdict} x{count}" for verdict, count in sorted(row["got"].items()))
        lines.append(f"  {name:44} {row['n']:>3} {row['correct']:>5} {accuracy:>5.0%}   {returned}")
    total = summary["sentences"]
    lines.append(f"  {'ALL':44} {total:>3} {summary['correct']:>5} {(summary['correct'] / total if total else 0):>5.0%}")
    latency = summary["latency_ms"]
    if latency["p50"] is not None:
        lines.append(f"  latency per sentence: p50 {latency['p50']:.2f} ms, p95 {latency['p95']:.2f} ms, max {latency['max']:.2f} ms")
    lines.append(f"  defects (must be none): {summary['defects'] or 'none'}")
    return "\n".join(lines)


def synthetic_cases(facts: dict) -> list[Case]:
    """Sentences with known answers against `server.check.synthetic.build`. No number is written here."""
    a, b = facts["provider_a_name"], facts["provider_b_name"]
    provider = {"audience": "provider", "contact_id": facts["provider_a"]}
    value, limit = int(facts["case_value"]), int(facts["policy_limit"])
    return [
        # supported: a source in the ledger says so
        Case("supported", f"The firm values the case at {usd(facts['case_value'])}.", "supported"),
        Case("supported", f"The policy limit is {usd(facts['policy_limit'])}.", "supported"),
        Case("supported", f"Total charges from {b} are {usd(facts['charges_b'])}.", "supported"),
        Case("supported", f"The incident occurred on {facts['incident_date']}.", "supported"),
        Case("supported", f"Records were requested from {b} on {facts['records_requested']}.", "supported"),
        Case("supported", f"The hearing was continued to {facts['hearing_new']}.", "supported"),
        # contradicted by amount
        Case("contradicted: amount", f"The firm values the case at {usd(facts['case_value'] + 5000)}.", "contradicted", replacement=usd(facts["case_value"])),
        Case("contradicted: amount", f"The policy limit is {usd(facts['policy_limit'] + 5000)}.", "contradicted", replacement=usd(facts["policy_limit"])),
        Case("contradicted: amount", f"Total charges from {b} are {usd(facts['charges_b'] + 77)}.", "contradicted", replacement=usd(facts["charges_b"])),
        Case("contradicted: entry vs document", f"The outstanding balance with {a} is {usd(facts['balance_a_notes'])}.", "contradicted", replacement=usd(facts["balance_a_document"])),
        Case("contradicted: entry vs document", f"Records from {a} have not been received.", "contradicted"),
        # contradicted by date
        Case("contradicted: date", f"The incident occurred on {shift(facts['incident_date'], 3)}.", "contradicted", replacement=facts["incident_date"]),
        Case("contradicted: date", f"Records were requested from {b} on {shift(facts['records_requested'], 2)}.", "contradicted", replacement=facts["records_requested"]),
        Case("contradicted: date", f"The hearing was continued to {shift(facts['hearing_new'], 7)}.", "contradicted", replacement=facts["hearing_new"]),
        # out of date: true once, superseded by a later entry
        Case("out of date", f"The hearing is set for {facts['hearing_old']}.", "out_of_date"),
        # not in file: nothing in the ledger speaks to it
        Case("not in file", f"The mediation is set for {shift(facts['hearing_new'], 400)}.", "not_in_file"),
        Case("not in file", "The interpreter's invoice was $61.07.", "not_in_file"),
        Case("not in file", "The client has moved to another state.", "not_in_file"),
        Case("not in file", "A second surgery has been scheduled.", "not_in_file"),
        # no verdict: not a statement of fact
        Case("no verdict (greeting)", "Good morning, and thank you for your call.", None),
        # don't send, provider audience: the figure as the file writes it
        Case("don't send: figure as written", f"We value the case at {usd(facts['case_value'])}.", "dont_send", **provider),
        Case("don't send: figure as written", f"The policy limit is {usd(facts['policy_limit'])}.", "dont_send", **provider),
        Case("don't send: figure as written", f"Total charges from {b} are {usd(facts['charges_b'])}.", "dont_send", **provider),
        # don't send, provider audience: the same disclosure, rephrased
        Case("don't send: rephrased figure", f"We value the case at {in_words(value)} dollars.", "dont_send", **provider),
        Case("don't send: rephrased figure", f"We value the case at about {value // 1000}k.", "dont_send", **provider),
        Case("don't send: rephrased figure", f"We value the case at between {usd(value - 5000)} and {usd(value + 5000)}.", "dont_send", **provider),
        Case("don't send: rephrased figure", f"The policy limit is {in_words(limit)} dollars.", "dont_send", **provider),
        # don't send, provider audience: no figure at all
        Case("don't send: strategy wording", "Liability is disputed and the firm expects a comparative fault argument.", "dont_send", **provider),
        Case("don't send: strategy wording", "We think the other side will argue our client was partly at fault.", "dont_send", **provider),
        # must stay sendable: the provider's own figure
        Case("sendable: provider's own figure", f"Your statement shows a balance due of {usd(facts['balance_a_document'])}.", "supported", **provider),
    ]


def model_checker(ledger, effort: str):
    """A checker over `ledger` whose second tier is the configured model at one reasoning effort.
    Returns (checker, store, label), or None when no model is configured. Nothing is cached between runs."""
    import os

    from server.check import engine, tier2
    from server.config import get_settings

    cfg = get_settings()
    model = os.environ.get("CHECK_MODEL", "").strip() or cfg.digest_model_bulk
    if not model or not cfg.openai_api_key:
        return None
    label = f"{model}@{effort}"
    store = engine.MemoryStore()
    checker = engine.Checker(ledger, store, model=label, caller=tier2.model_caller(cfg, model, effort), deadline=None)
    return checker, store, label


def model_lines(store, label: str, outcomes: list[Outcome]) -> str:
    """Latency and cost of the model calls of one run, and how many sentences the model decided."""
    latencies, cost, calls = store.measured(label)
    ordered = sorted(float(value) for value in latencies)
    decided = sum(1 for outcome in outcomes if outcome.tier == 2)
    unanswered = sum(1 for outcome in outcomes if outcome.pending or (outcome.verdict is None and outcome.case.expected is not None))
    lines = [f"  model calls: {calls}, answers stored: {len(ordered)}, sentences decided by the model: {decided}, left without a verdict: {unanswered}"]
    if ordered:
        quartile = lambda share: percentile(ordered, share)  # noqa: E731
        lines.append(
            f"  model latency per call: min {ordered[0]:.0f} ms, p25 {quartile(0.25):.0f}, p50 {quartile(0.5):.0f},"
            f" p75 {quartile(0.75):.0f}, p95 {quartile(0.95):.0f}, max {ordered[-1]:.0f} ms"
        )
    lines.append(f"  cost of this run: ${cost:.4f}" + (f" (${cost / calls:.5f} per call)" if calls else ""))
    return "\n".join(lines)


def requested_efforts() -> list[str]:
    import os

    return [effort.strip() for effort in os.environ.get("CHECK_EVAL_EFFORTS", "low").split(",") if effort.strip()]


def model_tier_requested() -> bool:
    import os

    return os.environ.get("CHECK_EVAL_MODEL", "").strip() in ("1", "true", "yes")


MODEL_TIER_SKIP = "model tier not requested: set CHECK_EVAL_MODEL=1 to measure it (calls the configured model and costs money)"


def write_misses(outcomes: list[Outcome], label: str) -> None:
    """When CHECK_EVAL_MISSES names a file, append one line per miss: run label, group, expected and
    returned verdict, and the id of the ledger claim the sentence was built from. Ids only, no text;
    the file is for the person debugging the checker and is never read by a test."""
    import os

    path = os.environ.get("CHECK_EVAL_MISSES", "").strip()
    if not path:
        return
    with open(path, "a") as out:
        for outcome in outcomes:
            if not outcome.correct:
                got = "pending" if outcome.pending else outcome.verdict
                out.write(f"{label}\t{outcome.case.group}\texpected={outcome.case.expected}\tgot={got}\ttier={outcome.tier}\tclaim={outcome.case.ref}\n")
