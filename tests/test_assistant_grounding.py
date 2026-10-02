"""What the assistant may show is decided in code, not by the model.

No model is called here. A made-up answer is handed to the code that turns the
model's blocks into what the lawyer sees, on a synthetic matter: a reference
that is not in the file is removed, a sentence left with none is marked as not
grounded, and a figure that its cited source does not contain is marked.
"""

from __future__ import annotations

import pytest
from test_provider_routes import MATTER, firm, rows

from server.db import connect, upsert_item


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("SWANS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CLIO_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FIRM_PASSCODE", "synthetic-passcode")
    conn = connect(tmp_path / "swans.db")
    for kind, payload in rows():
        upsert_item(conn, 1, MATTER, kind, payload, "2031-01-01T00:00:00Z")
    conn.commit()
    yield conn
    conn.close()


def blocks_for(conn, sentences):
    from server.assistant import engine
    from server.assistant.tools import Toolbox
    from server.config import get_settings
    from shared import assistant_contract as a

    cfg = get_settings()
    turn = engine.Turn(cfg, MATTER, a.AssistantRequest(message="synthetic question"))
    answer = [engine.OutBlock(type="paragraph", sentences=[engine.OutSentence(text=text, cite=cite) for text, cite in sentences])]
    return turn, turn._blocks(Toolbox(cfg, conn, MATTER), answer)


def test_a_reference_that_is_not_in_the_file_is_removed_and_the_sentence_marked(site) -> None:
    turn, blocks = blocks_for(site, [
        ("A sentence citing a record that exists.", ["note:1"]),
        ("A sentence citing something invented.", ["note:999999", "claim:made-up"]),
        ("A sentence with no reference at all.", []),
    ])
    sentences = [sentence for block in blocks for sentence in (block.sentences or [])]
    assert len(sentences) == 3
    real, invented, bare = sentences
    assert real.grounded and real.cite == ["note:1"]
    assert not invented.grounded and invented.cite == [], "an invented reference survived"
    assert not bare.grounded
    assert turn.dropped == 2


def test_a_figure_the_cited_source_does_not_contain_is_marked(site) -> None:
    _, blocks = blocks_for(site, [("The note records a payment of $41,307.55 on 2031-06-30.", ["note:1"])])
    sentence = blocks[0].sentences[0]
    assert sentence.grounded, "the reference itself is real"
    assert sentence.figures_unverified, "a figure and a date that are not in the cited note were shown as if sourced"


def test_no_answer_is_said_plainly_and_never_as_grounded(site) -> None:
    _, blocks = blocks_for(site, [])
    sentences = [sentence for block in blocks for sentence in (block.sentences or [])]
    assert sentences and not any(sentence.grounded for sentence in sentences)


def test_without_a_model_the_assistant_refuses_or_says_the_answer_is_not_a_models(site) -> None:
    from fastapi.testclient import TestClient

    from server import firm_auth
    from server.app import app

    with TestClient(app) as client:
        firm_auth.passcode.cache_clear()
        assert client.post(firm("/assistant"), json={"message": "what happened"}).status_code == 401
        assert client.post(firm_auth.LOGIN, json={"passcode": "synthetic-passcode"}).status_code == 200
        answered = client.post(firm("/assistant"), json={"message": "what happened"})
        # With no model it either refuses, or answers from the search index in code and says so.
        assert answered.status_code in (200, 503)
        if answered.status_code == 200:
            turn = answered.json()
            assert turn["usage"]["model_calls"] == 0
            assert any("model" in warning.lower() for warning in turn["warnings"]), "an answer built without the model does not say so"
        assert client.post("/api/matters/424242/assistant", json={"message": "x"}).status_code == 404
    firm_auth.passcode.cache_clear()
