// Case at a glance: who, where it stands, what it is worth, who pays, what the firm has put in, when someone last spoke to the client.
import { wrap, factFig, tag } from './_lib.js';

const initialsOf = (name) => (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '?';

export default {
  id: 'case-glance',
  title: 'Case at a glance',
  group: 'Case',
  size: 'l',
  depends: ['brief', 'matter'],
  empty(c) {
    const b = c.brief || {};
    return b.case_value || b.coverage || b.stage || b.firm_spend || b.last_client_contact || b.client_photo ? null : 'Nothing is summarised yet. The digest has not run on this matter.';
  },
  summary(c, ctx) {
    const b = c.brief || {};
    const client = c.matter?.client;
    const photo = b.client_photo;
    const initials = ctx.el('span', { text: initialsOf(client?.name) });
    const avatar = photo
      ? ctx.el('button', { class: 'mc-photo', type: 'button', title: 'Open the photo source', onclick: () => ctx.openSource(photo.source) },
        ctx.el('img', { src: photo.image_href, alt: `Photo of ${client?.name || 'the client'}`, onerror: (e) => e.target.replaceWith(initials) }))
      : ctx.el('div', { class: 'mc-photo', title: 'No photo found in the record' }, initials);
    return wrap(ctx, ctx.el('div', { class: 'mc-glance' },
      ctx.el('div', { class: 'mc-who' }, avatar,
        ctx.el('div', {},
          ctx.el('strong', { text: client?.name || c.matter?.description || 'Matter' }),
          ctx.el('p', { class: 'mc-line', text: [c.matter?.display_number, c.matter?.practice_area, c.matter?.responsible_attorney].filter(Boolean).join('  ·  ') }))),
      ctx.el('div', { class: 'mc-figs' },
        factFig(ctx, 'Stage', b.stage, { compact: true, big: false }),
        factFig(ctx, 'Estimated value', b.case_value, { compact: true }),
        factFig(ctx, 'Coverage', b.coverage, { compact: true }),
        factFig(ctx, 'Firm spend', b.firm_spend, { compact: true }),
        factFig(ctx, 'Last contact', b.last_client_contact, { compact: true, big: false }),
        factFig(ctx, 'Limitations date', b.limitations, { compact: true, big: false }))));
  },
  detail(c, ctx) {
    const b = c.brief || {};
    const rows = [['Stage', b.stage], ['Estimated value', b.case_value], ['Coverage', b.coverage], ['Firm spend', b.firm_spend], ['Last client contact', b.last_client_contact], ['Still moving', b.alive]].filter(([, f]) => f);
    return wrap(ctx, rows.length
      ? ctx.el('div', { class: 'mc-figs' }, rows.map(([l, f]) => factFig(ctx, l, f, { big: false })))
      : ctx.el('p', { class: 'mc-empty', text: 'No further matter facts are in the record yet.' }),
      b.limitations?.detail ? ctx.el('p', { class: 'mc-note', text: b.limitations.detail }) : null);
  },
};
