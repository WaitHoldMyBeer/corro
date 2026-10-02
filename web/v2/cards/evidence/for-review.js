// Differences for review: the top three by the server's rank. A difference to
// look at, never an error, and the card never says which side is right.
import { loadCss } from '../../tabs/_css.js';
loadCss('tabs.css');
import { sourceLine, firstRef, allRefs } from './_sources.js';


const STATES = [['unreviewed', 'Needs review'], ['confirmed', 'Confirmed'], ['dismissed', 'Dismissed']];

function reviewControl(x, ctx, onChange) {
  const { el } = ctx;
  const group = el('div', { class: 'ev-seg', role: 'group', 'aria-label': `Your review of: ${x.topic}` });
  const paint = () => group.querySelectorAll('button').forEach((b, i) => b.setAttribute('aria-pressed', String(STATES[i][0] === (x.review || 'unreviewed'))));
  STATES.forEach(([val, label]) => group.append(el('button', {
    type: 'button', 'aria-pressed': 'false', text: label,
    onclick: async () => {
      const prev = x.review || 'unreviewed';
      if (prev === val) return;
      x.review = val; paint(); onChange?.(val);
      try {
        await ctx.api(x.review_href || `/api/matters/${ctx.matterId}/conflicts/${encodeURIComponent(x.id)}/review`, { method: 'PUT', body: { review: val } });
      } catch (err) {
        x.review = prev; paint(); onChange?.(prev);
        ctx.toast(`Review not saved: ${err.message}`, 'error');
      }
    },
  })));
  paint();
  return group;
}

function difference(c, x, ctx) {
  const { el } = ctx;
  const claims = new Map((c.claims || []).map((k) => [k.id, k]));
  const side = (ids, label) => {
    const cl = (ids || []).map((id) => claims.get(id)).filter(Boolean);
    return el('button', {
      class: 'ev-side', type: 'button', disabled: !cl.length, title: cl.length ? 'Open the source' : null,
      onclick: () => ctx.openSources(cl.map((k) => k.source), x.topic),
    }, el('span', { class: 'ev-eyebrow', text: label }),
    el('span', { class: 'ev-side-text', text: cl[0]?.text || 'Nothing recorded on this side' }),
    cl[0]?.date ? el('span', { class: 'muted small', text: ctx.fmt.date(cl[0].date) }) : null);
  };
  const art = el('article', { 'data-ai-unit': '', 'data-ai-kind': 'conflict', 'data-conflict-id': x.id, 'data-ai-title': x.topic, class: `ev-diff rv-${x.review || 'unreviewed'}` },
    el('header', {}, el('h4', { text: x.topic }),
      x.kind_label ? ctx.pill(x.kind_label) : null,
      x.stale ? ctx.pill('a record changed since', 'st-assumed') : null),
    el('p', { class: 'small', text: x.summary }),
    el('div', { class: 'ev-sides' }, side(x.notes_claim_ids, 'The entries say'), side(x.document_claim_ids, 'A record in the file')),
    el('div', { class: 'ev-sides' }, sourceLine(ctx, allRefs(x.notes_claim_ids, claims), { max: 3, none: 'No source for the entry side' }), sourceLine(ctx, allRefs(x.document_claim_ids, claims), { max: 3, none: 'No source for the document side' })),
    reviewControl(x, ctx, (v) => { art.className = `ev-diff rv-${v}`; }));
  return art;
}

const claimsOf = (c) => new Map((c.claims || []).map((k) => [k.id, k]));
const byRank = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9);

export default {
  id: 'for-review',
  title: 'For review',
  group: 'Review',
  size: 'l',
  depends: ['conflicts', 'claims'],
  empty(c) {
    return (c.conflicts || []).length ? null : 'No differences between the entries and the documents have been found. If the digest has not run, none can be.';
  },
  summary(c, ctx) {
    const { el } = ctx;
    const all = [...c.conflicts].sort(byRank);
    const open = all.filter((x) => x.review !== 'dismissed');
    const top = open.slice(0, 3);
    return el('div', { class: 'ev-review' },
      el('p', { class: 'muted small', text: `${ctx.fmt.plural(open.length, 'difference')} to look at: places where the entries and a record differ, not findings that either is wrong.` }),
      el('ul', { class: 'v2-list' }, top.map((x) => el('li', { class: 'v2-row cs-claim', 'data-ai-unit': '', 'data-ai-kind': 'conflict', 'data-conflict-id': x.id, 'data-ai-title': x.topic },
        el('div', { class: 'v2-row-title' }, x.topic, ' ', x.kind_label ? ctx.pill(x.kind_label) : null),
        el('div', { class: 'v2-row-meta ev-claimline' }, el('span', { class: 'v2-clamp ev-grow', title: x.summary, text: x.summary }),
          sourceLine(ctx, [firstRef(x.notes_claim_ids, claimsOf(c)), firstRef(x.document_claim_ids, claimsOf(c))], { max: 2 }))))),
      open.length ? el('p', { class: 'muted small', text: `${open.length > 3 ? `${open.length - 3} more, and ` : ''}both sides and your review in the expanded view.` }) : null,
      !top.length ? el('p', { class: 'v2-empty', text: 'Every difference has been dismissed. They are still listed in the expanded view.' }) : null);
  },
  detail(c, ctx) {
    const { el } = ctx;
    const all = [...c.conflicts].sort(byRank);
    return el('div', { class: 'ev-review' }, all.map((x) => difference(c, x, ctx)));
  },
};
