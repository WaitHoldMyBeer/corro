"""Search over the case file on the server: the same index and the same scoring the
search bar runs in the browser, for callers that have no browser (the assistant's
`search_file` tool). `measure` checks the two rank identically.

    from server.graph import query
    hits = query.search_file(cfg, conn, matter_id, "words the user typed", kinds=None, limit=10)

Nothing is rebuilt per call: the graph payload is cached by ledger version (`api.stored`)
and its index is decoded once per version and kept in memory.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from ..config import Settings
from . import api, text

_decoded: dict[int, tuple[str, dict[str, Any], text.Index]] = {}


def corpus(cfg: Settings, conn: sqlite3.Connection, matter_id: int) -> tuple[dict[str, Any], text.Index]:
    """The current graph payload and its decoded index. Raises MatterNotSynced for an unknown matter."""
    version, body = api.stored(cfg, conn, matter_id)
    held = _decoded.get(matter_id)
    if held is None or held[0] != version:
        payload = json.loads(gzip.decompress(body))
        held = (version, payload, text.unwire(payload["index"]))
        _decoded[matter_id] = held
    return held[1], held[2]


def search_file(
    cfg: Settings,
    conn: sqlite3.Connection,
    matter_id: int,
    query: str,
    kinds: Iterable[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ranked hits, best first. A hit is a record or document (`claim_id` is None) or one
    statement read from it. `relevance` is 0-1 against the best hit of this query, as in the
    search bar. `kinds` keeps only hits whose source node is of one of those kinds."""
    payload, index = corpus(cfg, conn, matter_id)
    nodes, claims = payload["nodes"], payload["claims"]
    wanted = set(kinds) if kinds else None
    scored = []
    for unit, score in text.search(index, query).items():
        node = nodes[unit] if unit < len(nodes) else nodes[claims["node"][unit - len(nodes)]]
        if wanted is None or node["kind"] in wanted:
            scored.append((score, unit, node))
    if not scored:
        return []
    top = max(score for score, _, _ in scored)
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    hits = []
    for score, unit, node in scored[: max(0, limit)]:
        claim = unit - len(nodes) if unit >= len(nodes) else None
        href = node["href"]
        hits.append(
            {
                "node_id": node["id"],
                "kind": node["kind"],
                "clio_id": node["clio_id"],
                "label": node["label"],
                "date": node["date"],
                "href": href if claim is None or not href else f"{href}?claim={quote(claims['id'][claim], safe='')}",
                "relevance": round(score / top, 4),
                "claim_id": None if claim is None else claims["id"][claim],
                "page": None if claim is None else (claims["page"][claim] or None),
                "text": node["sub"] if claim is None else claims["text"][claim],
                "quote_verified": None if claim is None else bool(claims["ok"][claim]),
            }
        )
    return hits
