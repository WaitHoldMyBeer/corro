// A starting point for a card. Copy it into your set's folder, change the id, and list it in your manifest.
// Placeholder wording only. See README.md for the rules.
export default {
  id: 'example-count',
  title: 'Example',
  group: 'Tasks',
  size: 's',
  depends: ['agenda'],
  empty(c) {
    return (c.agenda.overdue || []).length ? null : 'Nothing overdue.';
  },
  summary(c, ctx) {
    const items = c.agenda.overdue.slice(0, 5);
    return ctx.el('ul', { class: 'v2-list' }, items.map((a) => ctx.el('li', {},
      ctx.el('span', { class: 'v2-li-main', text: a.title }),
      ctx.el('span', { class: 'muted small', text: ctx.fmt.days(a.days_from_today) ?? '' }),
      a.source ? ctx.chip(a.source) : null)));
  },
  detail(c, ctx) {
    return ctx.el('p', { text: `${ctx.fmt.plural(c.agenda.overdue.length, 'item')} overdue in all.` });
  },
};
