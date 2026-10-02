// The value river: hand-written SVG from the server-computed river stages.
// Geometry only. Every figure printed comes from the server (one field per
// number); this file scales amounts to pixels and never adds or subtracts them.
// Stage columns are generic, so a stage could later carry child bands.
import { el, svgEl, fmtMoney, empty } from './util.js';

const W = 880, H = 372, PAD_X = 84, BAR_W = 30, TOP = 84, ZONE = 190;
const MID = TOP + ZONE / 2;
const DEDUCT = new Set(['gate', 'lien', 'cost', 'fee']);   // columns whose headline is what leaves the river
const AMT_Y = TOP + ZONE + 46;

// Plain-words heading per stage kind; the server's label goes beneath it.
const HEAD = {
  value: 'What the case is worth', economic: 'What the case is worth', coverage: 'What can pay it',
  gate: 'Held back', lien: 'Liens come off', cost: 'Costs come off', fee: 'Fee comes off', net: 'What is left',
};

// at most two short lines, so neighbouring columns never run into each other
function wrapLines(s, n) {
  const words = String(s || '').split(/\s+/).filter(Boolean);
  const lines = [''];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    if (!cur || (cur + ' ' + w).length <= n) lines[lines.length - 1] = cur ? `${cur} ${w}` : w;
    else if (lines.length < 2) lines.push(w);
    else { lines[1] = `${lines[1]} ${w}`; }
  }
  return lines.map((l) => (l.length > n + 4 ? `${l.slice(0, n + 1)}...` : l));
}

const short = (s, n) => (s && s.length > n ? `${s.slice(0, n - 1)}...` : s || '');

export function renderRiver(host, river, onOpen) {
  host.replaceChildren();
  const stages = river?.stages || [];
  if (!stages.length) {
    host.append(empty('The river is drawn once the matter has a case value and coverage. Neither is in the file yet, or the digest has not run.'));
    return;
  }
  const unknown = new Set(river.unknown || []);
  const feeStage = stages.find((t) => t.kind === 'fee');
  const feeUnset = !feeStage || unknown.has(feeStage.id) || feeStage.diverted == null;
  const knownIn = stages.map((s) => s.inflow).filter((v) => v != null);
  const max = knownIn.length ? Math.max(...knownIn) : 0;
  const k = max > 0 ? ZONE / max : 0;             // pixels per dollar
  const n = stages.length;
  const x = (i) => PAD_X + i * ((W - 2 * PAD_X - BAR_W) / Math.max(n - 1, 1));

  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'river-svg', role: 'group', 'aria-label': 'Value river' });
  const ribbons = svgEl('g'), falls = svgEl('g'), bars = svgEl('g');
  svg.append(ribbons, falls, bars);

  let carry = 0;                                  // last known flow height, for stages with unknown amounts
  const geo = stages.map((s) => {
    const isUnknown = unknown.has(s.id) || s.inflow == null;
    const inH = s.inflow != null ? s.inflow * k : carry;
    const outH = s.outflow != null ? s.outflow * k : inH;
    const divH = s.diverted != null ? s.diverted * k : 0;
    carry = outH;
    return { s, isUnknown, inH, outH, divH, top: MID - Math.max(inH, 0) / 2 };
  });

  geo.forEach((g, i) => {
    const { s } = g;
    const bx = x(i);
    const x0 = bx + BAR_W;
    if (i + 1 < n) {
      const nxt = geo[i + 1];
      const x1 = x(i + 1), xm = (x0 + x1) / 2;
      const y0 = g.top, h0 = g.outH, y1 = nxt.top, h1 = nxt.inH;
      ribbons.append(svgEl('path', {
        class: `ribbon st-${s.status}${g.isUnknown || nxt.isUnknown ? ' dim' : ''}`,
        d: `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + h1} C${xm},${y1 + h1} ${xm},${y0 + h0} ${x0},${y0 + h0} Z`,
      }));
    }
    if (g.divH > 0 && !g.isUnknown) {
      const yd = g.top + g.outH, run = 56, drop = 16, th = Math.max(g.divH * 0.4, 3);
      falls.append(svgEl('path', {
        class: `fall ${s.kind}`,
        d: `M${x0},${yd} C${x0 + run / 2},${yd} ${x0 + run / 2},${yd + drop} ${x0 + run},${yd + drop} L${x0 + run},${yd + drop + th} C${x0 + run / 2},${yd + drop + th} ${x0 + run / 2},${yd + g.divH} ${x0},${yd + g.divH} Z`,
      }));
    }

    const grp = svgEl('g', { class: `stage ${s.kind} st-${s.status}${g.isUnknown ? ' is-unknown' : ''}`, tabindex: 0, role: 'button',
      'aria-label': `${HEAD[s.kind] || s.kind}: ${s.label}${s.inflow != null ? `, ${fmtMoney(s.outflow ?? s.inflow)}` : ', not in the file'}. Open source.` });
    if (g.isUnknown) {
      grp.append(svgEl('rect', { class: 'bar unknown', x: bx, y: MID - 22, width: BAR_W, height: 44 }));
      grp.append(svgEl('text', { class: 'q', x: bx + BAR_W / 2, y: MID + 6, 'text-anchor': 'middle' }, '?'));
    } else {
      grp.append(svgEl('rect', { class: 'bar flow', x: bx, y: g.top, width: BAR_W, height: Math.max(g.outH, 2) }));
      if (g.divH > 0) grp.append(svgEl('rect', { class: `bar divert ${s.kind}`, x: bx, y: g.top + g.outH, width: BAR_W, height: Math.max(g.divH, 2) }));
    }
    const cx = bx + BAR_W / 2;
    grp.append(svgEl('text', { class: 'head', x: cx, y: 18, 'text-anchor': 'middle' }, HEAD[s.kind] || s.kind));
    const shownLabel = s.kind === 'net' && feeUnset ? 'After liens and costs, before fee' : s.label;
    const label = svgEl('text', { class: 'lbl', x: cx, y: 36, 'text-anchor': 'middle' });
    wrapLines(shownLabel, 20).forEach((ln, i) => label.append(svgEl('tspan', { x: cx, dy: i ? 13 : 0 }, ln)));
    label.append(svgEl('title', {}, shownLabel));
    grp.append(label);
    if (s.kind === 'gate' && !g.isUnknown && s.diverted != null) grp.append(lock(cx, Math.max(g.top - 30, 44)));

    // Headline = what the column is about: the amount held back or taken off for a deduction
    // column, the amount flowing on otherwise. The other figure is the small line.
    const deduct = DEDUCT.has(s.kind);
    const head = deduct ? s.diverted : (s.outflow ?? s.inflow);
    const unknownHead = g.isUnknown || head == null;
    grp.append(svgEl('text', { class: `amt ${s.kind}${deduct && !unknownHead ? ' out' : ''}${unknownHead ? ' unk' : ''}`, x: cx, y: AMT_Y, 'text-anchor': 'middle' }, unknownHead ? (s.kind === 'gate' ? 'not yet confirmed' : s.kind === 'fee' ? 'fee not set' : 'not in the file') : `${deduct ? '\u2212' : ''}${fmtMoney(head)}`));
    let y = AMT_Y + 22;
    if (deduct && !unknownHead && s.outflow != null) {
      grp.append(svgEl('text', { class: 'div', x: cx, y, 'text-anchor': 'middle' }, `${fmtMoney(s.outflow)} passes on`));
      y += 20;
    }
    grp.append(svgEl('text', { class: 'stat', x: cx, y, 'text-anchor': 'middle' }, s.status));
    if (s.kind === 'net' && feeUnset) grp.append(svgEl('text', { class: 'div', x: cx, y: y + 18, 'text-anchor': 'middle' }, 'fee not applied'));
    const open = () => onOpen(s);
    grp.addEventListener('click', open);
    grp.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    bars.append(grp);
  });
  const notes = [];
  for (const s of stages.filter((t) => t.kind === 'gate')) {
    const text = s.diverted != null
      ? `Estimated case value exceeds confirmed coverage by ${fmtMoney(s.diverted)}.${s.status === 'contested' ? ' Entries and documents in the file differ on facts behind this figure: see the evidence.' : ''}`
      : `${s.label}. What this holds back is not in the file yet.`;
    notes.push(el('div', { class: 'gate-note' },
      el('span', { text }),
      s.sources?.length ? el('button', { class: 'btn', type: 'button', onclick: () => onOpen(s) }, 'See the evidence') : null));
  }
  host.append(...notes, svg);
}

function lock(cx, cy) {
  const g = svgEl('g', { class: 'lock', transform: `translate(${cx - 11},${cy - 4}) scale(1.6)` });
  g.append(svgEl('path', { d: 'M3.5 7V4.5a3.5 3.5 0 0 1 7 0V7', fill: 'none', 'stroke-width': 1.6 }));
  g.append(svgEl('rect', { x: 1, y: 7, width: 12, height: 9, rx: 2 }));
  return g;
}
