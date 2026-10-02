# Graph and search: architecture

Owner: architect. Code: `server/graph/` (server), `web/v2/js/graph/` (renderer and in-page search, owned by
graphui). Decided 2026-10-02 13:50 PDT; measured 2026-10-02 13:53–14:34 PDT on the live matter.

## What the graph is

- **Nodes (always drawn, a few hundred):** one per Clio object the firm would recognise — the matter, each
  folder, document, note, communication, task, calendar entry, custom field value, expense and contact — plus
  one hub per record kind that has no container in Clio.
- **Claims (searchable, drawn only when a search selects them):** every statement the digest extracted, each
  with its parent node, page and stored id. A few thousand; drawing them all is the clutter being removed.
- **Edges:** only links the data holds. `contains` (matter → folder → document, from Clio's folder parent),
  `party` (contact ↔ communication, from senders and receivers), `related` (matter ↔ contact, from Clio
  relationships). A fourth kind, `group`, ties a record to its kind hub; it is marked layout-only so nobody
  reads it as a Clio link. Claim → source is carried in the claims table, not the edge list.
- **Not from Clio:** a document the firm uploads into our own database (negative id) is a node around its own
  hub. A claim the lawyer retires in the review queue is left out of the graph and the index.

## Decisions and why

| Question | Decision | Reason, and what it rests on |
| --- | --- | --- |
| Where search runs | In the browser, over an index shipped once | A keystroke costs no network round trip and no server work. Rests on the corpus being small enough to ship: see payload bytes below. |
| Index form | Server-built inverted index: sorted vocabulary, per-term posting lists of (unit, impact byte), delta-varint, base64 inside the one JSON response; decoded once into `Uint16Array` / `Uint8Array` / `Uint32Array` | Tokenising and weighting happen once per ledger version in Python, not on every page load, and record bodies and verbatim quotes are searchable without being shipped as text. A query is a loop over contiguous memory with no object per posting. |
| Ranking | BM25 with field weights (title 3, claim statement 2, body 1), `k1` 1.2, `b` 0.75. The term-frequency half is computed on the server and quantised to one byte per posting; the browser multiplies by IDF and sums | Standard precomputed-impact layout. Quantising to 8 bits loses resolution below 1/255 of a term's maximum weight; its effect on rank order against unquantised BM25 was not measured. |
| As-you-type | Every query token matches its exact term and every term it prefixes (binary search for the range in the sorted vocabulary), weighted `0.85 × typed length / term length`; per unit the best expansion counts, not the sum | Relevance rises as a word is completed instead of jumping. Taking the maximum stops a one-letter prefix rewarding units that merely hold many words starting with it. |
| Several words | Sum over tokens × (tokens matched / tokens typed)² | Units holding every word outrank units holding one, without a hard AND that would blank the graph mid-phrase. |
| Typos | Only when a token of four or more letters matches nothing: terms at edit distance 1, weight 0.5 | One linear pass over the vocabulary, paid only on a miss. No stemming; recall on plurals was not measured. |
| Shading | `rel = score / best score of this query`, 0–1. A node's score is the larger of its own match and 0.9 × its best claim | Darker link = more relevant within a query. Not comparable across queries: a weak best hit still shows as 1. |
| Layout | Computed on the server, deterministic, static. Clusters (one per folder, per contact with its communications, per record kind) are packed greedily around the matter node, each family in its own sector, stretched 1.6 times sideways for a landscape stage (the bounds come out 1.41 : 1); inside a cluster, members sit on a sunflower spiral (`r = c·√k`, golden angle) in date order | No force simulation in the page: nothing drifts, and first paint needs no settling. Same input gives the same picture (checked: two builds compare equal). Radii are sent in world units; the closest pair of discs is 2.5 radii-sums apart, so the renderer may draw them up to 2.5 times true scale before any touch. |
| Claim nodes | Positioned by the renderer from fixed slots around the parent | Which claims show depends on the query; their positions must still not depend on frame timing. |
| Renderer | Canvas 2D, one `requestAnimationFrame` loop that stops when idle, all per-frame state in typed arrays | At a few hundred nodes Canvas 2D costs well under a millisecond of main thread per frame (below); WebGL would add a context and shaders for nothing measurable. The node count at which that stops being true was not measured. |
| Libraries | None; nothing loaded from a CDN | Index, scoring and layout are 744 lines of Python and 185 of JavaScript, comments included. |
| Titles | A document's label is the last `__` part of its file name, in words; words of one to three letters outside a short common-word list are shown as initials. Records use their own subject, name or summary | Deterministic and in code, from the record's own field. The file name is still sent (`name`) and still searchable. The initials rule is a heuristic and will sometimes capitalise a real short word. |
| Uploads | Placed after everything else, into room held open (and counted in the bounds) from the start, sized for 12; radii are scaled against what Clio holds | Checked with three synthetic uploads: no existing node moved, no radius changed, the bounds did not change. Past 12 uploads the upload cluster itself may move; nothing else does. The index is rebuilt whole (about half a second), not extended: unit ids and the length normalisation both shift when claims are added. |
| Search on the server | `server.graph.query.search_file` scores the same decoded index with the same function the parity check uses | The assistant and the search bar cannot disagree on ranking. |
| Caching | Built once per ledger version (hash of the stored Clio item hashes, the stored digest rows, the review queue's stamp and the builder's own version), gzip stored in SQLite table `graph_cache` and held in process memory; `ETag` + `Cache-Control: private, no-cache` | A reload is one conditional request and a 304. `private` because the payload is firm-only text. |

## Payload

`GET /api/matters/{id}/graph` — firm session required (the `/api/*` guard; 401 without one, checked).

```
{ v, version, matter_id, bounds:[minX,minY,maxX,maxY],
  nodes:[{id, kind, clio_id, label, sub, date, x, y, r, w, claims, href, pages?, name?, uploaded?}],
  edges:[[a, b, t]], edge_kinds:["contains","party","related","group"],
  claims:{id:[], node:[], page:[], text:[], ok:[]},
  claim_layout:{r, gap, step},
  index:{units, terms:[], df:[], postings:"<base64>"} }
```

A search unit is a node (unit id = node index) or a claim (unit id = node count + claim index). `postings` is,
for each term in order, `df` entries of: varint(unit id delta within the term's list), one impact byte 1–255.
`href` opens the existing source drawer; a claim opens at `href + "?claim=" + id`.

Tokeniser, identical in `server/graph/text.py` and `web/v2/js/graph/search.js`: NFKD, drop combining marks,
lowercase, remove commas between digits, take runs of `[a-z0-9]`, drop single characters and a short English
stop list (kept only for the word still being typed).

## Budget and measurements

Machine: the build laptop, Intel Core Ultra 9 275HX (24 logical CPUs), Linux, Chrome 150, while a dozen other
agent-driven tabs and servers were running on it. Matter: the live one, from copies of `data/` taken between
13:58 and 14:34. Reproduce the server and parity figures with
`SWANS_DATA_DIR=<copy> uv run --with mini-racer python -m server.graph.measure`. Queries are generated at run
time from the index's own vocabulary: random terms typed one character at a time, alone or after one or two
other words.

| What | Budget | Measured | Rests on |
| --- | --- | --- | --- |
| Corpus | — | 234 nodes, 233 edges, 2,953 claims; 3,187 units, 3,209 terms, 61,375 postings | `measure` |
| Payload | under 500 kB gzipped | 691,139 bytes raw, 171,413 bytes gzip -9 on the final build (Chrome reported 171,434 transferred for an earlier build of 171,134). Gzipped alone: index 103,639, claims 56,101, nodes 11,550, edges 519 | `measure`; Chrome Resource Timing |
| Server build | once per version | 428–574 ms first in a process, 175–223 ms after (5 runs; 502 and 223 ms on the final build). The first includes opening every PDF's text layer to verify quotes | `measure` |
| Server, cached | under 10 ms | version check + memory read 0.50 ms median, 0.55–0.66 ms 95th (200 calls, in process). Over HTTP on loopback with curl: 2.1–3.4 ms for the 200, 2.1–2.8 ms for the 304 (5 each) | `measure`; `curl -w` |
| Scoring correctness | — | 2,000 generated queries, shipped JavaScript against the Python implementation: same node set and same claim order in all 2,000; largest relevance difference 1.5e-7 | `measure` (V8 via mini-racer) |
| Query, per keystroke | under 5 ms at the 95th | Chrome tab, 2,050 and 2,146 queries: median at the clock floor (≤ 0.005 ms), 95th 0.28 and 0.32 ms, 99th 0.37 and 0.41 ms, worst 0.48 and 0.63 ms | Run with the search module as first written; the shipped module (same scoring, graphui's interface) measured in V8 outside a tab: 95th 0.26–0.30 ms, worst 0.42–0.57 ms (3 runs). Each query repeated 20 or 200 times and averaged, because `performance.now()` ticks at 0.1 ms. The slow tail is one- and two-letter prefixes |
| Query on the server (`search_file`) | — | 1.1 ms median, 2.7 ms 95th, version check included; about 1 s on the first call after the file changes (build plus decode) | 1,000 generated queries, in process |
| Query as the page reports it | — | 108 keystrokes per run, 4 runs: median ≤ 0.1 ms (clock floor), 95th 0.3–0.4 ms, worst 1.2–1.7 ms | Single un-repeated calls in the real page, so the worst case includes first calls before the JIT has warmed |
| Index decode at load | under 20 ms | 2.2 ms (shipped module, first call, Chrome tab); `JSON.parse` of the payload 0.9–1.8 ms | Chrome tab |
| Frame during search | 16.7 ms | Main-thread cost per frame: median 0.2 ms, 95th 0.4–0.5 ms, worst 0.9–1.4 ms. Gap between frames: median 16.7 ms, worst 16.8 ms; none over 20 ms. 4 runs of 1,024 frames, typing at one key per 70 ms | **Headless** Chrome, 938 px wide canvas, device pixel ratio 1, the engine's own probe. Not measured on a real display: the shared Chrome tab was in the background, where `requestAnimationFrame` does not fire. Raster cost is off the main thread and not in this number; at pixel ratio 2 there are four times the pixels to fill |
| Frame during search, real display | 16.7 ms | Main-thread cost per frame: median 0.3–0.4 ms, 95th 0.5–0.8 ms, worst 1.9–5.1 ms. Gap between frames: median 16.7 ms in four runs and 20.8 ms in one; 2, 3, 7, 12 and 34 gaps over 34 ms in runs of 185–249 frames | Five foreground Chrome windows, the shipped engine on a made-up model of 234 nodes with made-up results applied every 70 ms (no claims drawn), pixel ratio 1. The late frames come with the main thread idle, so they are below JavaScript: compositor, GPU or the loaded machine. Not separated; a quiet machine would tell. The panel also seems to switch between 60 and 48 Hz (gaps of exactly 20.8 ms) |
| Idle | no frames | 0 frames in 1 s with nothing moving | Headless Chrome |
| Time to first paint | — | Headless: 69 and 76 ms from the start of the module import to two frames after the graph was ready, of which fetch + parse 17–19 ms. Chrome tab (in the background): import 51 ms, fetch 33 ms, body 35 ms, parse 1.8 ms, model 0.5 ms, index 2.2 ms, engine 1.8 ms, resize 1.7 ms, first query 1.8 ms — about 130 ms plus one frame | Loopback, server cache warm. A cold server adds the build time above |

**First-load stall: found, and outside the page.** The first canvas resize blocked the main thread for 2.4–3.1 s
in 3 of 11 launches where a real, foreground Chrome was started cold straight onto the page (and 6 of 21
headless launches). Loaded into a Chrome that was already running, it took 2.2 and 2.5 ms after 55 s and 40 s
idle, and 1.7 ms in the shared browser. A CPU-backed canvas did not avoid it (stalled in 2 of 2 cold launches);
with the GPU disabled it did not occur (0 of 5). So it is the browser still starting when the canvas is first
touched — most likely the GPU process; that cause is inferred, not proven. No change in the page prevents it.
The guard is to have Chrome open before loading the app.

## Many cases in one install

Checked by reading every table definition, settings key, module-level cache and route on 2026-10-02 at 15:00
(commit 613f4b5 for the cases, 60e2ce8 for the graph), and again at 15:08 after a2351d6 and backend's 15:03 restart. The four tests in `tests/test_case_isolation.py` put
two cases in one database and check the same things from the outside.

**What a case is.** One matter id. A case read from the source system keeps the id that system gave it
(positive). A case created here gets the next id below zero from the `cases` table and a matter record of
the same shape, marked `origin: "upload"`, in the same store; every record in it — documents from a zip,
single uploads — also has an id below zero. A source sync never retires a record with an id below zero.

**Keyed by case, verified.** Every table that holds case content carries `matter_id` and every query on it
filters by it (stored records, claims, reconciliation, review queue and decisions, shares, share policies,
provider threads and replies, overrides, uploads and ingestions, assistant turns and documents, digest and
sync runs, model-call log, graph cache). Files are under `documents/<case>/` and `pages/<case>/`. Every
in-memory cache is keyed by case: the graph payload and its decoded index, the ledger, the assistant's shared
state and rendered documents, the firm overview rows, digest progress. The checker's answer cache has the
case in its key and in its lookup (a2351d6), so two cases holding word-for-word the same statements do not
share a verdict or its source links. `/api/spend` with `matter_id` gives that case's figures, pages read
included; without it, an all-cases total labelled as such. A provider's link carries a token; the
case is read from that token's own row, and replies join on case and contact together.

**The graph and search.** One payload per case, cached per case and version; `search_file` reads only that
payload. A case created here has no folder records, so documents are clustered by the folder name each one
carries from the zip. Documents that came in the zip are marked `origin: "zip"` and are the file; a document
uploaded afterwards (`origin: "uploaded"`) goes to the room held open for uploads, in a created case exactly
as in an imported one, so nothing already drawn moves. Only where every document of a case is a single
upload is there no "file" to add to, and they are all laid out as the file. An imported document that no
model has read yet is still searchable by its stored text layer. Checked with a synthetic created case beside
the imported one in one database: each graph and each search returned only its own case.

**Shared across cases on purpose.**
- Page reads and page fingerprints are keyed by the file's hash, not the case. The same file in two cases is
  read by the model once. Consequence: the second case shows almost no digest cost. Claims still reach a case
  only through that case's own document rows.
- The fee percent and basis are one firm-wide setting (the firm's standard contingency), by the Manager's
  ruling: changing it changes net-to-client and the negotiation terms in every case.
- One connection to the source system, one model-call throttle, one checker circuit breaker.

**Limits.**
- Access: one passcode and one session for the install. Cases are separate in data, not in who may open them.
- `selected_matter_id` is one value for the install, rewritten by each import and reported by `/api/status`.
  It only decides which case a fresh tab opens on: the pages address a case by the id in the URL, and
  `/api/settings/import?matter_id=` answers for the case asked.
- A case created here cannot be refreshed from the source system, which never issued its id: the import
  route answers 409 and changes nothing.
- A case created here has no contacts yet; nothing creates them, so it has no provider views.
- Ids must be integers everywhere (path parameters, the contract, many `int(...)` reads); below zero is the
  only scheme for local records.
