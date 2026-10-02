// What to do next: the server's ranked moves. Sorted and cut here, never re-ranked.
import { ensureCss, more, sourceSpan } from './_lib.js';

const ranked = (c) => [...(c.moves || [])].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));

function owner(m) {
  if (!m.owed_by) return null;
  return m.owed_by === 'firm' ? 'next step: the firm' : `next step: ${m.owed_by}`;
}

function row(m, ctx, full) {
  const meta = [
    owner(m),
    m.waiting_days != null ? `waiting ${ctx.fmt.plural(m.waiting_days, 'day')}` : null,
    m.times_asked ? `asked ${ctx.fmt.plural(m.times_asked, 'time')} since their last reply` : null,
    m.amount != null ? `${ctx.fmt.money(m.amount)} at stake` : null,
  ].filter(Boolean);
  const draft = () => {
    const audience = m.audience_contact_id != null ? { kind: 'provider', contact_id: m.audience_contact_id } : { kind: 'internal' };
    if (ctx.openWrite) ctx.openWrite(m.draft_text, audience);
    else ctx.openTab('write', { text: m.draft_text, audience });
  };
  return ctx.el('li', { class: 'wk-row' },
    ctx.el('div', { class: 'wk-main cs-claim' }, ctx.el('span', { class: 'wk-title', text: m.title, title: m.title })),
    full && m.reason ? ctx.el('div', { class: 'small muted', text: m.reason, title: m.reason }) : null,
    meta.length ? ctx.el('div', { class: 'wk-meta small', title: meta.join(', ') }, meta.map((t) => ctx.el('span', { text: t }))) : null,
    ctx.el('div', { class: 'wk-actions' },
      m.draft_text ? ctx.el('button', { class: 'btn small primary', type: 'button', onclick: draft, text: 'Draft the message' }) : null,
      sourceSpan(ctx, [m.source])));
}

export default {
  id: 'next-moves',
  title: 'What to do next',
  group: 'Tasks',
  size: 'm',
  depends: ['moves'],
  empty: (c) => ((c.moves || []).length ? null : 'No moves are ranked yet. They appear once the file has something to rank.'),
  summary(c, ctx) {
    ensureCss();
    const all = ranked(c);
    return ctx.el('div', { class: 'wk' },
      ctx.el('ol', { class: 'wk-list' }, all.slice(0, 2).map((m) => row(m, ctx, false))),
      more(ctx, Math.min(2, all.length), all.length));
  },
  detail(c, ctx) {
    ensureCss();
    return ctx.el('ol', { class: 'wk-list' }, ranked(c).map((m) => row(m, ctx, true)));
  },
};
