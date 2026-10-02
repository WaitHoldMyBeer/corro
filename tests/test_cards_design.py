"""Describe-a-card: the specification is data checked against a catalog derived
from the contract. No model is called here; the model's answer is stood in for.
(Written by checker; critic owns this directory.)"""

from __future__ import annotations

import pytest

from server.cards import design
from server.cards.catalog import catalog
from shared import contract as c


def answer(**changes) -> design.CardOut:
    base = {"possible": True, "reason": None, "title": "Overdue work", "kind": "list", "source": "agenda.overdue",
            "fields": ["title", "due"], "filter": None, "sort": None, "limit": 5, "stat": None, "text": None,
            "template": "none", "template_settings": []}
    return design.CardOut.model_validate({**base, **changes})


def test_every_source_and_field_exists_in_the_contract() -> None:
    sources = catalog()
    assert sources, "the catalog is empty"
    for path, source in sources.items():
        model = c.CaseModel
        for part in path.split("."):
            assert part in model.model_fields, f"{path} is not a path of the case model"
            inner = model.model_fields[part].annotation
            args = getattr(inner, "__args__", ())
            model = next((a for a in (inner, *args) if isinstance(a, type) and hasattr(a, "model_fields")), None) or next(
                (b for a in args for b in getattr(a, "__args__", ()) if isinstance(b, type) and hasattr(b, "model_fields")), model
            )
        for name in source.fields:
            assert name.split(".")[0] in model.model_fields, f"{path}.{name} is not a field of {model.__name__}"


def test_a_valid_answer_becomes_a_spec() -> None:
    spec = design.validate(answer(filter={"field": "kind", "op": "eq", "value": "task"}, sort={"field": "due", "dir": "asc"}))
    assert spec.source == "agenda.overdue" and spec.fields == ["title", "due"] and spec.limit == 5
    assert spec.filter is not None and spec.sort is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "claims"},  # a real collection, deliberately not offered
        {"source": "matter.__class__"},
        {"fields": ["title", "draft_text; drop table"]},
        {"fields": ["no_such_field"]},
        {"sort": {"field": "password", "dir": "asc"}},
        {"filter": {"field": "__proto__", "op": "eq", "value": "x"}},
        {"fields": []},
    ],
)
def test_anything_outside_the_catalog_is_refused_in_plain_words(changes) -> None:
    with pytest.raises(ValueError) as refused:
        design.validate(answer(**changes))
    assert "Traceback" not in str(refused.value) and len(str(refused.value)) < 400


def test_limits_are_clamped_and_a_note_card_carries_no_source() -> None:
    assert design.validate(answer(limit=500)).limit == 25
    note = design.validate(answer(kind="text", text="  Call   the office on Fridays ", source=None, fields=[]))
    assert note.source is None and note.text == "Call the office on Fridays"


def test_a_count_of_a_single_value_becomes_its_value_and_a_value_of_a_list_is_refused() -> None:
    assert design.validate(answer(kind="stat", source="brief.stage", fields=["display"], stat="count")).stat == "value"
    with pytest.raises(ValueError):
        design.validate(answer(kind="stat", stat="value"))


def test_the_instructions_carry_no_matter_data() -> None:
    """The prompt is the catalog plus the lawyer's words: field names and contract descriptions only."""
    text = design.INSTRUCTIONS + design._catalog_text()
    assert "$" not in text and "@" not in text


def test_a_ready_made_card_is_named_only_with_settings_from_its_list() -> None:
    good = design.validate_template(answer(template="checklist", template_settings=[{"key": "bucket", "value": "overdue"}]))
    assert good is not None and good.base == "checklist" and good.settings == {"bucket": "overdue"}
    assert design.validate_template(answer(template="checklist", template_settings=[{"key": "bucket", "value": "everything"}])) is None
    assert design.validate_template(answer(template="checklist", template_settings=[])) is None, "a required setting is missing"
    assert design.validate_template(answer(template="timeline", template_settings=[{"key": "from", "value": "last spring"}])) is None
    dated = design.validate_template(answer(template="timeline", template_settings=[{"key": "from", "value": "2031-01-01"}]))
    assert dated is not None and dated.settings == {"from": "2031-01-01"}
    assert design.validate_template(answer(template="stat", template_settings=[{"key": "fact", "value": "password"}])) is None
    assert design.validate_template(answer()) is None
