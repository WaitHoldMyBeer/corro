"""Route of the case graph. Mounted by `server/app.py`:

    from .graph.api import router as graph_router
    app.include_router(graph_router)

The payload is built once per ledger version, stored gzipped in our own database
and held in memory, so a request is a version check and a read. Firm-only: the
path is under /api/, which `firm_auth` guards.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..case import CaseBuilder, MatterNotSynced
from ..config import Settings, get_settings
from ..db import connect, now_iso
from . import build

router = APIRouter()

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_cache (
    matter_id  INTEGER PRIMARY KEY,
    version    TEXT    NOT NULL,
    body_gzip  BLOB    NOT NULL,
    raw_bytes  INTEGER NOT NULL,
    build_ms   REAL    NOT NULL,
    built_at   TEXT    NOT NULL
);
"""

_memory: dict[int, tuple[str, bytes]] = {}  # matter id -> (version, gzipped body)
_building = threading.Lock()


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def stored(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> tuple[str, bytes]:
    """(version, gzipped JSON) for the matter as it stands, building it if nothing current is held."""
    current = build.version(cfg, conn, matter_id)
    held = _memory.get(matter_id)
    if held and held[0] == current:
        return held
    with _building:
        held = _memory.get(matter_id)
        if held and held[0] == current:
            return held
        conn.executescript(CACHE_SCHEMA)
        row = conn.execute("SELECT version, body_gzip FROM graph_cache WHERE matter_id=?", (matter_id,)).fetchone()
        if row and row["version"] == current:
            held = (current, bytes(row["body_gzip"]))
        else:
            started = time.perf_counter()
            payload = build.build(cfg, conn, CaseBuilder(conn, matter_id))
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            # mtime=0 keeps the bytes identical for identical content
            held = (payload["version"], gzip.compress(raw, compresslevel=9, mtime=0))
            conn.execute(
                "INSERT OR REPLACE INTO graph_cache (matter_id, version, body_gzip, raw_bytes, build_ms, built_at)"
                " VALUES (?,?,?,?,?,?)",
                (matter_id, held[0], held[1], len(raw), (time.perf_counter() - started) * 1000, now_iso()),
            )
            conn.commit()
        _memory[matter_id] = held
        return held


@router.get("/api/matters/{matter_id}/graph")
def graph(
    matter_id: int,
    request: Request,
    cfg: Annotated[Settings, Depends(_settings)],
    conn: Annotated[sqlite3.Connection, Depends(_db)],
) -> Response:
    """Nodes with fixed positions, the links the data holds, every claim, and the search index
    the page scores in the browser. Sends an ETag; a reload gets 304 until the matter changes."""
    try:
        version, body = stored(cfg, conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error
    tag = f'"{version}"'
    headers = {"ETag": tag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding"}
    if tag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    if "gzip" in (request.headers.get("accept-encoding") or "").lower():
        return Response(content=body, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(content=gzip.decompress(body), media_type="application/json", headers=headers)
