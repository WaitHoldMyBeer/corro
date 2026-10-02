// Recent activity: what changed in the matter record since the lawyer last opened it.
import { ensureCss, sourceSpan } from './_lib.js';

const WORD = { new: 'new', updated: 'updated', removed: 'removed' };

function row(i, ctx) {
  return ctx.el('li', { class: 'wk-row' },
    ctx.el('div', { class: 'wk-main' }, ctx.pill(WORD[i.change] || i.change, ''), ctx.el('span', { class: 'wk-title', text: i.title })),
    ctx.el('div', { class: 'wk-meta small' },
      sourceSpan(ctx, [i.source]),
      ctx.fmt.date(i.at) ? ctx.el('span', { class: 'muted', text: `changed ${ctx.fmt.date(i.at)}` }) : null,
      i.summary ? ctx.el('span', { class: 'muted', text: i.summary }) : null));
}

const since = (c) => (c.changes.since ? `since ${ctx_date(c.changes.since)}` : null);
const ctx_date = (s) => String(s).slice(0, 10);

// Moves the "since" marker to now; the changes list then empties on the next case fetch.
function seenButton(ctx) {
  const b = ctx.el('button', { class: 'btn small', type: 'button', text: 'Mark as seen' });
  b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await ctx.api(`/api/matters/${ctx.matterId}/seen`, { method: 'POST' });
      if (ctx.reload) await ctx.reload();
    } catch (err) {
      console.error('mark as seen failed', err);
      ctx.toast('Could not mark the changes as seen. Try again.', 'error');
      b.disabled = false;
    }
  });
  return b;
}

const none = (c) => (c.changes.since ? `Nothing in the matter record has changed since ${ctx_date(c.changes.since)}.` : 'No earlier visit is on file, so there is nothing to compare against yet.');

export default {
  id: 'recent-activity',
  title: 'Recent activity',
  group: 'Activity',
  size: 'm',
  depends: ['changes'],
  empty: (c) => (c.changes.items.length ? null : none(c)),
  summary(c, ctx) {
    ensureCss();
    const items = c.changes.items;
    return ctx.el('div', { class: 'wk' },
      ctx.el('p', { class: 'wk-count' }, ctx.el('strong', { text: String(items.length) }), ` changed${since(c) ? ` ${since(c)}` : ''}`),
      ctx.el('ul', { class: 'wk-list' }, items.slice(0, 3).map((i) => row(i, ctx))),
      items.length > 3 ? ctx.el('p', { class: 'muted small wk-more', text: `${items.length - 3} more in the expanded view` }) : null,
      ctx.el('div', { class: 'wk-actions' }, seenButton(ctx)));
  },
  detail(c, ctx) {
    ensureCss();
    if (!c.changes.items.length) return ctx.el('p', { class: 'v2-empty', text: none(c) });
    const counts = Object.entries(c.changes.counts || {}).filter(([, n]) => n);
    return ctx.el('div', { class: 'wk' },
      ctx.el('div', { class: 'wk-actions' }, seenButton(ctx)),
      counts.length ? ctx.el('p', { class: 'small muted', text: counts.map(([k, n]) => `${n} ${k.replace('_', ' ')}`).join(', ') }) : null,
      ctx.el('ul', { class: 'wk-list' }, c.changes.items.slice(0, 100).map((i) => row(i, ctx))));
  },
};
