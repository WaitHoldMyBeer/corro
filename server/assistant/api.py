"""Routes of the assistant. Mounted by `server/app.py`:

    from .assistant.api import router as assistant_router
    app.include_router(assistant_router)

Firm-only: every path is under /api/, which `firm_auth` guards. The assistant
reads the stored file and writes only to its own tables; it has no route that
sends anything to a provider, so what it drafts can leave the firm only through
the existing share and message routes and their guard.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from shared import assistant_contract as a

from ..case import MatterNotSynced
from ..config import Settings, get_settings
from ..db import connect, items
from . import documents, engine, pdf, store
from .tools import Toolbox

router = APIRouter()
log = logging.getLogger("assistant")

UNAVAILABLE = "The assistant is not available right now. Try again in a moment."


def _settings() -> Settings:
    return get_settings()


def _db(cfg: Annotated[Settings, Depends(_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        store.ensure(conn)
        yield conn
    finally:
        conn.close()


Config = Annotated[Settings, Depends(_settings)]
Database = Annotated[sqlite3.Connection, Depends(_db)]


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _stream(cfg: Settings, matter_id: int, request: a.AssistantRequest) -> Iterator[str]:
    """The turn runs on its own thread; its progress is relayed as server-sent events."""
    events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def work() -> None:
        try:
            turn = engine.answer(cfg, matter_id, request, lambda name, payload: events.put((name, payload)))
            events.put(("done", turn.model_dump(mode="json")))
        except engine.AssistantUnavailable as error:
            log.warning("assistant: unavailable (%s)", error)  # the error text carries the error type only
            events.put(("error", {"message": UNAVAILABLE}))
        except MatterNotSynced:
            events.put(("error", {"message": "This matter has not been imported yet."}))
        except Exception as error:  # the stream must end with an event, whatever happened
            log.warning("assistant: turn failed (%s)", type(error).__name__)
            events.put(("error", {"message": UNAVAILABLE}))
        finally:
            events.put(None)

    threading.Thread(target=work, name=f"assistant-{matter_id}", daemon=True).start()
    while True:
        try:
            item = events.get(timeout=15)
        except queue.Empty:
            yield ": waiting\n\n"  # keeps a proxy from closing an idle stream
            continue
        if item is None:
            return
        yield _event(*item)


@router.post("/api/matters/{matter_id}/assistant", response_model=a.AssistantTurn)
def ask(matter_id: int, request: a.AssistantRequest, cfg: Config, conn: Database):
    """One turn: the answer as blocks, every reference resolved, and the trace of what was read.
    With `stream`, the same turn as server-sent events: start, round, tool (one per call), then done."""
    if not items(conn, matter_id, "matter"):
        raise HTTPException(404, f"matter {matter_id} has not been imported yet")
    if request.stream:
        return StreamingResponse(
            _stream(cfg, matter_id, request), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )
    try:
        return engine.answer(cfg, matter_id, request)
    except engine.AssistantUnavailable as error:
        log.warning("assistant: unavailable (%s)", error)
        raise HTTPException(503, UNAVAILABLE) from error


@router.get("/api/matters/{matter_id}/assistant/conversations", response_model=list[a.ConversationInfo])
def list_conversations(matter_id: int, conn: Database) -> list[a.ConversationInfo]:
    """The history list: title, times, turn count and the documents each conversation created."""
    return store.conversations(conn, matter_id)


@router.get("/api/matters/{matter_id}/assistant/conversations/{conversation_id}", response_model=a.Conversation)
def get_conversation(matter_id: int, conversation_id: str, conn: Database) -> a.Conversation:
    turns = store.turns(conn, matter_id, conversation_id)
    if not turns:
        raise HTTPException(404, "no such conversation on this matter")
    listed = next(iter(store.conversations(conn, matter_id, only=conversation_id)), None)
    return a.Conversation(id=conversation_id, title=listed.title if listed else store.title_of(turns[0].question), turns=turns)


@router.put("/api/matters/{matter_id}/assistant/conversations/{conversation_id}", response_model=a.ConversationInfo)
def rename_conversation(matter_id: int, conversation_id: str, request: a.RenameRequest, conn: Database) -> a.ConversationInfo:
    if not store.turns(conn, matter_id, conversation_id):
        raise HTTPException(404, "no such conversation on this matter")
    store.rename_conversation(conn, matter_id, conversation_id, request.title)
    return store.conversations(conn, matter_id, only=conversation_id)[0]  # looked up by id, so its age does not matter


@router.delete("/api/matters/{matter_id}/assistant/conversations/{conversation_id}", status_code=204)
def delete_conversation(matter_id: int, conversation_id: str, conn: Database) -> Response:
    """Removes the conversation from our database. Documents it created stay in the files list."""
    if not store.delete_conversation(conn, matter_id, conversation_id):
        raise HTTPException(404, "no such conversation on this matter")
    return Response(status_code=204)


@router.get("/api/matters/{matter_id}/assistant/document-kinds", response_model=list[a.DocumentKindInfo])
def document_kinds(matter_id: int) -> list[a.DocumentKindInfo]:
    """The documents the server can build in code, to offer as starting points."""
    return documents.kinds()


@router.get("/api/matters/{matter_id}/assistant/documents", response_model=list[a.DocumentInfo])
def list_documents(matter_id: int, conn: Database) -> list[a.DocumentInfo]:
    return store.documents(conn, matter_id)


@router.post("/api/matters/{matter_id}/assistant/documents", response_model=a.SavedDocument)
def build_document(matter_id: int, request: a.DocumentRequest, cfg: Config, conn: Database) -> a.SavedDocument:
    """Build one document in code from the stored file and save it. No model call."""
    if not items(conn, matter_id, "matter"):
        raise HTTPException(404, f"matter {matter_id} has not been imported yet")
    box = Toolbox(cfg, conn, matter_id)
    document = documents.compose(box, request.kind, title=request.title, date_from=request.date_from, date_to=request.date_to,
                                 provider=request.provider)
    citations = {}
    for entry in document.entries:
        kept = []
        for ref in entry.cite:
            citation = citations.get(ref) or box.citation(ref)
            if citation is not None:
                citations[ref] = citation
                kept.append(ref)
        entry.cite = kept
    saved = a.SavedDocument(document=document, citations=citations)
    store.save_document(conn, matter_id, saved)
    return saved


def _matter_line(conn: sqlite3.Connection, matter_id: int) -> str:
    """How the matter is named on a document, read from the stored matter."""
    matter = (items(conn, matter_id, "matter") or [{}])[0]
    parts = [matter.get("display_number"), matter.get("description"), (matter.get("client") or {}).get("name")]
    return " - ".join(dict.fromkeys(str(part).strip() for part in parts if part)) or f"Matter {matter_id}"


@router.get("/api/matters/{matter_id}/assistant/documents/{document_id}.pdf")
def get_document_pdf(matter_id: int, document_id: str, request: Request, conn: Database, download: int = 0) -> Response:
    """The document as a PDF, laid out in code from our saved copy each time it is asked for (and kept in
    memory per document id). Nothing is written into the matter's documents."""
    saved = store.document(conn, matter_id, document_id)
    if saved is None:
        raise HTTPException(404, "no such document on this matter")
    tag = f'"{document_id}"'
    headers = {"ETag": tag, "Cache-Control": "private, max-age=3600"}
    if tag in (request.headers.get("if-none-match") or ""):
        return Response(status_code=304, headers=headers)
    body, pages = pdf.cached(matter_id, saved, _matter_line(conn, matter_id))
    store.set_pages(conn, matter_id, document_id, pages)
    name = "".join(ch if ch.isalnum() or ch in " -_" else " " for ch in saved.document.title).strip()[:80] or "document"
    headers |= {"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.pdf"', "X-Page-Count": str(pages)}
    return Response(content=body, media_type="application/pdf", headers=headers)


@router.get("/api/matters/{matter_id}/assistant/documents/{document_id}", response_model=a.SavedDocument)
def get_document(matter_id: int, document_id: str, conn: Database) -> a.SavedDocument:
    saved = store.document(conn, matter_id, document_id)
    if saved is None:
        raise HTTPException(404, "no such document on this matter")
    return saved


@router.delete("/api/matters/{matter_id}/assistant/documents/{document_id}", status_code=204)
def delete_document(matter_id: int, document_id: str, conn: Database) -> Response:
    """Removes our saved copy. The matter itself holds nothing of it."""
    if not store.delete_document(conn, matter_id, document_id):
        raise HTTPException(404, "no such document on this matter")
    pdf.forget(matter_id, document_id)
    return Response(status_code=204)
