// Coverage: the layers behind the case, the one the gate uses, the ones that add nothing and the server's reason.
import { wrap, empty, fig, tag, tree, bandList, stageOf, stageRefs, openRefs, isUnknownStage } from './_lib.js';

function layers(c) {
  const t = tree(c);
  const root = (c.nodes || []).find((n) => n.kind === 'coverage' && !n.parent_id && t.kids.has(n.id));
  return { t, root, list: root ? t.kids.get(root.id) : [] };
}

export default {
  id: 'coverage',
  title: 'Coverage',
  group: 'Money',
  size: 'm',
  depends: ['nodes', 'river', 'brief'],
  empty(c) {
    return layers(c).list.length ? null : 'No coverage layer is in the record yet, so nothing can be said to pay this case.';
  },
  summary(c, ctx) {
    const { list } = layers(c);
    const gate = stageOf(c, 'gate');
    const gating = list.find((n) => n.counted !== false);
    const idle = list.filter((n) => n.counted === false);
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' },
        fig(ctx, gating ? `Gates the case: ${gating.label}` : 'Gates the case', gating?.amount ?? null, { unknown: 'No layer counts', sub: gating ? ctx.el('span', { class: 'mc-sub' }, tag(ctx, c.brief?.coverage?.status || gating.status || 'unknown')) : null, onclick: openRefs(ctx, gating?.sources, gating?.label) }),
        fig(ctx, 'Value above coverage', isUnknownStage(c, gate) ? null : gate?.diverted ?? null, { unknown: 'Not yet known', out: true, onclick: openRefs(ctx, stageRefs(c, gate), 'Held back') })),
      ctx.el('p', { class: 'mc-line', text: `${ctx.fmt.plural(list.length, 'layer')} listed${idle.length ? `, ${idle.length} adding nothing` : ''}.` }));
  },
  detail(c, ctx) {
    const { t, root, list } = layers(c);
    if (!root) return wrap(ctx, empty(ctx, 'No coverage layer is in the record yet.'));
    const fact = c.brief?.coverage?.status;
    const gating = list.find((n) => n.counted !== false);
    const differs = fact && gating?.status && gating.status !== fact;
    return wrap(ctx,
      differs ? ctx.el('p', { class: 'mc-note', text: `The coverage figure is marked ${fact} on the dashboard; the layer below that gates it is marked ${gating.status}. The layer's own explanation follows.` }) : null,
      bandList(ctx, root, list, t));
  },
};
