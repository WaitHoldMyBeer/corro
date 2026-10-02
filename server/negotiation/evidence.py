"""The one model call in this package: for each open fact, what in the file
supports the higher outcome and what supports the lower one, two sentences a
side, each citing claim ids from the review cards.

The model sees the statements of those claims and nothing else, and returns
ids; code keeps only ids it was given and resolves them to sources. It computes
nothing and ranks nothing. The instruction text below names no party and no
subject: the open facts are described by what they do to the arithmetic.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict

from shared import contract as c

from ..config import Settings
from ..db import content_hash, get_setting, set_setting
from ..digest import llm
from ..digest.pipeline import record_call
from . import analysis
from . import model as m

PROMPT_VERSION = "negotiation-evidence-1"
MAX_STATEMENT_CHARS = 400

INSTRUCTIONS = (
    "You are given open questions from a legal file. Each has a plain description and a list of statements from the"
    " file, each with an id and where it came from. For each question write two short passages of at most two"
    " sentences each: `higher` says what in the statements supports the outcome that is better for the client on"
    " that question, `lower` says what supports the outcome that is worse. Use only the statements given. After each"
    " passage list the ids of the statements it rests on. If nothing in the statements supports a side, return an"
    " empty text and no ids for that side. Do not give amounts, probabilities, advice or predictions."
)


class _Side(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    claim_ids: list[str]


class _Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    higher: _Side
    lower: _Side


class EvidenceSummaries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[_Fact]


def payload(case: c.CaseModel, facts: list[m.OpenFact]) -> list[dict[str, Any]]:
    claims = {claim.id: claim for claim in case.claims}
    out = []
    for fact in facts:
        ids = [i for i in dict.fromkeys(analysis.card_claim_ids(case, fact)) if i in claims]
        if ids:
            out.append({
                "question_id": fact.id,
                "question": fact.label,
                "statements": [
                    {"id": i, "from": claims[i].origin, "text": claims[i].text[:MAX_STATEMENT_CHARS]} for i in ids
                ],
            })
    return out


def version(body: list[dict[str, Any]]) -> str:
    """Changes when the claims behind the cards change, so a summary is never shown against a newer ledger."""
    return content_hash({"prompt": PROMPT_VERSION, "body": body})


def _key(matter_id: int) -> str:
    return f"negotiation_evidence:{matter_id}"


def cached(conn: sqlite3.Connection, matter_id: int, body: list[dict[str, Any]]) -> dict[str, Any] | None:
    raw = get_setting(conn, _key(matter_id))
    if not raw:
        return None
    stored = json.loads(raw)
    return stored["facts"] if stored.get("version") == version(body) else None


def summarise(cfg: Settings, conn: sqlite3.Connection, matter_id: int, body: list[dict[str, Any]]) -> dict[str, Any]:
    """One call, stored against the ledger version. Ids the model was not given are dropped here."""
    try:
        result, usage = llm.structured(
            cfg, model=cfg.digest_model, instructions=INSTRUCTIONS,
            content=[llm.text_part(json.dumps(body, ensure_ascii=False))], schema=EvidenceSummaries,
            effort="low", max_output_tokens=4000, timeout=90.0, max_retries=1,
        )
    except llm.LLMUsageError as error:
        record_call(conn, matter_id, None, "negotiation", error.usage, False)
        raise
    record_call(conn, matter_id, None, "negotiation", usage, True)
    given = {item["question_id"]: {s["id"] for s in item["statements"]} for item in body}
    facts: dict[str, Any] = {}
    for fact in result.facts:
        if fact.question_id not in given:
            continue
        sides = {}
        for name in ("higher", "lower"):
            side = getattr(fact, name)
            ids = [i for i in dict.fromkeys(side.claim_ids) if i in given[fact.question_id]]
            if ids and side.text.strip():
                sides[name] = {"text": side.text.strip(), "claim_ids": ids}
        facts[fact.question_id] = sides
    set_setting(conn, _key(matter_id), json.dumps({"version": version(body), "facts": facts}))
    return facts
