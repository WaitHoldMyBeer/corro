"""Command line: `uv run python -m server <command>`.

  auth            connect Clio (opens the consent page, waits for the callback;
                  `auth --code <code>` exchanges a code pasted from Clio's page)
  matters         list the account's matters, read from Clio
  sync            pull one matter into SQLite and download its documents
  probe           one small GET per endpoint, to check access and field names
  digest          read new or changed documents and records with the model
  digest-probe    measure tokens and cost on one page before a full run
  export-schema   write shared/contract.schema.json
  serve           run the API and the UI
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .clio import oauth
from .clio import resources as res
from .clio.client import ClioError
from .config import ROOT, get_settings
from .db import connect, get_setting, set_setting
from .sync import list_matters, make_client, sync_matter

SCHEMA_PATH = ROOT / "shared" / "contract.schema.json"


def cmd_auth(args) -> int:
    settings = get_settings()
    conn = connect(settings.db_path)
    if args.code:
        oauth.exchange_code(settings, conn, args.code.strip(), None, pasted=True)
        print("Clio connected.")
        return 0
    url = oauth.authorize_url(settings, conn)
    redirect = urlparse(settings.clio_redirect_uri)
    if redirect.hostname not in ("127.0.0.1", "localhost"):
        # e.g. https://app.clio.com/oauth/approval: Clio shows the code on screen.
        print("Open this page, approve read access, then paste the code Clio shows:\n")
        print(f"  {url}\n")
        if not args.no_browser:
            webbrowser.open(url)
        oauth.exchange_code(settings, conn, input("Authorization code: ").strip(), None, pasted=True)
        print("Clio connected.")
        return 0
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != redirect.path:
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            try:
                if "error" in query:
                    raise oauth.NotAuthorized(f"Clio refused: {query['error'][0]}")
                oauth.exchange_code(settings, conn, query["code"][0], query.get("state", [None])[0])
                result["ok"] = "Clio connected. You can close this tab."
            except Exception as error:  # shown to the person at the keyboard
                result["error"] = str(error)
            body = (result.get("ok") or result["error"]).encode()
            self.send_response(200 if "ok" in result else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # keep the code and state out of the terminal
            pass

    try:
        server = HTTPServer((redirect.hostname or "127.0.0.1", redirect.port or 80), Callback)
    except OSError:
        print(f"Port {redirect.port} is in use (is `serve` running?). Open this instead:")
        print(f"  {redirect.scheme}://{redirect.netloc}/oauth/start")
        return 1
    print("Open this page and approve read access:\n")
    print(f"  {url}\n")
    if not args.no_browser:
        webbrowser.open(url)
    while not result:
        server.handle_request()
    server.server_close()
    print(result.get("ok") or f"Failed: {result['error']}")
    return 0 if "ok" in result else 1


def _resolve_matter_id(args, settings, conn, client) -> int | None:
    """Flag, then env, then the last one chosen, then the only one in the account."""
    chosen = args.matter or settings.clio_matter_id or get_setting(conn, "selected_matter_id")
    if chosen:
        return int(chosen)
    matters = list_matters(client)
    if len(matters) == 1:
        return int(matters[0]["id"])
    print("More than one matter in this Clio account. Pick one with --matter <id>:")
    _print_matters(matters)
    return None


def _print_matters(matters) -> None:
    for m in matters:
        client_name = (m.get("client") or {}).get("name") or ""
        print(f"  {m['id']:>12}  {m.get('display_number') or '':<24} {m.get('status') or '':<8} {client_name}")


def cmd_matters(args) -> int:
    settings = get_settings()
    conn = connect(settings.db_path)
    client = make_client(settings, conn)
    matters = list_matters(client)
    print(f"{len(matters)} matter(s) in Clio:")
    _print_matters(matters)
    return 0


def cmd_sync(args) -> int:
    settings = get_settings()
    conn = connect(settings.db_path)
    client = make_client(settings, conn)
    matter_id = _resolve_matter_id(args, settings, conn, client)
    if matter_id is None:
        return 2
    report = sync_matter(settings, conn, client, matter_id, download=not args.no_documents)
    set_setting(conn, "selected_matter_id", str(matter_id))
    print(f"Matter {matter_id} synced from Clio with {report.requests} GET requests.\n")
    print(f"  {'object':<16}{'total':>7}{'new':>7}{'updated':>9}{'removed':>9}")
    for kind in res.REPORTED_KINDS:
        tally = report.kinds.get(kind)
        if tally:
            print(f"  {kind:<16}{tally.total:>7}{tally.new:>7}{tally.updated:>9}{tally.removed:>9}")
    custom_values = conn.execute(
        "SELECT json_array_length(payload, '$.custom_field_values') AS n FROM clio_items"
        " WHERE matter_id=? AND kind='matter'",
        (matter_id,),
    ).fetchone()
    print(f"\n  custom field values on the matter: {custom_values['n'] if custom_values else 0}")
    blobs = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b FROM document_blobs WHERE matter_id=?",
        (matter_id,),
    ).fetchone()
    print(
        f"  document files cached: {blobs['n']} ({blobs['b'] / 1e6:.1f} MB),"
        f" downloaded this run: {report.documents_downloaded} ({report.bytes_downloaded / 1e6:.1f} MB)"
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")
    print(f"\n{report.changed} changed item(s).")
    return 0


def cmd_probe(args) -> int:
    """One GET with limit=1 per endpoint. Prints status only, never content."""
    settings = get_settings()
    conn = connect(settings.db_path)
    client = make_client(settings, conn)
    matter_id = _resolve_matter_id(args, settings, conn, client)
    checks = [("who_am_i", res.WHO_AM_I[0], {"fields": res.WHO_AM_I[1]})]
    checks.append(("matters", "matters.json", {"fields": res.MATTER_LIST_FIELDS, "limit": 1}))
    if matter_id is not None:
        checks.append(("matter", f"matters/{matter_id}.json", {"fields": res.MATTER_FIELDS}))
        for r in res.MATTER_RESOURCES:
            checks.append((r.kind, r.path, {"fields": r.fields, "limit": 1, **r.params(matter_id)}))
    failed = 0
    for name, path, params in checks:
        try:
            page = client.get(path, params)
            records = (page.get("meta") or {}).get("records")
            print(f"  ok    {name:<16} records={records if records is not None else '-'}")
        except ClioError as error:
            failed += 1
            print(f"  FAIL  {name:<16} {error.status} {error.body[:300]}")
    return 1 if failed else 0


def cmd_digest(args) -> int:
    from .digest import overlay, pipeline

    settings = get_settings()
    conn = connect(settings.db_path)
    matter_id = args.matter or settings.clio_matter_id or get_setting(conn, "selected_matter_id")
    if not matter_id:
        print("No matter selected: run `sync` first or pass --matter <id>.")
        return 2
    matter_id = int(matter_id)
    before = overlay.digest_status(settings, conn, matter_id)
    print(
        f"Matter {matter_id}: {before.pages_digested}/{before.pages_total} pages and"
        f" {before.items_digested}/{before.items_total} records already digested."
    )
    if not settings.openai_api_key or not settings.digest_model:
        print("OPENAI_API_KEY and DIGEST_MODEL must both be set in .env before the digest can run (see .env.example).")
        return 2
    if args.reconcile and before.reconcile_estimate_seconds:
        print(
            f"Rebuilding the conflict cards: the last time took {before.reconcile_estimate_seconds:.0f} s"
            f" and cost ${before.reconcile_estimate_usd or 0:.2f}. Reviews follow their conflicts by claim id."
        )
    progress = pipeline.Progress()
    stop = threading.Event()
    worker = threading.Thread(
        target=lambda: pipeline.run(settings, connect(settings.db_path), matter_id, stop, progress, args.reconcile)
    )
    worker.start()
    try:
        while worker.is_alive():
            worker.join(timeout=10)
            print(
                f"  pages {progress.pages_done}/{progress.pages_total}  records {progress.items_done}/{progress.items_total}"
                f"  calls {progress.calls}  cost ${progress.cost_usd:.2f}  errors {len(progress.errors)}",
                flush=True,
            )
    except KeyboardInterrupt:
        print("Stopping after the requests in flight; run again to resume.")
        stop.set()
        worker.join()
    after = overlay.digest_status(settings, conn, matter_id)
    print(
        f"\nSent this run: {progress.pages_sent} pages, {progress.items_sent} records, {progress.calls} model calls,"
        f" ${progress.cost_usd:.4f}. Reconciled: {'yes' if progress.reconciled else 'no'}."
        f" Incoming communications checked: {progress.incoming_checked}."
    )
    if after.reconcile_stale:
        print(
            f"{after.changed_since_reconcile} record(s) changed since the conflict cards were built; the cards are"
            " unchanged. Run `digest --reconcile` to rebuild them."
    )
    for usage in after.usage:
        print(
            f"  {usage.model}: {usage.requests} calls, {usage.input_tokens} in"
            f" ({usage.cache_read_input_tokens} cached), {usage.output_tokens} out, ${usage.cost_usd:.4f}"
        )
    print(f"Cost of this case so far: ${after.cost_usd_total:.4f}")
    for error in progress.errors[:20]:
        print(f"  error: {error}")
    return 0 if not progress.errors else 1


def cmd_digest_probe(args) -> int:
    """Measure one page: tokens, seconds and cost at a given resolution and model. Counts only, unless --show."""
    from .digest import pipeline

    settings = get_settings()
    conn = connect(settings.db_path)
    matter_id = int(args.matter or settings.clio_matter_id or get_setting(conn, "selected_matter_id") or 0)
    row = conn.execute(
        "SELECT path FROM document_blobs WHERE matter_id=? AND document_id=?", (matter_id, args.document)
    ).fetchone()
    if row is None:
        print("No such downloaded document on the selected matter.")
        return 2
    pages = [int(p) for p in args.pages.split(",")]
    for dpi in [int(d) for d in args.dpi.split(",")]:
        for model in args.model or [settings.digest_model_bulk]:
            jpeg, _ = pipeline.render_page(settings.data_dir / row["path"], pages[0], dpi)
            try:
                batch, usage = pipeline.read_pages(settings, settings.data_dir / row["path"], pages, model, dpi, args.detail)
            except pipeline.llm.LLMError as error:
                print(f"dpi {dpi} {model}: FAILED {error}")
                continue
            pipeline.record_call(conn, matter_id, None, "probe", usage, True)
            facts = sum(len(read.facts) for read in batch.pages)
            boxes = sum(len(read.checkboxes) for read in batch.pages)
            print(
                f"dpi {dpi} detail {args.detail} {model}: {len(pages)} page(s), first JPEG {len(jpeg) / 1000:.0f} kB,"
                f" {usage.input_tokens} in / {usage.output_tokens} out ({usage.reasoning_tokens} reasoning),"
                f" {usage.seconds:.1f} s, ${usage.cost_usd if usage.cost_usd is not None else float('nan'):.4f};"
                f" {facts} facts, {boxes} checkboxes"
            )
            if args.show:
                for read in batch.pages:
                    print(f"   page {read.page}: {read.page_type}; photo={read.shows_photo_of_a_person}")
                    for box in read.checkboxes:
                        print(f"     [{'x' if box.checked else ' '}] {box.label}")
                    for fact in read.facts:
                        print(f"     {fact.kind}: {fact.statement}")
    return 0


def export_schema() -> str:
    from shared.contract import ContractBundle

    schema = ContractBundle.model_json_schema()
    # The live checker keeps its request and result shapes in its own module; they join the one schema file here.
    from shared.check_contract import CheckRequest, CheckResult

    for model in (CheckRequest, CheckResult):
        extra = model.model_json_schema(ref_template="#/$defs/{model}")
        schema["$defs"].update(extra.pop("$defs", {}))
        schema["$defs"][model.__name__] = extra
    schema["properties"]["check_request"] = {"$ref": "#/$defs/CheckRequest"}
    schema["properties"]["check_result"] = {"$ref": "#/$defs/CheckResult"}
    # Card design from a description keeps its shapes in shared/card_spec.py.
    from shared.card_spec import (
        CardDesignRequest,
        CardDesignResult,
        CardSpec,
        DashboardDesignRequest,
        DashboardDesignResult,
    )

    for model in (CardDesignRequest, CardDesignResult, CardSpec, DashboardDesignRequest, DashboardDesignResult):
        extra = model.model_json_schema(ref_template="#/$defs/{model}")
        schema["$defs"].update(extra.pop("$defs", {}))
        schema["$defs"][model.__name__] = extra
    schema["properties"]["card_design_request"] = {"$ref": "#/$defs/CardDesignRequest"}
    schema["properties"]["card_design_result"] = {"$ref": "#/$defs/CardDesignResult"}
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    SCHEMA_PATH.write_text(text)
    return text


def cmd_export_schema(args) -> int:
    export_schema()
    print(f"wrote {SCHEMA_PATH.relative_to(ROOT)}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="connect Clio")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--code", help="an authorization code copied from Clio's approval page")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("matters", help="list matters in the Clio account")
    p.set_defaults(func=cmd_matters)

    p = sub.add_parser("sync", help="pull one matter into SQLite")
    p.add_argument("--matter", type=int, help="Clio matter id (default: CLIO_MATTER_ID, or the only matter)")
    p.add_argument("--no-documents", action="store_true", help="skip downloading document bytes")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("probe", help="check each endpoint with one small GET")
    p.add_argument("--matter", type=int)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("digest", help="read new or changed documents and records with the model")
    p.add_argument("--matter", type=int)
    p.add_argument("--reconcile", action="store_true", help="also rebuild the conflict cards from the current claims")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("digest-probe", help="measure tokens and cost on one page before a full run")
    p.add_argument("--matter", type=int)
    p.add_argument("--document", type=int, required=True)
    p.add_argument("--pages", default="1", help="comma-separated 1-based pages sent in one request")
    p.add_argument("--dpi", default="110,150", help="comma-separated render resolutions to compare")
    p.add_argument("--detail", default="high")
    p.add_argument("--model", action="append", help="model id; repeat to compare (default DIGEST_MODEL_BULK)")
    p.add_argument("--show", action="store_true", help="print what was read (do not use on medical pages)")
    p.set_defaults(func=cmd_digest_probe)

    p = sub.add_parser("export-schema", help="write shared/contract.schema.json")
    p.set_defaults(func=cmd_export_schema)

    p = sub.add_parser("serve", help="run the API and UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except oauth.NotAuthorized as error:
        print(f"Not connected to Clio: {error}", file=sys.stderr)
        return 2
    except ClioError as error:
        print(f"Clio error: {error}", file=sys.stderr)
        return 1
