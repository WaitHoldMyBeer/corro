// One row per treating provider. Everything shown is what is on file: an empty
// field says "none on file", never a guess about why.
import { loadCss } from '../../tabs/_css.js';
loadCss('tabs.css');
loadCss('evidence.css');
import { sourceLine } from './_sources.js';


const STATE_WORDS = { unknown: 'none on file', requested: 'requested', partial: 'partly received', received: 'received' };

function lastContact(p, ctx) {
  const f = p.last_contact;
  if (!f) return 'no contact recorded';
  return f.date ? ctx.fmt.date(f.date) : f.display;
}

function toReview(p) {
  return (p.requests || []).filter((r) => (r.state || 'open') === 'open').length;
}

function row(p, ctx, full) {
  const { el } = ctx;
  if (!full) {
    const open = (p.asks || []).filter((a) => !a.replied_at).length;
    const incoming = toReview(p);
    return el('li', { class: 'ev-prov face cs-claim', 'data-ai-unit': '', 'data-ai-kind': 'provider', 'data-ai-title': p.contact.name, 'data-record-id': p.contact.id },
      el('div', { class: 'ev-line' },
        el('button', { class: 'ev-prov-name ev-clip ev-grow', type: 'button', title: 'Open the Share tab for this provider', onclick: () => ctx.openTab('share', { id: p.contact.id }), text: p.contact.name }),
        incoming ? ctx.pill(`${ctx.fmt.plural(incoming, 'request')} from them`, 'st-assumed') : null,
        sourceLine(ctx, [...(p.records?.sources || []), ...(p.bills?.sources || [])], { max: 2, none: 'No source on file' })),
      el('span', { class: 'small muted ev-clip', title: 'What is on file', text: `Records ${STATE_WORDS[p.records?.state] ?? 'none on file'} · Bills ${STATE_WORDS[p.bills?.state] ?? 'none on file'} · ${open} open ${open === 1 ? 'ask' : 'asks'} · Last contact ${lastContact(p, ctx)}` }));
  }
  const open = (p.asks || []).filter((a) => !a.replied_at).length;
  const incoming = toReview(p);
  const bits = [
    el('span', { text: `Records: ${STATE_WORDS[p.records?.state] ?? p.records?.state ?? 'none on file'}${p.records?.pages ? `, ${ctx.fmt.plural(p.records.pages, 'page')}` : ''}` }),
    el('span', { text: `Bills: ${STATE_WORDS[p.bills?.state] ?? p.bills?.state ?? 'none on file'}${p.bills?.line_count ? `, ${ctx.fmt.plural(p.bills.line_count, 'line')}` : ''}` }),
    el('span', { text: `Open asks: ${open}` }),
    el('span', { text: `Last contact: ${lastContact(p, ctx)}` }),
  ];
  return el('li', { class: 'ev-prov', 'data-ai-unit': '', 'data-ai-kind': 'provider', 'data-ai-title': p.contact.name, 'data-record-id': p.contact.id },
    el('button', { class: 'ev-prov-name', type: 'button', title: 'Open the Share tab for this provider', onclick: () => ctx.openTab('share', { id: p.contact.id }) }, p.contact.name),
    el('div', { class: `ev-prov-bits small${full ? '' : ' v2-trunc'}` }, full ? bits : bits.slice(0, 2).concat(bits.slice(3))),
    el('div', { class: 'ev-prov-flags' },
      incoming ? ctx.pill(`${ctx.fmt.plural(incoming, 'request')} from them`, 'st-assumed') : null,
      full && p.bills?.billed_total != null ? el('span', { class: 'small', text: `Billed ${ctx.fmt.money(p.bills.billed_total)}` }) : null,
      full ? sourceLine(ctx, [...(p.records?.sources || []), ...(p.bills?.sources || [])], { max: 3, none: 'No source on file' }) : null));
}

export default {
  id: 'providers',
  title: 'Providers',
  group: 'Providers',
  size: 'l',
  depends: ['providers'],
  empty: (c) => ((c.providers || []).length ? null : 'No treating providers were found on this matter.'),
  summary(c, ctx) {
    return ctx.el('ul', { class: 'ev-provs face' }, c.providers.slice(0, 4).map((p) => row(p, ctx, false)),
      c.providers.length > 4 ? ctx.el('li', { class: 'muted small', text: `${c.providers.length - 4} more in the expanded view.` }) : null);
  },
  detail(c, ctx) {
    return ctx.el('ul', { class: 'ev-provs' }, c.providers.map((p) => row(p, ctx, true)));
  },
};
