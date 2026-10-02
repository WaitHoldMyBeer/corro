"""The Write surface's leak guard, on the checker's own made-up ledger.

With a provider as the audience, a sentence that discloses anything from a
firm-only category must be locked, the provider's own figures must not be, a
suggested replacement must never carry a firm-only figure, and a sentence the
checker could not assess must say so instead of looking clean.
"""

from __future__ import annotations

import pytest

from server.check import synthetic
from shared.check_contract import CheckRequest

FIRM_ONLY = {"valuation", "strategy", "other_party", "internal"}


def usd(value: float) -> str:
    return f"${value:,.2f}"


@pytest.fixture(scope="module")
def built():
    return synthetic.build(seed=7)


def run(built, text: str, *, with_model: bool, audience: str = "provider", contact_key: str = "provider_a"):
    ledger, facts = built
    request = CheckRequest(
        text=text,
        audience=audience,
        audience_contact_id=facts[contact_key] if audience == "provider" else None,
        complete=True,
    )
    result = synthetic.checker(ledger, with_model=with_model).run(request)
    assert len(result.spans) == 1, "one sentence in, one span out"
    return result, result.spans[0]


@pytest.mark.parametrize("with_model", [False, True])
@pytest.mark.parametrize(
    "sentence",
    [
        lambda f: f"We value the case at {usd(f['case_value'])}.",
        lambda f: f"The policy limit is {usd(f['policy_limit'])}.",
        lambda f: f"Total charges from {f['provider_b_name']} are {usd(f['charges_b'])}.",
    ],
)
def test_firm_only_figures_are_locked_for_a_provider(built, with_model, sentence) -> None:
    result, span = run(built, sentence(built[1]), with_model=with_model)
    assert span.verdict == "dont_send" and span.category in FIRM_ONLY
    assert result.blocked
    assert span.replacement is None, "a locked sentence offers no replacement"


@pytest.mark.parametrize("with_model", [False, True])
def test_the_same_figures_are_not_locked_for_an_internal_note(built, with_model) -> None:
    _, span = run(built, f"We value the case at {usd(built[1]['case_value'])}.", with_model=with_model, audience="internal")
    assert span.verdict != "dont_send"


@pytest.mark.parametrize("with_model", [False, True])
def test_a_providers_own_balance_is_not_locked(built, with_model) -> None:
    facts = built[1]
    _, span = run(built, f"Your statement shows a balance due of {usd(facts['balance_a_document'])}.", with_model=with_model)
    assert span.verdict != "dont_send", f"the provider's own figure was locked as {span.category}"


@pytest.mark.parametrize("with_model", [False, True])
@pytest.mark.parametrize(
    "sentence",
    [
        lambda f: f"The outstanding balance with {f['provider_a_name']} is {usd(f['balance_a_notes'] + 123.45)}.",
        lambda f: f"We value the case at {usd(f['case_value'] + 5000)}.",
        lambda f: f"The policy limit is {usd(f['policy_limit'] + 5000)}.",
        lambda f: f"Total charges from {f['provider_b_name']} are {usd(f['charges_b'] + 77)}.",
    ],
)
def test_no_replacement_puts_a_firm_only_figure_into_a_provider_draft(built, with_model, sentence) -> None:
    facts = built[1]
    _, span = run(built, sentence(facts), with_model=with_model)
    firm_only_figures = {usd(facts[key]) for key in ("case_value", "policy_limit", "charges_b")}
    if span.replacement is not None:
        assert span.replacement.text not in firm_only_figures, "the one-click fix would insert a firm-only figure"
    for figure in firm_only_figures:
        assert span.verdict == "dont_send" or figure not in span.message or span.replacement is None


def test_strategy_wording_is_locked_when_the_model_is_available(built) -> None:
    _, span = run(built, "Liability is disputed and the firm expects a comparative fault argument.", with_model=True)
    assert span.verdict == "dont_send" and span.category == "strategy"


def test_a_sentence_the_checker_could_not_assess_does_not_look_clean(built) -> None:
    """No model: code cannot judge wording without a figure. For a provider that must be visible."""
    result, span = run(built, "Liability is disputed and the firm expects a comparative fault argument.", with_model=False)
    assert result.tier2_available is False
    assert span.verdict == "dont_send" or span.message, "blank verdict and blank message: indistinguishable from a cleared sentence"


@pytest.mark.parametrize("with_model", [False, True])
def test_a_sentence_resting_on_an_internal_claim_is_locked_for_a_provider(built, with_model) -> None:
    facts = built[1]
    _, span = run(built, f"The incident occurred on {facts['incident_date']}.", with_model=with_model)
    assert span.verdict == "dont_send", f"supported by an internal claim, returned {span.verdict}"
