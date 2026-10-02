"""How to get to each section of the application: the one guide the assistant answers from.

Every label here is copied from the navigation in `web/v2/js/shell.js`, and a
test fails when one of them is no longer there, so a renamed section breaks the
build instead of sending a lawyer to the wrong place. The guide covers how to
reach each section. It says nothing about what to click inside one: for that the
assistant answers that it has no checked instructions and names the section.
"""

from __future__ import annotations

import re
from typing import Any

SHELL = "web/v2/js/shell.js"

# (address key, label exactly as on screen)
FIRM_SECTIONS = (("cases", "Your cases"), ("settings", "Settings"))
CASE_SECTIONS = (
    ("dashboard", "Dashboard"), ("graph", "Graph"), ("documents", "Documents"), ("notes", "Notes"),
    ("communications", "Communications"), ("calendar", "Calendar"), ("tasks", "Tasks"), ("write", "Write"), ("share", "Share"),
    ("negotiation", "Negotiation"), ("review", "Review"), ("assistant", "Assistant"),
)
RECORDS_HEADING = "Records"
RECORD_SECTIONS = (("activities", "Activities"), ("fields", "Fields"), ("bills", "Bills"), ("transactions", "Transactions"),
                   ("cocounsel", "Co-counsel"))
SWITCH_TOOLTIP, ALL_CASES = "Switch case", "All cases"
UNKNOWN = "unknown"

# What a section holds, as its own source names it: label -> the file that must contain it (checked by the test).
INSIDE_LABELS = {
    "Prepare and send": "web/v2/tabs/share.js", "Conversation": "web/v2/tabs/share.js", "Requests and replies": "web/v2/tabs/share.js",
    "Share log": "web/v2/tabs/share.js",
    "Appearance": "web/v2/tabs/settings.js", "Import": "web/v2/tabs/settings.js", "Spend by purpose": "web/v2/tabs/settings.js",
}
# (id, title, section it is in, further steps)
INSIDE = (
    ("share-parts", "The parts of Share", "Share",
     ("The page has the tabs “Prepare and send”, “Conversation”, “Requests and replies” and “Share log”.",)),
    ("settings-parts", "The parts of Settings", "Settings",
     ("The page has the sections “Appearance”, “Import” and “Spend by purpose”, among others.",)),
)


def _entry(key: str, label: str, where: str, address: str, steps: list[str]) -> dict[str, Any]:
    return {"id": key, "label": label, "where": where, "address": address, "steps": steps}


def entries() -> list[dict[str, Any]]:
    out = [
        _entry(key, label, "Firm level (not inside a case)", f"#/{key}",
               [f"At firm level, select “{label}” in the navigation list.",
                f"From inside a case, open the case switcher (the button whose tooltip reads “{SWITCH_TOOLTIP}”) and choose"
                f" “{ALL_CASES}” to return to firm level first." if key == "cases" else
                f"If you are inside a case, go to firm level first: open the case switcher (tooltip “{SWITCH_TOOLTIP}”) and choose “{ALL_CASES}”."])
        for key, label in FIRM_SECTIONS
    ]
    for key, label in CASE_SECTIONS:
        out.append(_entry(key, label, "Inside a case", f"#/c/<case id>/{key}",
                          [f"Open the case (from “{FIRM_SECTIONS[0][1]}”).", f"In the case's navigation list, select “{label}”."]))
    for key, label in RECORD_SECTIONS:
        out.append(_entry(key, label, f"Inside a case, under the “{RECORDS_HEADING}” heading of the navigation list", f"#/c/<case id>/{key}",
                          [f"Open the case (from “{FIRM_SECTIONS[0][1]}”).",
                           f"In the case's navigation list, under “{RECORDS_HEADING}”, select “{label}”."]))
    out.append(_entry("switch-case", "Switching to another case", "Inside a case", "#/cases",
                      [f"Open the case switcher: the button whose tooltip reads “{SWITCH_TOOLTIP}”.",
                       f"Pick the case from its menu, or choose “{ALL_CASES}” to see the full list."]))
    for key, label, section, lines in INSIDE:
        out.append(_entry(key, label, f"Inside a case, in “{section}”" if section != "Settings" else "Firm level, in “Settings”",
                          "#/settings" if section == "Settings" else f"#/c/<case id>/{section.lower()}",
                          [f"Select “{section}” in the navigation list."] + list(lines)))
    return out


def entry(guide_id: str | None) -> dict[str, Any] | None:
    return next((item for item in entries() if item["id"] == guide_id), None)


def sections() -> list[str]:
    return [label for _key, label in FIRM_SECTIONS + CASE_SECTIONS + RECORD_SECTIONS]


def find(topic: str) -> dict[str, Any]:
    """Entries whose section is named in `topic`. No match returns none, with the section names: never a guess."""
    text = " " + re.sub(r"[^a-z0-9]+", " ", (topic or "").lower()) + " "
    found = []
    for item in entries():
        names = {re.sub(r"[^a-z0-9]+", " ", item["label"].lower()).strip(), item["id"].replace("-", " ")}
        if item["id"] == "cocounsel":
            names |= {"co counsel", "cocounsel"}
        if item["id"] == "bills":
            # "bills" on its own usually means a provider's medical bills, which are not what this section lists:
            # only a question that names the section itself is sent here.
            names = {"bills section", "bills tab", "bills page", "bills list"}
        if item["id"] == "share-parts":
            names = {"share log", "requests and replies", "conversation with", "prepare and send"}
        if item["id"] == "settings-parts":
            names = {"appearance", "spend", "import settings", "sync"}
        if item["id"] == "switch-case":
            names |= {"another case", "other case", "different case", "change case", "switch cases"}
        if any(f" {name} " in text or f" {name}s " in text for name in names if name):
            found.append(item)
    return {"matches": found[:4], "sections": sections(),
            "note": "Answer how-to and where-is questions about the application only by placing guide blocks with these ids."
                    " With no match, place a guide block with guide_id \"unknown\": do not describe the application from memory."}


def asks_about_the_application(message: str) -> bool:
    text = " " + (message or "").lower() + " "
    return any(phrase in text for phrase in (" where is ", " where are ", " where do i ", " where can i ", " how do i ", " how to ",
                                              " how can i ", " navigate ", " which tab ", " which section ", " which page "))
