// Template: one contact with role, last contact and open items.
import { wrap, empty, fig } from './_lib.js';

const opts = (c) => (c.contacts || []).map((p) => ({ value: String(p.id), label: p.name }));
const pick = (c, s) => (c.contacts || []).find((p) => String(p.id) === String(s?.contact)) || (c.contacts || [])[0];

export default {
  id: 'contact',
  title: 'Contact',
  group: 'People',
  size: 's',
  template: true,
  settings: [{ key: 'contact', label: 'Contact', options: opts }],
  depends: ['contacts', 'providers', 'agenda'],
  empty(c) {
    return (c.contacts || []).length ? null : 'No contacts are in the record yet.';
  },
  summary(c, ctx, settings) {
    const p = pick(c, settings);
    const panel = (c.providers || []).find((x) => x.contact?.id === p.id);
    const waiting = (c.agenda?.waiting || []).filter((a) => a.waiting_on_contact_id === p.id);
    const asks = (panel?.asks || []).filter((a) => !a.replied_at);
    const open = waiting.length + asks.length;
    return wrap(ctx,
      ctx.el('button', { class: 'mc-fig', type: 'button', onclick: () => ctx.openSource(p.source), title: 'Open the source' },
        ctx.el('span', { class: 'mc-lbl', text: p.role_text || p.role }), ctx.el('span', { class: 'mc-num sm', text: p.name })),
      ctx.el('div', { class: 'mc-figs' },
        fig(ctx, 'Last contact', null, { text: panel?.last_contact?.display, unknown: 'Not recorded', sm: true, refs: panel?.last_contact?.sources }),
        fig(ctx, 'Open items', null, { text: String(open), sm: true })));
  },
  detail(c, ctx, settings) {
    const p = pick(c, settings);
    const waiting = (c.agenda?.waiting || []).filter((a) => a.waiting_on_contact_id === p.id);
    const panel = (c.providers || []).find((x) => x.contact?.id === p.id);
    const asks = (panel?.asks || []).filter((a) => !a.replied_at);
    return wrap(ctx,
      ctx.el('p', { class: 'mc-line', text: [p.phone, p.email].filter(Boolean).join('  ·  ') || 'No phone or email in the record.' }),
      waiting.length || asks.length
        ? ctx.el('ul', { class: 'mc-list' },
          waiting.map((a) => ctx.el('li', {}, ctx.el('div', { class: 'mc-row' }, ctx.el('span', { class: 'grow', text: a.title }), a.source ? ctx.chip(a.source) : null))),
          asks.map((a) => ctx.el('li', {}, ctx.el('div', { class: 'mc-row' }, ctx.el('span', { class: 'grow', text: a.text }), a.sources?.[0] ? ctx.chip(a.sources[0]) : null))))
        : empty(ctx, 'Nothing is open with this contact.'));
  },
};
