"""Two cases in one database must never see each other's data.

Both cases are synthetic and created here: every text carries a marker naming its
case, and each surface of case A is read back and searched for case B's marker
(and the reverse). A provider link issued in case A must serve case A only.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

MARK = {"A": "«case-A»", "B": "«case-B»"}


@pytest.fixture()
def two_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from server import cases
    from server.app import app
    from server.config import get_settings
    from server.db import connect, content_hash, now_iso
    from server.digest import prompts

    cfg = get_settings()
    conn = connect(cfg.db_path)
    ids = {}
    for index, (name, mark) in enumerate(MARK.items()):
        case_id = cases.create_case(conn, f"Synthetic case {mark}", f"Client {mark}", f"N-{name}")
        ids[name] = case_id
        at = now_iso()
        provider_id, note_id, relationship_id = 500 + index, 700 + index, 600 + index
        rows = [
            ("contact", provider_id, {"id": provider_id, "name": f"Clinic {mark}", "type": "Company"}),
            ("relationship", relationship_id, {"id": relationship_id, "description": "Treating provider", "contact": {"id": provider_id}}),
            ("note", note_id, {"id": note_id, "subject": f"Note {mark}", "detail": f"Detail {mark}.", "date": "2026-01-05"}),
            ("task", 800 + index, {"id": 800 + index, "name": f"Task {mark}", "status": "pending", "due_at": "2099-01-01"}),
        ]
        for kind, clio_id, payload in rows:
            conn.execute(
                "INSERT INTO clio_items (matter_id, kind, clio_id, content_hash, payload, first_seen_at, changed_at, last_seen_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (case_id, kind, str(clio_id), content_hash(payload), json.dumps(payload), at, at, at),
            )
        text = f"Note {mark}\nDetail {mark}."
        claim = [{"item": f"note:{note_id}", "kind": "other", "topic": f"topic {mark}", "statement": f"Statement {mark}.",
                  "quote": f"Detail {mark}", "date": None, "amount_usd": None, "party": None, "category": "internal"}]
        conn.execute(
            "INSERT INTO item_claims (matter_id, kind, clio_id, content_hash, prompt_version, model, result, at) VALUES (?,?,?,?,?,?,?,?)",
            (case_id, "note", str(note_id), content_hash(["note", text, "2026-01-05"]), prompts.CLAIMS_VERSION, "synthetic-model", json.dumps(claim), at),
        )
        conn.execute(
            "INSERT INTO llm_calls (matter_id, run_id, purpose, model, input_tokens, cached_tokens, cache_write_tokens,"
            " output_tokens, cost_usd, seconds, ok, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_id, None, "claims", "synthetic-model", 100 * (index + 1), 0, 0, 10, 0.25 * (index + 1), 1.0, 1, at),
        )
    conn.commit()
    client = TestClient(app)
    assert client.post("/api/firm/login", json={"passcode": "synthetic-passcode"}).status_code == 200
    return client, ids


def other(name: str) -> str:
    return "B" if name == "A" else "A"


@pytest.mark.parametrize("name", ["A", "B"])
def test_case_model_graph_tabs_and_review_queue_hold_only_their_own_case(two_cases, name) -> None:
    client, ids = two_cases
    for path in ("case?claims=all", "graph", "records/notes", "records/tasks", "review-queue", "review-queue/claims",
                 "important-documents", "negotiation", "assistant/conversations", "assistant/documents", "dashboard"):
        response = client.get(f"/api/matters/{ids[name]}/{path}")
        assert response.status_code == 200, f"{path} answered {response.status_code} for an upload-only case"
        assert MARK[other(name)] not in response.text, f"{path} of case {name} contains case {other(name)}'s data"
    case = client.get(f"/api/matters/{ids[name]}/case?claims=all").json()
    assert MARK[name] in json.dumps(case, ensure_ascii=False), "the case's own data is missing, so the test proved nothing"
    assert case["meta"]["matter_id"] == ids[name]


def test_dashboard_layout_review_and_fee_free_state_are_per_case(two_cases) -> None:
    client, ids = two_cases
    layout = client.get(f"/api/matters/{ids['A']}/dashboard").json()
    saved = client.put(f"/api/matters/{ids['A']}/dashboard", json=layout)
    assert saved.status_code == 200
    client.put(f"/api/matters/{ids['A']}/conflicts/conflict:synthetic/review", json={"review": "confirmed"})
    client.post(f"/api/matters/{ids['A']}/seen")
    from server.config import get_settings
    from server.db import connect

    conn = connect(get_settings().db_path)
    keys = [row["key"] for row in conn.execute("SELECT key FROM settings")]
    assert not any(key.endswith(f":{ids['B']}") for key in keys), "writing case A's state created a key for case B"
    assert conn.execute("SELECT COUNT(*) AS n FROM conflict_reviews WHERE matter_id=?", (ids["B"],)).fetchone()["n"] == 0


def test_spend_and_overview_are_counted_per_case(two_cases) -> None:
    client, ids = two_cases
    costs = {name: client.get(f"/api/matters/{ids[name]}/digest").json()["cost_usd_total"] for name in ("A", "B")}
    assert costs == {"A": 0.25, "B": 0.5}, "one case's model spend is counted under the other"
    overview = client.get("/api/firm/overview").json()
    rows = {row["id"]: row for row in overview["cases"]}
    assert set(rows) == set(ids.values()) and overview["totals"]["cases"] == 2
    for name in ("A", "B"):
        assert MARK[name] in rows[ids[name]]["name"] and MARK[other(name)] not in json.dumps(rows[ids[name]], ensure_ascii=False)
    assert rows[ids["A"]]["value"] is None and overview["totals"]["value_total"] is None, "an unknown value was shown as a number"


def test_a_provider_link_of_one_case_serves_only_that_case(two_cases) -> None:
    client, ids = two_cases
    provider_a = 500
    preview = client.get(f"/api/matters/{ids['A']}/providers/{provider_a}/preview")
    assert preview.status_code == 200
    sent = client.post(f"/api/matters/{ids['A']}/providers/{provider_a}/share", json={"preview_hash": preview.json()["content_hash"]})
    assert sent.status_code == 200, sent.text
    token = sent.json()["link_href"].split("token=")[1]
    visitor = TestClient(client.app)
    view = visitor.get(f"/api/share/{token}")
    assert view.status_code == 200 and MARK["A"] in view.text and MARK["B"] not in view.text
    assert visitor.get(f"/api/matters/{ids['B']}/case").status_code == 401, "a provider link holder reached the firm side"
    # the same provider id does not exist in case B: A's contact is not B's provider
    assert client.get(f"/api/matters/{ids['B']}/providers/{provider_a}/preview").status_code == 404
    assert client.get(f"/api/matters/{ids['B']}/case").json()["providers"][0]["shares"] == [], "case A's share shows in case B's log"
