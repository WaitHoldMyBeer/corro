// The digest's key facts, with status as a word (never colour alone) and sources.
import { loadCss } from '../../tabs/_css.js';
loadCss('tabs.css');
loadCss('evidence.css');
import { sourceLine } from './_sources.js';


const STATUS_WORDS = { confirmed: 'Confirmed', assumed: 'Assumed', contested: 'Contested', stale: 'May be out of date', unknown: 'Not established' };

function row(f, ctx, full = true) {
  const { el } = ctx;
  const st = f.status || 'unknown';
  const stPill = ctx.pill(STATUS_WORDS[st] || st, st === 'confirmed' ? 'ok' : `st-${st}`);
  if (!full) {
    return el('li', { class: 'ev-fact face cs-claim', 'data-ai-unit': '', 'data-ai-kind': 'field', 'data-ai-title': f.label, 'data-record-id': f.id },
      el('div', { class: 'ev-line' }, el('span', { class: 'muted small ev-clip ev-grow', title: f.label, text: f.label }), stPill),
      el('div', { class: 'ev-line' }, el('span', { class: 'ev-fact-v ev-clip ev-grow', title: f.display, text: f.display }), sourceLine(ctx, f.sources, { max: 1 })));
  }
  return el('li', { class: 'ev-fact', 'data-ai-unit': '', 'data-ai-kind': 'field', 'data-ai-title': f.label, 'data-record-id': f.id },
    el('div', { class: 'ev-fact-main' },
      el('span', { class: 'muted small v2-trunc', title: f.label, text: f.label }),
      el('span', { class: `ev-fact-v${full ? '' : ' v2-trunc'}`, title: f.display, text: f.display })),
    el('div', { class: 'ev-fact-meta' },
      ctx.pill(STATUS_WORDS[st] || st, st === 'confirmed' ? 'ok' : `st-${st}`),
      full && f.derivation === 'ai' ? ctx.pill('AI reading') : null,
      full && f.conflict_ids?.length ? ctx.pill(ctx.fmt.plural(f.conflict_ids.length, 'difference') + ' for review', 'st-assumed') : null,
      full ? sourceLine(ctx, f.sources, { max: 3 }) : null));
}

export default {
  id: 'key-facts',
  title: 'Key facts',
  group: 'Case',
  size: 'm',
  depends: ['key_facts'],
  empty: (c) => ((c.key_facts || []).length ? null : 'No key facts yet. They appear once the digest has run.'),
  summary(c, ctx) {
    return ctx.el('ul', { class: 'ev-facts face' }, c.key_facts.slice(0, 5).map((f) => row(f, ctx, false)),
      c.key_facts.length > 5 ? ctx.el('li', { class: 'muted small', text: `${c.key_facts.length - 5} more, and the sources, in the expanded view.` }) : null);
  },
  detail(c, ctx) {
    const { el } = ctx;
    return el('ul', { class: 'ev-facts' }, c.key_facts.map((f) => el('li', { class: 'ev-fact-wrap' }, row(f, ctx),
      f.detail ? el('p', { class: 'small muted', text: f.detail }) : null)));
  },
};
