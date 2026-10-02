"""The search index: tokeniser, BM25 impacts and the wire encoding.

A search unit is anything a query can land on: a node of the graph or a claim.
The index is built here once per ledger version and scored in the browser
(`web/v2/js/graph/search.js`), so the tokeniser below and the one in that file
must stay identical. `search` is the same scoring in Python; it exists so tests
and `measure` can check the browser's ranking against a second implementation.
"""

from __future__ import annotations

import base64
import math
import re
import unicodedata
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field

K1 = 1.2
B = 0.75
PREFIX_WEIGHT = 0.85  # a term the token only prefixes scores this times typed/total length
FUZZY_WEIGHT = 0.5  # a term one edit away, tried only when nothing else matched
FUZZY_MIN_LENGTH = 4
CLAIM_LIFT = 0.9  # a node scores at least this much of its best claim
MAX_TOKEN = 32
MAX_QUERY_TOKENS = 16

STOP = frozenset("a an and are as at be by for from has have in is it of on or that the this to was were will with".split())

_MARKS = re.compile("[̀-ͯ]")
_DIGIT_COMMA = re.compile(r"(\d),(?=\d)")
_RUN = re.compile(r"[a-z0-9]+")
_TYPING = re.compile(r"[a-z0-9]$")


def fold(text: str) -> str:
    """Accents folded, lowercase, thousands separators removed."""
    text = _MARKS.sub("", unicodedata.normalize("NFKD", text)).lower()
    return _DIGIT_COMMA.sub(r"\1", text)


def runs(text: str) -> list[str]:
    return _RUN.findall(fold(text))


def tokens(text: str) -> list[str]:
    """What goes into the index."""
    return [run for run in runs(text) if 1 < len(run) <= MAX_TOKEN and run not in STOP]


def query_tokens(text: str) -> list[str]:
    """What a query is scored on. A stop word or single character counts only while it
    is still being typed: last in the query with nothing after it."""
    folded = fold(text)
    found = _RUN.findall(folded)
    typing = bool(_TYPING.search(folded))
    out: list[str] = []
    for position, run in enumerate(found):
        last = typing and position == len(found) - 1
        if len(run) > MAX_TOKEN or (not last and (len(run) < 2 or run in STOP)):
            continue
        if run not in out and len(out) < MAX_QUERY_TOKENS:
            out.append(run)
    return out


@dataclass
class Index:
    units: int
    terms: list[str] = field(default_factory=list)
    postings: list[list[tuple[int, int]]] = field(default_factory=list)  # per term: (unit, impact 1-255), unit ascending

    def wire(self) -> dict:
        data = bytearray()
        for entries in self.postings:
            previous = 0
            for unit, impact in entries:
                delta = unit - previous
                previous = unit
                while delta >= 0x80:
                    data.append((delta & 0x7F) | 0x80)
                    delta >>= 7
                data.append(delta)
                data.append(impact)
        return {
            "units": self.units,
            "terms": self.terms,
            "df": [len(entries) for entries in self.postings],
            "postings": base64.b64encode(bytes(data)).decode("ascii"),
        }


def unwire(wire: dict) -> Index:
    """The inverse of `Index.wire`: what the browser does on load."""
    data, at = base64.b64decode(wire["postings"]), 0
    postings: list[list[tuple[int, int]]] = []
    for count in wire["df"]:
        entries, unit = [], 0
        for _ in range(count):
            delta = shift = 0
            while True:
                byte = data[at]
                at += 1
                delta |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
            unit += delta
            entries.append((unit, data[at]))
            at += 1
        postings.append(entries)
    return Index(units=wire["units"], terms=list(wire["terms"]), postings=postings)


def build_index(units: list[list[tuple[float, str]]]) -> Index:
    """`units[i]` is the weighted text of unit i: (field weight, text) pairs."""
    weighted: list[dict[str, float]] = []
    lengths: list[float] = []
    for fields in units:
        frequency: dict[str, float] = defaultdict(float)
        length = 0.0
        for weight, text in fields:
            for token in tokens(text or ""):
                frequency[token] += weight
                length += weight
        weighted.append(frequency)
        lengths.append(length)
    filled = [length for length in lengths if length]
    average = sum(filled) / len(filled) if filled else 1.0
    by_term: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for unit, frequency in enumerate(weighted):
        norm = K1 * (1 - B + B * lengths[unit] / average)
        for term, f in frequency.items():
            by_term[term].append((unit, max(1, round(255 * f / (f + norm)))))
    terms = sorted(by_term)
    return Index(units=len(units), terms=terms, postings=[by_term[term] for term in terms])


def within_one(a: str, b: str) -> bool:
    """Edit distance exactly one: an insertion, deletion, substitution or adjacent swap."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i, n = 0, min(la, lb)
    while i < n and a[i] == b[i]:
        i += 1
    if i == n:
        return la != lb
    if la == lb:
        if a[i + 1 :] == b[i + 1 :]:
            return True
        return i + 1 < la and a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2 :] == b[i + 2 :]
    return a[i + 1 :] == b[i:] if la > lb else a[i:] == b[i + 1 :]


def search(index: Index, query: str) -> dict[int, float]:
    """Unit -> score, before the node/claim roll-up and normalisation. Mirrors the browser."""
    wanted = query_tokens(query)
    if not wanted:
        return {}
    total: dict[int, float] = defaultdict(float)
    matched: dict[int, int] = defaultdict(int)
    for token in wanted:
        best: dict[int, float] = {}

        def take(position: int, weight: float) -> None:
            df = len(index.postings[position])
            idf = math.log(1 + (index.units - df + 0.5) / (df + 0.5)) / 255
            for unit, impact in index.postings[position]:
                value = weight * idf * impact
                if value > best.get(unit, 0.0):
                    best[unit] = value

        position, any_term = bisect_left(index.terms, token), False
        while position < len(index.terms) and index.terms[position].startswith(token):
            term = index.terms[position]
            take(position, 1.0 if len(term) == len(token) else PREFIX_WEIGHT * len(token) / len(term))
            any_term = True
            position += 1
        if not any_term and len(token) >= FUZZY_MIN_LENGTH:
            for position, term in enumerate(index.terms):
                if within_one(token, term):
                    take(position, FUZZY_WEIGHT)
        for unit, value in best.items():
            total[unit] += value
            matched[unit] += 1
    return {unit: value * (matched[unit] / len(wanted)) ** 2 for unit, value in total.items()}


def rank(index: Index, nodes: int, claim_node: list[int], query: str, max_claims: int = 24, per_node: int = 4) -> dict:
    """The browser's result for a query: node relevance 0-1 and the claims it would show."""
    scores = search(index, query)
    top = max(scores.values(), default=0.0)
    if not top:
        return {"nodes": {}, "claims": []}
    node_score: dict[int, float] = defaultdict(float)
    candidates = []
    for unit, value in scores.items():
        if unit < nodes:
            node_score[unit] = max(node_score[unit], value)
        else:
            claim = unit - nodes
            node_score[claim_node[claim]] = max(node_score[claim_node[claim]], value * CLAIM_LIFT)
            candidates.append(claim)
    candidates.sort(key=lambda claim: (-scores[nodes + claim], claim))
    shown: list[tuple[int, float]] = []
    used: dict[int, int] = defaultdict(int)
    for claim in candidates:
        if len(shown) >= max_claims:
            break
        if used[claim_node[claim]] < per_node:
            used[claim_node[claim]] += 1
            shown.append((claim, scores[nodes + claim] / top))
    return {"nodes": {node: value / top for node, value in node_score.items()}, "claims": shown}
