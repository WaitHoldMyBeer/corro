// Review queue: how many of this cycle's five are decided, when the next five are due, and the next one to look at.
// Decisions are made in the Review tab; the card only reports.
import { loadCss } from '../tabs/_css.js';
import { loadQueue, restsLine, dots, KIND_PILL, pillWord, readable } from '../tabs/review.js';
loadCss('review.css');

function face(host, q, ctx) {
  const { el } = ctx;
  if (!q.total) { host.replaceChildren(el('p', { class: 'v2-empty', text: 'Nothing in the file qualifies for review today.' })); return; }
  const next = q.items.find((i) => !i.decision);
  host.replaceChildren(
    el('div', { class: 'rq-fig' }, el('strong', { text: `${q.decided} of ${q.total}` }), el('span', { class: 'muted small', text: 'decided' })),
    dots(q, ctx),
    next
      ? el('p', { class: 'rq-next', title: next.older?.text || '' }, ctx.pill(pillWord(next), KIND_PILL[next.tone] || ''), ' ', next.older?.text || '')
      : el('p', { class: 'muted small', text: 'All five decided.' }),
    el('p', { class: 'muted small', text: `Next five due ${ctx.fmt.date(q.due_at) || ''}.` }),
    el('button', { class: 'btn small', type: 'button', text: next ? 'Review' : 'Open review', onclick: () => ctx.openTab('review') }));
}

function full(host, q, ctx) {
  const { el } = ctx;
  host.replaceChildren(
    el('p', { class: 'muted small', text: `${q.decided} of ${q.total} decided. Next five due ${ctx.fmt.date(q.due_at) || ''}.` }),
    el('ol', { class: 'rq-card-list' }, q.items.map((i) => el('li', { 'data-ai-unit': '', 'data-ai-kind': 'claim', 'data-ai-title': i.older?.text || '' },
      el('div', {}, ctx.pill(i.decision ? { keep: 'Kept', discard: 'Retired', comment: 'Commented' }[i.decision.decision] : pillWord(i), i.decision ? '' : KIND_PILL[i.tone] || ''), ' ',
        el('span', { class: 'muted small', text: i.kind_label || '' })),
      el('span', { class: 'v2-li-main', text: i.older?.text || '' }),
      el('span', { class: 'muted small', text: i.reason || '' }),
      el('span', { class: 'muted small' }, `${restsLine(i, ctx)}. `, i.older?.source ? ctx.chip(readable(i.older.source)) : null)))),
    el('button', { class: 'btn primary', type: 'button', text: 'Decide in Review', onclick: () => ctx.openTab('review') }));
}

const mountInto = (render) => (c, ctx) => {
  const host = ctx.el('div', { class: 'rq-card' }, ctx.el('p', { class: 'muted small', text: 'Loading...' }));
  loadQueue(ctx, c).then((q) => render(host, q, ctx))
    .catch((err) => host.replaceChildren(ctx.el('p', { class: 'v2-empty', text: `The review queue is not available yet (${err.message}).` })));
  return host;
};

export default {
  id: 'review-queue',
  title: 'Review queue',
  group: 'Review',
  size: 's',
  depends: ['meta'],
  summary: mountInto(face),
  detail: mountInto(full),
};
