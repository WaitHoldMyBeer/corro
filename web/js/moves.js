// Moves: the few things that change the case, ranked by the server. This file only displays them.
// The shape below is a guess at backend's Move; every field is read through adaptMove() so the
// contract can land without touching the layout.
import { el, empty, fmtMoney, pill, section } from './util.js';
import { chip, openSources } from './drawer.js';
import { openWriteWith } from './write.js';

export function adaptMove(m, i) {
  const owed = (m.owed_by && typeof m.owed_by === 'object') ? m.owed_by : {};
  return {
    id: m.id ?? `move-${i}`,
    title: m.title || m.what || m.text || '',
    why: m.why || m.reason || null,
    owedKind: owed.kind || m.owed_by_kind || null,
    owedName: owed.name || m.owed_by_name || (typeof m.owed_by === 'string' ? m.owed_by : null),
    waitingDays: m.waiting_days ?? m.days_waiting ?? null,
    timesAsked: m.times_asked ?? null,
    amount: m.amount ?? m.amount_at_stake ?? null,
    source: m.source || (m.sources || [])[0] || null,
    rank: m.rank != null ? Number(m.rank) : null,
    due: m.due || null,
    draft: m.draft_text ? {
      text: m.draft_text,
      audience: (m.audience_contact_id ?? m.contact_id) != null ? 'provider' : (m.audience || m.draft_audience || 'internal'),
      contactId: (m.audience_contact_id ?? m.contact_id ?? owed.contact_id) != null ? Number(m.audience_contact_id ?? m.contact_id ?? owed.contact_id) : null,
    } : null,
  };
}

function moveRow(m) {
  const meta = [
    m.owedName && !(m.why || '').includes(m.owedName) ? `${m.owedKind ? `${m.owedKind}: ` : 'owed by '}${m.owedName}` : null,
    m.waitingDays != null ? `${m.waitingDays} day${m.waitingDays === 1 ? '' : 's'} past due` : (m.due ? `due ${m.due}` : null),
    m.timesAsked ? `asked ${m.timesAsked} time${m.timesAsked === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join('  |  ');
  return el('li', { class: 'move', title: [m.why, meta].filter(Boolean).join('\n') },
    el('div', { class: 'move-main' },
      el('div', { class: 'move-title', text: m.title }),
      meta ? el('div', { class: 'small muted move-meta', text: meta }) : null),
    el('div', { class: 'move-side' },
      m.amount != null ? el('div', { class: 'move-amt', title: 'Amount this touches', text: fmtMoney(m.amount) }) : null,
      !m.draft && m.source ? el('button', { class: 'btn small', type: 'button', onclick: () => openSources([m.source], m.title) }, 'Open the source') : null,
      m.draft ? el('button', { class: 'btn small primary', type: 'button', onclick: () => openWriteWith(m.draft.text,
        m.draft.audience === 'provider' ? { kind: 'provider', contact_id: m.draft.contactId } : { kind: m.draft.audience || 'internal', contact_id: null }) }, 'Draft the message') : null));
}

export function movesCard(c) {
  if (c.moves === undefined) return el('div', { hidden: true });   // the server does not send moves yet: show nothing rather than an apology
  const all = (c.moves || []).map(adaptMove).filter((m) => m.title).sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
  const host = section('What to do next', all.length ? `top ${Math.min(3, all.length)} of ${all.length}` : null);
  if (!all.length) {
    host.append(empty('Moves are ranked once the file has enough in it to rank. The agenda below lists what the file already holds.'));
    return host;
  }
  const rest = all.slice(3);
  const list = el('ol', { class: 'moves' }, all.slice(0, 3).map(moveRow));
  host.append(list);
  if (rest.length) {
    let open = false;
    const more = el('ol', { class: 'moves', hidden: true }, rest.map(moveRow));
    const link = el('button', { class: 'linkish', type: 'button', onclick: () => { open = !open; more.hidden = !open; link.textContent = open ? 'Show fewer moves' : `All moves (${all.length})`; } }, `All moves (${all.length})`);
    host.append(link, more);
  }
  return host;
}
