// Template: the latest N notes (or logged communications). Loaded after the card is drawn; the box keeps its height.
import { wrap, empty } from './_lib.js';

const N = [3, 5, 10].map((n) => ({ value: String(n), label: `Latest ${n}` }));
const TABS = [{ value: 'notes', label: 'Notes' }, { value: 'communications', label: 'Communications' }];

function fill(ctx, host, tab, n) {
  ctx.api(`/api/matters/${ctx.matterId}/records/${tab}?offset=0&limit=${n}`).then((res) => {
    const items = res?.available === false ? [] : res?.items || [];
    host.replaceChildren(items.length
      ? ctx.el('ul', { class: 'mc-list' }, items.map((r) => ctx.el('li', { 'data-ai-unit': '', 'data-ai-kind': tab === 'notes' ? 'note' : 'communication', 'data-record-id': r.id },
        ctx.el('div', { class: 'mc-row' }, ctx.el('strong', { class: 'grow', text: r.title }), r.date ? ctx.el('span', { class: 'mc-note', text: ctx.fmt.date(r.date) }) : null, ctx.chip(r.source)),
        r.snippet ? ctx.el('p', { class: 'mc-note', text: r.snippet }) : null)))
      : empty(ctx, res?.note && res.available === false ? res.note : 'Nothing is in the record yet.'));
  }).catch((err) => host.replaceChildren(empty(ctx, `Could not load: ${err.message}`)));
}

function build(ctx, s, max) {
  const tab = TABS.some((t) => t.value === s?.tab) ? s.tab : 'notes';
  const n = Math.min(Number(s?.n) || 3, max);
  const host = ctx.el('div', { style: `min-height:${n * 52}px` }, empty(ctx, 'Loading...'));
  fill(ctx, host, tab, n);
  return wrap(ctx, host);
}

export default {
  id: 'notes-excerpt',
  title: 'Latest notes',
  group: 'Communications',
  size: 'm',
  template: true,
  settings: [{ key: 'tab', label: 'From', options: () => TABS }, { key: 'n', label: 'How many', options: () => N }],
  depends: ['matter'],
  summary: (c, ctx, s) => build(ctx, s, 5),
  detail: (c, ctx, s) => build(ctx, s, 10),
};
