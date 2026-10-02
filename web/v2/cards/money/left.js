// What is left: the net the river arrives at, before or after the fee, and the firm's fee setting.
import { wrap, fig, stageOf, stageRefs, openRefs, isUnknownStage, usd } from './_lib.js';

const feeIsUnset = (c) => {
  const fee = stageOf(c, 'fee');
  return !fee || isUnknownStage(c, fee) || fee.diverted == null;
};

function feeControl(c, ctx) {
  const pct = ctx.el('input', { type: 'number', min: 0, max: 100, step: 0.1, value: c.river.fee_percent ?? '', 'aria-label': 'Fee percent', placeholder: 'set %' });
  const basis = ctx.el('select', { 'aria-label': 'Fee basis' },
    ctx.el('option', { value: 'gross', text: 'of the gross' }), ctx.el('option', { value: 'after_costs', text: 'of what is left after costs' }));
  ctx.api('/api/settings/fee').then((f) => { if (f?.basis) basis.value = f.basis; }).catch(() => {});
  const note = ctx.el('span', { class: 'mc-note', role: 'status', text: c.river.fee_percent == null ? 'The fee is not set, so the fee and the final figure stay unknown.' : '' });
  const save = ctx.el('button', { class: 'mc-btn', type: 'button', text: 'Save' });
  save.addEventListener('click', async () => {
    const v = pct.value === '' ? null : Number(pct.value);
    if (v != null && !(v >= 0 && v <= 100)) { note.textContent = 'Enter a percentage from 0 to 100.'; return; }
    save.disabled = true;
    try {
      await ctx.api('/api/settings/fee', { method: 'PUT', body: { percent: v, basis: basis.value } });
      if (ctx.reload) await ctx.reload(); else note.textContent = 'Saved. It shows on the next update.';
    } catch (err) { note.textContent = `Not saved: ${err.message}`; }
    save.disabled = false;
  });
  return ctx.el('div', { class: 'mc-ctl' }, ctx.el('label', { class: 'mc-note' }, 'Firm fee ', pct, ' %'), basis, save, note);
}

export default {
  id: 'what-is-left',
  title: 'What is left',
  group: 'Money',
  size: 's',
  depends: ['river'],
  empty(c) {
    return (c.river?.stages || []).length ? null : 'Nothing can be said about what is left until the case has a value and coverage.';
  },
  summary(c, ctx) {
    const net = stageOf(c, 'net');
    const unset = feeIsUnset(c);
    const fee = stageOf(c, 'fee');
    return wrap(ctx,
      ctx.el('div', { class: 'mc-figs' }, fig(ctx, unset ? 'Left, before the fee' : 'Left, after the fee', c.river.net ?? net?.outflow ?? null, { unknown: 'Not yet known', onclick: openRefs(ctx, stageRefs(c, net), 'What is left') })),
      ctx.el('p', { class: 'mc-line', text: unset ? 'Fee not set. Set it under details.' : `After liens, costs and a ${c.river.fee_percent}% fee${fee?.diverted != null ? ` (${usd(fee.diverted)})` : ''}.` }));
  },
  detail(c, ctx) {
    return wrap(ctx, feeControl(c, ctx),
      ctx.el('p', { class: 'mc-note', text: 'The fee is the firm’s own term and lives in this product, not on the matter record.' }));
  },
};
