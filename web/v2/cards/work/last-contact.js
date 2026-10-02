// Last contact: with the client, and per provider, as the file records it. An absence of a record is not a finding.
import { ensureCss } from './_lib.js';

const when = (f) => (f?.date ? String(f.date).slice(0, 10) : '');

function line(label, fact, ctx) {
  return ctx.el('li', { class: 'wk-row' },
    ctx.el('div', { class: 'wk-main' }, ctx.el('span', { class: 'wk-title', text: label })),
    fact
      ? ctx.el('div', { class: 'wk-meta small' }, ctx.el('span', { text: fact.display }), fact.detail ? ctx.el('span', { class: 'muted', text: fact.detail }) : null, ctx.chips(fact.sources))
      : ctx.el('div', { class: 'small muted', text: 'no contact recorded' }));
}

const providers = (c) => [...(c.providers || [])].sort((a, b) => when(b.last_contact).localeCompare(when(a.last_contact)));

export default {
  id: 'last-contact',
  title: 'Last contact',
  group: 'People',
  size: 'm',
  depends: ['brief', 'providers'],
  summary(c, ctx) {
    ensureCss();
    const p = providers(c).slice(0, 3);
    return ctx.el('ul', { class: 'wk-list' }, line('Client', c.brief?.last_client_contact, ctx), p.map((x) => line(x.contact.name, x.last_contact, ctx)),
      (c.providers || []).length > 3 ? ctx.el('li', { class: 'muted small wk-more', text: `${c.providers.length - 3} more providers in the expanded view` }) : null);
  },
  detail(c, ctx) {
    ensureCss();
    return ctx.el('ul', { class: 'wk-list' }, line('Client', c.brief?.last_client_contact, ctx), providers(c).map((x) => line(x.contact.name, x.last_contact, ctx)));
  },
};
