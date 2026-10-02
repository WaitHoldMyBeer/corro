// Stand-in for GET /api/matters/{id}/graph: a payload of the same shape and
// order of size, made of neutral placeholder words from a seeded generator.
// Used by mock mode (web/js/mock.js answers the graph route with it) and by the
// development harness. Nothing here comes from, or describes, any matter.

const WORDS = ('amber anchor arbor atlas basin beacon birch bridge canyon cedar cinder clover cobalt comet coral '
  + 'delta ember fable falcon fern fjord garnet glacier harbor hazel heron indigo iris jasper juniper kestrel lagoon '
  + 'lantern larch linen lotus maple marble meadow mesa nectar nimbus oak onyx opal orchard osprey pebble pine plume '
  + 'prairie quartz quill raven reef ridge river saffron sage slate sparrow spruce summit thistle timber topaz tundra '
  + 'umber valley velvet willow wren yarrow zephyr zinc').split(' ');
const KINDS = ['document', 'communication', 'note', 'task', 'calendar_entry', 'expense'];
const SHARE = [0.16, 0.36, 0.2, 0.1, 0.1, 0.08];
const K1 = 1.2, B = 0.75;

function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// The same index the server builds (server/graph/text.py): per term, (unit delta as a varint, impact byte), base64.
function wireIndex(texts) {
  const freq = texts.map((t) => { const f = new Map(); for (const w of t.toLowerCase().match(/[a-z0-9]+/g) || []) if (w.length > 1) f.set(w, (f.get(w) || 0) + 1); return f; });
  const len = freq.map((f) => [...f.values()].reduce((s, v) => s + v, 0));
  const avg = len.reduce((s, v) => s + v, 0) / (len.filter(Boolean).length || 1);
  const byTerm = new Map();
  freq.forEach((f, unit) => {
    const norm = K1 * (1 - B + B * len[unit] / avg);
    for (const [term, tf] of f) { if (!byTerm.has(term)) byTerm.set(term, []); byTerm.get(term).push([unit, Math.max(1, Math.round(255 * tf / (tf + norm)))]); }
  });
  const terms = [...byTerm.keys()].sort();
  const bytes = [];
  for (const term of terms) {
    let previous = 0;
    for (const [unit, impact] of byTerm.get(term)) {
      let delta = unit - previous; previous = unit;
      while (delta >= 0x80) { bytes.push((delta & 0x7f) | 0x80); delta >>= 7; }
      bytes.push(delta, impact);
    }
  }
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.slice(i, i + 8192));
  return { units: texts.length, terms, df: terms.map((t) => byTerm.get(t).length), postings: btoa(binary) };
}

export function standinGraph({ nodes = 220, claims = 3000, seed = 7 } = {}) {
  const rand = rng(seed);
  const word = () => WORDS[(rand() * rand() * WORDS.length) | 0];     // skewed, so some words are common
  const phrase = (k) => { const s = Array.from({ length: k }, word).join(' '); return s[0].toUpperCase() + s.slice(1); };
  const out = { v: 1, version: `standin-${seed}`, nodes: [], edges: [], claims: { id: [], node: [], page: [], text: [], ok: [] } };
  const add = (kind, label, x, y, r) => out.nodes.push({ id: `${kind}:${out.nodes.length}`, kind, label, sub: null, date: null, href: kind === 'group' ? null : `#standin-${out.nodes.length}`, claims: 0, w: 1, r, x, y }) - 1;
  add('matter', 'Placeholder matter', 0, 0, 20);
  const hubs = KINDS.map((kind, i) => {
    const a = i / KINDS.length * Math.PI * 2 - 1.2;
    const hub = add('group', `${phrase(1)} group`, Math.cos(a) * 520, Math.sin(a) * 520, 13);
    out.edges.push([0, hub, 3]);
    return { hub, members: 0 };
  });
  while (out.nodes.length < nodes) {
    let k = 0, roll = rand();
    while (roll > SHARE[k] && k < KINDS.length - 1) { roll -= SHARE[k]; k++; }
    const h = hubs[k], j = h.members++, hubNode = out.nodes[h.hub];
    const d = 30 * Math.sqrt(j + 2), ang = j * 2.39996;
    const i = add(KINDS[k], phrase(2 + ((rand() * 3) | 0)), hubNode.x + Math.cos(ang) * d, hubNode.y + Math.sin(ang) * d, 6);
    out.nodes[i].sub = phrase(2); out.nodes[i].date = `2000-01-${String(1 + (i % 28)).padStart(2, '0')}`;
    out.edges.push([h.hub, i, 3]);
  }
  for (let j = 0; j < claims; j++) {
    const node = 1 + KINDS.length + ((rand() ** 2.2 * (nodes - 1 - KINDS.length)) | 0);
    out.claims.id.push(`c${j}`); out.claims.node.push(node); out.claims.page.push(out.nodes[node].kind === 'document' ? 1 + ((rand() * 40) | 0) : 0);
    out.claims.text.push(`${phrase(7 + ((rand() * 12) | 0))}.`); out.claims.ok.push(1);
    out.nodes[node].claims++;
  }
  const most = Math.max(...out.nodes.map((nd) => nd.claims)) || 1;
  for (const nd of out.nodes) if (nd.kind !== 'matter' && nd.kind !== 'group') { nd.w = Math.log1p(nd.claims) / Math.log1p(most); nd.r = 6 + 5 * nd.w; }
  const xs = out.nodes.map((nd) => nd.x), ys = out.nodes.map((nd) => nd.y);
  out.bounds = [Math.min(...xs) - 60, Math.min(...ys) - 60, Math.max(...xs) + 60, Math.max(...ys) + 60];
  out.index = wireIndex(out.nodes.map((nd) => (nd.kind === 'group' ? '' : `${nd.label} ${nd.sub || ''}`)).concat(out.claims.text));
  return out;
}
