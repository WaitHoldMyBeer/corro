// Calendar tab: a month grid of calendar entries and task due dates, with the selected day's agenda beside it.
// Items come from case.agenda (every task and calendar entry of the matter, all four buckets).
// A day is as_of + days_from_today, both sent by the server, so the browser's time zone plays no part.
import { el } from '../../js/util.js';
import { ensureCss, sourceSpan, aiAttrs, diffDays, itemDay, timeOf, addDays, weekday, relative, who, dueWord } from '../cards/work/_lib.js';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const ymdOf = (y, m, d) => `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
const monthOf = (ymd) => ({ y: Number(ymd.slice(0, 4)), m: Number(ymd.slice(5, 7)) - 1 });
const shiftMonth = ({ y, m }, n) => { const t = y * 12 + m + n; return { y: Math.floor(t / 12), m: ((t % 12) + 12) % 12 }; };

function collect(all, asOf) {
  const byDay = new Map();
  const undated = [];
  for (const it of all) {
    const day = itemDay(it, asOf);
    if (!day) { undated.push(it); continue; }
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push(it);
  }
  const t = (it) => (it.kind === 'calendar_entry' ? timeOf(it)?.hhmm : null) || '99:99';
  for (const list of byDay.values()) list.sort((a, b) => t(a).localeCompare(t(b)) || String(a.title).localeCompare(String(b.title)));
  return { byDay, undated };
}

// A record-list row (every task and calendar entry in the matter) in the shape of an agenda item.
function fromRecord(r, asOf) {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(r.date || '');
  const days = m ? diffDays(asOf, m[1]) : null;
  const complete = r.kind === 'task' && /complete/i.test(r.meta?.status || '');
  return {
    id: r.id, kind: r.kind, title: r.title, detail: r.snippet, due: r.date, days_from_today: Number.isFinite(days) ? days : null,
    bucket: complete || (r.kind === 'calendar_entry' && days != null && days < 0) ? 'done' : r.kind === 'task' && days != null && days < 0 ? 'overdue' : 'coming',
    assignee: r.kind === 'task' ? r.who : null, source: r.source, is_limitations: false,
  };
}

const kindWord = (it) => (it.kind === 'calendar_entry' ? 'Event' : 'Task');
const flag = (it) => (it.bucket === 'done' && it.kind === 'task' ? 'done' : it.bucket === 'overdue' ? 'overdue' : null);
const offsetText = (o) => `UTC${o === 'Z' ? '+00:00' : o}`;

const kept = new WeakMap();

export function renderCalendar(host, c, ctx, params = {}) {
  ensureCss();
  const asOf = String(c.agenda?.as_of || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || !Number.isFinite(Date.parse(asOf))) {
    host.replaceChildren(el('p', { class: 'empty', text: 'The server did not report today\'s date, so the calendar cannot be drawn.' }));
    return;
  }
  const ag = c.agenda;
  let all = ['overdue', 'coming', 'waiting', 'done'].flatMap((k) => ag[k] || []);
  let { byDay, undated } = collect(all, asOf);
  let recordsNote = 'Loading every task and calendar entry in the matter...';
  const start = params.date && /^\d{4}-\d{2}-\d{2}$/.test(params.date) ? params.date : asOf;
  const state = kept.get(host) || { month: monthOf(start), selected: start, view: 'month' };   // a live refresh keeps the reader where they were
  kept.set(host, state);
  const root = el('div', { class: 'cal' });
  const sideTitle = (ymd) => `${weekday(ymd, 'long')} ${ymd}`;

  function eventLine(it) {
    const f = flag(it);
    const t = null;   // times show only in the day list, beside their offset
    return el('span', { class: `cal-ev ${it.kind === 'calendar_entry' ? 'event' : 'task'}${f ? ` ${f}` : ''}`, title: `${kindWord(it)}: ${it.title}${f ? ` (${f})` : ''}` },
      `${state.view === 'week' ? `${kindWord(it)} ` : ''}${f ? `(${f}) ` : ''}${it.title}`);
  }

  function agendaItem(it) {
    const t = it.kind === 'calendar_entry' ? timeOf(it) : null;
    const rel = relative(it, ctx);
    const w = who(it);
    const f = flag(it);
    return el('li', { class: 'wk-row', ...aiAttrs(it) },
      el('div', { class: 'wk-main' }, ctx.pill(kindWord(it), ''), el('span', { class: 'wk-title', text: it.title || 'Untitled' }),
        f ? ctx.pill(f, f === 'overdue' ? 'warn' : 'ok') : null, it.is_limitations ? ctx.pill('limitations', 'st-contested') : null),
      el('div', { class: 'wk-meta small' },
        t ? el('span', { text: `${dueWord(it)} ${t.hhmm}${t.offset ? ` (${offsetText(t.offset)})` : ''}` }) : null,
        rel ? el('span', { class: 'muted', text: rel }) : null, w ? el('span', { text: w }) : null),
      it.detail ? el('div', { class: 'small muted', text: it.detail.slice(0, 240) }) : null,
      el('div', { class: 'wk-actions' }, sourceSpan(ctx, [it.source])));
  }

  const weekStart = (ymd) => addDays(ymd, -new Date(Date.parse(`${ymd}T00:00:00Z`)).getUTCDay());

  function draw() {
    const weekly = state.view === 'week';
    let cells, title;
    if (weekly) {
      const ws = weekStart(state.selected);
      cells = Array.from({ length: 7 }, (_, i) => addDays(ws, i));
      title = `${cells[0]} to ${cells[6]}`;
    } else {
      const { y, m } = state.month;
      const first = ymdOf(y, m, 1);
      const gridStart = weekStart(first);   // Sunday-first
      cells = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));   // always six weeks: the grid never changes height
      title = `${MONTHS[m]} ${y}`;
    }
    const inView = (ymd) => weekly || monthOf(ymd).m === state.month.m;

    const go = (n) => {
      if (weekly) { state.selected = addDays(state.selected, 7 * n); state.month = monthOf(state.selected); }
      else state.month = shiftMonth(state.month, n);
      draw();
    };
    const setView = (v) => { state.view = v; if (v === 'week' && monthOf(state.selected).m !== state.month.m) state.selected = ymdOf(state.month.y, state.month.m, 1); draw(); };
    const head = el('div', { class: 'cal-head' },
      el('h2', { text: title, 'aria-live': 'polite' }),
      el('div', { class: 'cal-views', role: 'group', 'aria-label': 'View' },
        ['month', 'week'].map((v) => el('button', { class: 'btn small', type: 'button', 'aria-pressed': String(state.view === v), onclick: () => setView(v), text: v === 'month' ? 'Month' : 'Week' }))),
      el('button', { class: 'btn small', type: 'button', onclick: () => go(-1), text: 'Previous' }),
      el('button', { class: 'btn small', type: 'button', onclick: () => { state.month = monthOf(asOf); state.selected = asOf; draw(); }, text: 'Today' }),
      el('button', { class: 'btn small', type: 'button', onclick: () => go(1), text: 'Next' }));

    const grid = el('div', { class: `cal-grid${weekly ? ' week' : ''}`, role: 'grid', 'aria-label': title },
      ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((d) => el('div', { class: 'cal-dow', role: 'columnheader', text: d })),
      cells.map((ymd) => {
        const items = byDay.get(ymd) || [];
        const cls = ['cal-cell', inView(ymd) ? '' : 'out', ymd === asOf ? 'today' : '', ymd === state.selected ? 'sel' : ''].filter(Boolean).join(' ');
        const shown = weekly ? items : items.slice(0, 3);
        return el('button', {
          class: cls, type: 'button', 'aria-pressed': String(ymd === state.selected),
          'aria-label': `${sideTitle(ymd)}: ${items.length} item${items.length === 1 ? '' : 's'}${ymd === asOf ? ', today' : ''}`,
          onclick: () => { state.selected = ymd; if (!weekly && !inView(ymd)) state.month = monthOf(ymd); draw(); },
        }, el('span', { class: 'cal-num', text: String(Number(ymd.slice(8))) }),
        ymd === asOf ? el('span', { class: 'cal-today', text: 'Today' }) : null,
        shown.map(eventLine),
        items.length > shown.length ? el('span', { class: 'cal-more', text: `+${items.length - shown.length} more` }) : null);
      }));

    const day = byDay.get(state.selected) || [];
    const offsets = [...new Set(all.map((i) => timeOf(i)?.offset).filter(Boolean))];
    const side = el('aside', { class: 'cal-side' },
      el('section', { class: 'card' },
        el('header', { class: 'card-h' }, el('h3', { text: sideTitle(state.selected) }), el('span', { class: 'sub', text: `${day.length} item${day.length === 1 ? '' : 's'}` })),
        day.length ? el('ul', { class: 'wk-list' }, day.map(agendaItem))
          : el('p', { class: 'empty', text: all.length ? 'Nothing is dated on this day.' : 'The matter holds no calendar entries or tasks yet.' })),
      undated.length ? el('section', { class: 'card cal-undated' },
        el('header', { class: 'card-h' }, el('h3', { text: 'No date on record' }), el('span', { class: 'sub', text: String(undated.length) })),
        el('ul', { class: 'wk-list' }, undated.map(agendaItem))) : null,
      el('p', { class: 'small muted cal-note', text: `${recordsNote} Days are calendar days as the server reports them (today is ${asOf}, in the time zone the server is configured with). Times are shown as recorded${offsets.length ? `, with offset ${offsets.map(offsetText).join(', ')}` : ''}. An event is placed on its start day.` }));

    // The repaint replaces every button; put keyboard focus back on its counterpart.
    const active = root.contains(document.activeElement) ? document.activeElement : null;
    const key = active ? (active.getAttribute('aria-label') || active.textContent) : null;
    root.replaceChildren(el('div', {}, head, grid), side);
    if (key) [...root.querySelectorAll('button')].find((b) => (b.getAttribute('aria-label') || b.textContent) === key)?.focus({ preventScroll: true });
  }

  // Past and future months: every task and calendar entry the matter holds, not only what the agenda carries.
  async function loadRecords() {
    const seen = new Set(all.map((i) => i.id));
    const extra = [];
    try {
      for (const tab of ['calendar', 'tasks']) {
        for (let offset = 0, total = Infinity; offset < total; offset += 200) {
          const page = await ctx.api(`/api/matters/${ctx.matterId}/records/${tab}?offset=${offset}&limit=200`);
          if (page.available === false) break;
          total = page.total;
          const rows = page.items || [];
          if (!rows.length) break;
          for (const r of rows) if (!seen.has(r.id) && (r.kind === 'calendar_entry' || r.kind === 'task')) { seen.add(r.id); extra.push(fromRecord(r, asOf)); }
        }
      }
      all = [...all, ...extra];
      ({ byDay, undated } = collect(all, asOf));
      recordsNote = `${all.length} entries and tasks in the matter${extra.length ? `, ${extra.length} beyond the agenda` : ''}.`;
    } catch (err) {
      console.error('calendar: record list failed', err);
      recordsNote = 'The full record list could not be read; showing the agenda only.';
    }
    draw();
  }

  draw();
  host.replaceChildren(root);
  loadRecords();
}

// Same shape as the other tabs: mount(host, caseModel, ctx, params). A live refresh that changes the agenda redraws the tab.
const mount = (host, c, ctx, params) => {
  kept.delete(host);   // a fresh visit starts on today (or params.date)
  renderCalendar(host, c, ctx, params);
  return { update(keys, next) { if (!keys || (keys.has ? keys.has('agenda') : keys.includes('agenda'))) renderCalendar(host, next || ctx.caseModel, ctx, params); } };
};
export default mount;
