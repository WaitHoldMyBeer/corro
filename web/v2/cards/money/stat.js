// Template: one header fact as a single figure. Add it more than once, each bound to a different fact.
import { wrap, empty, factFig, headerFacts } from './_lib.js';

const pick = (c, settings) => {
  const opts = headerFacts(c);
  return { opts, key: opts.some((o) => o.value === settings?.fact) ? settings.fact : opts[0]?.value };
};

export default {
  id: 'stat',
  title: 'Single figure',
  group: 'Case',
  size: 's',
  template: true,
  settings: [{ key: 'fact', label: 'Show', options: (c) => headerFacts(c) }],
  depends: ['brief'],
  empty(c) {
    return headerFacts(c).length ? null : 'No header fact is in the record yet.';
  },
  summary(c, ctx, settings) {
    const { opts, key } = pick(c, settings);
    const f = c.brief[key];
    return wrap(ctx, ctx.el('div', { class: 'mc-figs' }, factFig(ctx, opts.find((o) => o.value === key)?.label || '', f)));
  },
  detail(c, ctx, settings) {
    const { key } = pick(c, settings);
    const f = c.brief[key];
    return wrap(ctx, f?.detail ? ctx.el('p', { class: 'mc-note', text: f.detail }) : empty(ctx, 'There is nothing more to say about this figure.'));
  },
};
