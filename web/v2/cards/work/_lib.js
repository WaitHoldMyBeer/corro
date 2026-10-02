// Helpers shared by the work cards and the Calendar tab. No case text lives here.

let cssLoaded = false;
export function ensureCss() {
  if (cssLoaded || typeof document === 'undefined') return;
  cssLoaded = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = new URL('../../css/calendar.css', import.meta.url).href;
  document.head.append(link);
}

// ---- dates. The server sends `as_of` (its "today") and, per item, `days_from_today`; the
// calendar day of an item is as_of + days_from_today, so no browser time zone is involved.

const DAY_MS = 86400000;
const ymdMs = (s) => Date.parse(`${String(s).slice(0, 10)}T00:00:00Z`);   // tolerate a full timestamp
export const addDays = (ymd, n) => {
  const t = ymdMs(ymd) + n * DAY_MS;
  return Number.isFinite(t) && Math.abs(t) < 8.64e15 ? new Date(t).toISOString().slice(0, 10) : null;
};
export const diffDays = (fromYmd, toYmd) => Math.round((ymdMs(toYmd) - ymdMs(fromYmd)) / DAY_MS);
export const weekday = (ymd, style = 'short') => new Date(ymdMs(ymd)).toLocaleDateString('en-US', { weekday: style, timeZone: 'UTC' });

export function itemDay(item, asOf) {
  const d = item.days_from_today != null && asOf ? addDays(asOf, item.days_from_today) : null;
  if (d) return d;
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(item.due || '');
  return m ? m[1] : null;
}

// "09:30" as written in the record, with the offset it was written with; null for a bare date.
export function timeOf(item) {
  const m = /T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(item.due || '');
  return m ? { hhmm: m[1], offset: m[2] || null } : null;
}

export const dueWord = (item) => (item.kind === 'calendar_entry' ? 'on' : 'due');

// How far the date is from today, said without judging it. Only an open task past its date is "overdue".
export function relative(item, ctx) {
  const d = item.days_from_today;
  if (d == null) return null;
  if (item.bucket === 'done' && item.kind === 'task') return 'marked complete';
  if (item.kind === 'calendar_entry' || item.bucket === 'done') {
    if (d === 0) return 'today';
    return d < 0 ? ctx.fmt.plural(-d, 'day') + ' ago' : `in ${ctx.fmt.plural(d, 'day')}`;
  }
  return ctx.fmt.days(d);
}

export function who(item) {
  if (item.waiting_on_name) return `waiting on ${item.waiting_on_name}`;
  if (item.assignee) return `assigned to ${item.assignee}`;
  return null;
}

// ---- rows

// The one claim -> source pattern: up to two chips that open the item itself, "+N" for the rest, or an honest "none".
export function sourceSpan(ctx, refs, max = 2) {
  const list = (refs || []).filter((r) => r && r.href);
  if (!list.length) return ctx.el('span', { class: 'cs-source cs-none muted small', text: 'no source in the file' });
  return ctx.el('span', { class: 'cs-source' }, list.slice(0, max).map((r) => ctx.chip(r)), list.length > max ? ctx.el('span', { class: 'muted small', text: `+${list.length - max}` }) : null);
}

export function dateButton(item, ctx, asOf) {
  const day = itemDay(item, asOf);
  if (!day) return ctx.el('span', { class: 'muted', text: 'no date on record' });
  const t = item.kind === 'calendar_entry' ? timeOf(item) : null;
  const text = `${dueWord(item)} ${day}${t ? ` ${t.hhmm}` : ''}`;
  if (!item.source) return ctx.el('span', { text });
  return ctx.el('button', { class: 'wk-date', type: 'button', title: 'Open the source of this date', onclick: (e) => { e.stopPropagation(); ctx.openSource(item.source); }, text });
}

// Hints for the drag-to-chat tool: one row is one task or event, with its source.
export const aiAttrs = (item) => ({
  'data-ai-unit': '', 'data-ai-kind': item.kind === 'calendar_entry' ? 'event' : 'task', 'data-record-id': item.id,
  'data-ai-ref': item.source ? JSON.stringify(item.source) : null,
});

export function agendaRow(item, ctx, asOf) {
  const rel = relative(item, ctx);
  const w = who(item);
  const day = itemDay(item, asOf);
  const t = item.kind === 'calendar_entry' ? timeOf(item) : null;
  return ctx.el('li', { class: 'wk-row', ...aiAttrs(item) },
    ctx.el('div', { class: 'wk-main cs-claim' },
      ctx.el('span', { class: 'wk-title', text: item.title || 'Untitled', title: item.title || null }),
      item.is_limitations ? ctx.pill('limitations', 'st-contested') : null),
    ctx.el('div', { class: 'wk-meta small cs-claim' },
      ctx.el('span', { text: day ? `${dueWord(item)} ${day}${t ? ` ${t.hhmm}` : ''}` : 'no date on record' }),
      rel ? ctx.el('span', { class: 'muted', text: rel }) : null,
      w ? ctx.el('span', { text: w }) : null),
    sourceSpan(ctx, [item.source]));
}

export const more = (ctx, shown, total, what = 'more') => (total > shown
  ? ctx.el('p', { class: 'muted small wk-more', text: `${total - shown} more in the expanded view` })
  : null);
