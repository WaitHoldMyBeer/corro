// How much of the digest code could check, what it cost, and whether it may be stale.
// The server supplies every figure; nothing is computed here but a difference of counts.
import { loadCss } from '../../tabs/_css.js';
loadCss('tabs.css');


const secs = (s) => (s == null ? null : s < 90 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);
const usd = (n) => (n == null ? null : `$${n.toFixed(2)}`);

export default {
  id: 'file-quality',
  title: 'File quality',
  group: 'Review',
  size: 's',
  depends: ['meta'],
  empty: (c) => (c.meta?.digest && c.meta.digest.state !== 'not_started' ? null : 'The digest has not run, so there is nothing to measure yet.'),
  summary(c, ctx) {
    const { el } = ctx;
    const d = c.meta.digest;
    const notFound = Math.max(0, (d.quotes_total || 0) - (d.quotes_verified || 0) - (d.quotes_uncheckable || 0));
    const stale = d.reconcile_stale || (d.changed_since_reconcile || 0) > 0 || (d.items_stale || 0) > 0;
    const line = (n, label) => el('li', {}, el('span', { class: 'ev-q-n', text: String(n) }), el('span', { text: label }));
    return el('div', { class: 'ev-quality' },
      el('p', { class: 'muted small', text: `${d.quotes_total || 0} quotes taken from the file` }),
      el('ul', { class: 'ev-q' },
        line(d.quotes_verified || 0, ' found word for word in the source text'),
        line(d.quotes_uncheckable || 0, ' came from scanned pages (shown as the page; code cannot check them)'),
        line(notFound, ' not found in the source text: check the page')),
      stale
        ? el('p', { class: 'ev-stale small' }, ctx.pill('May be out of date', 'st-assumed'), ` ${ctx.fmt.plural(d.changed_since_reconcile || d.items_stale || 0, 'record')} changed since the cards were built.`)
        : el('p', { class: 'muted small', text: 'Nothing has changed since the cards were built.' }));
  },
  detail(c, ctx) {
    const { el } = ctx;
    const d = c.meta.digest;
    const rows = [
      ['Cost of the digest', usd(d.cost_usd_total), 'reading pages and records, and reconciling; excludes live checks'],
      ['Last run', d.last_run_at ? ctx.fmt.dateTime(d.last_run_at) : null, d.cost_usd_last_run != null ? `cost ${usd(d.cost_usd_last_run)}` : null],
      ['Pages read', d.pages_total ? `${d.pages_digested} of ${d.pages_total}` : null, null],
      ['Records read', d.items_total ? `${d.items_digested} of ${d.items_total}` : null, d.items_stale ? `${d.items_stale} changed since` : null],
      ['Re-check', secs(d.reconcile_estimate_seconds), d.reconcile_estimate_usd != null ? `about ${usd(d.reconcile_estimate_usd)}` : null],
      ['Synced', c.meta.synced_at ? ctx.fmt.dateTime(c.meta.synced_at) : null, null],
    ];
    return el('div', {},
      this.summary(c, ctx),
      el('dl', { class: 'ev-dl' }, rows.filter((r) => r[1]).map(([k, v, n]) => [el('dt', { text: k }), el('dd', {}, v, n ? el('span', { class: 'muted small', text: ` ${n}` }) : null)])),
      d.reconcile_href && (d.reconcile_stale || d.changed_since_reconcile)
        ? el('button', { class: 'btn', type: 'button', onclick: async (e) => {
          e.target.disabled = true;
          try { await ctx.api(d.reconcile_href, { method: 'POST' }); ctx.toast('Re-check started. Reload in a minute.', 'info'); } catch (err) { ctx.toast(`Could not start: ${err.message}`, 'error'); e.target.disabled = false; }
        } }, 'Re-check the differences')
        : null);
  },
};
