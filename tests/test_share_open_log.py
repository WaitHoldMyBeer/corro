"""The share log's "opened" must mean the provider opened it.

A provider link loaded from a browser signed in to the firm side (the attorney
pressing "Open provider's page") is not an open; the same link loaded with no firm
session is. Synthetic case, created here.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

PROVIDER = 500


def test_only_a_visitor_without_a_firm_session_counts_as_an_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from server import cases
    from server.app import app
    from server.config import get_settings
    from server.db import connect, content_hash, now_iso

    conn = connect(get_settings().db_path)
    case_id = cases.create_case(conn, "Synthetic case", "Synthetic client")
    at = now_iso()
    for kind, clio_id, payload in (
        ("contact", PROVIDER, {"id": PROVIDER, "name": "Synthetic Clinic", "type": "Company"}),
        ("relationship", 600, {"id": 600, "description": "Treating provider", "contact": {"id": PROVIDER}}),
    ):
        conn.execute(
            "INSERT INTO clio_items (matter_id, kind, clio_id, content_hash, payload, first_seen_at, changed_at, last_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (case_id, kind, str(clio_id), content_hash(payload), json.dumps(payload), at, at, at),
        )
    conn.commit()

    firm = TestClient(app)
    assert firm.post("/api/firm/login", json={"passcode": "synthetic-passcode"}).status_code == 200
    preview = firm.get(f"/api/matters/{case_id}/providers/{PROVIDER}/preview").json()
    sent = firm.post(f"/api/matters/{case_id}/providers/{PROVIDER}/share", json={"preview_hash": preview["content_hash"]})
    assert sent.status_code == 200, sent.text
    token = sent.json()["link_href"].split("token=")[1]

    def opens() -> tuple[int, str | None]:
        row = conn.execute("SELECT open_count, first_opened_at FROM shares WHERE token=?", (token,)).fetchone()
        return row["open_count"], row["first_opened_at"]

    assert firm.get(f"/api/share/{token}").status_code == 200
    assert opens() == (0, None), "the firm looking at its own link was logged as the provider opening it"

    provider = TestClient(app)
    assert provider.get(f"/api/share/{token}?peek=1").status_code == 200
    assert opens()[0] == 0, "a poll was logged as an open"
    assert provider.get(f"/api/share/{token}").status_code == 200
    count, first = opens()
    assert count == 1 and first is not None, "a provider's own page load was not logged"
