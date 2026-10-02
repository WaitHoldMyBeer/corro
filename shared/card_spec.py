"""A card the lawyer described in words, as data: POST /api/matters/{matter_id}/cards/design.

The response is a specification, never code. It names one collection of the case
model, the fields to show, and optionally one filter and one sort. The browser
renders it with a generic card (web/v2/js/cards.js re-validates it). Which
collections and fields may be named is derived from `shared/contract.py` in
`server/cards/catalog.py`; a spec naming anything else is refused.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .contract import Model

CardKind = Literal["stat", "list", "table", "timeline", "text"]
FilterOp = Literal["eq", "contains", "gt", "lt"]

MAX_FIELDS = 5
MAX_LIMIT = 25
MAX_TITLE = 80
MAX_TEXT = 280


class CardFilter(Model):
    field: str
    op: FilterOp
    value: str = Field(description="Compared as text, or as a number or date for gt and lt.")


class CardSort(Model):
    field: str
    dir: Literal["asc", "desc"] = "asc"


class CardSpec(Model):
    kind: CardKind
    title: str = Field(max_length=MAX_TITLE)
    source: str | None = Field(None, description="One allowed path into the case model. Absent for a text card.")
    fields: list[str] | None = Field(None, description="Up to five field names of the source's items; the first is the headline.")
    limit: int | None = Field(None, ge=1, le=MAX_LIMIT)
    filter: CardFilter | None = None
    sort: CardSort | None = None
    stat: Literal["count", "value"] | None = Field(None, description="Stat cards: count the items, or show the single value.")
    text: str | None = Field(None, max_length=MAX_TEXT, description="Text cards: the note itself, as the lawyer worded it.")


class CardTemplate(Model):
    """A ready-made gallery card with its settings chosen: the same shape the dashboard
    layout stores for a template instance (`base` is the gallery card's id)."""

    base: str
    settings: dict[str, str] = Field(default_factory=dict)


class CardDesignRequest(Model):
    prompt: str = Field(min_length=3, max_length=600, description="The card, described in the lawyer's words.")


class CardDesignResult(Model):
    """Either a card or `error`, a plain sentence for the dialog. A card comes as `template` (a
    gallery card with settings) when one fits the description, as `spec` (the generic card)
    otherwise, and as both when the generic card can show the same thing: a client that does
    not know templates uses `spec`."""

    spec: CardSpec | None = None
    template: CardTemplate | None = None
    error: str | None = None
    model: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None


class CardSource(Model):
    """One entry of GET /api/cards/catalog: what a card may be built from."""

    path: str
    many: bool = Field(description="True for a list of items, false for a single value.")
    description: str = ""
    fields: dict[str, str] = Field(default_factory=dict, description="Field name -> its type and, where the contract has one, its description.")


# --------------------------------------------------------------------------- a whole dashboard from a description

CARD_ID = r"^[a-z0-9][a-z0-9:_-]{0,59}$"


class GalleryCard(Model):
    """One registered card of the gallery, as data: what a designed dashboard may place."""

    id: str = Field(pattern=CARD_ID)
    title: str = Field(max_length=60)
    group: str = Field("", max_length=30)
    template: bool = False
    about: str = Field("", max_length=200)


class DashboardDesignRequest(Model):
    prompt: str = Field(min_length=3, max_length=600, description="What the lawyer wants the dashboard for, in their words.")
    cards: list[GalleryCard] | None = Field(
        None, max_length=80, description="The browser's card registry. When absent the server reads it from the card manifests."
    )


class DashboardEntry(Model):
    """One card of a designed dashboard, in the shape PUT /api/matters/{id}/dashboard stores
    (`id`, optional `spec`), plus two lines for the preview."""

    id: str
    spec: dict | None = Field(None, description="Absent for a registered card; {base, settings} for a template; a CardSpec for a described card.")
    title: str = ""
    reason: str = Field("", description="One line: why this card is on this dashboard.")


class DashboardDesignResult(Model):
    """Either a layout of six to ten cards in display order, or `error`, a plain sentence."""

    title: str | None = None
    cards: list[DashboardEntry] = Field(default_factory=list)
    skipped: int = Field(0, description="Entries the model proposed that were not in the catalogue and were left out.")
    error: str | None = None
    model: str | None = None
    latency_ms: int = 0
    cost_usd: float | None = None
