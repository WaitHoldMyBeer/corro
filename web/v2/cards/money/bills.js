// Medical bills by provider: the provider bands of the case value, with the adds-up tick.
import { wrap, empty, fig, tag, tree, bandList, checkTag, usd } from './_lib.js';

// The parent whose children are owed to a contact: the provider bands.
function bands(c) {
  const t = tree(c);
  const parent = (c.nodes || []).find((n) => (t.kids.get(n.id) || []).some((k) => k.owed_by_contact_id != null));
  return { t, parent, list: parent ? t.kids.get(parent.id) : [] };
}

export default {
  id: 'medical-bills',
  title: 'Medical bills by provider',
  group: 'Money',
  size: 'm',
  depends: ['nodes', 'river'],
  empty(c) {
    return bands(c).parent ? null : 'No provider charges are in the record yet.';
  },
  summary(c, ctx) {
    const { t, parent, list } = bands(c);
    const top = [...list].filter((n) => n.amount != null).sort((a, b) => b.amount - a.amount).slice(0, 3);
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' }, fig(ctx, parent.label, parent.amount, {
        refs: parent.sources,
        sub: ctx.el('span', { class: 'mc-sub' }, checkTag(ctx, t.checks.get(parent.id)) || tag(ctx, parent.status || 'unknown')),
      })),
      ctx.el('ul', { class: 'mc-list' }, top.map((n) => ctx.el('li', {}, ctx.el('div', { class: 'mc-row' },
        ctx.el('span', { class: 'grow', text: n.label }), ctx.el('span', { class: 'amt', text: usd(n.amount) }))))),
      list.length > top.length ? ctx.el('p', { class: 'mc-line', text: `+ ${list.length - top.length} more in the detail` }) : null);
  },
  detail(c, ctx) {
    const { t, parent, list } = bands(c);
    if (!parent) return wrap(ctx, empty(ctx, 'No provider charges are in the record yet.'));
    return wrap(ctx, bandList(ctx, parent, list, t));
  },
};
