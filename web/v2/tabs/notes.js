// Notes: the matter's notes as a list, each marked when a document in the file differs from it.
// The join is made here from two things the server already holds: the case model's `conflicts` (entry side
// and document side, as claim ids) and each claim's source reference (kind 'note', Clio id of the note).
// Nothing is judged here: both sides are shown, side by side, and which is right is left to the reader.
import { el, empty } from '../../js/util.js';

const PAGE = 200;
// Kept across visits so reopening Notes does not read every note again (key: matter:note).
const detailCache = new Map();
const authorCache = new Map();

function ensureStyles() {
  if (document.getElementById('nw-styles')) return;
  const link = document.createElement('link');
  link.id = 'nw-styles'; link.rel = 'stylesheet';
  link.href = new URL('../css/notes-write.css', import.meta.url).href;
  document.head.append(link);
}

// note id (number as string) -> [{ conflict, entry: [claim], docs: [claim] }]
export function differencesByNote(c) {
  const claims = new Map((c?.claims || []).map((x) => [x.id, x]));
  const out = new Map();
  for (const conflict of c?.conflicts || []) {
    const entry = new Map();
    for (const id of conflict.notes_claim_ids || []) {
      const claim = claims.get(id);
      if (claim?.source?.kind !== 'note') continue;
      const key = String(claim.source.clio_id);
      if (!entry.has(key)) entry.set(key, []);
      entry.get(key).push(claim);
    }
    const docs = (conflict.document_claim_ids || []).map((id) => claims.get(id)).filter(Boolean);
    for (const [key, list] of entry) {
      if (!out.has(key)) out.set(key, []);
      out.get(key).push({ conflict, entry: list, docs });
    }
  }
  return out;
}

async function loadAll(ctx) {
  const items = [];
  for (let o = 0; ; o += PAGE) {
    const r = await ctx.api(`/api/matters/${ctx.matterId}/records/notes?offset=${o}&limit=${PAGE}`);
    if (!r.available) return { available: false, note: r.note, items: [] };
    items.push(...r.items);
    if (items.length >= r.total || !r.items.length) break;
  }
  return { available: true, items };
}

// The server hands over note text with HTML entities (&#39;); decode to plain text, never as markup.
const plain = (s) => (s && s.includes('&') ? new DOMParser().parseFromString(s, 'text/html').documentElement.textContent : s || '');
// Wrap each match of the search in <mark>, in a title or snippet span (plain text only).
function markMatches(node, needle) {
  const t = node.firstChild;
  if (!needle || !t || t.nodeType !== 3) return;
  const low = t.data.toLowerCase();
  const frag = document.createDocumentFragment();
  let at = 0;
  for (let i = low.indexOf(needle); i !== -1; i = low.indexOf(needle, at)) {
    frag.append(t.data.slice(at, i), el('mark', { text: t.data.slice(i, i + needle.length) }));
    at = i + needle.length;
  }
  frag.append(t.data.slice(at));
  t.replaceWith(frag);
}
const noteKey = (item) => String(item.source?.clio_id ?? item.id.split(':')[1]);

export default async function mount(host, c0, ctx, params = {}) {
  ensureStyles();
  let c = c0;
  let diffs = differencesByNote(c);
  let filter = params.differs === '1' ? 'differs' : 'all';
  let q = '';
  const open = new Set();
  const dk = (k) => `${ctx.matterId}:${k}`;
  const details = { has: (k) => detailCache.has(dk(k)), get: (k) => detailCache.get(dk(k)), set: (k, v) => detailCache.set(dk(k), v), delete: (k) => detailCache.delete(dk(k)) };
  const authors = { has: (k) => authorCache.has(dk(k)), get: (k) => authorCache.get(dk(k)), set: (k, v) => authorCache.set(dk(k), v) };
  let items = [];
  let destroyed = false;

  const count = el('span', { class: 'nw-count muted small', 'aria-live': 'polite' });
  const search = el('input', { type: 'search', class: 'tab-search', placeholder: 'Search notes', 'aria-label': 'Search notes' });
  const seg = el('div', { class: 'seg nw-seg', role: 'group', 'aria-label': 'Which notes' });
  const list = el('ol', { class: 'nw-list' });
  const note = el('div', { class: 'tab-note' });
  host.replaceChildren(el('section', { class: 'tab nw' },
    el('header', { class: 'tab-h' }, el('h2', { text: 'Notes' }), count, seg, search), note, list));

  const detailOf = (item) => {
    const k = noteKey(item);
    if (!item.source?.href) return Promise.reject(new Error('this note has no source to open'));
    if (!details.has(k)) {
      details.set(k, ctx.api(item.source.href).then((d) => {
        if (d.author) authors.set(k, d.author);
        return d;
      }));
      details.get(k).catch(() => details.delete(k));
    }
    return details.get(k);
  };

  function paintSeg() {
    const n = items.filter((i) => diffs.has(noteKey(i))).length;
    const defs = [['all', `All notes (${items.length})`], ['differs', `A document differs (${n})`]];
    seg.replaceChildren(...defs.map(([k, label]) => el('button', {
      type: 'button', 'aria-pressed': String(filter === k), text: label,
      onclick: () => { filter = k; paint(); },
    })));
  }

  function differenceBlock(d) {
    const { conflict, entry, docs } = d;
    return el('li', { class: 'nw-diff' },
      el('div', { class: 'nw-diff-h' },
        el('strong', { text: conflict.topic || 'A difference' }),
        conflict.review && conflict.review !== 'unreviewed' ? el('span', { class: 'nw-tag', text: `marked ${conflict.review}` }) : null),
      conflict.summary ? el('p', { class: 'small muted nw-sum', text: conflict.summary }) : null,
      el('div', { class: 'nw-sides' },
        el('div', { class: 'nw-side' },
          el('div', { class: 'eyebrow', text: 'This note says' }),
          entry.map((cl) => el('div', { class: 'nw-claim' }, el('p', { text: cl.text }), cl.source?.quote && cl.source.quote !== cl.text ? el('blockquote', { text: cl.source.quote }) : null))),
        el('div', { class: 'nw-side' },
          el('div', { class: 'eyebrow', text: 'This page shows' }),
          docs.length ? docs.slice(0, 4).map((cl) => el('div', { class: 'nw-claim' }, el('p', { text: cl.text }), ctx.chip(cl.source))) : el('p', { class: 'muted small', text: 'The page is not listed.' }),
          docs.length > 4 ? el('p', { class: 'muted small', text: `and ${docs.length - 4} more on the same pages` }) : null)));
  }

  function body(item) {
    const k = noteKey(item);
    const text = el('div', { class: 'nw-text muted', text: 'Loading the note...' });
    const meta = el('div', { class: 'small muted' });
    detailOf(item).then((d) => {
      if (destroyed) return;
      text.className = 'nw-text';
      text.textContent = plain(d.text) || plain(item.snippet) || 'This note has no text.';
      meta.textContent = [d.author ? `Entered by ${d.author}` : null, d.parties?.length ? d.parties.join(', ') : null].filter(Boolean).join('  |  ');
    }).catch((err) => { text.className = 'nw-text error'; text.textContent = `Could not open this note: ${err.message}`; });
    const mine = diffs.get(k) || [];
    return el('div', { class: `nw-body${mine.length ? ' has-diff' : ''}` },
      el('div', { class: 'nw-note' }, meta, text, el('button', { class: 'btn ghost small', type: 'button', onclick: () => ctx.openSource(item.source) }, 'Open in the source viewer')),
      mine.length ? el('div', { class: 'nw-diffs' },
        el('div', { class: 'eyebrow', text: `${mine.length} difference${mine.length === 1 ? '' : 's'} to look at` }),
        el('ul', { class: 'nw-difflist' }, mine.map(differenceBlock))) : null);
  }

  function row(item) {
    const k = noteKey(item);
    const n = (diffs.get(k) || []).length;
    const isOpen = open.has(k);
    const head = el('button', { class: 'nw-head', type: 'button', 'aria-expanded': String(isOpen) },
      el('span', { class: 'nw-date nowrap' }, el('span', { text: ctx.fmt.date(item.date) || 'undated' }), el('span', { class: 'nw-date-k small muted', text: 'note date' })),
      el('span', { class: 'nw-main' }, el('span', { class: 'nw-title', text: plain(item.title) }), !item.snippet ? null : el('span', { class: 'nw-snip muted small', text: plain(item.snippet), hidden: isOpen ? '' : null })),
      el('span', { class: 'nw-author small muted', 'data-k': k, text: authors.get(k) || '' }),
      n ? el('span', { class: 'nw-pill', title: 'A document in the file differs from this note. Open the note to compare.', text: `a document differs${n > 1 ? ` · ${n}` : ''}` }) : el('span', { class: 'nw-pill-gap' }));
    const li = el('li', { class: `nw-row${n ? ' marked' : ''}${isOpen ? ' open' : ''}`, 'data-k': k, 'data-ai-unit': '', 'data-ai-kind': 'note', 'data-ai-title': plain(item.title), 'data-record-id': item.id, 'data-ai-ref': JSON.stringify(item.source) }, head, isOpen ? body(item) : null);
    head.addEventListener('click', () => {
      // toggled in place: the button keeps focus and its aria-expanded is kept current
      const now = !open.has(k);
      if (now) open.add(k); else open.delete(k);
      head.setAttribute('aria-expanded', String(now));
      li.classList.toggle('open', now);
      li.querySelector('.nw-body')?.remove();
      if (now) li.append(body(item));
      head.querySelector('.nw-snip')?.toggleAttribute('hidden', now);
    });
    return li;
  }

  function paint() {
    paintSeg();
    const needle = q.trim().toLowerCase();
    const shown = items.filter((i) => (filter === 'all' || diffs.has(noteKey(i)))
      && (!needle || `${plain(i.title)} ${plain(i.snippet)} ${authors.get(noteKey(i)) || ''}`.toLowerCase().includes(needle)));
    list.replaceChildren(...shown.map(row));
    if (needle) list.querySelectorAll('.nw-title, .nw-snip').forEach((n) => markMatches(n, needle));
    count.textContent = shown.length === items.length ? `${items.length} note${items.length === 1 ? '' : 's'}` : `${shown.length} of ${items.length}`;
    note.replaceChildren(shown.length ? '' : empty(filter === 'differs' && !needle
      ? (c?.conflicts?.length ? 'No note has a document that differs from it.' : 'No differences have been worked out yet. They appear here once the documents have been read.')
      : 'Nothing matches that filter.'));
  }

  let timer;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value; paint(); }, 40); });

  try {
    const r = await loadAll(ctx);
    if (!r.available) { note.replaceChildren(empty(r.note || 'Notes are not available for this matter.')); return null; }
    items = r.items;
  } catch (err) {
    note.replaceChildren(el('p', { class: 'error', text: `Could not load the notes: ${err.message}` }));
    return null;
  }
  if (params.id) open.add(String(params.id).replace(/^note:/, ''));
  paint();
  if (params.id) list.querySelector(`[data-k="${CSS.escape(String(params.id).replace(/^note:/, ''))}"]`)?.scrollIntoView({ block: 'center' });

  // Authors fill in quietly, four reads at a time, into a reserved column: nothing moves.
  (async () => {
    const queue = items.filter((i) => !authors.has(noteKey(i)));
    const worker = async () => {
      while (queue.length && !destroyed) {
        const item = queue.shift();
        try { await detailOf(item); } catch { continue; }
        const cell = list.querySelector(`.nw-author[data-k="${CSS.escape(noteKey(item))}"]`);
        if (cell) cell.textContent = authors.get(noteKey(item)) || '';
      }
    };
    await Promise.all([worker(), worker(), worker(), worker()]);
  })();

  return {
    destroy: () => { destroyed = true; clearTimeout(timer); },
    update: (changed, next) => {
      if (!changed.has('conflicts') && !changed.has('claims')) return;
      c = next; diffs = differencesByNote(c); paint();
    },
  };
}
