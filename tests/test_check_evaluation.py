"""Evaluation of the live checker on its synthetic ledger: thirty sentences with
known verdicts, scored per verdict, with tier 1 latency.

This is a measurement, not a pass mark. The report is printed (run with -s, or
through scripts/check.sh) and misses are part of it. What is asserted are the
things that must hold whatever the score: every cited claim exists, offsets
slice back, `blocked` matches the spans, a locked sentence offers no
replacement, an id the model invents never reaches a span, and the wording of
a verdict claims no more than the verdict means.

Tier 2 here is the checker's lexical stand-in, not a model: its column says how
the plumbing behaves, not how a model reasons. A real model is measured by
tests/test_check_evaluation_live.py on the ledger in the local database.
"""

from __future__ import annotations

import check_eval
import pytest

from server.check import synthetic, tier2
from server.digest import llm
from shared.check_contract import CheckRequest


@pytest.fixture(scope="module")
def built():
    return synthetic.build(seed=7)


def evaluate(built, with_model: bool):
    ledger, facts = built
    checker = synthetic.checker(ledger, with_model=with_model)
    return check_eval.summarise(check_eval.run(checker, ledger, check_eval.synthetic_cases(facts)))


def test_report_tier_1_alone(built) -> None:
    summary = evaluate(built, with_model=False)
    print(check_eval.render("Checker, synthetic ledger, tier 1 (code only)", summary))
    assert summary["sentences"] >= 20
    assert not summary["defects"], summary["defects"]


def test_report_with_the_lexical_stand_in(built) -> None:
    summary = evaluate(built, with_model=True)
    print(check_eval.render("Checker, synthetic ledger, tier 1 + lexical stand-in for the model", summary))
    assert not summary["defects"], summary["defects"]


@pytest.mark.parametrize("effort", check_eval.requested_efforts())
def test_report_with_the_configured_model(built, effort: str) -> None:
    """Opt-in: the same thirty sentences with the real model as tier 2, once per effort in CHECK_EVAL_EFFORTS."""
    if not check_eval.model_tier_requested():
        pytest.skip(check_eval.MODEL_TIER_SKIP)
    ledger, facts = built
    made = check_eval.model_checker(ledger, effort)
    if made is None:
        pytest.skip("no model is configured (OPENAI_API_KEY and a model id)")
    checker, store, label = made
    outcomes = check_eval.run(checker, ledger, check_eval.synthetic_cases(facts))
    summary = check_eval.summarise(outcomes)
    print(check_eval.render(f"Checker, synthetic ledger, tier 1 + model {label}", summary))
    print(check_eval.model_lines(store, label, outcomes))
    assert not summary["defects"], summary["defects"]


def test_figures_as_written_are_always_locked_for_a_provider(built) -> None:
    """The one group where anything under 100% is a defect, not a miss: it needs no model."""
    for with_model in (False, True):
        row = evaluate(built, with_model)["groups"]["don't send: figure as written"]
        assert row["correct"] == row["n"], row


def test_tier_1_latency_on_the_synthetic_ledger(built) -> None:
    ledger, facts = built
    checker = synthetic.checker(ledger, with_model=False)
    cases = check_eval.synthetic_cases(facts)
    times = [outcome.ms for _ in range(20) for outcome in check_eval.run(checker, ledger, cases)]
    p50, p95 = check_eval.percentile(times, 0.5), check_eval.percentile(times, 0.95)
    print(f"\nTier 1 latency, synthetic ledger ({len(ledger.claims)} claims), {len(times)} checks: p50 {p50:.2f} ms, p95 {p95:.2f} ms")
    assert p95 < 250, "tier 1 is meant to answer while the lawyer is typing"


def test_a_claim_id_the_model_invents_never_reaches_a_span(built) -> None:
    ledger, facts = built

    def inventing_caller(prompt: tier2.Prompt):
        answer = {"states_fact": True, "verdict": "supported", "agrees": ["Z99", "claim:made-up"], "disagrees": [], "category": "status"}
        # Fields the schema has gained since: an invented id wherever an id is asked for.
        for name, field in tier2.SentenceCheck.model_fields.items():
            kind = str(field.annotation)
            answer.setdefault(name, False if "bool" in kind else ["Z98"] if "list" in kind else "Z98")
        return tier2.SentenceCheck.model_validate(answer), llm.Usage(model="test", cost_usd=0.0)

    checker = synthetic.checker(ledger, caller=inventing_caller)
    result = checker.run(CheckRequest(text="The client has moved to another state.", complete=True))
    for span in result.spans:
        assert all(claim_id in ledger.claims for claim_id in span.claim_ids)
        assert span.verdict != "supported", "supported with no real claim behind it"


def test_spelled_out_numbers_helper() -> None:
    assert check_eval.in_words(0) == "zero" and check_eval.in_words(19) == "nineteen"
    assert check_eval.in_words(42) == "forty-two" and check_eval.in_words(300) == "three hundred"
    assert check_eval.in_words(95_000) == "ninety-five thousand"
    assert check_eval.in_words(1_250_017) == "one million two hundred fifty thousand seventeen"
