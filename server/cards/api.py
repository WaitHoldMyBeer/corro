"""Routes for describing a card in words. Mounted by `server/app.py`:

    from .cards.api import router as cards_router
    app.include_router(cards_router)
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from shared.card_spec import (
    CardDesignRequest,
    CardDesignResult,
    CardSource,
    DashboardDesignRequest,
    DashboardDesignResult,
)

from ..config import Settings, get_settings
from ..db import connect
from . import dashboard as dashboards
from . import design as designer
from .catalog import catalog

router = APIRouter()
log = logging.getLogger("cards")


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


Config = Annotated[Settings, Depends(_settings)]
Database = Annotated[sqlite3.Connection, Depends(_db)]


@router.post("/api/matters/{matter_id}/cards/design", response_model=CardDesignResult, response_model_exclude_none=True)
def design_card(matter_id: int, request: CardDesignRequest, cfg: Config, conn: Database) -> CardDesignResult:
    """A card specification from a description. Reads nothing from the matter: the id only says whose cost this is."""
    from ..digest.pipeline import record_call

    def record(usage, ok: bool) -> None:
        try:
            record_call(conn, matter_id, None, "card_design", usage, ok)
        except sqlite3.Error as error:
            log.warning("cards: could not record a call (%s)", type(error).__name__)

    try:
        result = designer.design(cfg, request.prompt, record)
    except designer.DesignerUnavailable as error:
        log.warning("cards: designer unavailable (%s)", error)  # the error text carries the error type only
        raise HTTPException(503, "The card designer is not available right now. Pick a card from the gallery, or try again in a moment.") from error
    log.info("cards: design %s in %d ms", "made" if result.spec else "refused", result.latency_ms)
    return result


@router.post("/api/matters/{matter_id}/cards/dashboard", response_model=DashboardDesignResult, response_model_exclude_none=True)
def design_dashboard(matter_id: int, request: DashboardDesignRequest, cfg: Config, conn: Database) -> DashboardDesignResult:
    """A whole dashboard layout from a description: six to ten cards from the gallery's catalogue, in the
    shape the dashboard route stores. Reads nothing from the matter."""
    from ..digest.pipeline import record_call

    def record(usage, ok: bool) -> None:
        try:
            record_call(conn, matter_id, None, "dashboard_design", usage, ok)
        except sqlite3.Error as error:
            log.warning("cards: could not record a call (%s)", type(error).__name__)

    try:
        result = dashboards.design_dashboard(cfg, request.prompt, request.cards, record)
    except designer.DesignerUnavailable as error:
        log.warning("cards: dashboard designer unavailable (%s)", error)
        raise HTTPException(503, "The dashboard designer is not available right now. Pick a dashboard template, or try again in a moment.") from error
    log.info("cards: dashboard %s in %d ms, %d cards, %d left out", "made" if result.cards else "refused", result.latency_ms, len(result.cards), result.skipped)
    return result


@router.get("/api/cards/catalog", response_model=list[CardSource])
def card_catalog() -> list[CardSource]:
    """What a described card may be built from: sources and fields, derived from the contract."""
    return list(catalog().values())
