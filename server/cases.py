"""Cases in our own store, and the lawyer-level overview of all of them.

A case is either read from the source system (Clio) or created here and filled by
upload. A case created here gets an id below zero, which no source system issues,
and a matter record of the same shape as an imported one, so the case builder,
graph, tabs, assistant and share all work on it unchanged, scoped by that id.
Archiving is a flag in our store only; nothing is ever sent to the source system.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from shared import contract as c

from . import rules
from .db import content_hash, now_iso

SOURCE_LABEL = {"clio": "Imported", "upload": "Uploaded"}
AGENDA_LIMIT = 12
DUE_SOON_DAYS = 14


def create_case(conn: sqlite3.Connection, name: str, client_name: str | None = None, number: str | None = None) -> int:
    """Create an empty case in our store and return its id (below zero)."""
    lowest = conn.execute("SELECT MIN(id) AS low FROM cases").fetchone()["low"]
    case_id = min(lowest or 0, 0) - 1
    at = now_iso()
    conn.execute(
        "INSERT INTO cases (id, name, client_name, number, source, created_at) VALUES (?,?,?,?,'upload',?)",
        (case_id, name.strip(), (client_name or "").strip() or None, (number or "").strip() or None, at),
    )
    # The matter record every other part of the app reads, in the shape an imported one has.
    payload: dict[str, Any] = {
        "id": case_id, "display_number": (number or "").strip() or None, "description": name.strip(), "status": "Open",
        "open_date": at[:10], "created_at": at, "updated_at": at, "origin": "upload", "custom_field_values": [],
    }
    if (client_name or "").strip():
        payload["client"] = {"name": client_name.strip(), "type": "Person"}
    conn.execute(
        "INSERT INTO clio_items (matter_id, kind, clio_id, etag, content_hash, payload, first_seen_at, changed_at,"
        " last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (case_id, "matter", str(case_id), None, content_hash(payload), json.dumps(payload), at, at, at),
    )
    conn.commit()
    return case_id


def set_archived(conn: sqlite3.Connection, matter_id: int, archived: bool) -> bool:
    """Hide or show a case, in our store only. False when we hold no such case."""
    held = conn.execute("SELECT payload FROM clio_items WHERE matter_id=? AND kind='matter'", (matter_id,)).fetchone()
    if held is None:
        return False
    matter = json.loads(held["payload"])
    conn.execute(
        "INSERT INTO cases (id, name, client_name, number, source, created_at, archived_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET archived_at=excluded.archived_at",
        (
            matter_id, matter.get("description") or matter.get("display_number") or "", (matter.get("client") or {}).get("name"),
            matter.get("display_number"), source_of(matter), now_iso(), now_iso() if archived else None,
        ),
    )
    conn.commit()
    return True


def source_of(matter: dict[str, Any]) -> str:
    return "upload" if matter.get("origin") == "upload" else "clio"


def archived_ids(conn: sqlite3.Connection) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM cases WHERE archived_at IS NOT NULL")}


def held_matters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every case we hold, imported or created here, as its matter record."""
    return [
        json.loads(row["payload"])
        for row in conn.execute("SELECT payload FROM clio_items WHERE kind='matter' AND removed_at IS NULL ORDER BY matter_id")
    ]


# --------------------------------------------------------------------------- overview


_overview_cache: dict[int, tuple[str, c.CaseOverview]] = {}


def _stamp(conn: sqlite3.Connection, matter_id: int, today: date) -> str:
    """Changes whenever anything the overview row is built from does. A few indexed reads."""
    parts = [today.isoformat()]
    for sql in (
        "SELECT COUNT(*) AS n, MAX(changed_at) AS at, MAX(removed_at) AS gone FROM clio_items WHERE matter_id=?",
        "SELECT at AS at, input_hash AS n, NULL AS gone FROM reconciliations WHERE matter_id=?",
        "SELECT COUNT(*) AS n, MAX(reviewed_at) AS at, NULL AS gone FROM conflict_reviews WHERE matter_id=?",
        "SELECT COUNT(*) AS n, MAX(downloaded_at) AS at, SUM(page_count) AS gone FROM document_blobs WHERE matter_id=?",
        "SELECT COUNT(*) AS n, MAX(at) AS at, NULL AS gone FROM item_claims WHERE matter_id=?",
        "SELECT COUNT(*) AS n, MAX(finished_at) AS at, NULL AS gone FROM sync_runs WHERE matter_id=?",
        "SELECT COUNT(*) AS n, MAX(finished_at) AS at, NULL AS gone FROM digest_runs WHERE matter_id=?",
        "SELECT archived_at AS n, name AS at, NULL AS gone FROM cases WHERE id=?",
    ):
        row = conn.execute(sql, (matter_id,)).fetchone()
        parts.append("" if row is None else f"{row['n']}|{row['at']}|{row['gone']}")
    for key in ("fee_percent", "fee_basis"):
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        parts.append(row["value"] if row else "")
    return "/".join(parts)


def case_overview(cfg, conn: sqlite3.Connection, matter_id: int, today: date | None = None) -> c.CaseOverview:
    """One case's row for the lawyer-level overview. Stored data only: no source call, no model call."""
    from .case import CaseBuilder
    from .digest import overlay

    today = today or rules.firm_today()
    stamp = _stamp(conn, matter_id, today)
    cached = _overview_cache.get(matter_id)
    if cached and cached[0] == stamp:
        return cached[1]
    build = CaseBuilder(conn, matter_id, today)
    case = overlay.apply(cfg, build, build.build())
    brief, digest = case.brief, case.meta.digest
    upcoming = [item for item in case.agenda.overdue + case.agenda.waiting + case.agenda.coming if item.due]
    nearest = min(
        (item for item in upcoming if item.days_from_today is not None and item.days_from_today >= 0),
        key=lambda item: item.days_from_today,
        default=None,
    )
    pages_total = digest.pages_total if case.documents else None
    if not case.documents:
        reading = "no documents yet"
    elif digest.pages_digested == 0:
        reading = "not read yet"
    else:
        reading = f"{digest.pages_digested} of {digest.pages_total} pages read"
    row = c.CaseOverview(
        id=matter_id,
        name=case.matter.description or case.matter.display_number or f"Case {matter_id}",
        display_number=case.matter.display_number,
        client_name=case.matter.client.name if case.matter.client else (build.matter.get("client") or {}).get("name"),
        source=source_of(build.matter),
        source_label=SOURCE_LABEL[source_of(build.matter)],
        archived=matter_id in archived_ids(conn),
        status=case.matter.status,
        stage=brief.stage.display if brief.stage else None,
        value=brief.case_value,
        coverage=brief.coverage,
        spend=brief.firm_spend,
        limitations=brief.limitations,
        next_deadline=c.OverviewAgendaItem(
            case_id=matter_id, case_name=case.matter.description or "", title=nearest.title, date=nearest.due,
            days_from_today=nearest.days_from_today, kind=nearest.kind, bucket=nearest.bucket,
        ) if nearest else None,
        overdue=len(case.agenda.overdue),
        waiting=len(case.agenda.waiting),
        coming=len(case.agenda.coming),
        to_review=sum(1 for conflict in case.conflicts if conflict.review == "unreviewed"),
        last_activity=(brief.alive.date if brief.alive and brief.alive.date else None) or _last_added(build),
        documents=len(case.documents),
        pages=pages_total,
        pages_read=digest.pages_digested,
        reading=reading,
        imported_at=case.meta.synced_at or _last_added(build, full=True),
        agenda=[
            c.OverviewAgendaItem(
                case_id=matter_id, case_name=case.matter.description or "", title=item.title, date=item.due,
                days_from_today=item.days_from_today, kind=item.kind, bucket=item.bucket,
            )
            for item in upcoming
        ],
    )
    _overview_cache[matter_id] = (stamp, row)
    return row


def _last_added(build, full: bool = False) -> str | None:
    """For a case filled by upload: when its newest document was added (or the case created)."""
    if source_of(build.matter) != "upload":
        return None
    stamps = [d.get("created_at") for d in build.documents if d.get("created_at")] or [build.matter.get("created_at")]
    newest = max((s for s in stamps if s), default=None)
    return newest if full or newest is None else newest[:10]


def firm_overview(cfg, conn: sqlite3.Connection, include_archived: bool = True) -> c.FirmOverview:
    """Every case on one page, totals added up here, and the next dated items across all of them."""
    rows, errors = [], []
    for matter in held_matters(conn):
        try:
            rows.append(case_overview(cfg, conn, int(matter["id"])))
        except Exception as error:  # one unreadable case must not blank the lawyer's whole page
            errors.append(f"case {matter['id']}: {type(error).__name__}")
    shown = [row for row in rows if include_archived or not row.archived]
    live = [row for row in rows if not row.archived]

    def total(facts: list[c.Fact | None]) -> float | None:
        amounts = [Decimal(str(fact.amount)) for fact in facts if fact is not None and fact.amount is not None]
        return float(sum(amounts, Decimal("0"))) if amounts else None  # unknown stays unknown

    agenda = sorted(
        (item for row in live for item in row.agenda if item.days_from_today is not None),
        key=lambda item: item.days_from_today,
    )
    return c.FirmOverview(
        generated_at=now_iso(),
        cases=shown,
        totals=c.OverviewTotals(
            cases=len(rows),
            open_cases=len(live),
            archived=len(rows) - len(live),
            overdue=sum(row.overdue for row in live),
            waiting=sum(row.waiting for row in live),
            coming=sum(row.coming for row in live),
            due_14_days=sum(1 for item in agenda if 0 <= item.days_from_today <= DUE_SOON_DAYS),
            to_review=sum(row.to_review for row in live),
            value_total=total([row.value for row in live]),
            coverage_total=total([row.coverage for row in live]),
            spend_total=total([row.spend for row in live]),
        ),
        agenda=agenda[:AGENDA_LIMIT],
        warnings=errors,
    )
