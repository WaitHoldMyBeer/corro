// Template: one agenda bucket as tick-boxes. Display only: ticking opens the source, nothing is written back.
import { wrap, empty } from './_lib.js';

const BUCKETS = [{ value: 'overdue', label: 'Overdue' }, { value: 'coming', label: 'Coming up' }, { value: 'waiting', label: 'Waiting on someone' }, { value: 'done', label: 'Done' }];
const itemsOf = (c, s) => c.agenda?.[BUCKETS.some((b) => b.value === s?.bucket) ? s.bucket : 'overdue'] || [];

function list(ctx, items, done) {
  return ctx.el('ul', { class: 'mc-list' }, items.map((a) => ctx.el('li', { 'data-ai-unit': '', 'data-ai-kind': 'task', 'data-record-id': a.id }, ctx.el('div', { class: 'mc-row' },
    ctx.el('button', {
      class: 'mc-btn', type: 'button', role: 'checkbox', 'aria-checked': String(done), title: 'Open the source (nothing is changed)',
      onclick: () => a.source && ctx.openSource(a.source), text: done ? '☑' : '☐',
    }),
    ctx.el('span', { class: 'grow', text: a.title }),
    a.due ? ctx.el('span', { class: 'mc-note', text: ctx.fmt.date(a.due) }) : null,
    a.waiting_on_name ? ctx.el('span', { class: 'mc-note', text: `waiting on ${a.waiting_on_name}` }) : null))));
}

export default {
  id: 'checklist',
  title: 'Checklist',
  group: 'Tasks',
  size: 's',
  template: true,
  settings: [{ key: 'bucket', label: 'Show', options: () => BUCKETS }],
  depends: ['agenda'],
  summary(c, ctx, settings) {
    const items = itemsOf(c, settings);
    const done = (settings?.bucket) === 'done';
    return wrap(ctx, items.length ? list(ctx, items.slice(0, 5), done) : empty(ctx, 'Nothing here.'),
      items.length > 5 ? ctx.el('p', { class: 'mc-line', text: `+ ${items.length - 5} more in the detail` }) : null);
  },
  detail(c, ctx, settings) {
    const items = itemsOf(c, settings);
    return wrap(ctx, items.length ? list(ctx, items, settings?.bucket === 'done') : empty(ctx, 'Nothing here.'));
  },
};
