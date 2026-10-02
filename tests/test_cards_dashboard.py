"""A dashboard designed from a description: every entry is checked against the
gallery's catalogue, read from the browser's own manifests. No model is called
here; the model's answer is stood in for. (Written by checker; critic owns this directory.)"""

from __future__ import annotations

from server.cards import dashboard, design
from server.cards.gallery import gallery


def card(card_id: str) -> dict:
    return {"use": "card", "id": card_id, "settings": [], "described": None, "reason": "why"}


def template(base: str, **settings: str) -> dict:
    return {"use": "template", "id": base, "settings": [{"key": k, "value": v} for k, v in settings.items()], "described": None, "reason": "why"}


def described(**changes) -> dict:
    spec = {"possible": True, "reason": None, "title": "Open work", "kind": "list", "source": "agenda.overdue", "fields": ["title", "due"],
            "filter": None, "sort": None, "limit": 5, "stat": None, "text": None, "template": "none", "template_settings": []}
    return {"use": "described", "id": None, "settings": [], "described": {**spec, **changes}, "reason": "why"}


def answer(cards: list[dict], **changes) -> dashboard.DashboardOut:
    return dashboard.DashboardOut.model_validate({"possible": True, "refusal": None, "title": "Working dashboard", "cards": cards, **changes})


def plain_ids() -> list[str]:
    return [item.id for item in gallery() if not item.template]


def test_the_gallery_is_read_from_the_manifests() -> None:
    cards = gallery()
    assert len(cards) >= 10 and len({item.id for item in cards}) == len(cards)
    assert any(item.template for item in cards) and all(item.title for item in cards)


def test_registered_cards_templates_and_described_cards_make_a_layout() -> None:
    ids = plain_ids()
    out = answer([card(i) for i in ids[:5]] + [template("checklist", bucket="waiting"), described()])
    result = dashboard.assemble(out, gallery())
    assert result.error is None and len(result.cards) == 7 and result.title == "Working dashboard"
    assert result.cards[5].spec == {"base": "checklist", "settings": {"bucket": "waiting"}} and result.cards[5].id.startswith("checklist:")
    assert result.cards[6].spec["source"] == "agenda.overdue" and result.cards[0].spec is None


def test_what_is_not_in_the_catalogue_is_left_out_and_counted() -> None:
    ids = plain_ids()
    out = answer(
        [card(i) for i in ids[:6]]
        + [card("no-such-card"), card("checklist"), template("checklist", bucket="everything"), template("contact", contact="12"),
           described(source="claims"), described(fields=["__proto__"])]
    )
    result = dashboard.assemble(out, gallery())
    assert [entry.id for entry in result.cards] == ids[:6]
    assert result.skipped == 6


def test_duplicates_are_dropped_and_the_layout_is_capped() -> None:
    ids = plain_ids()
    out = answer([card(i) for i in ids[:6]] + [card(ids[0]), template("stat", fact="stage"), template("stat", fact="stage"), template("stat", fact="coverage")]
                 + [card(i) for i in ids[6:12]])
    result = dashboard.assemble(out, gallery())
    kept = [entry.id for entry in result.cards]
    assert len(kept) == dashboard.MAX_CARDS and kept.count(ids[0]) == 1
    assert sum(1 for entry in result.cards if entry.spec == {"base": "stat", "settings": {"fact": "stage"}}) == 1


def test_at_most_two_described_cards_and_too_few_cards_is_a_plain_refusal() -> None:
    ids = plain_ids()
    many = answer([card(i) for i in ids[:5]] + [described(), described(source="agenda.coming"), described(source="agenda.waiting")])
    result = dashboard.assemble(many, gallery())
    assert sum(1 for entry in result.cards if entry.id.startswith("custom-")) == dashboard.MAX_DESCRIBED
    thin = dashboard.assemble(answer([card(i) for i in ids[:3]]), gallery())
    assert not thin.cards and thin.error and "Traceback" not in thin.error
    refused = dashboard.assemble(answer([], possible=False, refusal="That is not about a matter."), gallery())
    assert not refused.cards and refused.error == "That is not about a matter."


def test_the_instructions_carry_no_matter_data() -> None:
    text = dashboard.INSTRUCTIONS + dashboard._catalogue_text(gallery())
    assert "$" not in text and "@" not in text
    assert design.INSTRUCTIONS  # the described-card rules are the same module's
