// Firm spend: the expense lines and their total.
import { wrap, fig, tag, openRefs, usd } from './_lib.js';

export default {
  id: 'firm-spend',
  title: 'Firm spend',
  group: 'Money',
  size: 's',
  depends: ['spend', 'brief'],
  empty(c) {
    return (c.spend?.lines || []).length || c.brief?.firm_spend ? null : 'No expenses are recorded on this matter.';
  },
  summary(c, ctx) {
    const f = c.brief?.firm_spend;
    const lines = c.spend?.lines || [];
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' }, f
        ? fig(ctx, 'Spent so far', null, { text: f.display, onclick: openRefs(ctx, f.sources, 'Firm spend'), sub: f.status && f.status !== 'unknown' ? ctx.el('span', { class: 'mc-sub' }, tag(ctx, f.status)) : null })
        : fig(ctx, 'Spent so far', c.spend.total)),
      ctx.el('p', { class: 'mc-line', text: lines.length ? `${ctx.fmt.plural(lines.length, 'expense line')}` : 'No expense lines.' }));
  },
  detail(c, ctx) {
    const lines = [...(c.spend?.lines || [])].sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
    return wrap(ctx,
      ctx.el('ul', { class: 'mc-list' }, lines.map((l) => ctx.el('li', { 'data-ai-unit': '', 'data-ai-kind': 'expense', 'data-record-id': l.clio_id },
        ctx.el('div', { class: 'mc-row' },
          ctx.el('span', { class: 'grow', text: l.category_name || 'Expense' }),
          ctx.el('span', { class: 'amt', text: usd(l.amount) }),
          l.date ? ctx.el('span', { class: 'mc-note', text: ctx.fmt.date(l.date) }) : null,
          ctx.chip(l.source)),
        l.note ? ctx.el('p', { class: 'mc-note', text: l.note }) : null))),
      ctx.el('div', { class: 'mc-row' }, ctx.el('strong', { class: 'grow', text: 'Total' }), ctx.el('span', { class: 'amt', text: usd(c.spend.total) })));
  },
};
