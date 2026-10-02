"""The customise route, end to end, with a stand-in for the model.

The real model is never called: `share_customise.propose` is replaced by a
function that returns whatever the test says the model proposed, including
what it must never be allowed to do, or raises as an unreachable model does.
What is checked is what the route stores and returns.
"""

from __future__ import annotations

import pytest
import test_provider_routes as routes
from test_provider_routes import ADVERSE, CASE_VALUE, CLIENT, A, B, firm
from test_share_customise import op

from server import share_customise as sc
from shared import contract as c

client = routes.client  # the signed-in firm client on the synthetic matter (a fixture)
SHAREABLE = {category.value for category in c.SHAREABLE_CATEGORIES}
NEVER = [category.value for category in c.ShareCategory if category.value not in SHAREABLE] if hasattr(c, "ShareCategory") else ["valuation", "strategy", "other_party", "internal"]


def stand_in(monkeypatch, proposed: list[dict] | Exception, not_done: list[str] | None = None) -> list[dict]:
    """Replace the model call. Returns the list of calls made, each with the instruction and the draft it was shown."""
    calls: list[dict] = []

    def propose(cfg, instruction, target, draft, record=None):
        calls.append({"instruction": instruction, "target": target, "draft": draft})
        if isinstance(proposed, Exception):
            raise proposed
        return list(proposed), list(not_done or [])

    monkeypatch.setattr(sc, "propose", propose)
    return calls


def policy_of(client, contact_id: int) -> dict:
    return client.get(firm(f"/providers/{contact_id}/policy")).json()


def customise(client, contact_id: int, instruction: str = "synthetic instruction", **extra):
    return client.post(firm(f"/providers/{contact_id}/customise"), json={"instruction": instruction, **extra})


def test_whatever_the_stand_in_proposes_the_route_stores_only_what_is_shareable_and_sends_nothing(client, monkeypatch) -> None:
    other_before = policy_of(client, B)
    hostile = [op("set_category", category=category, on=True) for category in NEVER + ["everything", ""]]
    hostile += [op("send_now"), op("share_everything"), op("hide_item", id="ask:task:2"), op("reword_ask", id="ask:task:2", text="Synthetic wording for another office."),
                op("set_cover_note", text=f"The case is worth ${int(CASE_VALUE):,} to us."), op("reorder_sections", sections=NEVER), op("set_category", category="attendance", on=True)]
    calls = stand_in(monkeypatch, hostile, not_done=["send it now"])
    answered = customise(client, A)
    assert answered.status_code == 200, answered.text[:300]
    body = answered.json()
    assert len(calls) == 1
    assert set(body["policy"]["allowed_categories"]) <= SHAREABLE, body["policy"]["allowed_categories"]
    assert "attendance" in body["policy"]["allowed_categories"], "the one allowed operation was not applied"
    assert str(int(CASE_VALUE)) not in (body["policy"].get("message") or "").replace(",", ""), "a cover note carrying the case value was stored"
    assert body["refused"], "nothing was reported as refused"
    stored = policy_of(client, A)
    assert stored["allowed_categories"] == body["policy"]["allowed_categories"] and stored["hidden_item_ids"] == body["policy"]["hidden_item_ids"]
    assert "ask:task:2" not in stored["hidden_item_ids"] and "ask:task:2" not in stored["approved_asks"], "another provider's request was changed through this provider's draft"
    assert policy_of(client, B) == other_before, "customising one provider's share changed another's policy"
    assert client.get(firm("/shares/1/snapshot")).status_code == 404, "customising a draft sent a share"
    panel = routes.panel(client, A)
    assert not panel.get("shares"), "customising a draft sent a share"
    shown = calls[0]["draft"]
    assert str(int(CASE_VALUE)) not in str(shown) and routes.B_NAME not in str(shown) and routes.ADVERSE_NAME not in str(shown), "the model was shown more than this provider's draft"
    history = client.get(firm(f"/providers/{A}/customise")).json()
    assert len(history) == 1 and history[0]["instruction"] == "synthetic instruction"


def test_when_the_model_does_not_answer_nothing_is_changed(client, monkeypatch) -> None:
    before = policy_of(client, A)
    for error in (sc.ModelUnavailable("synthetic outage"), sc.ModelUnavailable("RateLimitError: synthetic")):
        stand_in(monkeypatch, error)
        answered = customise(client, A)
        assert answered.status_code == 503 and "unchanged" in answered.json()["detail"]
    assert policy_of(client, A) == before


def test_a_customise_request_that_is_malformed_never_reaches_the_model(client, monkeypatch) -> None:
    calls = stand_in(monkeypatch, [op("set_category", category="attendance", on=True)])
    before = policy_of(client, A)
    for contact in (CLIENT, ADVERSE, 999_999):
        assert customise(client, contact).status_code == 404
    for body in ({}, {"instruction": None}, {"instruction": 5}, {"instruction": "x", "target": "nope"}, {"instruction": "x", "zzz": 1}, {"instruction": "x", "draft_policy": {"zzz": 1}}):
        assert client.post(firm(f"/providers/{A}/customise"), json=body).status_code == 422, body
    assert calls == [] and policy_of(client, A) == before
    for instruction in ("", "   ", "x" * 100_000, "‮\u0000\U0001f600"):
        answered = customise(client, A, instruction)
        assert answered.status_code in (200, 400, 413, 422), f"{instruction[:10]!r}: {answered.status_code}"
    assert set(policy_of(client, A)["allowed_categories"]) <= SHAREABLE


def test_a_draft_naming_a_never_shared_category_is_refused_and_nothing_is_stored(client, monkeypatch) -> None:
    stand_in(monkeypatch, [op("set_category", category="attendance", on=True)])
    before = policy_of(client, A)
    for never in NEVER:
        refused = customise(client, A, draft_policy={"contact_id": A, "allowed_categories": ["status", never]})
        assert refused.status_code in (400, 422), never
    assert policy_of(client, A) == before


@pytest.mark.xfail(reason="sharer: a draft_policy naming a never-shared category is refused only after the model has been called (a paid call for a request that cannot succeed)", strict=False)
def test_a_draft_naming_a_never_shared_category_is_refused_before_the_model_is_called(client, monkeypatch) -> None:
    calls = stand_in(monkeypatch, [])
    customise(client, A, draft_policy={"contact_id": A, "allowed_categories": ["status", NEVER[0]]})
    assert calls == []


def test_a_draft_policy_naming_another_provider_is_applied_to_the_one_in_the_path(client, monkeypatch) -> None:
    stand_in(monkeypatch, [op("set_category", category="attendance", on=False)])
    other_before = policy_of(client, B)
    answered = customise(client, A, draft_policy={"contact_id": B, "allowed_categories": ["status", "attendance"]})
    assert answered.status_code == 200 and answered.json()["policy"]["contact_id"] == A
    assert policy_of(client, B) == other_before


@pytest.mark.parametrize("proposed", [[], [op("nope")], [{"verb": "set_category"}], [{}], [op("set_category", category=None, on=None)], [op("hide_item", id=None)], [op("reword_ask", id="ask:task:1", text=None)],
                                      [op("reorder_sections", sections=None)], [op("set_cover_note", text="x" * 50_000)], [op("set_category", category="status", on="yes")] * 50])
def test_a_proposal_that_is_empty_or_malformed_is_answered_and_changes_nothing_forbidden(client, monkeypatch, proposed: list[dict]) -> None:
    stand_in(monkeypatch, proposed)
    answered = customise(client, A)
    assert answered.status_code == 200, answered.text[:300]
    assert set(answered.json()["policy"]["allowed_categories"]) <= SHAREABLE
    assert answered.json()["reply"], "the lawyer is told nothing about what happened"
