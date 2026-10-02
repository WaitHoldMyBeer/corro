"""The assistant's guide to the application must match the application.

Every label the guide quotes is looked for in the shell's navigation at HEAD, so a
renamed or removed section fails here instead of sending a lawyer to the wrong place.
"""

from __future__ import annotations

import re
from pathlib import Path

from server.assistant import guide

ROOT = Path(__file__).resolve().parent.parent
SHELL = (ROOT / guide.SHELL).read_text(encoding="utf-8")
QUOTED = re.compile(r"“([^”]+)”")


def test_every_section_is_in_the_shells_navigation_under_the_same_address():
    for key, label in guide.FIRM_SECTIONS + guide.CASE_SECTIONS + guide.RECORD_SECTIONS:
        assert f"['{key}', '{label}']" in SHELL, f"{label!r} at {key!r} is not in {guide.SHELL}"
    assert f"'{guide.RECORDS_HEADING}'," in SHELL
    for text in (guide.SWITCH_TOOLTIP, guide.ALL_CASES):
        assert f"'{text}'" in SHELL, text
    # The guide sends the lawyer through "All cases"; the shell hides that entry when this switch is off.
    assert "const SHOW_ALL_CASES = true" in SHELL


def test_the_guide_misses_no_section_of_the_navigation():
    listed = set(re.findall(r"\['([a-z-]+)', '([^']+)'\]", SHELL.split("const RECORD_TABS")[0] + SHELL.split("const FIRM_NAV")[1].split("\n")[0]))
    known = set(guide.FIRM_SECTIONS + guide.CASE_SECTIONS + guide.RECORD_SECTIONS)
    assert listed <= known, f"in the navigation but not in the guide: {sorted(listed - known)}"


def test_every_label_a_step_quotes_is_a_checked_label():
    checked = {label for _key, label in guide.FIRM_SECTIONS + guide.CASE_SECTIONS + guide.RECORD_SECTIONS}
    checked |= {guide.RECORDS_HEADING, guide.SWITCH_TOOLTIP, guide.ALL_CASES}
    checked |= set(guide.INSIDE_LABELS)
    for item in guide.entries():
        for step in item["steps"] + [item["where"]]:
            for quoted in QUOTED.findall(step):
                assert quoted in checked, f"{item['id']}: step quotes {quoted!r}, which is not a checked label"


def test_a_question_with_no_matching_section_gets_no_guess():
    assert guide.find("where is the flux capacitor")["matches"] == []
    assert [item["id"] for item in guide.find("Where is the calendar?")["matches"]] == ["calendar"]
    assert [item["id"] for item in guide.find("how do I open co-counsel")["matches"]] == ["cocounsel"]
    assert [item["id"] for item in guide.find("how do I get to another case")["matches"]] == ["switch-case"]


def test_every_label_inside_a_section_is_in_that_sections_source():
    for label, path in guide.INSIDE_LABELS.items():
        assert label in (ROOT / path).read_text(encoding="utf-8"), f"{label!r} is not in {path}"


def test_a_question_about_medical_bills_is_not_sent_to_the_bills_section():
    """The word alone usually means a provider's bills, which that section does not list: no match, so no wrong turn."""
    for question in ("Where do I find the medical bills?", "where are the provider bills", "how do I see the hospital bills", "where are the bills"):
        assert "bills" not in [item["id"] for item in guide.find(question)["matches"]], question
    assert [item["id"] for item in guide.find("where is the Bills section")["matches"]] == ["bills"]


def test_the_model_cannot_print_an_entry_the_guide_did_not_return():
    """A guide block naming a section the guide did not match for this question is answered with the no-guess line."""
    from server.assistant import engine
    from shared import assistant_contract as a

    turn = engine.Turn.__new__(engine.Turn)
    turn.drafted, turn.warnings, turn.citations, turn.dropped, turn.dropped_refs, turn.guide_ids = {}, [], {}, 0, [], {"calendar"}
    reached = turn._blocks(None, [engine.OutBlock(type="guide", guide_id="bills")])
    assert [block.type for block in reached] == ["paragraph"] and "will not guess" in reached[0].sentences[0].text
    returned = turn._blocks(None, [engine.OutBlock(type="guide", guide_id="calendar")])
    assert returned[0].text == "How to reach: Calendar" and isinstance(returned[0], a.Block)
