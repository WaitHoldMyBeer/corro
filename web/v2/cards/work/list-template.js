// The template: a list card bound to one agenda bucket with a chosen limit.
// overdue.js, waiting.js and coming-up.js are instances of it; agenda-done.js is the spare.
import { ensureCss, agendaRow, more } from './_lib.js';

export function listCard({ id, title, group = 'Tasks', size = 's', bucket, limit = 2, noun, none }) {
  const items = (c) => c.agenda[bucket] || [];
  return {
    id, title, group, size,
    depends: ['agenda'],
    empty: (c) => (items(c).length ? null : none),
    summary(c, ctx) {
      ensureCss();
      const all = items(c);
      return ctx.el('div', { class: 'wk' },
        ctx.el('p', { class: 'wk-count' }, ctx.el('strong', { text: String(all.length) }), ` ${noun}`),
        ctx.el('ul', { class: 'wk-list' }, all.slice(0, limit).map((a) => agendaRow(a, ctx, c.agenda.as_of))),
        more(ctx, Math.min(limit, all.length), all.length));
    },
    detail(c, ctx) {
      ensureCss();
      const all = items(c);
      return ctx.el('ul', { class: 'wk-list' }, all.map((a) => agendaRow(a, ctx, c.agenda.as_of)));
    },
  };
}

// The card the gallery offers for the template itself.
export default listCard({ id: 'agenda-done', title: 'Done', bucket: 'done', limit: 2, noun: 'items', none: 'Nothing is marked done or past yet.' });
