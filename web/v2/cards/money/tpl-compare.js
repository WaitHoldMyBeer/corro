// Template: two figures side by side with their sources. No difference is computed here.
import { wrap, fig, tag, headerFacts, usd } from './_lib.js';

function options(c) {
  const facts = headerFacts(c).map((o) => ({ value: `fact:${o.value}`, label: o.label }));
  const nodes = (c.nodes || []).filter((n) => n.amount != null).map((n) => ({ value: `node:${n.id}`, label: n.label }));
  return [...facts, ...nodes];
}

function resolve(c, ctx, key) {
  const [kind, id] = String(key || '').split(/:(.*)/s);
  if (kind === 'fact' && c.brief?.[id]) {
    const f = c.brief[id];
    return { label: f.label || id, text: f.display, status: f.status, refs: f.sources };
  }
  const n = kind === 'node' ? (c.nodes || []).find((x) => x.id === id) : null;
  return n ? { label: n.label, text: n.amount != null ? usd(n.amount) : null, status: n.status, refs: n.sources } : null;
}

function side(ctx, r) {
  if (!r) return fig(ctx, 'Not chosen', null, { unknown: 'Nothing to compare' });
  return fig(ctx, r.label, null, {
    text: r.text ?? undefined, unknown: 'Not in the record',
    sub: r.status && r.status !== 'unknown' ? ctx.el('span', { class: 'mc-sub' }, tag(ctx, r.status)) : null,
    refs: r.refs,
  });
}

export default {
  id: 'compare',
  title: 'Comparison',
  group: 'Money',
  size: 'm',
  template: true,
  settings: [{ key: 'a', label: 'Left', options }, { key: 'b', label: 'Right', options }],
  depends: ['brief', 'nodes'],
  empty(c) {
    return options(c).length >= 2 ? null : 'There are not two figures in the record to compare yet.';
  },
  summary(c, ctx, s) {
    const opts = options(c);
    const a = s?.a || opts[0]?.value, b = s?.b || opts[1]?.value;
    return wrap(ctx, ctx.el('div', { class: 'mc-figs' }, side(ctx, resolve(c, ctx, a)), side(ctx, resolve(c, ctx, b))));
  },
};
