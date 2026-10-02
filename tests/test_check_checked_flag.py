"""`CheckSpan.checked`: true only when the model tier read that sentence.

A send guard clears a provider-bound sentence on this flag, so it must be false
whenever the model did not answer for that sentence: no model configured, the
call failed for that one sentence while others succeeded, or the answer had not
arrived. Synthetic ledger only (written by checker; critic owns this directory).
"""

from __future__ import annotations

import pytest

from server.check import engine, synthetic, tier2
from server.digest import llm
from shared.check_contract import CheckRequest

FAILS_ON = "comparative"  # a word in the one sentence the stand-in below refuses


@pytest.fixture()
def built():
    engine._breaker.report(True)  # no carry-over from another test's failures
    return synthetic.build(seed=7)


def provider_request(facts: dict, text: str) -> CheckRequest:
    return CheckRequest(text=text, audience="provider", audience_contact_id=facts["provider_a"], complete=True)


def failing_for_one_sentence(prompt: tier2.Prompt):
    if FAILS_ON in prompt.sentence:
        raise llm.LLMError("model call failed: APITimeoutError")
    return tier2.fake_caller(prompt)


def test_checked_is_true_when_the_model_read_the_sentence(built) -> None:
    ledger, facts = built
    result = synthetic.checker(ledger).run(provider_request(facts, "The hearing was continued and a new date was set by the court."))
    assert [span.checked for span in result.spans] == [True]


def test_checked_is_false_without_a_model(built) -> None:
    ledger, facts = built
    result = synthetic.checker(ledger, with_model=False).run(
        provider_request(facts, f"Your statement shows a balance due of ${facts['balance_a_document']:,.2f}. Thank you for your help.")
    )
    assert result.tier2_available is False
    assert [span.checked for span in result.spans] == [False, False]


def test_one_sentence_failing_leaves_only_that_sentence_unchecked(built) -> None:
    ledger, facts = built
    text = (
        "Records were requested and the request was acknowledged in writing. "
        "The firm expects a comparative fault argument from the other side. "
        f"Your statement shows a balance due of ${facts['balance_a_document']:,.2f}."
    )
    result = synthetic.checker(ledger, caller=failing_for_one_sentence).run(provider_request(facts, text))
    assert result.tier2_available is True, "one failure does not switch the tier off"
    assert [span.checked for span in result.spans] == [True, False, True]
    failed = result.spans[1]
    assert not failed.pending, "a failed sentence resolves; it does not stay on 'checking'"
    assert failed.verdict == "dont_send" or failed.message, "an unread sentence must not look cleared"


def test_a_sentence_still_waiting_is_pending_and_unchecked(built) -> None:
    ledger, facts = built
    result = synthetic.checker(ledger).run(
        CheckRequest(text="The other side is expected to argue about fault at the hearing.", audience="provider",
                     audience_contact_id=facts["provider_a"], complete=True, max_tier=1)
    )
    span = result.spans[0]
    assert span.pending and not span.checked and span.message


def test_a_lock_code_reached_alone_is_not_marked_checked(built) -> None:
    ledger, facts = built
    result = synthetic.checker(ledger).run(provider_request(facts, f"We value the case at ${facts['case_value']:,.2f}."))
    span = result.spans[0]
    assert span.verdict == "dont_send" and not span.checked
