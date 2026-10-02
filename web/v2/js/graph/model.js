// Turns the payload of GET /api/matters/{id}/graph into flat typed arrays, so
// the frame loop never walks objects. The payload (server/graph/build.py) is:
//   nodes  [{ id, kind, label, sub, date, href, claims, w, r, x, y }]   positions and radii in world units
//   edges  [[a, b, kind]]                                               node indexes
//   claims { id[], node[], page[], text[], ok[] }                       column-wise; node = index of the source node
//   bounds [x0, y0, x1, y1], index (see search.js)
// Nothing here knows about any one matter: kinds below are Clio object types.

// Record kinds, in the fixed order their colour slots are given out (the colours themselves: palette.js).
const RECORD_KINDS = [                      // payload kind, legend label, name of one
  ['document', 'Documents', 'Document'],
  ['communication', 'Emails and calls', 'Communication'],
  ['note', 'Notes', 'Note'],
  ['task', 'Tasks', 'Task'],
  ['calendar_entry', 'Calendar', 'Calendar entry'],
  ['expense', 'Expenses', 'Expense'],
  ['contact', 'People', 'Contact'],
  ['custom_field', 'Matter fields', 'Matter field'],
];
// Containers are drawn as one neutral kind: they organise the map, they are not records.
const HUBS = { matter: 'Matter', folder: 'Folder', group: 'Group' };

const pretty = (k) => String(k || 'other').replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());

export function buildModel(p) {
  const raw = p.nodes || [];
  const n = raw.length;

  const kinds = [], slot = new Map();
  const present = new Set(raw.map((nd) => nd.kind));
  for (const [key, label, one] of RECORD_KINDS) {
    if (present.has(key)) { slot.set(key, kinds.length); kinds.push({ key, label, one, slot: kinds.length, color: '', count: 0, nodes: null }); }
  }
  for (const key of present) {
    if (slot.has(key) || HUBS[key]) continue;
    slot.set(key, kinds.length);
    kinds.push({ key, label: pretty(key), one: pretty(key), slot: kinds.length, color: '', count: 0, nodes: null });
  }
  if ([...present].some((k) => HUBS[k])) {
    for (const key of Object.keys(HUBS)) slot.set(key, kinds.length);
    kinds.push({ key: 'hub', label: 'Folders and groups', one: 'Group', slot: -1, color: '', count: 0, nodes: null, hub: true });
  }

  const x = new Float32Array(n), y = new Float32Array(n), r = new Float32Array(n);
  const kind = new Uint8Array(n), claimCount = new Uint32Array(n);
  const labels = new Array(n), subs = new Array(n), dates = new Array(n), hrefs = new Array(n), kindName = new Array(n), ids = new Array(n);
  const idIndex = new Map();
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    const nd = raw[i];
    x[i] = +nd.x || 0; y[i] = +nd.y || 0; r[i] = +nd.r || 6;
    if (x[i] - r[i] < x0) x0 = x[i] - r[i]; if (x[i] + r[i] > x1) x1 = x[i] + r[i];
    if (y[i] - r[i] < y0) y0 = y[i] - r[i]; if (y[i] + r[i] > y1) y1 = y[i] + r[i];
    const k = slot.get(nd.kind);
    kind[i] = k; kinds[k].count++;
    kindName[i] = HUBS[nd.kind] || kinds[k].one;
    labels[i] = String(nd.label ?? kindName[i]);
    subs[i] = nd.sub ? String(nd.sub) : '';
    dates[i] = nd.date ? String(nd.date).slice(0, 10) : '';
    hrefs[i] = nd.href || null;
    ids[i] = String(nd.id ?? i); idIndex.set(ids[i], i);
    claimCount[i] = nd.claims || 0;
  }
  for (const k of kinds) k.nodes = new Uint16Array(k.count);
  const fill = new Uint16Array(kinds.length);
  for (let i = 0; i < n; i++) kinds[kind[i]].nodes[fill[kind[i]]++] = i;

  // label priority at rest: the biggest circles first (the matter, folders, groups, people, then heavy records)
  const bySize = new Uint16Array(n), byClaims = new Uint16Array(n);
  for (let i = 0; i < n; i++) { bySize[i] = i; byClaims[i] = i; }
  bySize.sort((a, b) => r[b] - r[a] || claimCount[b] - claimCount[a] || a - b);
  byClaims.sort((a, b) => claimCount[b] - claimCount[a] || a - b);

  // Links Clio holds first; links that only group the layout (edge kind 3) after them, drawn fainter.
  const rawEdges = p.edges || [];
  const groupKind = (p.edge_kinds || []).indexOf('group');
  const edges = new Uint16Array(rawEdges.length * 2);
  let e = 0, real = 0;
  for (let pass = 0; pass < 2; pass++) {
    for (const ed of rawEdges) {
      const a = ed[0], b = ed[1];
      if ((ed[2] === groupKind) !== (pass === 1)) continue;
      if (!(a >= 0 && a < n && b >= 0 && b < n) || a === b) continue;
      edges[e++] = a; edges[e++] = b;
    }
    if (pass === 0) real = e >> 1;
  }

  const c = p.claims || {};
  const m = (c.node || []).length;
  const iparent = Uint16Array.from(c.node || []), iord = new Uint16Array(m), perNode = new Uint16Array(n);
  for (let j = 0; j < m; j++) iord[j] = perNode[iparent[j]]++;

  // What the drawer opens. A claim reference carries its id, so the drawer lands on that claim's own quote.
  function sourceRef(node, claim = -1) {
    if (!hrefs[node]) return null;
    const [refKind, ...rest] = ids[node].split(':');
    const ref = { kind: refKind, clio_id: rest.join(':'), label: labels[node], date: dates[node] || null, href: hrefs[node] };
    if (claim >= 0) {
      ref.href += `${ref.href.includes('?') ? '&' : '?'}claim=${encodeURIComponent(c.id[claim])}`;
      if (c.page[claim]) ref.page = c.page[claim];
    }
    return ref;
  }

  const b = Array.isArray(p.bounds) && p.bounds.length === 4 ? p.bounds : null;
  return {
    n, m, version: p.version || '', x, y, r, kind, kinds, labels, subs, dates, kindName, claimCount, bySize, byClaims,
    ids, indexOf: (id) => idIndex.get(String(id)) ?? -1,
    edges: edges.subarray(0, e), edgeCount: e >> 1, realEdgeCount: real,
    iparent, iord, itexts: c.text || [], ipages: c.page || [], sourceRef,
    bounds: b ? { x0: b[0], y0: b[1], x1: b[2], y1: b[3] } : (n ? { x0, x1, y0, y1 } : { x0: 0, x1: 1, y0: 0, y1: 1 }),
  };
}
