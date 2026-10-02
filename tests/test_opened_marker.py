"""The "since you last opened" marker: opening another tab must not move it.

It moves only when an open starts a new visit (the matter sat unopened for longer
than the visit gap) or when the lawyer marks everything as seen. Synthetic matter
id; no case data; times are set directly in our own settings table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

MATTER = 4242


def stamp(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    monkeypatch.setenv("VISIT_GAP_HOURS", "4")
    from server.app import app
    from server.config import get_settings
    from server.db import connect, set_setting

    client = TestClient(app)
    assert client.post("/api/firm/login", json={"passcode": "synthetic-passcode"}).status_code == 200
    conn = connect(get_settings().db_path)
    at = stamp(0)
    conn.execute(  # the marker is kept only for a case we hold
        "INSERT INTO clio_items (matter_id, kind, clio_id, content_hash, payload, first_seen_at, changed_at, last_seen_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (MATTER, "matter", str(MATTER), "h", '{"id": 4242, "description": "Synthetic case"}', at, at, at),
    )
    conn.commit()

    def set_times(last_opened: str | None, seen_since: str | None) -> None:
        if last_opened:
            set_setting(conn, f"last_opened:{MATTER}", last_opened)
        if seen_since:
            set_setting(conn, f"seen_since:{MATTER}", seen_since)

    return client, set_times


def test_the_first_open_ever_starts_the_marker_at_now(firm) -> None:
    client, _ = firm
    first = client.post(f"/api/matters/{MATTER}/opened").json()
    assert first["moved"] is True and first["previous"] == first["now"]


def test_opening_again_within_a_visit_leaves_the_marker_where_it_was(firm) -> None:
    client, set_times = firm
    marker = stamp(3.0)
    set_times(last_opened=stamp(0.5), seen_since=marker)
    for _ in range(3):  # three more tabs
        answer = client.post(f"/api/matters/{MATTER}/opened").json()
        assert answer["previous"] == marker and answer["since"] == marker and answer["moved"] is False


def test_an_open_after_the_visit_gap_measures_from_the_previous_open(firm) -> None:
    client, set_times = firm
    previous_open, old_marker = stamp(6.0), stamp(30.0)
    set_times(last_opened=previous_open, seen_since=old_marker)
    answer = client.post(f"/api/matters/{MATTER}/opened").json()
    assert answer["moved"] is True and answer["previous"] == previous_open
    again = client.post(f"/api/matters/{MATTER}/opened").json()
    assert again["previous"] == previous_open and again["moved"] is False, "a second tab in the new visit moved the marker"


def test_mark_as_seen_moves_the_marker_to_now_and_it_stays(firm) -> None:
    client, set_times = firm
    marker = stamp(2.0)
    set_times(last_opened=stamp(0.2), seen_since=marker)
    seen = client.post(f"/api/matters/{MATTER}/seen").json()
    assert seen["previous"] == marker and seen["since"] == seen["now"] and seen["since"] > marker
    after = client.post(f"/api/matters/{MATTER}/opened").json()
    assert after["previous"] == seen["since"] and after["moved"] is False


def test_both_routes_need_a_firm_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    from server.app import app

    visitor = TestClient(app)
    assert visitor.post(f"/api/matters/{MATTER}/opened").status_code == 401
    assert visitor.post(f"/api/matters/{MATTER}/seen").status_code == 401


def test_no_marker_is_kept_for_a_case_we_do_not_hold(firm) -> None:
    client, _ = firm
    assert client.post("/api/matters/999/opened").status_code == 404
    assert client.post("/api/matters/999/seen").status_code == 404
