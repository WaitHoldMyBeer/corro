"""The gallery's ready-made cards a description can be turned into.

Only templates whose settings need nothing from the matter are offered: a
description cannot pick a contact or a ledger node, because the model is shown
no case data. Setting values that mirror the contract are read from it (agenda
buckets, header facts); the rest are the gallery's own option lists, agreed
with the card authors (web/v2/cards/money/tpl-*.js).
"""

from __future__ import annotations

from functools import lru_cache

from shared import contract as c

from .catalog import _unwrap


@lru_cache(maxsize=1)
def header_facts() -> tuple[str, ...]:
    """Fields of the header brief that hold one sourced value."""
    return tuple(name for name, field in c.Brief.model_fields.items() if _unwrap(field.annotation) == (c.Fact, False))


@lru_cache(maxsize=1)
def templates() -> dict[str, dict]:
    """template id -> {about, settings: {key: allowed values, or None for a date}}"""
    buckets = tuple(member.value for member in c.AgendaBucket)
    facts = header_facts()
    return {
        "checklist": {"about": "the tasks and events of one agenda bucket as a tick list", "settings": {"bucket": buckets}},
        "timeline": {"about": "the matter's dated events, optionally between two dates", "settings": {"from": None, "to": None}},
        "notes-excerpt": {"about": "the latest notes or communications", "settings": {"tab": ("notes", "communications"), "n": ("3", "5", "10")}},
        "progress": {"about": "how far along something is: tasks done, requests to providers answered, or document pages read",
                     "settings": {"kind": ("tasks", "asks", "pages")}},
        "stat": {"about": "one figure from the matter's header", "settings": {"fact": facts}},
        "compare": {"about": "two header figures side by side",
                    "settings": {"a": tuple(f"fact:{name}" for name in facts), "b": tuple(f"fact:{name}" for name in facts)}},
    }
