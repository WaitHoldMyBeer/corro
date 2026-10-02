"""FastAPI app: the JSON API and the static UI in `web/`.

Every POST and PUT here is to our own API and writes to our own SQLite
database. The only traffic to Clio is in `server/clio/` and it is GET.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Iterator

import httpx
from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from shared import contract as c

from . import cases, firm_auth, pages, share, sources, threads
from .case import CaseBuilder, MatterNotSynced, share_entry
from .clio import oauth
from .clio.client import ClioError
from .config import ROOT, Settings, get_settings
from .db import connect, get_setting, now_iso, set_setting
from .digest import overlay, pipeline
from .sync import list_matters, make_client, sync_matter

app = FastAPI(title="Corro", version=c.CONTRACT_VERSION)
# Every /api route except a provider's own link needs a firm session.
firm_auth.install(app)


@app.exception_handler(OverflowError)
def _id_out_of_range(request: Request, error: OverflowError) -> Response:
    """An id too large for the database cannot name anything we hold: answer 404, not a server error."""
    return Response(content='{"detail":"no such record"}', status_code=404, media_type="application/json")


def settings() -> Settings:
    return get_settings()


def _held_or_404(conn: sqlite3.Connection, matter_id: int) -> None:
    """State is stored only for a case we hold."""
    if conn.execute("SELECT 1 FROM clio_items WHERE matter_id=? AND kind='matter'", (matter_id,)).fetchone() is None:
        raise HTTPException(404, f"matter {matter_id} has not been imported yet")


def db(cfg: Settings = Depends(settings)) -> Iterator[sqlite3.Connection]:
    conn = connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


def builder(matter_id: int, conn: sqlite3.Connection = Depends(db)) -> CaseBuilder:
    try:
        return CaseBuilder(conn, matter_id)
    except MatterNotSynced as error:
        raise HTTPException(404, str(error)) from error


# --------------------------------------------------------------------------- Clio connection


@app.get("/oauth/start")
def oauth_start(cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)):
    try:
        return RedirectResponse(oauth.authorize_url(cfg, conn))
    except oauth.NotAuthorized as error:
        raise HTTPException(400, str(error)) from error


@app.get("/oauth/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
):
    if error or not code:
        raise HTTPException(400, f"Clio did not grant access: {error or 'no code returned'}")
    try:
        oauth.exchange_code(cfg, conn, code, state)
    except oauth.NotAuthorized as failure:
        raise HTTPException(400, str(failure)) from failure
    return RedirectResponse("/")


@app.get("/api/status")
def status(cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)) -> dict:
    selected = get_setting(conn, "selected_matter_id")
    return {
        "clio_connected": oauth.is_connected(conn),
        "clio_configured": bool(cfg.clio_client_id and cfg.clio_client_secret),
        "digest_configured": bool(cfg.openai_api_key and cfg.digest_model),
        "selected_matter_id": int(selected) if selected else cfg.clio_matter_id,
        "contract_version": c.CONTRACT_VERSION,
    }


# --------------------------------------------------------------------------- matters


def _synced_at(conn: sqlite3.Connection, matter_id: int) -> str | None:
    row = conn.execute(
        "SELECT finished_at FROM sync_runs WHERE matter_id=? AND error IS NULL AND finished_at IS NOT NULL"
        " ORDER BY id DESC LIMIT 1",
        (matter_id,),
    ).fetchone()
    return row["finished_at"] if row else None


# The matter picker never waits on the source system when we already hold something to show.
# The list read from Clio is kept in memory and refreshed in the background once it is older
# than MATTER_LIST_TTL_S; a page load is answered at once from memory or from our database.
# (Clio also allows only about 50 requests a minute per token at peak.)
MATTER_LIST_TTL_S = 60.0
MATTER_LIST_RETRY_S = 15.0
_matter_list: dict = {"at": float("-inf"), "rows": [], "refreshing": False}
_matter_list_lock = threading.Lock()


def _refresh_matter_list(cfg: Settings, quick: bool = False) -> None:
    """Read the account's matters from Clio (one GET). `quick` is for the one case where a
    request waits on it; the background refresh can afford Clio's slower answers."""
    conn = connect(cfg.db_path)
    client = make_client(cfg, conn, quick=quick)
    try:
        rows = list_matters(client)
        _matter_list.update(at=time.monotonic(), rows=rows)
    except (ClioError, oauth.NotAuthorized, httpx.HTTPError, OSError, ValueError):
        # Unreachable, or an answer that is not the list: what we hold keeps being served, and the next attempt waits a little.
        _matter_list["at"] = time.monotonic() - MATTER_LIST_TTL_S + MATTER_LIST_RETRY_S
    finally:
        client.close()
        conn.close()
        _matter_list["refreshing"] = False


@app.on_event("startup")
def _warm_matter_list() -> None:
    """Read the list once behind start-up, so the first page load after a restart has it."""
    cfg = get_settings()
    conn = connect(cfg.db_path)
    try:
        connected = oauth.is_connected(conn)
    finally:
        conn.close()
    if connected and not _matter_list["refreshing"]:
        _matter_list["refreshing"] = True
        threading.Thread(target=_refresh_matter_list, args=(cfg,), daemon=True).start()


@app.get("/api/matters", response_model=c.MatterList)
def matters(
    include_archived: bool = Query(False, description="true also lists cases the firm has archived"),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> c.MatterList:
    """The matter picker. `live` is true when the list is the one last read from Clio,
    false when it is the matters held in our database."""
    selected = get_setting(conn, "selected_matter_id")
    result = c.MatterList(
        connected=oauth.is_connected(conn),
        selected_matter_id=int(selected) if selected else cfg.clio_matter_id,
    )
    held = [
        json.loads(row["payload"])
        for row in conn.execute("SELECT payload FROM clio_items WHERE kind='matter' AND removed_at IS NULL")
    ]
    rows: list[dict] = []
    if result.connected:
        fresh = time.monotonic() - _matter_list["at"] < MATTER_LIST_TTL_S
        if not fresh and (held or _matter_list["rows"]):
            with _matter_list_lock:  # something to show already: refresh behind the response
                if not _matter_list["refreshing"]:
                    _matter_list["refreshing"] = True
                    threading.Thread(target=_refresh_matter_list, args=(cfg,), daemon=True).start()
        elif not fresh:
            _matter_list["refreshing"] = True  # nothing held yet (first run): the picker needs the list now
            _refresh_matter_list(cfg, quick=True)
        if _matter_list["rows"]:
            rows, result.live = _matter_list["rows"], True
    if not result.live:
        rows = held
    else:
        # The source system's list, plus the cases created in our own store (it knows nothing of those).
        rows = list(rows) + [row for row in held if cases.source_of(row) == "upload"]
    archived = cases.archived_ids(conn)
    for row in rows:
        if int(row["id"]) in archived and not include_archived:
            continue  # archived cases are left out unless asked for
        result.items.append(
            c.MatterListItem(
                id=int(row["id"]),
                display_number=row.get("display_number"),
                description=row.get("description"),
                status=row.get("status"),
                client_name=(row.get("client") or {}).get("name"),
                synced_at=_synced_at(conn, int(row["id"])),
                source=cases.source_of(row),
                archived=int(row["id"]) in archived,
            )
        )
    return result


@app.post("/api/matters", response_model=c.MatterListItem)
def create_case(body: c.NewCase, conn: sqlite3.Connection = Depends(db)) -> c.MatterListItem:
    """Create an empty case in our own store; documents are added to it by upload. Nothing is sent anywhere."""
    if not body.name.strip():
        raise HTTPException(422, "a case needs a name")
    case_id = cases.create_case(conn, body.name, body.client_name, body.number)
    return c.MatterListItem(
        id=case_id, display_number=body.number, description=body.name, status="Open", client_name=body.client_name, source="upload"
    )


@app.post("/api/matters/{matter_id}/archive", response_model=c.MatterListItem)
def archive_case(matter_id: int, conn: sqlite3.Connection = Depends(db)) -> c.MatterListItem:
    """Hide a case from the lawyer's lists. A flag in our store only."""
    return _set_archived(conn, matter_id, True)


@app.post("/api/matters/{matter_id}/restore", response_model=c.MatterListItem)
def restore_case(matter_id: int, conn: sqlite3.Connection = Depends(db)) -> c.MatterListItem:
    return _set_archived(conn, matter_id, False)


def _set_archived(conn: sqlite3.Connection, matter_id: int, archived: bool) -> c.MatterListItem:
    if not cases.set_archived(conn, matter_id, archived):
        raise HTTPException(404, "no such case in our store")
    row = next(m for m in cases.held_matters(conn) if int(m["id"]) == matter_id)
    return c.MatterListItem(
        id=matter_id, display_number=row.get("display_number"), description=row.get("description"), status=row.get("status"),
        client_name=(row.get("client") or {}).get("name"), synced_at=_synced_at(conn, matter_id),
        source=cases.source_of(row), archived=archived,
    )


@app.get("/api/firm/overview", response_model=c.FirmOverview)
def firm_overview(
    archived: bool = Query(True, description="false leaves archived cases out of `cases`"),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> c.FirmOverview:
    """Every case the firm holds on one page: stored data only, no source call, no model call."""
    return cases.firm_overview(cfg, conn, include_archived=archived)


@app.post("/api/matters/{matter_id}/sync", response_model=c.SyncResult)
def sync(matter_id: int, cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)) -> c.SyncResult:
    """Read the matter from Clio into our database. GET requests only."""
    held = conn.execute("SELECT payload FROM clio_items WHERE matter_id=? AND kind='matter'", (matter_id,)).fetchone()
    if held is not None and cases.source_of(json.loads(held["payload"])) == "upload":
        raise HTTPException(409, "This case was created here and filled by upload; there is nothing to import for it.")
    client = make_client(cfg, conn)
    try:
        report = sync_matter(cfg, conn, client, matter_id)
    except oauth.NotAuthorized as error:
        raise HTTPException(401, str(error)) from error
    except ClioError as error:
        raise HTTPException(502, f"Clio returned {error.status}") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "Clio could not be reached; the stored copy of the matter is unchanged") from error
    finally:
        client.close()
    set_setting(conn, "selected_matter_id", str(matter_id))
    return c.SyncResult(
        matter_id=matter_id,
        requests=report.requests,
        changed=report.changed,
        kinds={kind: vars(tally) for kind, tally in report.kinds.items()},
        documents_downloaded=report.documents_downloaded,
        warnings=report.warnings,
    )


def etag_response(request: Request, model, volatile: tuple[str, ...]) -> Response:
    """JSON with an ETag over everything except the generation timestamp, so a poller
    gets 304 Not Modified until something a viewer can see has actually changed."""
    body = model.model_dump_json()
    stable = body
    for key in volatile:
        stable = stable.replace(f'"{key}":"{getattr(model, key, None) or getattr(model.meta, key, "")}"', "")
    tag = '"' + hashlib.sha256(stable.encode()).hexdigest()[:32] + '"'
    headers = {"ETag": tag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@app.get("/api/matters/{matter_id}/case", response_model=c.CaseModel)
def case(
    request: Request,
    since: str | None = Query(None, description="ISO timestamp; default is this matter's last open."),
    claims: str = Query("referenced", pattern="^(referenced|all)$", description="'all' returns every extracted claim."),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
):
    """The whole case. Sends an ETag; poll with If-None-Match (browsers do) and get 304 until it changes."""
    since = since or _seen_since(build.conn, build.matter_id)
    return etag_response(request, full_case(cfg, build, since, all_claims=claims == "all"), ("generated_at",))


def full_case(cfg: Settings, build: CaseBuilder, since: str | None = None, all_claims: bool = False) -> c.CaseModel:
    """What Clio's own fields give, with the stored digest laid over it. No model call happens here."""
    return overlay.apply(cfg, build, build.build(since), all_claims)


@app.post("/api/matters/{matter_id}/digest", response_model=c.DigestStatus)
def start_digest(
    reconcile: bool = Query(False, description="true = also rebuild the conflict cards from the current claims"),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> c.DigestStatus:
    """Digest whatever is new or changed since the last run, in the background. Existing
    conflict cards are left as they are unless `reconcile` is set; `meta.digest` says
    when they are out of date and how long and how much the last rebuild took."""
    if not (cfg.openai_api_key and cfg.digest_model):
        raise HTTPException(400, "OPENAI_API_KEY and DIGEST_MODEL must be set in .env")
    pipeline.start_background(cfg, build.matter_id, reconcile)
    return overlay.digest_status(cfg, build.conn, build.matter_id)


@app.get("/api/matters/{matter_id}/digest", response_model=c.DigestStatus)
def get_digest(cfg: Settings = Depends(settings), build: CaseBuilder = Depends(builder)) -> c.DigestStatus:
    return overlay.digest_status(cfg, build.conn, build.matter_id, pipeline.collect_claims(cfg, build.conn, build.matter_id))


def _seen_since(conn: sqlite3.Connection, matter_id: int) -> str | None:
    """The moment "since you last opened" is measured from."""
    return get_setting(conn, f"seen_since:{matter_id}") or get_setting(conn, f"last_opened:{matter_id}")


@app.post("/api/matters/{matter_id}/opened")
def opened(matter_id: int, cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)) -> dict:
    """Record that the matter was opened. `previous` is the moment "since you last
    opened" is measured from, and opening another tab does not move it.

    The marker moves in two cases only: this open starts a new visit (the matter had
    not been opened for `VISIT_GAP_HOURS`), when it becomes the time of the previous
    open, so what changed while away shows for this visit; or the lawyer marks
    everything as seen (POST .../seen)."""
    _held_or_404(conn, matter_id)
    now, last = now_iso(), get_setting(conn, f"last_opened:{matter_id}")
    marker, moved = _seen_since(conn, matter_id), False
    if last is None:
        marker, moved = now, True  # first open ever: nothing predates it
    elif _hours_between(last, now) > cfg.visit_gap_hours and marker != last:
        marker, moved = last, True
    set_setting(conn, f"last_opened:{matter_id}", now)
    set_setting(conn, f"seen_since:{matter_id}", marker)
    return {"previous": marker, "since": marker, "now": now, "moved": moved, "visit_gap_hours": cfg.visit_gap_hours}


@app.post("/api/matters/{matter_id}/seen")
def seen(matter_id: int, conn: sqlite3.Connection = Depends(db)) -> dict:
    """The lawyer has seen what is on Recent activity: measure from now on."""
    _held_or_404(conn, matter_id)
    previous, now = _seen_since(conn, matter_id), now_iso()
    set_setting(conn, f"seen_since:{matter_id}", now)
    set_setting(conn, f"last_opened:{matter_id}", now)
    return {"previous": previous, "since": now, "now": now}


def _hours_between(earlier: str, later: str) -> float:
    from datetime import datetime

    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: E731
    return (parse(later) - parse(earlier)).total_seconds() / 3600.0


# --------------------------------------------------------------------------- sources and documents


def _claim(cfg: Settings, build: CaseBuilder, claim_id: str) -> dict | None:
    return next((x for x in pipeline.collect_claims(cfg, build.conn, build.matter_id) if x["id"] == claim_id), None)


@app.get("/api/matters/{matter_id}/sources/{kind}/{clio_id}", response_model=c.SourceDetail)
def source(
    kind: c.SourceKind,
    clio_id: str,
    page: int | None = None,
    quote: str | None = None,
    claim: str | None = Query(None, description="A claim id: the drawer opens on that claim's quote."),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> c.SourceDetail:
    found = _claim(cfg, build, claim) if claim else None
    if found is not None:
        detail = sources.detail(
            cfg, build, kind.value, clio_id, page or found["page"], found["quote"], claim, bool(found["quote_verified"])
        )
    else:
        detail = sources.detail(cfg, build, kind.value, clio_id, page, quote)
    if detail is None:
        raise HTTPException(404, "no such source on this matter")
    return detail


@app.get("/api/matters/{matter_id}/documents/{document_id}/file")
def document_file(matter_id: int, document_id: int, cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)):
    path = pages.blob_path(cfg, conn, matter_id, document_id)
    if path is None:
        raise HTTPException(404, "document not downloaded")
    # Only a file that really is a PDF is shown in the browser. Anything else (a page, a picture with
    # script, a program that arrived in an archive) is handed back as a download, never rendered on this origin.
    with path.open("rb") as handle:
        is_pdf = b"%PDF-" in handle.read(1024)
    if is_pdf:
        return FileResponse(path, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"})
    return FileResponse(path, media_type="application/octet-stream", filename=f"document{document_id}.bin", headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/matters/{matter_id}/documents/{document_id}/pages/{page}.png")
def document_page(
    matter_id: int,
    document_id: int,
    page: int,
    crop: str | None = Query(None, description="left,top,right,bottom as fractions of the page"),
    mark: str | None = Query(None, description="A claim id: its quote is highlighted where the page's text has it."),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
):
    quote = None
    if mark:
        found = next((x for x in pipeline.collect_claims(cfg, conn, matter_id) if x["id"] == mark), None)
        if found and str(found["clio_id"]) == str(document_id) and found["page"] == page:
            quote = found["quote"]
    png = pages.page_png(cfg, conn, matter_id, document_id, page, pages.parse_crop(crop), quote)
    if png is None:
        raise HTTPException(404, "no such page")
    return FileResponse(png, media_type="image/png")


# --------------------------------------------------------------------------- provider sharing


def _provider_or_404(build: CaseBuilder, contact_id: int) -> None:
    contact = build.contact_by_id.get(contact_id)
    if contact is None or contact.role != c.ContactRole.provider.value:
        raise HTTPException(404, "not a treating provider on this matter")


@app.get("/api/matters/{matter_id}/providers/{contact_id}/policy", response_model=c.SharePolicy)
def get_policy(contact_id: int, build: CaseBuilder = Depends(builder)) -> c.SharePolicy:
    _provider_or_404(build, contact_id)
    return build.policy(contact_id)


@app.put("/api/matters/{matter_id}/providers/{contact_id}/policy", response_model=c.SharePolicy)
def put_policy(contact_id: int, policy: c.SharePolicy, build: CaseBuilder = Depends(builder)) -> c.SharePolicy:
    _provider_or_404(build, contact_id)
    policy.contact_id = contact_id
    try:
        return share.save_policy(build.conn, build.matter_id, policy)
    except share.NotShareable as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/matters/{matter_id}/providers/{contact_id}/preview", response_model=c.ProviderView)
def preview(contact_id: int, cfg: Settings = Depends(settings), build: CaseBuilder = Depends(builder)) -> c.ProviderView:
    """Exactly what this provider would see under the current policy."""
    _provider_or_404(build, contact_id)
    return share.provider_view(full_case(cfg, build), contact_id, build.policy(contact_id), preview=True)


@app.post("/api/matters/{matter_id}/providers/{contact_id}/share", response_model=c.ShareLogEntry)
def send_share(
    contact_id: int, request: c.ShareRequest, cfg: Settings = Depends(settings), build: CaseBuilder = Depends(builder)
) -> c.ShareLogEntry:
    """Freeze and send what the attorney previewed. 409 if it changed since the preview; 423 if
    text the attorney wrote is held by the checker for a provider and no reason came with it."""
    _provider_or_404(build, contact_id)
    try:
        return share.send(
            build.conn,
            build,
            full_case(cfg, build),
            contact_id,
            previewed=request.preview_hash,
            checker=share.provider_checker(cfg, build.conn, build.matter_id, contact_id),
            override_reason=request.override_reason,
        )
    except share.PreviewChanged as error:
        raise HTTPException(409, str(error)) from error
    except share.LockedText as error:
        raise HTTPException(423, error.detail()) from error


@app.get("/api/matters/{matter_id}/shares/{share_id}/snapshot", response_model=c.ProviderView)
def share_snapshot(matter_id: int, share_id: int, conn: sqlite3.Connection = Depends(db)) -> c.ProviderView:
    """For the attorney: exactly what was sent. Does not count as the provider opening it."""
    row = conn.execute("SELECT * FROM shares WHERE id=? AND matter_id=?", (share_id, matter_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such share")
    return share.snapshot(conn, row)


@app.post("/api/matters/{matter_id}/shares/{share_id}/revoke", response_model=c.ShareLogEntry)
def revoke_share(matter_id: int, share_id: int, conn: sqlite3.Connection = Depends(db)) -> c.ShareLogEntry:
    row = share.revoke(conn, matter_id, share_id)
    if row is None:
        raise HTTPException(404, "no such share")
    return share_entry(row)


def _live_share(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    row = share.find_live(conn, token)
    if row is None:
        raise HTTPException(410, "this link is not valid, has expired, or was withdrawn by the firm")
    return row


@app.get("/api/share/{token}", response_model=c.ProviderView)
def shared_view(
    request: Request,
    token: str,
    peek: bool = Query(False, description="true = a poll for changes; not logged as an open"),
    conn: sqlite3.Connection = Depends(db),
):
    """The provider's link: the view as frozen when the attorney sent it. A page load
    is logged as an open; a `peek` poll is not, and neither is a load from a browser
    signed in to the firm side: the share log records what the provider saw, and the
    firm looking at its own link is not the provider opening it. The ETag is over the
    provider's own view, so it changes only when something that provider can see changes."""
    row = _live_share(conn, token)
    if not peek and not firm_auth.signed_in(request):
        share.record_open(conn, row)
    return etag_response(request, share.snapshot(conn, row), ("generated_at",))


@app.post("/api/share/{token}/asks/{ask_id}/reply", response_model=c.ProviderView)
def reply(
    token: str,
    ask_id: str,
    background: BackgroundTasks,
    text: str = Body(..., embed=True, min_length=1, max_length=4000),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> c.ProviderView:
    """The provider answers an ask. Stored in our database; Clio is not touched."""
    row = _live_share(conn, token)
    if ask_id not in {ask.id for ask in share.snapshot(conn, row).asks}:
        raise HTTPException(404, "that request is not part of this link")
    share.reply_to_ask(conn, row, ask_id, text)
    background.add_task(share.check_incoming, cfg, row["matter_id"], row["contact_id"])
    return share.snapshot(conn, row)


# --------------------------------------------------------------------------- provider requests and thread


@app.post("/api/share/{token}/requests", response_model=c.ProviderView)
def provider_request(
    token: str,
    background: BackgroundTasks,
    kind: str = Body(..., embed=True),
    text: str | None = Body(None, embed=True, max_length=2000),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> c.ProviderView:
    """The provider asks the firm something. Stored in our database; the firm sees it in its inbox."""
    row = _live_share(conn, token)
    try:
        threads.create_request(conn, row["matter_id"], row["contact_id"], kind, text)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    background.add_task(share.check_incoming, cfg, row["matter_id"], row["contact_id"])
    return share.snapshot(conn, row)


@app.post("/api/share/{token}/messages", response_model=c.ProviderView)
def provider_message(
    token: str,
    background: BackgroundTasks,
    text: str = Body(..., embed=True, min_length=1, max_length=4000),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> c.ProviderView:
    row = _live_share(conn, token)
    threads.add_message(conn, row["matter_id"], row["contact_id"], "from_provider", text)
    background.add_task(share.check_incoming, cfg, row["matter_id"], row["contact_id"])
    return share.snapshot(conn, row)


@app.get("/api/matters/{matter_id}/providers/{contact_id}/thread", response_model=c.ProviderThread)
def provider_thread(contact_id: int, build: CaseBuilder = Depends(builder)) -> c.ProviderThread:
    _provider_or_404(build, contact_id)
    allowed = list(build.policy(contact_id).allowed_categories)
    return c.ProviderThread(
        requests=threads.requests(build.conn, build.matter_id, contact_id, allowed),
        thread=threads.messages(build.conn, build.matter_id, contact_id),
    )


@app.get("/api/matters/{matter_id}/incoming-checks/unreviewed", response_model=dict[int, int])
def incoming_unreviewed(matter_id: int, conn: sqlite3.Connection = Depends(db)) -> dict[int, int]:
    """Provider contact id -> texts from that provider the file contradicts and nobody has reviewed.
    Stored results only: cheap enough for a badge on a list."""
    return share.unreviewed_contradictions(conn, matter_id)


@app.get("/api/matters/{matter_id}/providers/{contact_id}/incoming-checks", response_model=c.ProviderIncoming)
def incoming_checks(
    contact_id: int, cfg: Settings = Depends(settings), build: CaseBuilder = Depends(builder)
) -> c.ProviderIncoming:
    """What this provider wrote, read against the file, with the attorney's review of each.
    Firm side only; nothing under /api/share/ carries it."""
    _provider_or_404(build, contact_id)
    return share.provider_incoming(cfg, build.conn, build.matter_id, contact_id)


@app.put("/api/matters/{matter_id}/providers/{contact_id}/incoming-checks/review", response_model=c.ProviderIncoming)
def review_incoming_check(
    contact_id: int,
    item: str = Body(..., embed=True, max_length=200),
    review: str = Body(..., embed=True, pattern="^(unreviewed|confirmed|dismissed)$"),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> c.ProviderIncoming:
    """The attorney's call on one checked text from a provider. Stored in our database."""
    _provider_or_404(build, contact_id)
    try:
        share.review_incoming(build.conn, build.matter_id, contact_id, item, review)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    return share.provider_incoming(cfg, build.conn, build.matter_id, contact_id)


@app.get("/api/matters/{matter_id}/providers/{contact_id}/overrides")
def send_overrides(contact_id: int, build: CaseBuilder = Depends(builder)) -> list[dict]:
    """Every sentence that went to this provider over the checker's hold: the sentence, why it
    was held, the attorney's reason and the time. `share_id` matches a ShareLogEntry.id; `item`
    is `message:<Message.id>` for a thread message. Firm side only."""
    _provider_or_404(build, contact_id)
    return share.overrides(build.conn, build.matter_id, contact_id)


def _release(cfg: Settings, build: CaseBuilder, contact_id: int, text: str, override_reason: str | None, store) -> None:
    """The firm's own words to a provider pass the checker on the server, whatever the browser
    did. `store` writes the message and runs only if it may go."""
    checker = share.provider_checker(cfg, build.conn, build.matter_id, contact_id)
    try:
        share.release(build.conn, checker, build.matter_id, contact_id, text, override_reason, store)
    except share.LockedText as error:
        raise HTTPException(423, error.detail()) from error


@app.post("/api/matters/{matter_id}/providers/{contact_id}/messages", response_model=c.ProviderThread)
def firm_message(
    contact_id: int,
    text: str = Body(..., embed=True, min_length=1, max_length=4000),
    override_reason: str | None = Body(None, embed=True, max_length=1000),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> c.ProviderThread:
    """The firm writes to a provider. The text is read by the checker as that provider would receive
    it: 423 if a sentence is held and no reason came with it; with a reason it is sent and logged."""
    _provider_or_404(build, contact_id)
    _release(
        cfg, build, contact_id, text, override_reason,
        lambda: threads.add_message(build.conn, build.matter_id, contact_id, "from_firm", text),
    )
    return provider_thread(contact_id, build)


@app.post("/api/matters/{matter_id}/providers/{contact_id}/requests/{request_id}/answer", response_model=c.ProviderThread)
def answer_request(
    contact_id: int,
    request_id: int,
    action: str = Body(..., embed=True, pattern="^(enable_category|resend|reply|decline)$"),
    text: str | None = Body(None, embed=True, max_length=4000),
    override_reason: str | None = Body(None, embed=True, max_length=1000),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> c.ProviderThread:
    """Answer a provider's request.

    enable_category: switch on the category the request is about. Nothing is sent: the
    attorney reviews the updated preview and presses Send, and that send closes the
    request. resend: the category is already on; same, nothing is sent here. reply:
    answer in words (`text`), read by the checker first. decline: close it with a
    fixed line, or with `text`, read by the checker first."""
    _provider_or_404(build, contact_id)
    row = build.conn.execute(
        "SELECT * FROM provider_requests WHERE id=? AND matter_id=? AND contact_id=?",
        (request_id, build.matter_id, contact_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such request from this provider")
    if action in ("enable_category", "resend"):
        category = threads.REQUEST_KINDS.get(row["kind"], threads.REQUEST_KINDS["other"])[1]
        if category is None:
            raise HTTPException(422, "this kind of request is not answered by a category; reply in words")
        policy = build.policy(contact_id)
        if category.value not in policy.allowed_categories:
            policy.allowed_categories.append(category)
            share.save_policy(build.conn, build.matter_id, policy)
    elif action == "reply":
        if not (text or "").strip():
            raise HTTPException(422, "a reply needs text")
        _release(
            cfg, build, contact_id, text, override_reason,
            lambda: threads.answer(build.conn, build.matter_id, contact_id, request_id, "answered", text.strip()),
        )
    elif (text or "").strip():
        _release(
            cfg, build, contact_id, text, override_reason,
            lambda: threads.answer(build.conn, build.matter_id, contact_id, request_id, "declined", text.strip()),
        )
    else:
        threads.answer(build.conn, build.matter_id, contact_id, request_id, "declined", threads.DECLINE_TEXT)
    return provider_thread(contact_id, build)


# --------------------------------------------------------------------------- firm settings and review


@app.get("/api/settings/fee")
def get_fee(conn: sqlite3.Connection = Depends(db)) -> dict:
    percent = get_setting(conn, "fee_percent")
    return {"percent": float(percent) if percent else None, "basis": get_setting(conn, "fee_basis") or "gross"}


@app.put("/api/settings/fee")
def put_fee(
    percent: float | None = Body(None, embed=True, ge=0, le=100),
    basis: str = Body("gross", embed=True, pattern="^(gross|after_costs)$"),
    conn: sqlite3.Connection = Depends(db),
) -> dict:
    """The contingency fee is the firm's own term; Clio does not hold it for this matter."""
    set_setting(conn, "fee_percent", "" if percent is None else str(percent))
    set_setting(conn, "fee_basis", basis)
    return {"percent": percent, "basis": basis}


@app.put("/api/matters/{matter_id}/conflicts/{conflict_id}/review")
def review_conflict(
    matter_id: int,
    conflict_id: str,
    review: str = Body(..., embed=True, pattern="^(unreviewed|confirmed|dismissed)$"),
    cfg: Settings = Depends(settings),
    build: CaseBuilder = Depends(builder),
) -> dict:
    """The attorney's call on a notes-versus-document conflict. Stored with the claims the
    card rested on, so the call follows the conflict if the cards are rebuilt."""
    conn, at = build.conn, now_iso()
    card = next((x for x in full_case(cfg, build).conflicts if x.id == conflict_id), None)
    if card is None:
        raise HTTPException(404, "no such difference on this case")
    claim_ids = json.dumps(card.notes_claim_ids + card.document_claim_ids) if card else None
    conn.execute(
        "INSERT INTO conflict_reviews (matter_id, conflict_id, review, reviewed_at, claim_ids) VALUES (?,?,?,?,?)"
        " ON CONFLICT(matter_id, conflict_id) DO UPDATE SET review=excluded.review, reviewed_at=excluded.reviewed_at,"
        " claim_ids=COALESCE(excluded.claim_ids, claim_ids)",
        (matter_id, conflict_id, review, at, claim_ids),
    )
    conn.commit()
    return {"conflict_id": conflict_id, "review": review, "reviewed_at": at}


@app.get("/api/contract.schema.json")
def schema() -> Response:
    return FileResponse(ROOT / "shared" / "contract.schema.json", media_type="application/json")


# --------------------------------------------------------------------------- import (settings area)
# The same reads from the matter's source system under the names the Settings / Import area uses.
# The source today is Clio Manage, read-only; the older /oauth and /sync paths remain.


@app.get("/api/settings/import")
def import_status(
    matter_id: int | None = Query(None, description="The case the page is on; default is the last one imported."),
    cfg: Settings = Depends(settings),
    conn: sqlite3.Connection = Depends(db),
) -> dict:
    """Where matters are imported from and whether that source is connected."""
    if matter_id is None:
        selected = get_setting(conn, "selected_matter_id")
        matter_id = int(selected) if selected else cfg.clio_matter_id
    return {
        "source": "Clio Manage",
        "access": "read-only",
        "configured": bool(cfg.clio_client_id and cfg.clio_client_secret),
        "connected": oauth.is_connected(conn),
        "connect_href": "/api/settings/import/connect",
        "selected_matter_id": matter_id,
        "last_import_at": _synced_at(conn, matter_id) if matter_id else None,
        "import_href": f"/api/matters/{matter_id}/import" if matter_id and matter_id > 0 else None,
    }


@app.get("/api/settings/import/connect")
def import_connect(cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)):
    return oauth_start(cfg, conn)


@app.post("/api/matters/{matter_id}/import", response_model=c.SyncResult)
def import_matter(matter_id: int, cfg: Settings = Depends(settings), conn: sqlite3.Connection = Depends(db)) -> c.SyncResult:
    """Read the matter again from its source into our database (GET requests only)."""
    return sync(matter_id, cfg, conn)


# The live fact-checker (server/check/) brings its own routes.
from .check.api import router as check_router  # noqa: E402

app.include_router(check_router)

# The case graph and its search index (server/graph/) bring their own route.
from .graph.api import router as graph_router  # noqa: E402

app.include_router(graph_router)

# Card design from a description, and the catalog it is checked against (server/cards/).
from .cards.api import router as cards_router  # noqa: E402

app.include_router(cards_router)

# The review queue: five statements every five days to keep, discard or comment on (server/review_queue.py).
from .review_queue import router as review_queue_router  # noqa: E402

app.include_router(review_queue_router)

# The assistant: a tool-using reader over the ledger and the graph (server/assistant/).
from .assistant.api import router as assistant_router  # noqa: E402

app.include_router(assistant_router)

# Negotiation: inputs, evidence and the worked position (server/negotiation/).
from .negotiation.api import router as negotiation_router  # noqa: E402

app.include_router(negotiation_router)

# Model spend by purpose, read from our own usage ledger (server/costs/).
from .costs.api import router as costs_router  # noqa: E402

app.include_router(costs_router)

# Document upload and incremental ingestion (server/ingest.py).
from .ingest import router as ingest_router  # noqa: E402

app.include_router(ingest_router)

# Customise what one provider is shown by a written instruction (server/share_customise.py).
from .share_customise import router as share_customise_router  # noqa: E402

app.include_router(share_customise_router)

# Dashboard layout, important documents and record lists (server/dashboard.py).
from .dashboard import router as dashboard_router  # noqa: E402

app.include_router(dashboard_router)

# The UI: static files, no build step. Mounted last so /api and /oauth win.
class RevalidatedStaticFiles(StaticFiles):
    """Static files that the browser must revalidate on every use. They still carry an
    ETag, so an unchanged file costs a 304; a changed one is never served stale after a deploy."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatedStaticFiles(directory=ROOT / "web", html=True, check_dir=False), name="web")
