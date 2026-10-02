// Template: the components of any value that has them. Add it more than once, each bound to a different node.
import { wrap, empty, fig, tag, tree, bandList, checkTag, usd } from './_lib.js';

const parents = (c) => {
  const t = tree(c);
  return { t, list: (c.nodes || []).filter((n) => t.kids.has(n.id)) };
};
const pick = (c, settings) => {
  const { t, list } = parents(c);
  return { t, list, node: list.find((n) => n.id === settings?.node) || list[0] };
};

export default {
  id: 'breakdown',
  title: 'Breakdown',
  group: 'Money',
  size: 'm',
  template: true,
  settings: [{ key: 'node', label: 'Break down', options: (c) => parents(c).list.map((n) => ({ value: n.id, label: n.label })) }],
  depends: ['nodes', 'river'],
  empty(c) {
    return parents(c).list.length ? null : 'No value in the record has components yet.';
  },
  summary(c, ctx, settings) {
    const { t, node } = pick(c, settings);
    const kids = t.kids.get(node.id);
    const top = [...kids].sort((a, b) => (b.amount ?? -1) - (a.amount ?? -1)).slice(0, 3);
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' }, fig(ctx, node.label, node.amount, {
        sub: ctx.el('span', { class: 'mc-sub' }, checkTag(ctx, t.checks.get(node.id)) || tag(ctx, node.status || 'unknown')),
        refs: node.sources,
      })),
      ctx.el('ul', { class: 'mc-list' }, top.map((n) => ctx.el('li', {}, ctx.el('div', { class: 'mc-row' },
        ctx.el('span', { class: 'grow', text: n.label }),
        ctx.el('span', { class: `amt${n.amount == null ? ' unk' : ''}`, text: n.amount != null ? usd(n.amount) : 'not in the record' }))))),
      kids.length > top.length ? ctx.el('p', { class: 'mc-line', text: `+ ${kids.length - top.length} more in the detail` }) : null);
  },
  detail(c, ctx, settings) {
    const { t, node } = pick(c, settings);
    if (!node) return wrap(ctx, empty(ctx, 'No value in the record has components yet.'));
    return wrap(ctx, bandList(ctx, node, t.kids.get(node.id), t));
  },
};
