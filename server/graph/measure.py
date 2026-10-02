"""Measures the graph on whatever matter the data directory holds, and checks the
browser's scoring against the Python implementation of the same thing.

    SWANS_DATA_DIR=<copy of data/> uv run --with mini-racer python -m server.graph.measure [matter id]

Queries are generated here, at run time, from the index's own vocabulary; nothing
about any matter is written in this file, and only numbers are printed. The
JavaScript under test is the module the page ships (web/v2/js/graph/search.js), run
in V8 through mini-racer when that package is present. That is the engine Chrome
uses but not a browser tab; docs/ARCHITECTURE.md records the same loop run in the
page.
"""

from __future__ import annotations

import gzip
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

from ..case import CaseBuilder
from ..config import get_settings
from ..db import connect
from . import api, build, text

# The file the page runs. GRAPH_SEARCH_JS points the check at another copy.
SHIPPED = Path(__file__).resolve().parents[2] / "web" / "v2" / "js" / "graph" / "search.js"

SHIM = """
var performance = { now: function () { return Date.now(); } };
function atob(s) {
  var A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", out = "", bits = 0, have = 0;
  for (var i = 0; i < s.length; i++) {
    var v = A.indexOf(s.charAt(i));
    if (v < 0) continue;
    bits = (bits << 6) | v; have += 6;
    if (have >= 8) { have -= 8; out += String.fromCharCode((bits >> have) & 255); }
  }
  return out;
}
"""

HARNESS = """
var model = { n: payload.nodes.length, m: payload.claims.id.length, kind: new Uint8Array(payload.nodes.length),
              iparent: Uint16Array.from(payload.claims.node), index: payload.index };
function make() { return createSearch.length >= 2 ? createSearch(payload, model) : createSearch(model); }
var searcher = make();
function run(query) {
  var r = searcher.run(query), nodes = [], claims = [], used = {};
  for (var i = 0; i < r.nodeHits; i++) nodes.push([r.nodeOrder[i], r.nodeRel[r.nodeOrder[i]]]);
  for (var j = 0; j < r.itemHits && claims.length < 24; j++) {
    var id = r.itemOrder[j], parent = model.iparent[id];
    if ((used[parent] || 0) >= 4) continue;
    used[parent] = (used[parent] || 0) + 1;
    claims.push([id, r.itemRel[id]]);
  }
  return JSON.stringify({ nodes: nodes, claims: claims });
}
function decodeMs(times) {
  var started = Date.now();
  for (var i = 0; i < times; i++) make();
  return (Date.now() - started) / times;
}
function parseMs(times) {
  var started = Date.now();
  for (var i = 0; i < times; i++) JSON.parse(payloadText);
  return (Date.now() - started) / times;
}
// Each query repeated `repeat` times and the mean taken: Date.now() ticks in milliseconds.
function latency(queries, repeat) {
  var times = new Float64Array(queries.length), i, r, started;
  for (i = 0; i < queries.length; i++) searcher.run(queries[i]);
  for (i = 0; i < queries.length; i++) {
    started = Date.now();
    for (r = 0; r < repeat; r++) searcher.run(queries[i]);
    times[i] = (Date.now() - started) / repeat;
  }
  times.sort();
  function at(p) { return times[Math.min(times.length - 1, Math.floor(p * times.length))]; }
  return JSON.stringify({ queries: queries.length, repeat: repeat, p50_ms: at(0.5), p95_ms: at(0.95), p99_ms: at(0.99), max_ms: at(1) });
}
"""


def typed(terms: list[str], words: int, seed: int = 11) -> list[str]:
    """Words from the vocabulary typed one character at a time, alone or after one or two other words."""
    rng = random.Random(seed)
    out = []
    for number in range(words):
        lead = "" if number % 3 == 0 else rng.choice(terms) + " " + (rng.choice(terms) + " " if number % 3 == 2 else "")
        word = rng.choice(terms)
        out += [lead + word[:length] for length in range(1, len(word) + 1)]
    return out


def queries(terms: list[str], count: int, seed: int = 7) -> list[str]:
    """Typed-as-you-go queries from the vocabulary: prefixes, two words, and a swapped pair of letters."""
    rng = random.Random(seed)
    out = []
    for number in range(count):
        word = rng.choice(terms)
        shape = number % 4
        if shape == 0:
            out.append(word[: rng.randint(1, len(word))])
        elif shape == 1:
            out.append(word)
        elif shape == 2:
            other = rng.choice(terms)
            out.append(f"{word} {other[: rng.randint(1, len(other))]}")
        else:
            at = rng.randrange(max(1, len(word) - 1))
            out.append(word[:at] + word[at + 1 : at + 2] + word[at : at + 1] + word[at + 2 :])
    return out


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def main() -> None:
    cfg = get_settings()
    conn = connect(cfg.db_path)
    if len(sys.argv) > 1:
        matter_id = int(sys.argv[1])
    else:
        matter_id = conn.execute("SELECT matter_id FROM clio_items WHERE kind='matter' LIMIT 1").fetchone()[0]

    started = time.perf_counter()
    payload = build.build(cfg, conn, CaseBuilder(conn, matter_id))
    cold = time.perf_counter() - started
    started = time.perf_counter()
    payload = build.build(cfg, conn, CaseBuilder(conn, matter_id))
    warm = time.perf_counter() - started
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    wire = payload["index"]
    print(f"nodes {len(payload['nodes'])}  edges {len(payload['edges'])}  claims {len(payload['claims']['id'])}")
    print(f"index: units {wire['units']}  terms {len(wire['terms'])}  postings {sum(wire['df'])}")
    print(f"payload: {len(raw)} bytes raw, {len(packed)} bytes gzip -9")
    for part in ("nodes", "edges", "claims", "index"):
        piece = json.dumps(payload[part], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        print(f"  {part}: {len(piece)} raw, {len(gzip.compress(piece, 9))} gzip alone")
    print(f"build: {cold * 1000:.0f} ms first in this process, {warm * 1000:.0f} ms second")

    api.stored(cfg, conn, matter_id)
    timings = []
    for _ in range(200):
        started = time.perf_counter()
        api.stored(cfg, conn, matter_id)
        timings.append((time.perf_counter() - started) * 1000)
    print(f"cached hit, in process (version check + memory read): p50 {statistics.median(timings):.2f} ms,"
          f" p95 {percentile(timings, 0.95):.2f} ms")

    index = text.unwire(wire)
    asked = queries(index.terms, 2000)
    claim_node = payload["claims"]["node"]
    timings = []
    for query in asked:
        started = time.perf_counter()
        text.rank(index, len(payload["nodes"]), claim_node, query)
        timings.append((time.perf_counter() - started) * 1000)
    print(f"python reference, {len(asked)} queries: p50 {statistics.median(timings):.2f} ms, p95 {percentile(timings, 0.95):.2f} ms")

    try:
        from py_mini_racer import MiniRacer
    except ImportError:
        print("mini-racer not installed: JavaScript parity and V8 timings skipped")
        return
    engine = MiniRacer()
    path = Path(os.environ.get("GRAPH_SEARCH_JS") or SHIPPED)
    print(f"JavaScript under test: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    # a script, not a module, in this engine
    source = re.sub(r"^export\s+(function|const)", r"\1", path.read_text(), flags=re.M)
    engine.eval(SHIM + source)
    engine.eval("var payloadText = " + json.dumps(raw.decode("utf-8")) + "; var payload = JSON.parse(payloadText);")
    engine.eval(HARNESS)

    worst_node = worst_claim = 0.0
    same_nodes = same_claims = 0
    for query in asked:
        theirs = json.loads(engine.call("run", query))
        ours = text.rank(index, len(payload["nodes"]), claim_node, query)
        their_nodes = dict((int(node), rel) for node, rel in theirs["nodes"])
        if set(their_nodes) == set(ours["nodes"]):
            same_nodes += 1
            worst_node = max([worst_node] + [abs(their_nodes[node] - rel) for node, rel in ours["nodes"].items()])
        if len(theirs["claims"]) == len(ours["claims"]):
            worst_claim = max([worst_claim] + [abs(a[1] - b[1]) for a, b in zip(theirs["claims"], ours["claims"])])
            same_claims += [int(a[0]) for a in theirs["claims"]] == [b[0] for b in ours["claims"]]
    print(f"parity over {len(asked)} queries: same node set {same_nodes}, same claim list {same_claims};"
          f" largest relevance difference nodes {worst_node:.2e}, claims {worst_claim:.2e}")

    print(f"V8 JSON.parse of the payload: {engine.call('parseMs', 20):.2f} ms; index decode (createSearch): {engine.call('decodeMs', 50):.2f} ms")
    result = engine.call("latency", typed(index.terms, 300), 200)
    print(f"V8 query latency, vocabulary words typed a character at a time: {result}")


if __name__ == "__main__":
    main()
