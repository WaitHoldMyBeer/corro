"""From a lawyer's description of what they are doing to a whole dashboard.

One model call. The model is shown the gallery's registered cards (ids, titles,
what each shows), the ready-made templates with their allowed settings, and the
sources a described card may use; none of it comes from a matter. It returns an
ordered list. Each entry is then checked here: a card id must be registered, a
template's settings must be on its list, a described card must pass the same
catalogue check as describe-a-card. What fails is left out and counted, and
duplicates are dropped. The result is a layout in the shape the dashboard
already stores, never code.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import ValidationError

from shared.card_spec import DashboardDesignResult, DashboardEntry, GalleryCard

from ..digest import llm
from ..digest.schemas import Strict
from . import design
from .gallery import gallery
from .templates import templates

DASHBOARD_VERSION = "dashboard-design-1"
MIN_CARDS, MAX_CARDS = 6, 10
MAX_DESCRIBED = 2
TIMEOUT_SECONDS = 25.0
RETRIES = 1
OUTPUT_TOKENS = 2000  # a ten-card layout is about 600 tokens; the ceiling counts against the per-minute budget


class EntryOut(Strict):
    use: Literal["card", "template", "described"]
    id: str | None
    settings: list[design.SettingOut]
    described: design.CardOut | None
    reason: str


class DashboardOut(Strict):
    possible: bool
    refusal: str | None
    title: str
    cards: list[EntryOut]


INSTRUCTIONS = """You lay out a dashboard for a lawyer working on one legal matter, from the lawyer's description of what they are doing or want to see. You do not write code, you do not answer questions about the matter, and you have no data from it: you only choose which of the dashboard's existing cards to show, and in what order.

You are given three lists: the registered cards (each with an id, a title and what it shows), the ready-made template cards with the settings each takes and their allowed values, and the sources a described card may be built from.

Return:
- possible: false if the description is not about working on a legal matter or asks for something other than a dashboard. Then give refusal, one plain sentence, and an empty list of cards.
- title: a short name for the dashboard in the lawyer's own terms, at most six words.
- cards: eight entries unless the description calls for fewer or more, never fewer than six or more than ten, most important first: what this lawyer needs to read first goes first. No card twice. Each entry:
  - use "card" with the id of a registered card, exactly as given. Prefer these.
  - use "template" with the id of a template and its settings as key and value pairs from the allowed values (dates as YYYY-MM-DD; leave a date setting out if the description gives none). The same template may appear twice with different settings.
  - use "described" at most twice, only when no registered card or template shows what is needed: give the card in described, following the rules for a described card (one source and its field names exactly as listed, kind list, table, timeline, stat or text, an optional filter and sort, a limit) with possible true, template "none" and no template settings. Otherwise described is null.
  - reason: one plain line saying why this card is on this dashboard, in terms of the description, with no figures, names or facts about the matter."""


def _catalogue_text(cards: tuple[GalleryCard, ...]) -> str:
    plain = [f"- {card.id}: {card.title}" + (f" [{card.group}]" if card.group else "") + (f". {card.about}" if card.about else "")
             for card in cards if not card.template]
    ready = []
    for name, template in templates().items():
        settings = "; ".join(f"{key}: {' | '.join(values) if values else 'a date'}" for key, values in template["settings"].items())
        ready.append(f"- {name}: {template['about']}. settings: {settings}")
    sources = design._catalog_text().split("\n\nReady-made cards:")[0]
    return "Registered cards:\n" + "\n".join(plain) + "\n\nTemplate cards:\n" + "\n".join(ready) + "\n\n" + sources


def _entry(out: EntryOut, number: int, by_id: dict[str, GalleryCard]) -> tuple[DashboardEntry, tuple] | None:
    """(entry, what makes it the same card as another) or None when it is not in the catalogue."""
    reason = " ".join(out.reason.split())[:200]
    if out.use == "card":
        card = by_id.get(out.id or "")
        if card is None or card.template:
            return None
        return DashboardEntry(id=card.id, spec=None, title=card.title, reason=reason), ("card", card.id)
    if out.use == "template":
        stand_in = design.CardOut.model_construct(template=out.id, template_settings=out.settings)
        chosen = design.validate_template(stand_in)
        if chosen is None or chosen.base not in by_id:
            return None
        said = ", ".join(chosen.settings.values())
        title = by_id[chosen.base].title + (f": {said}" if said else "")
        entry = DashboardEntry(id=f"{chosen.base}:d{number}", spec={"base": chosen.base, "settings": chosen.settings}, title=title, reason=reason)
        return entry, ("template", chosen.base, tuple(sorted(chosen.settings.items())))
    if out.described is None or not out.described.possible:
        return None
    try:
        spec = design.validate(out.described)
    except (ValueError, ValidationError):
        return None
    entry = DashboardEntry(id=f"custom-d{number}", spec=spec.model_dump(exclude_none=True), title=spec.title, reason=reason)
    return entry, ("described", spec.kind, spec.source, tuple(spec.fields or ()), spec.text)


def assemble(out: DashboardOut, cards: tuple[GalleryCard, ...]) -> DashboardDesignResult:
    """The model's list checked against the catalogue. Kept apart from the call so it can be tested without one."""
    result = DashboardDesignResult.model_validate({})
    if not out.possible:
        result.error = " ".join((out.refusal or "").split())[:300] or "That is not something a dashboard can be laid out for."
        return result
    by_id = {card.id: card for card in cards}
    seen: set[tuple] = set()
    described = 0
    for number, proposed in enumerate(out.cards, start=1):
        made = _entry(proposed, number, by_id)
        if made is None:
            result.skipped += 1
            continue
        entry, same_as = made
        if same_as in seen or (same_as[0] == "described" and described >= MAX_DESCRIBED):
            continue
        seen.add(same_as)
        described += same_as[0] == "described"
        result.cards.append(entry)
        if len(result.cards) == MAX_CARDS:
            break
    if len(result.cards) < MIN_CARDS:
        result.cards, result.error = [], "A dashboard could not be laid out from that description. Say what you are working on, or pick a dashboard template."
        return result
    result.title = " ".join(out.title.split())[:60] or "Dashboard"
    return result


def design_dashboard(cfg, prompt: str, cards: list[GalleryCard] | None = None, record=None) -> DashboardDesignResult:
    model, effort = design.choose_model(cfg)
    if model is None:
        raise design.DesignerUnavailable("no model is configured")
    catalogue = tuple(cards) if cards else gallery()
    started = time.perf_counter()
    try:
        out, usage = llm.structured(
            cfg, model=model, instructions=INSTRUCTIONS,
            content=[{**llm.text_part(_catalogue_text(catalogue)), **design.CACHE_BREAKPOINT}, llm.text_part("The lawyer's description:\n" + prompt.strip())],
            schema=DashboardOut, effort=effort, max_output_tokens=OUTPUT_TOKENS, timeout=TIMEOUT_SECONDS, max_retries=RETRIES,
        )
    except llm.LLMUsageError as error:
        if record:
            record(error.usage, False)
        raise design.DesignerUnavailable(str(error)) from error
    except llm.LLMError as error:
        raise design.DesignerUnavailable(str(error)) from error
    if record:
        record(usage, True)
    result = assemble(out, catalogue)
    result.model, result.latency_ms, result.cost_usd = model, round((time.perf_counter() - started) * 1000), usage.cost_usd
    return result
