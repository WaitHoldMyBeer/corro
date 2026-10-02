"""The matter picker must not wait on the source system when there is something to show.

Three cases: a stored matter and an expired list (answer at once, refresh behind the
response); a fresh list in memory (served as live); nothing stored at all (the one case
that waits for the source). Synthetic matters; the source call is replaced by a stub.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

STORED = {"id": 901, "display_number": "00901-Placeholder", "description": "Synthetic stored matter", "status": "Open"}
FROM_SOURCE = [{"id": 901, "display_number": "00901-Placeholder", "status": "Open"}, {"id": 902, "display_number": "00902-Placeholder", "status": "Open"}]


@pytest.fixture()
def picker(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    from server import app as appmod
    from server.config import get_settings
    from server.db import connect, now_iso

    conn = connect(get_settings().db_path)
    conn.execute("INSERT INTO oauth_tokens (id, access_token, obtained_at) VALUES (1, 'synthetic-token', ?)", (now_iso(),))
    conn.commit()
    calls = {"n": 0, "release": threading.Event(), "started": threading.Event()}

    def slow_source(client):  # stands in for the list call to the source system
        calls["n"] += 1
        calls["started"].set()
        calls["release"].wait(5)
        return FROM_SOURCE

    monkeypatch.setattr(appmod, "list_matters", slow_source)
    appmod._matter_list.update(at=float("-inf"), rows=[], refreshing=False)
    client = TestClient(appmod.app)
    assert client.post("/api/firm/login", json={"passcode": "synthetic-passcode"}).status_code == 200

    def store_matter() -> None:
        at = now_iso()
        conn.execute(
            "INSERT INTO clio_items (matter_id, kind, clio_id, content_hash, payload, first_seen_at, changed_at, last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (STORED["id"], "matter", str(STORED["id"]), "h", json.dumps(STORED), at, at, at),
        )
        conn.commit()

    yield client, calls, store_matter, appmod
    calls["release"].set()


def test_a_stored_matter_is_served_at_once_while_the_source_is_read_behind_the_response(picker) -> None:
    client, calls, store_matter, appmod = picker
    store_matter()
    started = time.monotonic()
    answer = client.get("/api/matters").json()
    assert time.monotonic() - started < 1.0, "the picker waited on the source although a matter was stored"
    assert answer["live"] is False and [m["id"] for m in answer["items"]] == [STORED["id"]]
    assert calls["started"].wait(2), "no refresh from the source was started"
    calls["release"].set()
    for _ in range(50):
        if not appmod._matter_list["refreshing"]:
            break
        time.sleep(0.05)
    after = client.get("/api/matters").json()
    assert after["live"] is True and len(after["items"]) == 2 and calls["n"] == 1, "the refreshed list was not served, or the source was read twice"


def test_a_fresh_list_in_memory_is_served_without_reading_the_source(picker) -> None:
    client, calls, store_matter, appmod = picker
    appmod._matter_list.update(at=time.monotonic(), rows=FROM_SOURCE)
    answer = client.get("/api/matters").json()
    assert answer["live"] is True and len(answer["items"]) == 2 and calls["n"] == 0


def test_with_nothing_stored_the_picker_waits_for_the_source(picker) -> None:
    client, calls, _, _ = picker
    calls["release"].set()  # the source answers immediately in this case
    answer = client.get("/api/matters").json()
    assert calls["n"] == 1 and answer["live"] is True and len(answer["items"]) == 2
