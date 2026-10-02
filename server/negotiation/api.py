"""Routes for the negotiation analysis. Mounted by `server/app.py`, before the static files:

    from .negotiation.api import router as negotiation_router
    app.include_router(negotiation_router)

Every route is under /api/matters/ and so behind the firm session guard. The
analysis is strategy and valuation: it is not part of the case model, not part
of any provider view, and there is no share category for it.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError

from ..case import CaseBuilder, MatterNotSynced
from ..config import Settings, get_settings
from ..db import connect, get_setting, set_setting
from ..digest import llm, overlay
from . import analysis, evidence
from . import model as m

router = APIRouter()
log = logging.getLogger("negotiation")


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


UNUSABLE = "The assumptions saved for this matter could not be used and were set aside. Set them again under Assumptions."


def _inputs(conn: sqlite3.Connection, matter_id: int) -> tuple[m.Inputs, bool]:
    """The stored assumptions, and whether they could be read. A stored value that no longer validates is treated as unset."""
    raw = get_setting(conn, f"negotiation_inputs:{matter_id}")
    try:
        return (m.Inputs.model_validate_json(raw) if raw else m.Inputs()), True
    except ValueError:
        return m.Inputs(), False


def _analyse(cfg: Settings, conn: sqlite3.Connection, matter_id: int, inputs: m.Inputs | None = None) -> tuple[m.Analysis, list[dict]]:
    try:
        build = CaseBuilder(conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error
    case = overlay.apply(cfg, build, build.build(None))
    basis, usable = build.fee_basis(), True
    if inputs is None:
        inputs, usable = _inputs(conn, matter_id)
        try:
            analysis.build(case, inputs, basis)
        except (ArithmeticError, ValueError):  # a stored value the arithmetic cannot take must never break the page
            inputs, usable = m.Inputs(), False
    body = evidence.payload(case, analysis.build(case, inputs, basis).open_facts)
    result = analysis.build(case, inputs, basis, evidence.cached(conn, matter_id, body) if body else None)
    if not usable:
        result.notes.insert(0, UNUSABLE)
    return result, body


@router.get("/api/matters/{matter_id}/negotiation", response_model=m.Analysis)
def get_negotiation(matter_id: int, cfg: Config, conn: Database) -> m.Analysis:
    """The analysis under the stored inputs. Arithmetic only; no model call happens here."""
    return _analyse(cfg, conn, matter_id)[0]


@router.put("/api/matters/{matter_id}/negotiation/inputs", response_model=m.Analysis)
def put_inputs(matter_id: int, payload: Annotated[dict, Body()], cfg: Config, conn: Database) -> m.Analysis:
    """Store the attorney's assumptions (our database, never the source system) and return the analysis under them.
    The body is an `Inputs` object, validated here so a refusal names the field and never echoes the value."""
    try:
        inputs = m.Inputs.model_validate(payload)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in error.errors())
        raise HTTPException(422, problems) from error
    result = _analyse(cfg, conn, matter_id, inputs)[0]  # computed first: inputs that cannot be analysed are never stored
    set_setting(conn, f"negotiation_inputs:{matter_id}", inputs.model_dump_json())
    return result


@router.post("/api/matters/{matter_id}/negotiation/evidence", response_model=m.Analysis)
def post_evidence(matter_id: int, cfg: Config, conn: Database) -> m.Analysis:
    """Write the two-sided evidence summaries once per ledger version, then return the analysis with them."""
    result, body = _analyse(cfg, conn, matter_id)
    if not body or result.evidence_state == "ready":
        return result
    try:
        evidence.summarise(cfg, conn, matter_id, body)
    except llm.LLMError as error:
        log.warning("negotiation: evidence summaries unavailable (%s)", type(error).__name__)
        raise HTTPException(503, "The evidence summaries are not available right now. The figures do not depend on them.") from error
    return _analyse(cfg, conn, matter_id)[0]
