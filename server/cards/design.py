"""From a lawyer's description to a card specification.

One model call. The model is shown the catalog of sources and fields (which
comes from the contract and holds nothing from any matter) and the lawyer's
words, and returns a structure. Everything it returns is then checked here
against the same catalog: a source, field or operator that is not on the list
is refused with a plain sentence, never passed on. No code is generated and no
case data is sent: the description is the only thing from the session that
leaves.
"""

from __future__ import annotations

import os
import re
import time
from typing import Literal

from pydantic import ValidationError

from shared.card_spec import (
    MAX_FIELDS,
    MAX_LIMIT,
    MAX_TEXT,
    MAX_TITLE,
    CardDesignResult,
    CardFilter,
    CardSort,
    CardSpec,
    CardTemplate,
)

from ..digest import llm
from ..digest.schemas import Strict
from .catalog import catalog
from .templates import templates

DESIGN_VERSION = "card-design-2"
TIMEOUT_SECONDS = 15.0
RETRIES = 1
OUTPUT_TOKENS = 1200
DEFAULT_LIMIT = 5
CACHE_BREAKPOINT = {"prompt_cache_breakpoint": {"mode": "explicit"}}  # the provider reuses everything before it


class FilterOut(Strict):
    field: str
    op: Literal["eq", "contains", "gt", "lt"]
    value: str


class SortOut(Strict):
    field: str
    dir: Literal["asc", "desc"]


class SettingOut(Strict):
    key: str
    value: str


class CardOut(Strict):
    possible: bool
    reason: str | None
    title: str
    kind: Literal["stat", "list", "table", "timeline", "text"]
    source: str | None
    fields: list[str]
    filter: FilterOut | None
    sort: SortOut | None
    limit: int
    stat: Literal["count", "value"] | None
    text: str | None
    template: Literal["none", "checklist", "timeline", "notes-excerpt", "progress", "stat", "compare"]
    template_settings: list[SettingOut]


INSTRUCTIONS = """You turn a lawyer's description of a dashboard card into a card specification. The dashboard shows one legal matter. You do not write code, you do not answer questions about the matter, and you have no data from it: you only choose which of the dashboard's existing data a card should show, and how.

You are given the sources a card may be built from. Each has a path, whether it is a list of items or a single value, and the fields its items have. Use only these paths and these field names, spelled exactly as given.

Return:
- possible: false if the description asks for something none of the sources holds, asks a question instead of describing a card, or asks for anything other than a card. Then give reason, one plain sentence saying what is missing or what a card can show instead, and fill the other fields with the nearest harmless values.
- title: a short heading for the card in the lawyer's own terms, at most eight words, with no figures or names you were not given.
- kind: list = rows with a headline field and a few details. table = the same as columns, for comparing several fields. timeline = dated items in date order; the first field must be the item's date. stat = one figure: with stat "count", the number of items in a list source after the filter; with stat "value", the single value of a source that is not a list. text = a fixed note in the lawyer's words, for when the description is a reminder or a note rather than data; give it in text and leave source null.
- source: exactly one path from the list.
- fields: one to five field names of that source, most important first. Prefer the fields a reader needs: what it is, who, when, how much. For stat cards give the one field that names or carries the value.
- filter: at most one condition on a field of the source: eq, contains, gt or lt against a value written as text (dates as YYYY-MM-DD). null when the description does not narrow the items.
- sort: one field and a direction, or null to keep the dashboard's own order.
- limit: how many items to show, 1 to 25; 5 when the description does not say.
- stat: count or value for a stat card, otherwise null.
- text: the note for a text card, otherwise null.
- template: the dashboard also has ready-made cards, listed after the sources with their settings. Name one when it shows what was asked without needing particular fields, a filter or a sort, and give its settings in template_settings as key and value pairs taken from the allowed values (dates as YYYY-MM-DD; leave a date setting out when the description gives none). Otherwise "none" and an empty list. Fill the other fields as well in either case."""


def _catalog_text() -> str:
    lines = []
    for source in catalog().values():
        fields = "; ".join(f"{name} ({about})" for name, about in source.fields.items())
        shape = "list" if source.many else "single value"
        about = f" {source.description}" if source.description else ""
        lines.append(f"- {source.path} [{shape}]{about}\n  fields: {fields}")
    ready = []
    for name, template in templates().items():
        settings = "; ".join(f"{key}: {' | '.join(values) if values else 'a date'}" for key, values in template["settings"].items())
        ready.append(f"- {name}: {template['about']}. settings: {settings}")
    return "Sources:\n" + "\n".join(lines) + "\n\nReady-made cards:\n" + "\n".join(ready)


def choose_model(cfg) -> tuple[str | None, str]:
    model = os.environ.get("CHECK_MODEL", "").strip() or cfg.digest_model_bulk
    effort = os.environ.get("CHECK_EFFORT", "").strip() or "low"
    return (model if model and cfg.openai_api_key else None), effort


class DesignerUnavailable(RuntimeError):
    """No model is configured, or it did not answer."""


def validate(out: CardOut) -> CardSpec:
    """The model's structure checked against the catalog. Raises ValueError with a plain sentence."""
    title = " ".join(out.title.split())[:MAX_TITLE]
    if not title:
        raise ValueError("The card needs a title; describe what it should show.")
    if out.kind == "text":
        text = " ".join((out.text or "").split())[:MAX_TEXT]
        if not text:
            raise ValueError("A note card needs the note's text.")
        return CardSpec.model_validate({"kind": "text", "title": title, "text": text})

    sources = catalog()
    source = sources.get(out.source or "")
    if source is None:
        raise ValueError("That card would need data the dashboard does not hold. A card can show: " + ", ".join(sources) + ".")
    allowed = source.fields

    def known(name: str, what: str) -> str:
        if name not in allowed:
            raise ValueError(f"The {what} '{name}' is not something {source.path} has, so the card was not made.")
        return name

    fields = [known(name, "field") for name in dict.fromkeys(out.fields)][:MAX_FIELDS]
    card_filter = CardFilter(field=known(out.filter.field, "filter field"), op=out.filter.op, value=out.filter.value[:120]) if out.filter else None
    sort = CardSort(field=known(out.sort.field, "sort field"), dir=out.sort.dir) if out.sort else None

    if out.kind == "stat":
        stat = out.stat or ("count" if source.many else "value")
        if stat == "count" and not source.many:
            stat = "value"
        if stat == "value" and source.many:
            raise ValueError(f"{source.path} is a list; a figure card can count its items, or use one of the single values instead.")
        return CardSpec.model_validate(
            {"kind": "stat", "title": title, "source": source.path, "fields": fields or None, "filter": card_filter, "stat": stat}
        )
    if not fields:
        raise ValueError("The card needs at least one field to show.")
    limit = max(1, min(MAX_LIMIT, out.limit or DEFAULT_LIMIT))
    return CardSpec.model_validate(
        {"kind": out.kind, "title": title, "source": source.path, "fields": fields, "limit": limit, "filter": card_filter, "sort": sort}
    )


NO_GENERIC_EQUIVALENT = {"progress", "notes-excerpt", "compare"}  # what they show is not a case-model collection
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_template(out: CardOut) -> CardTemplate | None:
    """The ready-made card the model named, if its settings are all on the list. An unknown
    template or value is dropped silently: the generic spec is still there."""
    template = templates().get(out.template)
    if template is None:
        return None
    settings: dict[str, str] = {}
    for item in out.template_settings:
        allowed = template["settings"].get(item.key, ())
        if item.key not in template["settings"]:
            return None
        if allowed is None:
            if not DATE.match(item.value):
                return None
        elif item.value not in allowed:
            return None
        settings[item.key] = item.value
    required = [key for key, allowed in template["settings"].items() if allowed is not None]
    if any(key not in settings for key in required):
        return None
    return CardTemplate(base=out.template, settings=settings)


def design(cfg, prompt: str, record=None) -> CardDesignResult:
    """`record(usage, ok)` is called with the call's token usage so its cost is counted."""
    model, effort = choose_model(cfg)
    if model is None:
        raise DesignerUnavailable("no model is configured")
    started = time.perf_counter()
    try:
        out, usage = llm.structured(
            cfg, model=model, instructions=INSTRUCTIONS,
            # The catalogue is the same for every description: marked as the reusable prefix, the words last.
            content=[{**llm.text_part(_catalog_text()), **CACHE_BREAKPOINT}, llm.text_part("The lawyer's description of the card:\n" + prompt.strip())],
            schema=CardOut, effort=effort, max_output_tokens=OUTPUT_TOKENS, timeout=TIMEOUT_SECONDS, max_retries=RETRIES,
        )
    except llm.LLMUsageError as error:
        if record:
            record(error.usage, False)
        raise DesignerUnavailable(str(error)) from error
    except llm.LLMError as error:
        raise DesignerUnavailable(str(error)) from error
    if record:
        record(usage, True)
    result = CardDesignResult(model=model, latency_ms=round((time.perf_counter() - started) * 1000), cost_usd=usage.cost_usd)
    if not out.possible:
        result.error = " ".join((out.reason or "").split())[:300] or "That is not something a card can show."
        return result
    result.template = validate_template(out)
    if result.template is not None and result.template.base in NO_GENERIC_EQUIVALENT:
        return result  # the generic card cannot show this; a guess at it would mislead
    try:
        result.spec = validate(out)
    except (ValueError, ValidationError) as error:
        if result.template is None:  # a ready-made card stands on its own when the generic one does not hold
            plain = isinstance(error, ValueError) and not isinstance(error, ValidationError)
            result.error = str(error) if plain else "The card that came back was not a valid card, so it was not made."
    return result
