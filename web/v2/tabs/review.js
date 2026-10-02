// Review: every five days, five statements in the file that look contradicted or out of date.
// The lawyer keeps, discards or comments on each. Decisions are stored by this app only.
import { loadCss } from './_css.js';

const MOCK = ['1', 'empty'].includes(new URLSearchParams(location.search).get('mock'));
const base = (ctx) => `/api/matters/${ctx.matterId}/review-queue`;

// ---------------------------------------------------------------- the one place that knows the routes

export const loadQueue = (ctx, c) => (MOCK ? mock.queue(c || ctx.caseModel) : ctx.api(base(ctx)));
export const reviewNow = (ctx, c) => (MOCK ? mock.fresh(c || ctx.caseModel) : ctx.api(`${base(ctx)}/refresh`, { method: 'POST' }));
export const decide = (ctx, claimId, decision, note) => (MOCK ? mock.decide(claimId, decision, note)
  : ctx.api(`${base(ctx)}/decision`, { method: 'PUT', body: { claim_id: claimId, decision, note: note || null } }));
export const restore = (ctx, claimId) => (MOCK ? mock.restore(claimId) : ctx.api(`${base(ctx)}/restore`, { method: 'POST', body: { claim_id: claimId } }));
export const loadHistory = (ctx) => (MOCK ? mock.history() : ctx.api(`${base(ctx)}/history`));

export const KIND_PILL = { contradicted: 'st-contested', outdated: 'warn', check: '' };
// The server words the pill; this is only for an item stored before it did.
export const pillWord = (item) => item.pill || 'Unreviewed difference';

// A stored file name as a title: extension, folder and docket prefixes dropped, separators to spaces.
const title = (name) => (String(name || '').replace(/\.[a-z0-9]{2,4}\.?$/i, '').split('__').pop() || '').replace(/[-_]/g, ' ').trim() || name;
export const readable = (ref) => (ref && ref.kind === 'document' ? { ...ref, label: title(ref.label) } : ref);

// ---------------------------------------------------------------- mock mode: a queue made from the placeholder case, kept in memory

const MOCK_KINDS = [
  ['difference', 'A document reads differently', 'contradicted', 'Unreviewed difference', 'An entry says this; a document in the file reads differently. Nobody has reviewed the difference.'],
  ['superseded', 'A later entry on the same subject', 'outdated', 'May be out of date', 'A later entry on the same subject gives a different figure.'],
  ['entry_only', 'Figure found only in an entry', 'contradicted', 'A different figure in the file', 'This figure appears only in an entry; a page whose quote was found word for word gives a different one.'],
  ['field_figure', 'Figure read from a text field', 'check', 'A different figure in the file', 'This figure was read from a text field, which carries no date; a document in the file gives a different figure on the same subject.'],
  ['difference', 'An expert\'s opinion differs', 'check', 'Unreviewed difference', 'An entry says this; an expert\'s report in the file gives a different opinion. Nobody has reviewed the difference.'],
];
const mock = {
  cycle: 0, items: null, log: [], decided: new Map(),
  side: (k, what = 'An entry says', label = 'Entry dated') => ({ claim_id: k.id, what, text: k.text, dates: k.date ? [{ label, value: k.date }] : [], origin: k.origin, source: k.source }),
  build(c) {
    // The placeholder case holds only a few statements, so they are reused under made-up ids until there are five.
    const claims = new Map((c.claims || []).map((k) => [k.id, k]));
    const pool = [];
    for (const x of c.conflicts || []) {
      const newer = (x.document_claim_ids || []).map((i) => claims.get(i)).filter(Boolean);
      const depends = [...(x.node_ids || []).map((n) => ({ type: 'node', id: n, label: (c.nodes || []).find((v) => v.id === n)?.label || n })),
        { type: 'card', id: x.id, label: `Difference for review: ${x.topic}` }];
      (x.notes_claim_ids || []).forEach((i) => claims.get(i) && pool.push([claims.get(i), newer, depends]));
    }
    const pages = (c.claims || []).filter((k) => k.origin === 'document');
    (c.claims || []).filter((k) => k.origin !== 'document').forEach((k, n) => pool.push([k, pages.slice(n, n + 1), []]));
    const items = [];
    for (let n = 0; pool.length && items.length < 5 && n < 50; n += 1) {
      const [k, newer, depends] = pool[n % pool.length];
      const id = `placeholder:${this.cycle}:${n}`;
      if (this.decided.has(id)) continue;
      const [kind, kind_label, tone, pill, reason] = MOCK_KINDS[items.length % MOCK_KINDS.length];
      const other = (o) => (kind === 'superseded' ? this.side(o, 'A later entry says') : this.side(o, 'A document in the file says', 'Received'));
      const counts = { node: 0, move: 0, card: 0 };
      depends.forEach((d) => { counts[d.type] += 1; });
      items.push({ id, kind, kind_label, tone, pill, reason, older: { ...this.side(k), claim_id: id }, newer: newer.slice(0, 3).map(other), depends, depends_counts: counts, decision: null });
    }
    return items;
  },
  view() {
    const items = this.items.map((i) => ({ ...i, decision: this.decided.get(i.id) || null }));
    const due = new Date(this.at.getTime() + 5 * 864e5);
    const all = [...this.decided.values()];
    return { cycle: this.cycle, cycle_days: 5, generated_at: this.at.toISOString(), due_at: due.toISOString(), days_until_due: 5,
      total: items.length, decided: items.filter((i) => i.decision).length, candidates: items.length,
      counts: { retired: all.filter((d) => d.decision === 'discard').length, kept: all.filter((d) => d.decision === 'keep').length, commented: all.filter((d) => d.decision === 'comment').length },
      items };
  },
  async queue(c) { if (!this.items) return this.fresh(c); return this.view(); },
  async fresh(c) { this.items = this.build(c || {}); this.cycle += 1; this.at = new Date(); return this.view(); },
  async decide(id, decision, note) {
    const at = new Date().toISOString();
    this.decided.set(id, { decision, note: note || null, decided_at: at });
    const item = this.items.find((i) => i.id === id);
    this.log.forEach((h) => { if (h.claim_id === id) h.current = false; });
    this.log.unshift({ id: this.log.length + 1, claim_id: id, action: decision, note: note || null, at, current: true, kind_label: item?.kind_label, reason: item?.reason, older: item?.older });
    return this.view();
  },
  async restore(id) {
    const last = this.log.find((h) => h.claim_id === id);
    this.decided.delete(id);
    this.log.forEach((h) => { if (h.claim_id === id) h.current = false; });
    this.log.unshift({ ...last, id: this.log.length + 1, action: 'restore', note: null, at: new Date().toISOString(), current: false });
    return this.view();
  },
  async history() { return this.log.map((h) => ({ ...h })); },
};

// ---------------------------------------------------------------- pieces shared with the dashboard card

export function restsLine(item, ctx) {
  const n = item.depends_counts || {};
  const parts = [n.node ? ctx.fmt.plural(n.node, 'value term') : null, n.move ? ctx.fmt.plural(n.move, 'next move') : null, n.card ? ctx.fmt.plural(n.card, 'card') : null].filter(Boolean);
  return parts.length ? `Rests on it: ${parts.join(', ')}` : 'Nothing on screen rests on it';
}

export function dots(q, ctx) {
  return ctx.el('div', { class: 'rq-dots', role: 'img', 'aria-label': `${q.decided} of ${q.total} decided` },
    (q.items || []).map((i) => ctx.el('span', { class: i.decision ? 'on' : '' })));
}

const DONE_WORD = { keep: 'Kept as right', discard: 'Retired as out of date', comment: 'Kept, with your note' };
const DONE_MORE = {
  keep: 'It will not be shown again.',
  discard: 'It is no longer used by the checker, the assistant, search or the value figures. It is not deleted.',
  comment: 'The note is shown wherever this statement appears, and given to the assistant.',
};
const ACTION_WORD = { keep: 'Kept', discard: 'Retired', comment: 'Commented', restore: 'Restored' };

// ---------------------------------------------------------------- the tab

export default async function mount(host, c, ctx) {
  loadCss('review.css');
  const { el } = ctx;
  const top = el('section', { class: 'rq-top', 'aria-live': 'polite' });
  const list = el('ol', { class: 'rq-list' });
  const hist = el('section', { class: 'rq-hist' });
  host.append(
    el('div', { class: 'v2-page-h' }, el('h1', { text: 'Review' }), el('span', { class: 'muted', text: 'Five statements every five days' })),
    el('p', { class: 'muted rq-intro', text: "These are statements in the firm's entries that another source in the file disagrees with. Keep what is right, retire what is out of date, or leave a note on how to read it. Your decisions are stored here only; nothing in the matter record is changed." }),
    top, list, hist);
  list.append(el('li', { class: 'muted small', text: 'Looking through the file...' }));

  let queue = null;
  const open = new Map();   // claim id -> 'discard' | 'comment': which form is open, kept across repaints

  // One request at a time: a click while one is in flight does nothing, so a double click sends one decision.
  // The button that started it is disabled while it runs and comes back if the request fails.
  let busy = false;
  const act = async (work, failed, button, done) => {
    if (busy) return false;
    busy = true;
    if (button) button.disabled = true;
    try {
      queue = await work();
      done?.();
      paint(); paintHistory();
      return true;
    } catch (err) {
      ctx.toast(`${failed}: ${err.message}`, 'error');
      if (button) button.disabled = false;
      return false;
    } finally { busy = false; }
  };

  // Each side is named for what it is, and each date for what that date is. Neither is called older or newer.
  function side(s, fallback) {
    const dates = s.dates || (s.date ? [{ label: 'Dated', value: s.date }] : []);
    return el('div', { class: 'rq-side' },
      el('div', { class: 'rq-side-h' }, el('span', { text: s.what || fallback }),
        el('span', { text: dates.length ? dates.map((d) => `${d.label} ${ctx.fmt.date(d.value)}`).join(' \u00b7 ') : 'No date on record' })),
      el('p', { text: s.text, title: s.text }),
      s.source ? ctx.chip(readable(s.source)) : null);
  }

  function form(item, mode) {
    const id = `rq-note-${item.id.replace(/[^a-z0-9]/gi, '-')}`;
    const box = el('textarea', { id, maxlength: '2000', placeholder: mode === 'discard' ? 'For example: replaced by the later page.' : 'For example: read this as the amount before the adjustment.' });
    const save = el('button', { class: mode === 'discard' ? 'btn danger' : 'btn primary', type: 'button', text: mode === 'discard' ? 'Retire this statement' : 'Save note' });
    save.addEventListener('click', () => {
      const note = box.value.trim();
      if (mode === 'comment' && !note) { box.focus(); return; }
      // The form closes only once the decision is stored; if the request fails the note is still in the box.
      act(() => decide(ctx, item.id, mode, note), 'Not saved', save, () => open.delete(item.id));
    });
    const f = el('div', { class: 'rq-form' },
      el('label', { for: id, text: mode === 'discard' ? 'Why is it out of date? (optional, kept with the decision)' : 'How should this be read or updated?' }),
      box,
      el('div', { class: 'row' }, save, el('button', { class: 'btn quiet', type: 'button', text: 'Cancel', onclick: () => { open.delete(item.id); paint(); } })));
    queueMicrotask(() => box.focus());
    return f;
  }

  function actions(item) {
    const d = item.decision;
    if (d) {
      return el('div', { class: 'rq-act' }, el('div', { class: 'rq-done' },
        ctx.pill(DONE_WORD[d.decision], d.decision === 'discard' ? 'st-stale' : 'ok'),
        el('span', { text: `${ctx.fmt.dateTime(d.decided_at) || ''}. ${DONE_MORE[d.decision]}` }),
        el('button', { class: 'btn small quiet', type: 'button', text: d.decision === 'discard' ? 'Restore' : 'Undo', onclick: (e) => act(() => restore(ctx, item.id), 'Not restored', e.currentTarget) }),
        d.note ? el('div', { class: 'rq-note', text: d.note }) : null));
    }
    const mode = open.get(item.id);
    if (mode) return el('div', { class: 'rq-act' }, form(item, mode));
    return el('div', { class: 'rq-act' },
      el('button', { class: 'btn', type: 'button', text: 'Keep', title: 'It is right. Do not show it again.', onclick: (e) => act(() => decide(ctx, item.id, 'keep'), 'Not saved', e.currentTarget) }),
      el('button', { class: 'btn', type: 'button', text: 'Discard', title: 'It is out of date. Retire it; it can be restored.', onclick: () => { open.set(item.id, 'discard'); paint(); } }),
      el('button', { class: 'btn', type: 'button', text: 'Comment', title: 'Leave a note on how it should be read or updated.', onclick: () => { open.set(item.id, 'comment'); paint(); } }),
      el('span', { class: 'rq-hint', text: 'Nothing in the matter record is changed.' }));
  }

  function card(item, n) {
    const d = item.decision;
    const newer = item.newer || [];
    return el('li', {
      class: `rq-item t-${item.tone || 'check'}${d ? ` decided d-${d.decision}` : ''}`,
      'data-ai-unit': '', 'data-ai-kind': 'claim', 'data-ai-title': item.older?.text || '', 'data-claim-id': item.id,
    },
    el('div', { class: 'rq-head' },
      el('span', { class: 'rq-n', text: `${n + 1} of ${queue.total}` }),
      ctx.pill(pillWord(item), d ? '' : KIND_PILL[item.tone] || ''),
      el('span', { text: item.kind_label || '' })),
    el('p', { class: 'rq-says', text: item.older?.text || '' }),
    el('p', { class: 'rq-why', text: item.reason || '' }),
    el('div', { class: 'rq-sides' },
      item.older ? side(item.older, 'An entry says') : null,
      newer[0] ? side(newer[0], 'Another source in the file says') : null),
    newer.length > 1 ? el('div', { class: 'rq-more' }, `${ctx.fmt.plural(newer.length - 1, 'more source')}: `, ctx.chips(newer.slice(1).map((s) => readable(s.source)))) : null,
    el('details', { class: 'rq-rests' },
      el('summary', { text: restsLine(item, ctx) }),
      (item.depends || []).length ? el('ul', {}, item.depends.map((x) => el('li', { text: x.label }))) : null),
    actions(item));
  }

  function paint() {
    const q = queue;
    const again = el('button', { class: 'btn', type: 'button', text: 'Review now', title: 'Make a new five from what is undecided today.' });
    again.addEventListener('click', () => act(() => reviewNow(ctx, c), 'No new queue', again, () => open.clear()));
    const n = q.counts || {};
    top.replaceChildren(
      el('div', { class: 'rq-progress' }, el('strong', { text: `${q.decided} of ${q.total} decided` }), dots(q, ctx)),
      el('div', { class: 'rq-meta' },
        el('span', {}, 'Next five due ', el('b', { text: ctx.fmt.date(q.due_at) || '' }), q.days_until_due != null ? ` (${q.days_until_due === 0 ? 'today' : `in ${ctx.fmt.plural(q.days_until_due, 'day')}`})` : ''),
        el('span', { text: `This five made ${ctx.fmt.dateTime(q.generated_at) || ''} from ${ctx.fmt.plural(q.candidates ?? q.total, 'statement')} that qualified.` })),
      el('div', { class: 'rq-meta' },
        el('span', {}, el('b', { text: String(n.retired ?? 0) }), ' retired'),
        el('span', {}, el('b', { text: String(n.commented ?? 0) }), ' with a note, ', el('b', { text: String(n.kept ?? 0) }), ' kept')),
      again);
    list.replaceChildren(...(q.items.length ? q.items.map(card)
      : [el('li', { class: 'v2-empty page', text: 'Nothing in the file qualifies for review today. A new look is taken when the next five are due.' })]));
  }

  async function paintHistory() {
    let rows = [];
    try { rows = await loadHistory(ctx); } catch { /* the queue above still works */ }
    hist.replaceChildren(el('h2', { text: 'Past decisions' }),
      rows.length ? el('ul', { class: 'rq-hist-list' }, rows.map((h) => el('li', {},
        el('span', { class: 'rq-h-at', text: ctx.fmt.dateTime(h.at) || '' }),
        el('span', {}, ctx.pill(ACTION_WORD[h.action] || h.action, h.current && h.action === 'discard' ? 'st-stale' : '')),
        el('div', {}, el('div', { class: 'rq-h-text', text: h.older?.text || h.claim_id }),
          h.note ? el('div', { class: 'muted', text: `Note: ${h.note}` }) : null,
          h.older?.source ? ctx.chip(readable(h.older.source)) : null),
        h.current ? el('button', { class: 'btn small', type: 'button', text: 'Restore', title: 'Undo this decision. The statement counts again everywhere.', onclick: (e) => act(() => restore(ctx, h.claim_id), 'Not restored', e.currentTarget) }) : el('span', {}))))
        : el('p', { class: 'v2-empty', text: 'No decisions yet. Each one is kept here with its time and reason, and can be restored.' }));
  }

  try { queue = await loadQueue(ctx, c); } catch (err) {
    list.replaceChildren(el('li', { class: 'v2-empty page', text: `The review queue is not available (${err.message}).` }));
    return null;
  }
  paint(); paintHistory();
  // When the case changes underneath (a decision made in another window moves the value figures), read the
  // queue again. Never while a request is in flight or a note is being typed, and only repaint on a real change.
  return {
    update: async () => {
      if (busy || open.size) return;
      try {
        const next = await loadQueue(ctx, c);
        if (busy || open.size || JSON.stringify(next) === JSON.stringify(queue)) return;
        queue = next; paint(); paintHistory();
      } catch { /* the next change tries again */ }
    },
  };
}
