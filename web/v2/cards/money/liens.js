// Liens and costs: each with its source and status. Amounts are the river's own.
import { wrap, fig, tag, tree, bandList, stagesOf, stageRefs, openRefs, isUnknownStage, usd, sources } from './_lib.js';

const NAME = { lien: 'Liens', cost: 'Costs' };

export default {
  id: 'liens-costs',
  title: 'Liens and costs',
  group: 'Money',
  size: 'm',
  depends: ['river', 'nodes'],
  empty(c) {
    return stagesOf(c, 'lien', 'cost').length ? null : 'No lien or cost stage is in the river yet.';
  },
  summary(c, ctx) {
    const stages = stagesOf(c, 'lien', 'cost');
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' }, stages.map((s) => fig(ctx, NAME[s.kind] || s.label, isUnknownStage(c, s) ? null : s.diverted, {
        out: true, unknown: 'Not in the record',
        sub: ctx.el('span', { class: 'mc-sub' }, tag(ctx, s.status || 'unknown')),
        onclick: openRefs(ctx, stageRefs(c, s), s.label),
      }))),
      ctx.el('p', { class: 'mc-line', text: stages.map((s) => s.label).join('  ·  ') }));
  },
  detail(c, ctx) {
    const t = tree(c);
    return wrap(ctx, ...stagesOf(c, 'lien', 'cost').map((s) => {
      const node = t.byId.get(s.node_id);
      const kids = node ? t.kids.get(node.id) || [] : [];
      const refs = stageRefs(c, s);
      return ctx.el('div', {},
        ctx.el('div', { class: 'mc-row' },
          ctx.el('strong', { class: 'grow', text: s.label }),
          ctx.el('span', { class: `amt${isUnknownStage(c, s) || s.diverted == null ? ' unk' : ''}`, text: isUnknownStage(c, s) || s.diverted == null ? 'not in the record' : `−${usd(s.diverted)}` }),
          tag(ctx, s.status || 'unknown'),
          sources(ctx, refs, 99)),
        node?.basis ? ctx.el('p', { class: 'mc-note', text: node.basis }) : null,
        kids.length ? bandList(ctx, node, kids, t) : null);
    }));
  },
};
