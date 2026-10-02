"""Spend by purpose, from `llm_calls`. Arithmetic only.

`usd` is what the calls cost as each call's usage was priced when it ran.
`usd_without_caching` reprices the same calls with every input token at the
plain input rate: the difference is what the provider's prompt cache saved, or
cost, since writing to that cache can be billed above the plain rate.
"""

from __future__ import annotations

import sqlite3

from shared.spend_contract import CaseSpend, SpendLine, SpendReport, StoredWork

from ..db import now_iso
from ..digest import llm

GROUPS = (  # purpose prefix -> group; first match wins
    # The digest line is the cost of reading the case, the same calls the cost-per-case figure counts.
    # One-off probes made while setting up (measuring a page before a full run) are not part of it.
    ("pages", "digest"), ("claims", "digest"), ("reconcile", "digest"), ("photo", "digest"), ("probe", "other"),
    ("check", "check"), ("card_design", "card_design"), ("dashboard_design", "dashboard_design"),
    ("assistant", "assistant"), ("chat", "assistant"), ("ingest", "ingestion"), ("negotiat", "negotiation"),
)
ORDER = ["digest", "check", "card_design", "dashboard_design", "assistant", "ingestion", "negotiation", "other"]


def group_of(purpose: str) -> str:
    return next((group for prefix, group in GROUPS if purpose.startswith(prefix)), "other")


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    try:
        return int(conn.execute(sql, args).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0  # a table another module creates on first use


def report(conn: sqlite3.Connection, matter_id: int | None = None) -> SpendReport:
    where, args = ("WHERE matter_id=?", (matter_id,)) if matter_id is not None else ("", ())
    rows = conn.execute(
        "SELECT purpose, model, input_tokens, cached_tokens, cache_write_tokens, output_tokens, cost_usd, ok FROM llm_calls " + where, args
    ).fetchall()
    lines: dict[str, SpendLine] = {}
    unpriced: set[str] = set()
    for row in rows:
        line = lines.setdefault(group_of(row["purpose"]), SpendLine.model_validate({"group": group_of(row["purpose"])}))
        if row["purpose"] not in line.purposes:
            line.purposes.append(row["purpose"])
        if row["model"] not in line.models:
            line.models.append(row["model"])
        line.calls += 1
        line.failed += 0 if row["ok"] else 1
        line.input_tokens += row["input_tokens"]
        line.cached_tokens += row["cached_tokens"]
        line.cache_write_tokens += row["cache_write_tokens"]
        line.output_tokens += row["output_tokens"]
        cost = row["cost_usd"] or 0.0
        line.usd += cost
        price = llm.PRICES["models"].get(row["model"])
        plain = llm.cost_usd(row["model"], row["input_tokens"], 0, 0, row["output_tokens"])
        if price is None or plain is None:
            unpriced.add(row["model"])
            line.usd_without_caching += cost
        elif price.get("cached_input") is None and price.get("cache_write") is None:
            line.cache_priced = False  # no cached rate on record: these calls were priced at the plain rate throughout
            line.usd_without_caching += cost
        else:
            line.usd_without_caching += plain

    def finish(line: SpendLine) -> SpendLine:
        line.usd, line.usd_without_caching = round(line.usd, 4), round(line.usd_without_caching, 4)
        line.caching_effect_usd = round(line.usd_without_caching - line.usd, 4)
        line.cached_share = round(line.cached_tokens / line.input_tokens, 3) if line.input_tokens else None
        line.purposes.sort()
        return line

    ordered = [finish(lines[group]) for group in ORDER if group in lines]
    total = SpendLine.model_validate({"group": "total"})
    for line in ordered:
        total.calls += line.calls
        total.failed += line.failed
        total.input_tokens += line.input_tokens
        total.cached_tokens += line.cached_tokens
        total.cache_write_tokens += line.cache_write_tokens
        total.output_tokens += line.output_tokens
        total.usd += line.usd
        total.usd_without_caching += line.usd_without_caching
        total.cache_priced = total.cache_priced and line.cache_priced
        total.models = sorted(set(total.models) | set(line.models))
    finish(total)

    notes = []
    if any(not line.cache_priced for line in ordered):
        notes.append("The price list has no cached-input rate for one of the models used, so its cached tokens are priced at the plain rate and no saving is counted for them.")
    if unpriced:
        notes.append("No price on record for: " + ", ".join(sorted(unpriced)) + ". Their cost is not counted.")
    scope, scoped = (" WHERE matter_id=?", (matter_id,)) if matter_id is not None else ("", ())
    if matter_id is None:
        pages = _count(conn, "SELECT COUNT(*) FROM (SELECT DISTINCT sha256, page FROM page_reads)")
        paid_here = None
    else:
        # A page read is kept by the file's content, not by case: a file that is in two cases is read once.
        pages = _count(
            conn,
            "SELECT COUNT(*) FROM (SELECT DISTINCT r.sha256, r.page FROM page_reads r"
            " JOIN document_blobs b ON b.sha256 = r.sha256 WHERE b.matter_id=?)",
            (matter_id,),
        )
        paid_here = _count(conn, "SELECT COUNT(*) FROM llm_calls WHERE matter_id=? AND purpose='pages' AND ok=1", (matter_id,))
    stored = StoredWork.model_validate({
        "pages_read": pages,
        "pages_read_here": paid_here,
        "records_read": _count(conn, "SELECT COUNT(*) FROM item_claims" + scope, scoped),
        "check_answers": _count(conn, "SELECT COUNT(*) FROM check_cache" + scope, scoped),
        "incoming_checks": _count(conn, "SELECT COUNT(*) FROM incoming_checks" + scope, scoped),
    })
    notes.append(
        "Pages are read once per file, whichever case holds it: a case made from files another case already has reads none again,"
        " so its page-reading cost is zero and its pages read still counts them."
    )
    by_case = []
    if matter_id is None:
        by_case = [
            CaseSpend(matter_id=row["matter_id"], calls=row["calls"], usd=round(row["usd"] or 0.0, 4))
            for row in conn.execute("SELECT matter_id, COUNT(*) AS calls, SUM(cost_usd) AS usd FROM llm_calls GROUP BY matter_id ORDER BY matter_id")
        ]
    return SpendReport(as_of=now_iso(), scope="case" if matter_id is not None else "all_cases", matter_id=matter_id, by_case=by_case,
                       lines=ordered, total=total, stored=stored, prices_read_on=llm.PRICES.get("read_on"), notes=notes)
