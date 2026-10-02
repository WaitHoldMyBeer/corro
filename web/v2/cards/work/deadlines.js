// Deadlines: the limitations fact as the record holds it, then the moves the server marked as deadlines.
// It states what is on file and its status; it never says a date was met or missed.
import { ensureCss, diffDays } from './_lib.js';

const SAYS = {
  confirmed: null,
  assumed: 'assumed, not stated in the record',
  contested: 'contested: sources disagree',
  stale: 'may be out of date',
  unknown: 'status unknown',
};
const basis = (d) => (d === 'computed' ? 'computed in code from the matter record' : d === 'ai' ? 'read from a document by the model' : 'read from the matter record');

const dayText = (n) => (n === 0 ? 'today' : n < 0 ? `${-n} day${n === -1 ? '' : 's'} before today` : `${n} day${n === 1 ? '' : 's'} after today`);

function limitations(c, ctx) {
  const f = c.brief?.limitations;
  if (!f) return ctx.el('p', { class: 'v2-empty', text: 'No limitations date is in the file.' });
  const day = f.date ? String(f.date).slice(0, 10) : null;
  const n = day && c.agenda?.as_of ? diffDays(c.agenda.as_of, day) : null;
  const caveat = SAYS[f.status];
  return ctx.el('div', { class: 'wk-lim' },
    ctx.el('div', { class: 'eyebrow', text: f.label || 'Limitations' }),
    ctx.el('div', { class: 'wk-big', text: f.display }),
    ctx.el('div', { class: 'small' },
      day ? `date on record: ${day}` : 'no date on record',
      n != null ? ctx.el('span', { class: 'muted', text: `  (${dayText(n)}, by the server's date ${String(c.agenda.as_of).slice(0, 10)})` }) : null),
    ctx.el('div', { class: 'small muted', text: basis(f.derivation) }),
    ctx.el('div', { class: 'wk-actions' }, caveat ? ctx.pill(caveat, f.status === 'contested' ? 'st-contested' : 'st-stale') : null, ctx.chips(f.sources)),
    f.detail ? ctx.el('p', { class: 'small', text: f.detail }) : null);
}

const hardMoves = (c) => (c.moves || []).filter((m) => m.kind === 'deadline')
  .sort((a, b) => (a.due == null) - (b.due == null) || String(a.due).localeCompare(String(b.due)));

function moveLine(m, ctx) {
  return ctx.el('li', { class: 'wk-row' },
    ctx.el('div', { class: 'wk-main' }, ctx.el('span', { class: 'wk-title', text: m.title })),
    ctx.el('div', { class: 'wk-meta small' },
      m.due && m.source ? ctx.el('button', { class: 'wk-date', type: 'button', title: 'Open the source of this date', onclick: () => ctx.openSource(m.source), text: `due ${String(m.due).slice(0, 10)}` })
        : ctx.el('span', { class: 'muted', text: m.due ? `due ${String(m.due).slice(0, 10)}` : 'no date on record' }),
      m.reason ? ctx.el('span', { class: 'muted', text: m.reason }) : null));
}

export default {
  id: 'deadlines',
  title: 'Deadlines',
  group: 'Case',
  size: 'm',
  depends: ['brief', 'moves', 'agenda'],
  summary(c, ctx) {
    ensureCss();
    const hard = hardMoves(c);
    return ctx.el('div', { class: 'wk' }, limitations(c, ctx),
      hard.length ? ctx.el('ul', { class: 'wk-list' }, hard.slice(0, 2).map((m) => moveLine(m, ctx))) : null);
  },
  detail(c, ctx) {
    ensureCss();
    const hard = hardMoves(c);
    return ctx.el('div', { class: 'wk' }, limitations(c, ctx),
      ctx.el('h4', { text: 'Dates the server marked as deadlines' }),
      hard.length ? ctx.el('ul', { class: 'wk-list' }, hard.map((m) => moveLine(m, ctx))) : ctx.el('p', { class: 'v2-empty', text: 'No ranked move is marked as a deadline.' }));
  },
};
