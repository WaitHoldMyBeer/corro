// What counts as one unit on the firm's screen, and what the assistant is told about it.
// Reads the DOM only: visible text, source references and ids. Markup is never sent.
//
// The convention (all optional; the fallback below works without it):
//   data-ai-unit            this element is one unit
//   data-ai-kind="task"     what it is; data-ai-title="..." a short name
//   data-ai-ref='{...}'     a SourceRef (or an array of them) on the unit or anything inside it
//   data-*-id, data-*-ids   ids, collected from the unit, what it contains and what contains it
//   data-ai-skip            never picked, never read
//   data-ai-probe + el.aiUnitAt(x, y) -> { rect: {x, y, w, h}, item }   for a canvas that knows what is under a point

const SKIP = '[data-ai-skip], .aiifier, .as-panel, dialog.signin-dlg, .v2-nav, .v2-top, .mock-banner, #toasts, iframe, .v2-add, .v2-skel';
const ROWS = 'tr, li, dd, blockquote, figure, .v2-row, .wk-row, .mc-row, .gx-row, .v2-fig, .mc-fig, .v2-stat';
const BLOCKS = '.v2-card, .card, .v2-preview, article, table, details, p';
const UNIT = `[data-ai-unit], ${ROWS}, ${BLOCKS}`;
const TEXT_SKIP = 'script, style, [hidden], [aria-hidden="true"], [data-ai-skip], .icon-btn, .grip, .chip, select, textarea, input, svg';
const TEXT_BLOCK = 'td, th, p, li, tr, div, section, article, header, h1, h2, h3, h4, h5, dt, dd, blockquote, button, label, summary';
const TITLE = 'h1, h2, h3, h4, .v2-row-title, .v2-li-main, .wk-title, .gx-row-title, strong, th, td';
const ID_ATTRS = '[data-id], [data-k], [data-conflict-id], [data-document-id], [data-claim-id], [data-claim-ids], [data-record-id], [data-node-id], [data-contact-id], [data-provider-id], [data-task-id], [data-note-id]';
const REF_KEYS = ['kind', 'clio_id', 'label', 'page', 'date', 'href', 'quote'];
const TAB_KIND = { calendar: 'event', communications: 'communication', notes: 'note', documents: 'document', tasks: 'task', activities: 'activity', fields: 'field', bills: 'bill', transactions: 'transaction' };
const MAX_TEXT = 4000, MAX_REFS = 24, MAX_IDS = 50, MIN_PARAGRAPH = 80;

const squash = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
const cut = (s, n) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

function qualifies(el) {
  if (el.hasAttribute('data-ai-unit')) return true;
  if (el.localName === 'p') return el.textContent.length >= MIN_PARAGRAPH;      // a short line belongs to whatever holds it
  if (el.localName === 'tr') return !el.closest('thead');
  if (el.localName === 'li') return !el.matches('.sep, [role="separator"]') && !el.querySelector(':scope > .gx-row');   // the row inside is the unit
  return true;
}

// The unit under a viewport point, or null. One elementFromPoint; no layout is read here beyond that.
let lastEl = null, lastUnit = null;
export function unitAt(x, y) {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  // A canvas has no elements to walk: its owner says what is drawn under the point. Controls laid over the
  // canvas (a search box, a legend) are ordinary elements and are not asked about.
  const probe = el.closest('[data-ai-probe]');
  if (probe && typeof probe.aiUnitAt === 'function' && (el === probe || el.localName === 'canvas') && !probe.closest(SKIP)) {
    let hit = null;
    try { hit = probe.aiUnitAt(x, y); } catch { hit = null; }
    if (!hit || !hit.rect || !hit.item) return null;
    const ids = hit.item.ids || {};
    return { el: probe, virtual: hit, key: `probe:${ids.node_id ?? ids.clio_id ?? hit.item.title ?? ''}` };
  }
  if (el === lastEl && lastUnit?.el.isConnected) return lastUnit;
  lastEl = el;
  lastUnit = unitFrom(el);
  return lastUnit;
}

export function unitFrom(el) {
  if (!el || el.closest(SKIP)) return null;
  for (let n = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
    if (n.matches(UNIT) && qualifies(n)) return { el: n, key: n };
  }
  return null;
}

// Every unit inside a scope, in reading order: the keyboard path walks this list.
export function unitsIn(scope, max = 400) {
  const out = [];
  for (const el of scope.querySelectorAll(UNIT)) {
    if (!qualifies(el) || el.closest(SKIP)) continue;
    if (el.checkVisibility && !el.checkVisibility()) continue;
    out.push({ el, key: el });
    if (out.length >= max) break;
  }
  return out;
}

// The open section: '#/c/<case id>/<tab>' inside a case, '#/<tab>' at firm level and in older links.
function routeName() {
  const parts = location.hash.replace(/^#\/?/, '').split('?')[0].split('/');
  return (parts[0] === 'c' ? parts[2] : parts[0]) || 'dashboard';
}

function kindOf(el) {
  const k = el.getAttribute('data-ai-kind');
  if (k) return k;
  if (el.hasAttribute('data-node-id')) return 'graph-node';
  if (el.matches('.v2-card, .card, .v2-preview')) return 'card';
  if (el.localName === 'table') return 'table';
  if (el.matches('p, blockquote')) return 'paragraph';
  if (el.matches('.v2-fig, .mc-fig, .v2-stat, figure')) return 'figure';
  if (el.matches(ROWS)) return (!el.closest('.v2-card') && TAB_KIND[routeName()]) || 'row';
  return 'section';
}

function titleOf(el) {
  const given = el.getAttribute('data-ai-title');
  if (given) return cut(squash(given), 120);
  if (el.matches('p, blockquote')) return cut(squash(el.textContent), 80);
  const h = el.querySelector(TITLE);
  const t = squash(h ? h.textContent : '');
  return cut(t || squash(visibleText(el, 160)), 120);
}

// Where on the screen it was: the section, and the card or panel that holds it.
function whereOf(el) {
  const parts = [routeName().replace(/^./, (c) => c.toUpperCase())];
  const holder = el.parentElement?.closest('.v2-card, dialog, .card');
  const h = holder?.querySelector('h1, h2, h3');
  if (h) parts.push(cut(squash(h.textContent), 80));
  return parts.join(' > ');
}

// Visible text, block by block. Controls, chips and hidden parts are left out; table cells are joined with " | ".
function visibleText(root, max = MAX_TEXT) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.nodeType === 1 ? (n.matches(TEXT_SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP) : NodeFilter.FILTER_ACCEPT),
  });
  let out = '', lastBlock = null;
  while (w.nextNode() && out.length < max) {
    const s = squash(w.currentNode.data);
    if (!s) continue;
    const block = w.currentNode.parentElement.closest(TEXT_BLOCK) || root;
    if (!out) out = s;
    else if (block === lastBlock) out += ` ${s}`;
    else if (lastBlock && /^t[dh]$/.test(block.localName) && block.parentElement === lastBlock.parentElement) out += ` | ${s}`;
    else out += `\n${s}`;
    lastBlock = block;
  }
  return cut(out, max);
}

function cleanRef(r) {
  if (!r || typeof r !== 'object') return null;
  const out = {};
  for (const k of REF_KEYS) if (r[k] != null && r[k] !== '') out[k] = typeof r[k] === 'string' ? cut(r[k], k === 'quote' ? 300 : 200) : r[k];
  return out.label != null || out.clio_id != null ? out : null;
}

function refsOf(el) {
  const out = [], seen = new Set();
  const add = (r) => {
    const ref = cleanRef(r);
    if (!ref) return;
    const key = `${ref.kind ?? ''}:${ref.clio_id ?? ref.label}:${ref.page ?? ''}`;
    if (seen.has(key) || out.length >= MAX_REFS) return;
    seen.add(key); out.push(ref);
  };
  for (const n of [el, ...el.querySelectorAll('[data-ai-ref]')]) {
    const raw = n.getAttribute('data-ai-ref');
    if (!raw) continue;
    try { const v = JSON.parse(raw); (Array.isArray(v) ? v : [v]).forEach(add); } catch { /* not a reference */ }
  }
  // A chip that does not say what it points at still names its source.
  if (!out.length) for (const c of el.querySelectorAll('.chip')) add({ label: squash(c.querySelector('.chip-label')?.textContent || c.textContent) });
  return out;
}

function idName(attr, node) {
  const m = /^data-(?:(.+)-(ids?)|(id|k))$/.exec(attr);
  if (!m) return null;
  if (m[3] === 'k') return ['record', 'id'];
  if (m[3] === 'id') return [node.matches('.v2-card') ? 'card' : '', 'id'];
  if (m[1] === 'ai') return null;
  return [m[1].replace(/-/g, '_'), m[2]];
}

function idsOf(el) {
  const ids = {};
  const many = (name, values) => { const cur = ids[name] || (ids[name] = []); for (const v of values) if (v && !cur.includes(v) && cur.length < MAX_IDS) cur.push(v); };
  const read = (node, own) => {
    for (const a of node.attributes) {
      const n = idName(a.name, node);
      if (!n || !a.value) continue;
      const [base, form] = n;
      const one = base ? `${base}_id` : 'id';
      if (form === 'ids') many(`${base}_ids`, a.value.split(/[\s,]+/));
      else if (own) { if (ids[one] == null) ids[one] = a.value; }
      else many(`${one}s`, [a.value]);
    }
  };
  for (let n = el; n && n !== document.body; n = n.parentElement) read(n, true);     // the unit, then what holds it
  let seen = 0;
  for (const n of el.querySelectorAll(ID_ATTRS)) { if (seen++ >= MAX_IDS) break; read(n, false); }
  for (const k of Object.keys(ids)) if (Array.isArray(ids[k]) && !ids[k].length) delete ids[k];
  return ids;
}

// Single ids written on the unit's own element, as opposed to inherited or contained ones.
function ownIds(el) {
  const out = {};
  for (const a of el.attributes) {
    const n = idName(a.name, el);
    if (n && a.value && n[1] === 'id') out[n[0] ? `${n[0]}_id` : 'id'] = a.value;
  }
  return out;
}

function keyOf(item) {
  const s = `${item.kind}|${JSON.stringify(item.ids)}|${item.title}|${item.text.length}`;
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return `u${(h >>> 0).toString(36)}`;
}

// The short label shown beside the highlight: what would be brought in.
export function labelOf(unit) {
  if (unit.virtual) return [unit.virtual.item.kind || 'item', cut(squash(unit.virtual.item.title), 60)];
  return [kindOf(unit.el), cut(titleOf(unit.el), 60)];
}

// The item handed to the assistant. Plain data: strings, numbers, lists.
export function describe(unit) {
  let item;
  if (unit.virtual) {
    const v = unit.virtual.item;
    item = {
      kind: squash(v.kind) || 'item', title: cut(squash(v.title), 120), text: cut(String(v.text ?? '').trim(), MAX_TEXT),
      where: whereOf(unit.el), source_refs: (v.source_refs || []).map(cleanRef).filter(Boolean).slice(0, MAX_REFS), ids: { ...(v.ids || {}) },
    };
  } else {
    const el = unit.el;
    item = { kind: kindOf(el), title: titleOf(el), text: visibleText(el), where: whereOf(el), source_refs: refsOf(el), ids: idsOf(el) };
  }
  item.key = keyOf(item);
  // The panel's tray dedupes on `id` and the server opens it in its first round. It is a real id only when the
  // unit itself carries one the server can resolve: a claim id, or a record as "kind:clio_id" (a graph node id is
  // one), or a document id. An id inherited from what holds the unit, or a bare number, would make two different
  // rows look like one thing, so those fall back to the key; the server still opens every source reference.
  const own = unit.virtual ? item.ids : ownIds(unit.el);
  const typed = (v) => (typeof v === 'string' && /^[a-z_]+:\S/.test(v) ? v : undefined);
  const doc = own.document_id != null && own.document_id !== '' ? (typed(String(own.document_id)) ?? `document:${own.document_id}`) : undefined;
  item.id = String(own.claim_id || typed(own.node_id) || typed(own.record_id) || doc || item.key);
  item.label = item.title || item.kind;
  return item;
}
