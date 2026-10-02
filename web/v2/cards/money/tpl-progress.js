// Template: a count against a total. Counts of items, never money.
import { wrap, empty } from './_lib.js';

const KINDS = [
  { value: 'tasks', label: 'Tasks done' },
  { value: 'asks', label: 'Requests to providers answered' },
  { value: 'pages', label: 'Document pages read' },
];
const measure = (c, kind) => {
  if (kind === 'asks') {
    const asks = (c.providers || []).flatMap((p) => p.asks || []);
    return { word: 'answered', done: asks.filter((a) => a.replied_at).length, total: asks.length };
  }
  if (kind === 'pages') {
    const docs = c.documents || [];
    return { word: 'read', done: docs.reduce((n, d) => n + (d.pages_digested || 0), 0), total: docs.reduce((n, d) => n + (d.page_count || 0), 0) };
  }
  const a = c.agenda || {};
  return { word: 'done', done: (a.done || []).length, total: ['overdue', 'coming', 'waiting', 'done'].reduce((n, k) => n + (a[k] || []).length, 0) };
};

export default {
  id: 'progress',
  title: 'Progress',
  group: 'Review',
  size: 's',
  template: true,
  settings: [{ key: 'kind', label: 'Measure', options: () => KINDS }],
  depends: ['agenda', 'providers', 'documents'],
  summary(c, ctx, settings) {
    const k = KINDS.some((x) => x.value === settings?.kind) ? settings.kind : 'tasks';
    const m = measure(c, k);
    const label = KINDS.find((x) => x.value === k).label;
    if (!m.total) return wrap(ctx, empty(ctx, `${label}: nothing to count yet.`));
    const w = Math.min(100, (m.done / m.total) * 100);   // geometry only
    return wrap(ctx,
      ctx.el('div', { class: 'mc-fig' }, ctx.el('span', { class: 'mc-lbl', text: label }), ctx.el('span', { class: 'mc-num', text: `${m.done} of ${m.total}` })),
      ctx.el('div', { class: 'mc-bar', role: 'img', 'aria-label': `${m.done} of ${m.total} ${m.word}` }, ctx.el('span', { style: `width:${w}%` })));
  },
};
