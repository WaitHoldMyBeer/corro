"""Routes of the live checker. Mounted by `server/app.py`:

    from .check.api import router as check_router
    app.include_router(check_router)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from shared import check_contract as k

from ..case import MatterNotSynced
from ..config import Settings, get_settings
from ..db import connect
from . import engine

router = APIRouter()
log = logging.getLogger("check")


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

_synthetic = None


def _synthetic_checker():
    """CHECK_SYNTHETIC=1 serves a made-up ledger and the lexical stand-in for
    every matter id, for building the UI before a case has been digested. The
    ledger version says so, and it is never on unless the variable is set."""
    global _synthetic
    if _synthetic is None:
        from . import synthetic

        ledger, _ = synthetic.build()
        ledger.version = "synthetic"
        _synthetic = synthetic.checker(ledger)
    return _synthetic


def _use_synthetic() -> bool:
    return os.environ.get("CHECK_SYNTHETIC", "").strip() in ("1", "true", "yes")


@router.post("/api/matters/{matter_id}/check", response_model=k.CheckResult)
def post_check(matter_id: int, request: k.CheckRequest, cfg: Config, conn: Database) -> k.CheckResult:
    """Check what is being written, said or received against the matter's ledger. Reads only."""
    if _use_synthetic():
        return _synthetic_checker().run(request)
    try:
        return engine.check(cfg, conn, matter_id, request)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error
    except (*engine.RECOVERABLE, ValidationError) as error:
        # The editor keeps working without the checker; the error type is enough to find the cause.
        # The error type only: a validation error's own text quotes the input, and the input is case text.
        log.warning("check: request failed (%s)", type(error).__name__)
        raise HTTPException(503, f"checker unavailable ({type(error).__name__})") from error


@router.get("/api/matters/{matter_id}/check/stats", response_model=k.CheckStats)
def get_check_stats(matter_id: int, cfg: Config, conn: Database) -> k.CheckStats:
    if _use_synthetic():
        return _synthetic_checker().stats()
    try:
        return engine.stats(cfg, conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error
