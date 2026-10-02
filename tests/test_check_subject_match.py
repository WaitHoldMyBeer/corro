"""A figure is support only for the subject it belongs to.

The same dollar amount can sit in a file for two different things (one party's
limit, another benefit). A sentence that gives one subject the other's figure
must never come back "supported"; where the file holds a different value for the
sentence's own subject, it is contradicted with that value. Synthetic ledger
only (written by checker; critic owns this directory).
"""

from __future__ import annotations

import pytest

from server.check import synthetic
from shared.check_contract import CheckRequest


def usd(value: float) -> str:
    return f"${value:,.2f}"


@pytest.fixture(scope="module")
def built():
    return synthetic.build(seed=7)


@pytest.mark.parametrize("with_model", [False, True])
def test_another_subjects_figure_is_not_support(built, with_model) -> None:
    ledger, facts = built
    text = f"The policy limit is {usd(facts['no_fault_limit'])}."  # the no-fault figure, given to the policy limit
    for max_tier in (1, 2):
        result = synthetic.checker(ledger, with_model=with_model).run(CheckRequest(text=text, complete=True, max_tier=max_tier))
        span = result.spans[0]
        assert span.verdict != "supported", f"a figure that belongs to another subject was called supported (max_tier {max_tier})"
        supports = [item.claim_id for item in span.evidence if item.role == "supports"]
        assert "note:110#0" not in supports


def test_it_is_contradicted_with_the_value_the_file_holds_for_that_subject(built) -> None:
    ledger, facts = built
    text = f"The policy limit is {usd(facts['no_fault_limit'])}."
    span = synthetic.checker(ledger, with_model=False).run(CheckRequest(text=text, complete=True)).spans[0]
    assert span.verdict == "contradicted"
    assert span.replacement is not None and span.replacement.text == usd(facts["policy_limit"])
    assert span.replacement.claim_id == "note:103#0"


def test_the_figure_on_its_own_subject_is_still_supported(built) -> None:
    ledger, facts = built
    for text in (f"The no-fault limit is {usd(facts['no_fault_limit'])}.", f"The policy limit is {usd(facts['policy_limit'])}."):
        span = synthetic.checker(ledger, with_model=False).run(CheckRequest(text=text, complete=True)).spans[0]
        assert span.verdict == "supported"


def test_a_figure_found_only_elsewhere_says_so_when_nothing_else_is_known(built) -> None:
    """No same-subject claim to compare with, no model: the span must not be blank and must not say supported."""
    ledger, facts = built
    text = f"The interpreter's deposit was {usd(facts['no_fault_limit'])}."
    span = synthetic.checker(ledger, with_model=False).run(CheckRequest(text=text, complete=True)).spans[0]
    assert span.verdict is None and "not clearly for this subject" in span.message


def test_your_figure_is_compared_only_with_that_providers_own(built) -> None:
    """To provider A, "your ... is <the firm's case value>" must not be judged against the firm's valuation claim."""
    ledger, facts = built
    text = f"Your itemised bill on file totals {usd(facts['case_value'] + 250)}."
    request = CheckRequest(text=text, audience="provider", audience_contact_id=facts["provider_a"], complete=True)
    span = synthetic.checker(ledger, with_model=False).run(request).spans[0]
    assert span.replacement is None or span.replacement.text != usd(facts["case_value"])
    assert usd(facts["case_value"]) not in span.message
