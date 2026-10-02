// This week: seven days from the server's "today", calendar entries and tasks by day.
import { ensureCss, agendaRow, addDays, weekday, itemDay } from './_lib.js';

function week(c) {
  const ag = c.agenda;
  const all = [...ag.overdue, ...ag.coming, ...ag.waiting, ...ag.done].filter((a) => a.days_from_today != null && a.days_from_today >= 0 && a.days_from_today < 7);
  const days = Array.from({ length: 7 }, (_, i) => ({ ymd: addDays(ag.as_of, i), items: all.filter((a) => a.days_from_today === i) }));
  return { all, days };
}

function strip(days, ctx) {
  return ctx.el('ol', { class: 'wk-strip', 'aria-label': 'Next seven days' }, days.map((d, i) => ctx.el('li', {
    class: `wk-day${d.items.length ? ' has' : ''}${i === 0 ? ' today' : ''}`,
    'aria-label': `${weekday(d.ymd, 'long')} ${d.ymd}: ${d.items.length} item${d.items.length === 1 ? '' : 's'}${i === 0 ? ', today' : ''}`,
  },
  ctx.el('span', { class: 'wk-dow', text: i === 0 ? 'Today' : weekday(d.ymd) }),
  ctx.el('span', { class: 'wk-dom', text: String(Number(d.ymd.slice(8))) }),
  ctx.el('span', { class: 'wk-n', text: d.items.length ? String(d.items.length) : '-' }))));
}

export default {
  id: 'this-week',
  title: 'This week',
  group: 'Tasks',
  size: 'm',
  depends: ['agenda'],
  empty: (c) => (c.agenda.as_of ? null : 'The server did not report today\'s date, so the week cannot be drawn.'),
  summary(c, ctx) {
    ensureCss();
    const { all, days } = week(c);
    const first = all.slice().sort((a, b) => a.days_from_today - b.days_from_today).slice(0, 3);
    return ctx.el('div', { class: 'wk' }, strip(days, ctx),
      all.length ? ctx.el('ul', { class: 'wk-list' }, first.map((a) => agendaRow(a, ctx, c.agenda.as_of)))
        : ctx.el('p', { class: 'v2-empty', text: 'Nothing is dated in the next seven days.' }),
      all.length > 3 ? ctx.el('p', { class: 'muted small wk-more', text: `${all.length - 3} more in the expanded view` }) : null,
      ctx.el('button', { class: 'linkish small', type: 'button', onclick: () => ctx.openTab('calendar', {}), text: 'Open the calendar' }));
  },
  detail(c, ctx) {
    ensureCss();
    const { days } = week(c);
    return ctx.el('div', { class: 'wk' }, strip(days, ctx), days.filter((d) => d.items.length).map((d) => ctx.el('section', {},
      ctx.el('h4', { text: `${weekday(d.ymd, 'long')} ${d.ymd}` }),
      ctx.el('ul', { class: 'wk-list' }, d.items.map((a) => agendaRow(a, ctx, c.agenda.as_of))))));
  },
};
