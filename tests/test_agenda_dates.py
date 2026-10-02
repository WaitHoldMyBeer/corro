"""Deadlines, day boundaries and the header's dated facts, on a synthetic matter.

A lawyer trusts the Overdue list to be complete and a date to be the day Clio
holds. These tests build a made-up matter in a temporary database, fix "today",
and check the buckets and day counts, including on a machine whose clock is in
a different time zone from the firm's.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

import pytest

from server.case import CaseBuilder
from server.db import connect, upsert_item

MATTER = 7
CLIENT, PROVIDER, STAFF = 100, 101, 900
TODAY = date(2031, 3, 15)
FIRM_OFFSET = "-07:00"


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def task(task_id: int, name: str, due: str | None, status: str = "pending", **extra) -> dict:
    return {"id": task_id, "name": name, "description": extra.pop("description", None), "due_at": due, "status": status, **extra}


def entry(entry_id: str, summary: str, start_at: str) -> dict:
    return {"id": entry_id, "summary": summary, "description": None, "start_at": start_at, "end_at": start_at}


def message(message_id: int, when: str, party: int, kind: str = "PhoneCommunication") -> dict:
    return {
        "id": message_id, "type": kind, "subject": f"Synthetic message {message_id}", "date": when,
        "senders": [{"id": STAFF, "type": "User", "name": "Synthetic staff"}],
        "receivers": [{"id": party, "type": "Person", "name": "Synthetic party"}],
    }


def expense(expense_id: int, price: float, **extra) -> dict:
    return {"id": expense_id, "type": "ExpenseEntry", "date": day(-40), "quantity": 1.0, "price": price,
            "total": extra.pop("total", price), "note": f"Synthetic expense {expense_id}", **extra}


ROWS: list[tuple[str, dict]] = [
    ("matter", {"id": MATTER, "status": "Open", "client": {"id": CLIENT}, "custom_field_values": []}),
    ("contact", {"id": CLIENT, "name": "Casey Placeholder", "first_name": "Casey", "last_name": "Placeholder", "type": "Person"}),
    ("contact", {"id": PROVIDER, "name": "Alpha Example Clinic", "type": "Company"}),
    ("relationship", {"id": 1, "description": "Treating provider", "contact": {"id": PROVIDER}}),
    ("task", task(1, "Synthetic task due yesterday", day(-1))),
    ("task", task(2, "Synthetic task due today", day(0))),
    ("task", task(3, "Synthetic task due tomorrow", day(1))),
    ("task", task(4, "Records from Alpha Example Clinic", day(-30))),
    ("task", task(5, "Synthetic task finished late", day(-10), status="complete")),
    ("task", task(6, "Synthetic task with no date", None)),
    ("task", task(7, "Synthetic limitations task", day(5), statute_of_limitations=True)),
    ("calendar_entry", entry("c1", "Synthetic late-evening hearing today", f"{day(0)}T23:30:00{FIRM_OFFSET}")),
    ("calendar_entry", entry("c2", "Synthetic early-morning hearing tomorrow", f"{day(1)}T00:15:00{FIRM_OFFSET}")),
    ("calendar_entry", entry("c3", "Synthetic hearing yesterday", f"{day(-1)}T09:00:00{FIRM_OFFSET}")),
    ("communication", message(1, day(-3), CLIENT)),
    ("communication", message(2, day(-1), PROVIDER)),
    ("communication", message(3, day(4), CLIENT)),  # dated in the future: not yet a contact
    ("expense", expense(1, 100.25)),
    ("expense", expense(2, 200.50)),
    ("expense", expense(3, 90_000.0, total=None, non_billable=True)),  # a charge recorded on the matter, not firm money
]


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "synthetic.db")
    for kind, payload in ROWS:
        upsert_item(connection, 1, MATTER, kind, payload, "2031-03-15T00:00:00Z")
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture(params=["UTC", "Pacific/Kiritimati", "Pacific/Pago_Pago", "America/Los_Angeles"])
def machine_zone(request):
    """Run the test with this machine's clock in each of four zones (UTC+14 to UTC-11)."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    yield request.param
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def agenda(conn):
    return CaseBuilder(conn, MATTER, today=TODAY).agenda()


def find(agenda_, item_id: str):
    for bucket in ("overdue", "coming", "waiting", "done"):
        for item in getattr(agenda_, bucket):
            if item.id == item_id:
                return bucket, item
    raise AssertionError(f"{item_id} is in no bucket")


def test_day_counts_and_buckets_at_the_boundary(conn, machine_zone) -> None:
    result = agenda(conn)
    assert result.as_of == TODAY.isoformat()
    for item_id, bucket, days in (
        ("task:1", "overdue", -1),
        ("task:2", "coming", 0),
        ("task:3", "coming", 1),
        ("task:5", "done", -10),
        ("task:6", "coming", None),
    ):
        found_bucket, item = find(result, item_id)
        assert (found_bucket, item.days_from_today) == (bucket, days), f"{item_id} in zone {machine_zone}"


def test_a_past_due_task_is_overdue_even_when_it_names_someone_outside_the_firm(conn) -> None:
    result = agenda(conn)
    overdue_ids = {item.id for item in result.overdue}
    assert "task:4" in overdue_ids, "30 days past due and not complete, but missing from Overdue"
    _, item = find(result, "task:4")
    assert item.waiting_on_contact_id == PROVIDER, "it should still say who the firm is waiting on"


def test_a_completed_task_is_never_overdue(conn) -> None:
    assert "task:5" not in {item.id for item in agenda(conn).overdue}


def test_a_calendar_entry_falls_on_the_day_clio_gave_whatever_this_machine_thinks(conn, machine_zone) -> None:
    result = agenda(conn)
    for item_id, days in (("calendar_entry:c1", 0), ("calendar_entry:c2", 1), ("calendar_entry:c3", -1)):
        _, item = find(result, item_id)
        assert item.days_from_today == days, f"{item_id} in zone {machine_zone}"
    timeline = {event.id: event.date for event in CaseBuilder(conn, MATTER, today=TODAY).timeline()}
    assert timeline["calendar_entry:c1"] == day(0) and timeline["calendar_entry:c2"] == day(1)


def test_limitations_date_is_the_date_clio_holds_and_is_not_called_confirmed_while_open(conn, machine_zone) -> None:
    builder = CaseBuilder(conn, MATTER, today=TODAY)
    fact = builder._limitations()
    assert fact is not None and fact.date == day(5) and fact.display == day(5)
    assert fact.status != "confirmed", "an open limitations task must not read as confirmed"
    bucket, item = find(builder.agenda(), "task:7")
    assert bucket == "coming" and item.is_limitations and item.days_from_today == 5


def test_last_client_contact_is_the_latest_past_communication_with_the_client(conn) -> None:
    builder = CaseBuilder(conn, MATTER, today=TODAY)
    brief = builder.brief({}, builder.spend())
    assert brief.last_client_contact is not None, "the client was contacted three days ago"
    assert brief.last_client_contact.date == day(-3), "a later message to someone else, or a future-dated one, must not count"


def test_firm_spend_counts_only_what_the_firm_laid_out(conn) -> None:
    spend = CaseBuilder(conn, MATTER, today=TODAY).spend()
    assert spend.total == pytest.approx(300.75)
    assert len(spend.lines) == 2
