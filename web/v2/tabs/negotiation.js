// Negotiation: the server's bargaining analysis, drawn. Every figure on this page is printed as the
// endpoint returned it; the only arithmetic here is where a bar sits on its track.
import { el, pill, empty } from '../../js/util.js';
import { chips, openSources } from '../../js/drawer.js';
import { loadCss } from './_css.js';

const STATUS_CLASS = { confirmed: 'ok', set: 'ok', range: 'st-assumed', assumed: 'st-assumed', contested: 'st-contested', unknown: 'st-stale', 'no zone': 'st-contested' };
const UNIT = { percent: '%', months: ' months', usd: '' };

export default async function mount(host, caseModel, ctx) {
  loadCss('negotiation.css');
  const base = `/api/matters/${ctx.matterId}/negotiation`;
  const view = { a: null, scenario: null, traceOpen: null, drawerOpen: false, busy: false, askedEvidence: false, focusKey: null };
  const pending = {};  // assumptions changed here that the server has not answered for yet
  let seq = 0;
  const body = el('div', { class: 'ng' });
  host.append(el('div', { class: 'v2-page-h' }, el('h1', { text: 'Negotiation' })), body);

  const paint = () => {
    const a = view.a;
    if (!a) return;
    if (!a.ready || !a.scenarios?.length) { body.replaceChildren(empty(a.reason || (a.ready === false ? 'There is nothing to analyse yet.' : 'The negotiation analysis is not part of the sample data.'))); return; }
    const sc = a.scenarios.find((s) => s.id === view.scenario) || a.scenarios[0];
    body.replaceChildren(standing(a), figures(a, sc), zones(a, sc), moves(a), plan(a, sc), assumptions(a));
    // A save repaints the page: put the keyboard back on the control the reader was using.
    if (view.focusKey) body.querySelector(`[data-key="${view.focusKey}"]`)?.focus({ preventScroll: true });
  };

  // One pattern everywhere: a claim, then where it comes from. A chip opens the record or the page; a typed
  // assumption says so; a figure with nothing behind it in the file says that.
  const src = (refs, fallback = 'no source in the file') => el('span', { class: 'cs-sources' },
    refs?.length ? chips(refs) : el('span', { class: 'cs-none', text: fallback }));
  const uniq = (refs) => { const seen = new Set(); return (refs || []).filter((r) => { const k = `${r.kind}:${r.clio_id}:${r.page ?? ''}`; if (seen.has(k)) return false; seen.add(k); return true; }); };

  const standing = (a) => el('p', { class: 'ng-standing muted', text: a.standing_line });

  // ---- three figures for the selected scenario
  function figures(a, sc) {
    const byId = Object.fromEntries(a.figures.map((f) => [f.id, f]));
    const first = sc.id === a.scenarios[0].id;
    const tiles = [
      ['walk_away', sc.walk_away.display, first ? byId.walk_away?.status : sc.walk_away.exact ? 'set' : 'range'],
      ['target', sc.zone === 'none' ? 'No zone' : sc.target.display, sc.zone === 'none' ? 'no zone' : sc.target.exact ? 'set' : 'range'],
      ['ceiling', sc.ceiling_display, sc.status],
    ].map(([id, display, status]) => {
      const f = byId[id] || { label: id, formula: '', trace: [] };
      const open = view.traceOpen === id;
      const btn = el('button', { class: 'ng-fig-btn', type: 'button', 'aria-expanded': String(open), title: 'Show what this figure was computed from',
        onclick: () => { view.traceOpen = open ? null : id; paint(); } },
      el('span', { class: 'ng-fig-label', text: f.label }),
      el('span', { class: 'ng-fig-value', text: display }),
      pill(status || '', STATUS_CLASS[status] || 'st-stale'));
      const fromFile = uniq(id === 'ceiling' ? sc.sources : (byId.walk_away?.trace || []).flatMap((t) => t.sources || [])).slice(0, 1);
      const typed = id !== 'ceiling' && (f.trace || []).some((t) => t.origin === 'input' && t.display !== 'not set');
      return el('div', { class: `ng-fig${open ? ' is-open' : ''}` }, btn,
        el('div', { class: 'ng-fig-src' }, src(fromFile), typed ? el('span', { class: 'cs-none cs-own', text: 'your assumptions' }) : null));
    });
    const shown = a.figures.find((f) => f.id === view.traceOpen);
    return el('section', { class: 'ng-figs-wrap', 'aria-label': 'Walk-away, target and ceiling' },
      el('div', { class: 'ng-figs' }, tiles),
      el('p', { class: 'small muted', text: `Shown for: ${sc.label}. ${sc.basis}` }),
      shown ? trace(shown, sc) : null);
  }

  function trace(f, sc) {
    const lines = f.id === 'ceiling' && sc.id !== view.a.scenarios[0].id ? [] : f.trace;
    return el('div', { class: 'card inner ng-trace' },
      el('p', { text: f.id === 'ceiling' ? `${sc.label}. ${sc.basis}` : f.formula }),
      el('ul', { class: 'ng-trace-list' }, lines.map((t) => el('li', {},
        el('span', { class: 'ng-trace-label', text: t.label }),
        el('span', { class: `ng-trace-value${t.display === 'not set' ? ' muted' : ''}`, text: t.display }),
        t.sources?.length ? src(t.sources) : el('span', { class: 'cs-none cs-own', text: t.origin === 'input' ? (t.display === 'not set' ? 'not set' : 'your assumption') : t.origin === 'setting' ? 'firm setting' : t.origin === 'computed' ? 'computed in code from the file' : 'no source in the file' })))),
      f.id === 'ceiling' && sc.sources.length ? chips(sc.sources) : null);
  }

  // ---- one bar per coverage scenario, all on one scale
  function zones(a, sc) {
    const pct = (n) => `${Math.max(0, Math.min(100, (n / a.scale_max) * 100))}%`;
    const width = (lo, hi) => `${Math.max(0.6, Math.min(100, ((hi - lo) / a.scale_max) * 100))}%`;
    const rows = a.scenarios.map((s) => {
      const sure = s.walk_away.hi <= s.top.lo;  // the part of the zone that holds across every unset input
      const track = el('div', { class: 'ng-track', role: 'img', 'aria-label': `${s.label}: walk-away ${s.walk_away.display}, top of the zone ${s.top.display}, target ${s.target.display}, ceiling ${s.ceiling_display}` },
        el('span', { class: 'ng-band wide', style: `left:${pct(s.walk_away.lo)};width:${width(s.walk_away.lo, s.top.hi)}` }),
        sure ? el('span', { class: 'ng-band sure', style: `left:${pct(s.walk_away.hi)};width:${width(s.walk_away.hi, s.top.lo)}` }) : null,
        s.zone !== 'none' ? el('span', { class: `ng-target${s.target.exact ? '' : ' is-range'}`, style: `left:${pct(s.target.lo)};width:${width(s.target.lo, s.target.hi)}` }) : null,
        el('span', { class: 'ng-ceiling', style: `left:${pct(s.ceiling)}` }));
      const row = el('button', { class: `ng-zone${s.id === sc.id ? ' is-on' : ''}`, type: 'button', 'aria-pressed': String(s.id === sc.id),
        onclick: () => { view.scenario = s.id; paint(); } },
      el('span', { class: 'ng-zone-label' }, el('strong', { text: s.label }), pill(s.status, STATUS_CLASS[s.status] || 'st-stale')),
      track,
      el('span', { class: 'ng-zone-read small', text: s.zone === 'none' ? `No zone: walk-away ${s.walk_away.display} is above the top, ${s.top.display}` : `Walk-away ${s.walk_away.display}. Top of the zone ${s.top.display}. Target ${s.target.display}.` }));
      return el('div', { class: 'ng-zone-wrap' }, row, el('div', { class: 'ng-zone-src' }, src(uniq(s.sources).slice(0, 3))));
    });
    return el('section', { class: 'card ng-card' },
      el('h2', { text: 'Zone of agreement' }),
      el('p', { class: 'small muted', text: 'From the client\'s walk-away figure up to the most each coverage scenario can pay. Band: the zone. Dark line: the target. Tick: the ceiling.' }),
      el('div', { class: 'ng-zones' }, rows),
      el('div', { class: 'ng-axis small muted' }, el('span', { text: '$0' }), el('span', { text: ctx.fmt.money(a.scale_max) })));
  }

  // ---- what moves the number
  function moves(a) {
    const rows = a.open_facts.slice(0, 3).map((f, i) => el('li', { class: 'ng-move' },
      el('div', { class: 'ng-move-h' },
        el('span', { class: 'ng-rank', text: String(i + 1) }),
        el('strong', { text: f.label }),
        el('span', { class: 'ng-swing', text: f.swing.exact ? f.swing.display : `up to ${ctx.fmt.money(f.swing.hi)}` })),
      el('p', { class: 'small', text: f.swing_note }),
      f.higher || f.lower ? el('details', { class: 'ng-evidence' }, el('summary', { class: 'small', text: 'Evidence on each side' }),
        f.higher ? side('Supports the higher outcome', f.higher) : null,
        f.lower ? side('Supports the lower outcome', f.lower) : null) : null,
      el('div', { class: 'ng-move-src' },
        f.cards.length ? el('button', { class: 'btn small', type: 'button', onclick: () => openSources(f.cards.flatMap((c) => c.sources), f.label) }, `${ctx.fmt.plural(f.cards.length, 'review card')}: open the sources`) : null,
        src(f.sources))));
    const waiting = a.evidence_state === 'missing' && view.busy;
    return el('section', { class: 'card ng-card' },
      el('h2', { text: 'What moves the number' }),
      el('p', { class: 'small muted', text: 'Open facts in the file, ranked by how far the client\'s expected net moves between one answer and the other.' }),
      rows.length ? el('ol', { class: 'ng-moves' }, rows) : empty('Nothing in the file is open on value, coverage or liens.'),
      waiting ? el('p', { class: 'small muted', role: 'status', text: 'Writing the evidence on each side from the review cards...' }) : null);
  }

  const side = (label, s) => el('div', { class: 'ng-side' }, el('span', { class: 'ng-side-label small muted', text: label }), el('p', { text: s.text }), chips(s.sources));

  // ---- the plan
  function plan(a) {
    return el('section', { class: 'card ng-card' },
      el('h2', { text: 'Plan' }),
      el('ol', { class: 'ng-plan' }, a.plan.map((p) => el('li', {}, el('strong', { text: p.title }), el('p', { text: p.text }), p.sources?.length ? src(p.sources) : null))));
  }

  // ---- assumptions: unset until the attorney sets them
  function assumptions(a) {
    const rows = a.inputs.map((spec) => {
      const isSet = spec.value != null;
      const read = el('span', { class: `ng-in-read${isSet ? '' : ' muted'}`, text: isSet ? show(spec, spec.value) : 'not set' });
      const input = spec.unit === 'usd'
        ? el('input', { type: 'number', min: '0', step: String(spec.step), inputmode: 'numeric', class: 'ng-in-num', 'aria-label': spec.label, 'data-key': spec.key, placeholder: 'USD' })
        : el('input', { type: 'range', min: '0', max: String(spec.max), step: String(spec.step), class: `ng-in-range${isSet ? '' : ' is-unset'}`, 'aria-label': spec.label, 'data-key': spec.key });
      if (isSet) input.value = String(spec.value); else if (spec.unit !== 'usd') input.value = '0';
      input.addEventListener('input', () => { if (input.value !== '') read.textContent = show(spec, Number(input.value)); read.classList.remove('muted'); input.classList.remove('is-unset'); });
      input.addEventListener('change', () => save(spec.key, input.value === '' ? null : Number(input.value)));
      const clear = el('button', { class: 'btn small quiet', type: 'button', disabled: isSet ? null : '', onclick: () => save(spec.key, null) }, 'Clear');
      return el('div', { class: 'ng-in' },
        el('label', { class: 'ng-in-label' }, el('span', { text: spec.label }), isSet ? null : el('span', { class: 'small muted', text: spec.when_unset })),
        input, read, el('span', { class: 'cs-none cs-own', text: isSet ? 'your assumption' : 'not set' }), clear);
    });
    const unsetLines = new Set(a.inputs.filter((s) => s.value == null).map((s) => `${s.label}: not set. ${s.when_unset}`));
    const general = a.notes.filter((n) => !unsetLines.has(n));  // the per-input lines are already beside each control
    const d = el('details', { class: 'card ng-card ng-assume' },
      el('summary', {}, el('h2', { text: 'Assumptions' }), el('span', { class: 'small muted', text: `${a.inputs.filter((s) => s.value != null).length} of ${a.inputs.length} set. None has a default: an unset one is shown as a range.` })),
      el('div', { class: 'ng-ins' }, rows),
      general.length ? el('ul', { class: 'small muted ng-notes' }, general.map((n) => el('li', { text: n }))) : null);
    if (view.drawerOpen) d.setAttribute('open', '');
    d.addEventListener('toggle', () => { view.drawerOpen = d.open; });
    return d;
  }

  const show = (spec, v) => (spec.unit === 'usd' ? ctx.fmt.money(v) : `${v}${UNIT[spec.unit] || ''}`);

  // Two quick changes must not undo each other: every save carries all unanswered changes, and only the latest reply is drawn.
  async function save(key, value) {
    pending[key] = value;
    view.focusKey = key;
    const inputs = { ...Object.fromEntries(view.a.inputs.map((s) => [s.key, s.value])), ...pending };
    const mine = ++seq;
    try {
      const next = await ctx.api(view.a.inputs_href || `${base}/inputs`, { method: 'PUT', body: inputs });
      if (mine !== seq) return;
      for (const k of Object.keys(pending)) delete pending[k];
      view.a = next; paint();
    } catch (err) {
      if (mine !== seq) return;
      for (const k of Object.keys(pending)) delete pending[k];
      ctx.toast(`Not saved: ${err.message}`, 'error'); paint();
    }
  }

  async function evidence() {
    if (view.askedEvidence || view.a?.evidence_state !== 'missing') return;
    view.askedEvidence = true; view.busy = true; paint();
    try { view.a = await ctx.api(view.a.evidence_href || `${base}/evidence`, { method: 'POST' }); } catch { /* the figures do not depend on the summaries */ }
    view.busy = false; paint();
  }

  async function load() {
    try { view.a = await ctx.api(base); } catch (err) {
      if (view.a) return;  // a failed refresh keeps the page the reader is on
      body.replaceChildren(empty(err.status === 404 || !err.status ? 'The negotiation analysis is not available on this server yet.' : `The analysis could not be loaded (${err.message}).`));
      return;
    }
    paint();
    evidence();
  }

  body.append(el('p', { class: 'muted', role: 'status', text: 'Working through the file...' }));
  await load();
  // The analysis rests on the value graph and the review cards: recompute when either changes.
  return { update: (changed) => { if (['nodes', 'river', 'conflicts'].some((k) => changed.has(k))) { view.askedEvidence = false; load(); } } };
}
