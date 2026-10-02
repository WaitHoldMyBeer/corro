// Money: the value river in one glance. All figures are the server's river stages.
import { wrap, empty, fig, stageOf, stagesOf, isUnknownStage, stageRefs, openRefs, tree, bandList, riverSvg, tag, usd } from './_lib.js';

const amountOf = (c, s, key) => (isUnknownStage(c, s) ? null : s?.[key] ?? null);

// Where the estimated value goes, as widths: each stage's own figure against the value (geometry, no sums).
function strip(c, ctx) {
  const value = stageOf(c, 'value') || stageOf(c, 'economic');
  const total = value ? (value.outflow ?? value.inflow) : null;
  if (!total) return null;
  const segs = ['gate', 'lien', 'cost', 'fee', 'net'].map((k) => {
    const s = stageOf(c, k);
    const v = s && !isUnknownStage(c, s) ? (k === 'net' ? c.river.net ?? s.outflow : s.diverted) : null;
    return v ? { k, s, v } : null;
  }).filter(Boolean);
  if (!segs.length) return null;
  const word = { gate: 'held back', lien: 'liens', cost: 'costs', fee: 'fee', net: 'left' };
  return ctx.el('div', { class: 'mc-strip', role: 'img', 'aria-label': segs.map((x) => `${word[x.k]} ${usd(x.v)}`).join(', ') },
    segs.map((x) => ctx.el('span', { class: x.k, style: `width:${Math.min(100, (x.v / total) * 100)}%`, title: `${word[x.k]} ${usd(x.v)}` })));
}

export default {
  id: 'money',
  title: 'Money',
  group: 'Money',
  size: 's',
  depends: ['river', 'nodes'],
  empty(c) {
    return (c.river?.stages || []).length ? null : 'The river is drawn once the matter has a case value and coverage. Neither is in the record yet, or the digest has not run.';
  },
  summary(c, ctx) {
    const value = stageOf(c, 'value') || stageOf(c, 'economic');
    const gate = stageOf(c, 'gate');
    const net = stageOf(c, 'net');
    const fee = stageOf(c, 'fee');
    const feeUnset = !fee || isUnknownStage(c, fee) || fee.diverted == null;
    const off = stagesOf(c, 'lien', 'cost', 'fee').map((st) => {
      const known = amountOf(c, st, 'diverted');
      return { st, name: st.kind === 'lien' ? 'Liens' : st.kind === 'cost' ? 'Costs' : 'Fee', known };
    });
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' },
        fig(ctx, 'Estimated value', value ? (value.outflow ?? value.inflow) : null, { onclick: openRefs(ctx, stageRefs(c, value), 'Estimated value') }),
        fig(ctx, 'Held back', amountOf(c, gate, 'diverted'), { unknown: 'Coverage not confirmed', out: true, onclick: openRefs(ctx, stageRefs(c, gate), 'Held back') }),
        fig(ctx, feeUnset ? 'Left, before fee' : 'Left', c.river.net ?? net?.outflow ?? null, { unknown: 'Not yet known', onclick: openRefs(ctx, stageRefs(c, net), 'What is left') })),
      strip(c, ctx),
      off.length
        ? ctx.el('ul', { class: 'mc-list' }, off.map((o) => ctx.el('li', {}, ctx.el('div', { class: 'mc-row' },
          ctx.el('span', { class: 'grow', text: o.name === 'Fee' ? 'Fee comes off' : `${o.name} come off` }),
          ctx.el('span', { class: `amt${o.known == null ? ' unk' : ''}`, text: o.known != null ? `\u2212${usd(o.known)}` : o.st.kind === 'fee' ? 'not set' : 'not in the record' }),
          o.st.status && o.st.status !== 'unknown' ? tag(ctx, o.st.status) : null))))
        : ctx.el('p', { class: 'mc-line', text: 'No liens, costs or fee in the record yet.' }));
  },
  detail(c, ctx) {
    const river = c.river;
    const onOpen = (s) => {
      const refs = stageRefs(c, s);
      if (refs.length) ctx.openSources(refs, s.label); else ctx.toast(`No source is recorded for "${s.label}".`);
    };
    const t = tree(c);
    const roots = (c.nodes || []).filter((n) => !n.parent_id && t.kids.has(n.id));
    return wrap(ctx,
      river.reading ? ctx.el('p', { class: 'mc-read', text: river.reading }) : null,
      riverSvg(ctx, river, onOpen),
      ...roots.map((n) => ctx.el('div', {},
        ctx.el('div', { class: 'mc-row' }, ctx.el('strong', { text: n.label }), tag(ctx, n.status || 'unknown')),
        bandList(ctx, n, t.kids.get(n.id), t))),
      !roots.length && !river.reading ? empty(ctx, 'No component bands are in the record yet.') : null);
  },
};
