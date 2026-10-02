"""Spend route. Mounted by `server/app.py`:

    from .costs.api import router as costs_router
    app.include_router(costs_router)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from shared.spend_contract import SpendReport

from ..config import Settings, get_settings
from ..db import connect
from .report import report

router = APIRouter()


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


@router.get("/api/spend", response_model=SpendReport)
def get_spend(
    conn: Annotated[sqlite3.Connection, Depends(_db)],
    matter_id: Annotated[int | None, Query(description="One case: every figure is then that case's. Absent: the total across all cases, with each case's share.")] = None,
) -> SpendReport:
    """What the model calls have cost, by purpose, with what the provider's prompt cache saved or cost. Firm only."""
    return report(conn, matter_id)
