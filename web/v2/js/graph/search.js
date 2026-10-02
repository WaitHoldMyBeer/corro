// Search over the index the server ships in the graph payload, scored here in
// the browser so a keystroke costs no round trip.
//
// The server (server/graph/text.py) tokenised the matter once and sent, per
// term, a list of (unit, impact byte): the term-frequency half of BM25, already
// computed. A query is a binary search for the term range, idf x impact added
// into a Float32Array, claims rolled up to their nodes, and a 0..1 relevance
// relative to the best match. A unit is a node (unit = node index) or a claim
// (unit = node count + claim index).
//
// This is the only copy of the browser scoring. The constants and the tokeniser
// must stay identical to server/graph/text.py; `python -m server.graph.measure`
// runs this file against the Python scoring and reports any difference. Beyond
// the scoring: the kind filter, and per node the count of matching claims and
// the best one.

const PREFIX_WEIGHT = 0.85;      // a term the token only prefixes scores this times typed/total length
const FUZZY_WEIGHT = 0.5;        // a term one edit away, tried only when nothing else matched
const FUZZY_MIN_LENGTH = 4;
const CLAIM_LIFT = 0.9;          // a node scores at least this much of its best claim
const MAX_TOKEN = 32;
const MAX_QUERY_TOKENS = 16;
const STOP = new Set('a an and are as at be by for from has have in is it of on or that the this to was were will with'.split(' '));

function fold(text) {
  return text.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/(\d),(?=\d)/g, '$1');
}

// A stop word or single character counts only while it is still being typed.
export function queryTokens(text) {
  const folded = fold(text);
  const found = folded.match(/[a-z0-9]+/g) || [];
  const typing = /[a-z0-9]$/.test(folded);
  const out = [];
  for (let i = 0; i < found.length; i++) {
    const run = found[i];
    const last = typing && i === found.length - 1;
    if (run.length > MAX_TOKEN || (!last && (run.length < 2 || STOP.has(run)))) continue;
    if (!out.includes(run) && out.length < MAX_QUERY_TOKENS) out.push(run);
  }
  return out;
}

function withinOne(a, b) {
  const la = a.length, lb = b.length;
  if (la - lb > 1 || lb - la > 1) return false;
  const n = la < lb ? la : lb;
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  if (i === n) return la !== lb;
  if (la === lb) {
    if (a.slice(i + 1) === b.slice(i + 1)) return true;
    return i + 1 < la && a.charCodeAt(i) === b.charCodeAt(i + 1) && a.charCodeAt(i + 1) === b.charCodeAt(i) && a.slice(i + 2) === b.slice(i + 2);
  }
  return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
}

export function createSearch(payload, model) {
  const index = payload.index || { units: 0, terms: [], df: [], postings: '' };
  const terms = index.terms, T = terms.length, U = index.units;
  const { n, m, kind, iparent } = model;

  // postings into typed arrays, once
  let P = 0;
  for (let t = 0; t < T; t++) P += index.df[t];
  const start = new Uint32Array(T + 1);
  const unitOf = U > 65535 ? new Uint32Array(P) : new Uint16Array(P);
  const impact = new Uint8Array(P);
  const idf = new Float32Array(T);                  // already divided by 255, the impact scale
  const termLength = new Uint8Array(T);
  const binary = atob(index.postings || '');
  for (let t = 0, at = 0, o = 0; t < T; t++) {
    const df = index.df[t];
    start[t] = o;
    for (let j = 0, unit = 0; j < df; j++) {
      let delta = 0, shift = 0, byte;
      do { byte = binary.charCodeAt(at++); delta |= (byte & 127) << shift; shift += 7; } while (byte & 128);
      unit += delta;
      unitOf[o] = unit; impact[o++] = binary.charCodeAt(at++);
    }
    start[t + 1] = o;
    idf[t] = Math.log(1 + (U - df + 0.5) / (df + 0.5)) / 255;
    termLength[t] = terms[t].length;
  }

  // scratch, reused by every query
  const score = new Float32Array(U), best = new Float32Array(U), matched = new Uint8Array(U);
  const touched = new Int32Array(U), tokenTouched = new Int32Array(U);
  let touchedN = 0, tokenN = 0;

  const res = {
    query: '', tokens: [], active: false, ms: 0, marker: null,
    nodeRel: new Float32Array(n), nodeOrder: new Uint16Array(n), nodeHits: 0,
    nodeCnt: new Uint16Array(n), bestItem: new Int32Array(n),
    itemRel: new Float32Array(m), itemOrder: new Int32Array(m), itemHits: 0,
  };
  const byNode = (a, b) => res.nodeRel[b] - res.nodeRel[a] || a - b;
  const byItem = (a, b) => res.itemRel[b] - res.itemRel[a] || a - b;

  function take(t, weight) {
    const w = weight * idf[t];
    for (let i = start[t], end = start[t + 1]; i < end; i++) {
      const unit = unitOf[i], value = w * impact[i];
      if (best[unit] === 0) tokenTouched[tokenN++] = unit;
      if (value > best[unit]) best[unit] = value;
    }
  }

  function lowerBound(token) {
    let lo = 0, hi = T;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (terms[mid] < token) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // kindOn: one byte per kind; a node of a switched-off kind, and its claims, never match.
  function run(text, kindOn) {
    const t0 = performance.now();
    for (let i = 0; i < touchedN; i++) { score[touched[i]] = 0; matched[touched[i]] = 0; }
    touchedN = 0;
    res.nodeRel.fill(0); res.nodeCnt.fill(0); res.bestItem.fill(-1); res.itemRel.fill(0);
    res.nodeHits = 0; res.itemHits = 0;
    const wanted = queryTokens(text);
    res.query = text; res.tokens = wanted; res.active = wanted.length > 0; res.marker = null;
    if (!wanted.length) { res.ms = performance.now() - t0; return res; }

    for (let q = 0; q < wanted.length; q++) {
      const token = wanted[q];
      tokenN = 0;
      let t = lowerBound(token);
      const first = t;
      for (; t < T && terms[t].startsWith(token); t++) take(t, termLength[t] === token.length ? 1 : PREFIX_WEIGHT * token.length / termLength[t]);
      if (t === first && token.length >= FUZZY_MIN_LENGTH) for (t = 0; t < T; t++) if (withinOne(token, terms[t])) take(t, FUZZY_WEIGHT);
      for (let i = 0; i < tokenN; i++) {
        const unit = tokenTouched[i];
        if (matched[unit] === 0) touched[touchedN++] = unit;
        score[unit] += best[unit]; matched[unit]++; best[unit] = 0;
      }
    }

    // coordination (a unit matching half the words scores a quarter), then claims roll up to their nodes
    const { nodeRel, nodeCnt, bestItem, itemRel } = res;
    let top = 0;
    for (let i = 0; i < touchedN; i++) {
      const unit = touched[i], share = matched[unit] / wanted.length, value = score[unit] * share * share;
      if (unit < n) {
        if (kindOn && !kindOn[kind[unit]]) continue;
        if (value > nodeRel[unit]) nodeRel[unit] = value;
      } else {
        const claim = unit - n, node = iparent[claim];
        if (claim >= m || (kindOn && !kindOn[kind[node]])) continue;
        itemRel[claim] = value; res.itemOrder[res.itemHits++] = claim;
        nodeCnt[node]++;
        if (bestItem[node] < 0 || value > itemRel[bestItem[node]]) bestItem[node] = claim;
        if (value * CLAIM_LIFT > nodeRel[node]) nodeRel[node] = value * CLAIM_LIFT;
      }
      if (value > top) top = value;
    }
    if (top > 0) {
      for (let node = 0; node < n; node++) if (nodeRel[node] > 0) { nodeRel[node] /= top; res.nodeOrder[res.nodeHits++] = node; }
      for (let j = 0; j < res.itemHits; j++) itemRel[res.itemOrder[j]] /= top;
      res.nodeOrder.subarray(0, res.nodeHits).sort(byNode);
      res.itemOrder.subarray(0, res.itemHits).sort(byItem);
    }
    res.ms = performance.now() - t0;
    return res;
  }

  // The words around the first hit, split so the caller can mark the hit with DOM nodes.
  function snippet(text, width = 110) {
    if (!text) return null;
    if (!res.marker && res.tokens.length) {
      const alt = res.tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
      res.marker = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alt})[\\p{L}\\p{N}]*`, 'iu');
    }
    const hit = res.marker ? res.marker.exec(text) : null;
    if (!hit) return { pre: text.length > width ? `${text.slice(0, width).trimEnd()}…` : text, hit: '', post: '' };
    const from = hit.index > width * 0.6 ? Math.max(0, hit.index - (width >> 1)) : 0;
    const to = Math.min(text.length, Math.max(from + width, hit.index + hit[0].length));
    return {
      pre: (from > 0 ? '…' : '') + text.slice(from, hit.index).trimStart(),
      hit: hit[0],
      post: text.slice(hit.index + hit[0].length, to).trimEnd() + (to < text.length ? '…' : ''),
    };
  }

  return { run, snippet, res, terms, units: U, postings: P };
}
