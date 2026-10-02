import { listTab, localLoader } from './_list.js';
import { el, pill } from '../../js/util.js';

const BUCKETS = [['overdue', 'Overdue', 'bad'], ['coming', 'Coming up', ''], ['waiting', 'Waiting on someone else', 'warn'], ['done', 'Done', 'ok']];
// The server decides the bucket; this only flattens the four lists in the order shown.
const flat = (c) => BUCKETS.flatMap(([k]) => c.agenda?.[k] || []);
const bucket = (a) => BUCKETS.find(([k]) => k === a.bucket) || [a.bucket, a.bucket, ''];
const day = (a) => (a.due ? String(a.due).slice(0, 10) : null);
// Every day count below is the server's days_from_today; nothing here does date arithmetic of its own
// except naming the Monday a date falls in, for grouping by week.
const within = (n) => (a) => a.days_from_today != null && a.days_from_today >= 0 && a.days_from_today <= n;
const open = (a) => a.bucket !== 'done';
const rank = (a) => (a.bucket === 'overdue' ? 0 : a.bucket === 'done' ? 2 : 1);
const party = (a) => (a.waiting_on_name ? String(a.waiting_on_contact_id ?? a.waiting_on_name) : null);
const monday = (iso) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return d.toISOString().slice(0, 10); };
const isClient = (a, c) => { const cl = c.matter?.client; return !!cl && (a.waiting_on_contact_id != null ? a.waiting_on_contact_id === cl.id : a.waiting_on_name === cl.name); };
const owner = (a, c) => (a.waiting_on_name ? (isClient(a, c) ? `The client (${a.waiting_on_name})` : a.waiting_on_name) : a.kind === 'task' ? 'The firm' : 'Calendar dates');

export default listTab({
  id: 'tasks', aiKind: 'task', title: 'Tasks', noun: 'tasks and dates',
  none: 'No tasks or calendar dates are on file.',
  load: localLoader(flat, (a) => `${a.title} ${a.assignee || ''} ${a.waiting_on_name || ''} ${bucket(a)[1]}`),
  source: (a) => a.source,
  // soonest first, with what is overdue on top and what is finished at the bottom, most recent first
  sort: { key: 'date', dir: 'asc' },
  order: (a, b) => rank(a) - rank(b) || (a.bucket === 'done' ? (day(b) || '').localeCompare(day(a) || '') : (day(a) || '9999').localeCompare(day(b) || '9999')),
  facets: [
    { id: 'state', label: 'State', options: () => [
      { key: 'overdue', label: 'Overdue', test: (a) => a.bucket === 'overdue' },
      { key: 'week', label: 'Due this week', test: (a) => open(a) && within(6)(a) },
      { key: 'coming', label: 'Coming up', test: (a) => a.bucket === 'coming' },
      { key: 'waiting', label: 'Waiting on someone else', test: (a) => a.bucket === 'waiting' },
      { key: 'done', label: 'Done', test: (a) => a.bucket === 'done' },
    ] },
    // the firm, then the client, then each party the rows themselves name
    { id: 'who', label: 'Who owes it', options: (rows, c) => {
      const names = new Map(rows.filter(party).map((a) => [party(a), a]));
      return [
        { key: 'firm', label: 'The firm', test: (a) => a.kind === 'task' && !a.waiting_on_name },
        ...[...names].map(([k, a]) => ({ key: `p${k}`, label: owner(a, c), client: isClient(a, c), test: (x) => party(x) === k }))
          .sort((x, y) => y.client - x.client || x.label.localeCompare(y.label)),
      ];
    } },
    { id: 'date', label: 'Date', rangeLabel: 'Due or starting', range: day, options: (rows, c) => [
      { key: 'today', label: 'Today', test: (a) => a.days_from_today === 0 },
      { key: 'next7', label: 'Next 7 days', test: within(7) },
      { key: 'next30', label: 'Next 30 days', test: within(30) },
      { key: 'month', label: 'This month', test: (a) => !!day(a) && !!c.agenda?.as_of && day(a).slice(0, 7) === String(c.agenda.as_of).slice(0, 7) },
      { key: 'past', label: 'Past due', test: (a) => open(a) && a.days_from_today != null && a.days_from_today < 0 },
      { key: 'none', label: 'No date', test: (a) => !day(a) },
    ] },
  ],
  groups: [
    { id: 'state', label: 'state', of: (a) => ({ key: a.bucket, label: bucket(a)[1], order: BUCKETS.findIndex(([k]) => k === a.bucket) }) },
    { id: 'who', label: 'who owes it', of: (a, c) => ({ key: party(a) || (a.kind === 'task' ? 'firm' : 'dates'), label: owner(a, c), order: `${a.waiting_on_name ? (isClient(a, c) ? 1 : 2) : a.kind === 'task' ? 0 : 3} ${a.waiting_on_name || ''}` }) },
    { id: 'week', label: 'week', of: (a) => (day(a) ? { key: monday(day(a)), label: `Week of ${monday(day(a))}`, order: monday(day(a)) } : { key: 'none', label: 'No date', order: '9999' }) },
  ],
  columns: [
    { label: 'State', key: 'state', cls: 'nowrap', sort: (a) => BUCKETS.findIndex(([k]) => k === a.bucket), cell: (a) => { const p = pill(a.bucket === 'waiting' ? 'Waiting' : bucket(a)[1], bucket(a)[2]); p.title = bucket(a)[1]; return p; } },   // the Who column names who it is waiting on
    { label: 'Title', cell: (a) => a.title + (a.is_limitations ? ' (limitations date)' : ''), sort: (a) => a.title },
    // the date always says what it is: a task is due, a calendar entry starts
    { label: 'Date', key: 'date', cls: 'nowrap', sort: day, cell: (a, ctx) => {
      if (!day(a)) return el('span', { class: 'muted', text: 'No date' });
      // Finished items are never "overdue": say how long ago the date was.
      const n = a.days_from_today;
      const rel = n == null ? '' : a.bucket === 'done' && n < 0 ? `${-n} day${n === -1 ? '' : 's'} ago` : ctx.fmt.days(n);
      return el('span', {}, `${a.kind === 'calendar_entry' ? 'Starts' : 'Due'} ${ctx.fmt.date(a.due)}`, rel ? el('span', { class: 'tab-when', text: rel }) : null);
    } },
    { label: 'Who', sort: (a) => a.waiting_on_name || a.assignee, cell: (a) => (a.waiting_on_name ? `Waiting on ${a.waiting_on_name}` : a.assignee || '') },
    { label: 'Type', cls: 'nowrap', sort: (a) => a.kind, cell: (a) => (a.kind === 'calendar_entry' ? 'Calendar' : 'Task') },
  ],
});
