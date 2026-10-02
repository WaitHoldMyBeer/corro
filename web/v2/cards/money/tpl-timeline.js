// Template: dated events between two dates (either may be left empty).
import { wrap, empty } from './_lib.js';

const events = (c, s) => [...(c.timeline || [])]
  .filter((e) => e.date && (!s?.from || e.date.slice(0, 10) >= s.from) && (!s?.to || e.date.slice(0, 10) <= s.to))
  .sort((a, b) => String(a.date).localeCompare(String(b.date)));

// The dates events actually fall on, so the lawyer picks a bound that exists; the first option leaves that end open.
const dateOptions = (c, open) => [{ value: '', label: open },
  ...[...new Set((c.timeline || []).filter((e) => e.date).map((e) => e.date.slice(0, 10)))].sort().map((d) => ({ value: d, label: d }))];

function rows(ctx, list) {
  return ctx.el('ul', { class: 'mc-list' }, list.map((e) => ctx.el('li', { 'data-ai-unit': '', 'data-ai-kind': 'event', 'data-record-id': e.id }, ctx.el('div', { class: 'mc-row' },
    e.sources?.length
      ? ctx.el('button', { class: 'mc-btn', type: 'button', title: 'Open the source of this date', onclick: () => ctx.openSources(e.sources, e.label), text: ctx.fmt.date(e.date) })
      : ctx.el('span', { class: 'mc-note', text: ctx.fmt.date(e.date) }),
    ctx.el('span', { class: 'grow', text: e.label })))));
}

export default {
  id: 'timeline',
  title: 'Timeline',
  group: 'Activity',
  size: 'm',
  template: true,
  settings: [
    { key: 'from', label: 'From', options: (c) => dateOptions(c, 'Earliest') },
    { key: 'to', label: 'To', options: (c) => dateOptions(c, 'Latest') },
  ],
  depends: ['timeline'],
  empty(c) {
    return (c.timeline || []).length ? null : 'No dated events are in the record yet.';
  },
  summary(c, ctx, settings) {
    const list = events(c, settings);
    return wrap(ctx, list.length ? rows(ctx, list.slice(-5)) : empty(ctx, 'No events fall between those dates.'),
      list.length > 5 ? ctx.el('p', { class: 'mc-line', text: `Latest 5 of ${list.length}. All are in the detail.` }) : null);
  },
  detail(c, ctx, settings) {
    const list = events(c, settings);
    return wrap(ctx, list.length ? rows(ctx, list) : empty(ctx, 'No events fall between those dates.'));
  },
};
