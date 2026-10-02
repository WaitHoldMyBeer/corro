// Per-provider message thread. The shape is a guess at sharer's Message (id, direction, text, sent_at,
// receipts, and a checker result attached to incoming messages for the firm's eyes only); it is read
// through adaptMessage() so the contract can land without touching the layout.
import { el, empty, fmtDateTime, pill } from './util.js';
import { chip } from './drawer.js';
import { VERDICT } from './write.js';

export function adaptMessage(m, i) {
  const dir = ['in', 'incoming', 'provider', 'from_provider'].includes(m.direction) || m.from === 'provider' ? 'in' : 'out';
  return { id: m.id ?? `m-${i}`, dir, text: m.text || m.body || '', at: m.at || m.sent_at || m.created_at || null, requestId: m.request_id ?? null, overrides: m.overrides || (m.override ? [m.override] : []) };
}

// The checker's reading of what a provider wrote lives apart from the message (firm-only call), keyed
// "message:<id>", "request:<id>", "reply:<ask id>". A null verdict means "not assessed yet", never "clean".
// Firm only: a send that went through with a reason says so afterwards. The log is a separate firm-only route
// (never a field on a provider-visible object). One row per held sentence; rows from one send share a reason and time,
// so they are grouped.
const STATE_WORD = { dont_send: (c) => (c ? `discloses ${String(c).replace('_', ' ')}` : 'disclosed something this provider is never shown'), unchecked: () => "the checker's model had not read this wording", unavailable: () => 'the checker could not be run' };

export function groupOverrides(rows) {
  const groups = new Map();
  for (const o of rows || []) {
    const key = `${o.share_id || o.item}|${o.reason}|${o.at}`;
    if (!groups.has(key)) groups.set(key, { ...o, items: [] });
    groups.get(key).items.push(o);
  }
  return [...groups.values()];
}

export function overrideNote(groups) {
  const list = (groups || []).filter((g) => g && g.reason);
  if (!list.length) return null;
  return el('div', { class: 'override-note small' }, list.map((g) => el('div', {},
    el('strong', { text: 'Sent with a reason: ' }), g.reason,
    ...g.items.map((o) => el('div', { class: 'muted' },
      o.text ? ['Held sentence: ', el('em', { text: `"${o.text}"` })] : 'Held: the checker could not read the wording',
      ` (${(STATE_WORD[o.state] || (() => o.state))(o.category)})`)),
    g.at ? el('div', { class: 'muted', text: fmtDateTime(g.at) }) : null)));
}

const REVIEW_WORD = { unreviewed: 'Needs review', confirmed: 'Confirmed', dismissed: 'Dismissed' };

// The attorney's call on what the file says about a provider's text: the same three states as the review cards.
function reviewControl(current, onReview) {
  const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Your review' });
  const paint = (state) => [...seg.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.r === state)));
  for (const r of ['unreviewed', 'confirmed', 'dismissed']) {
    seg.append(el('button', { type: 'button', 'data-r': r, 'aria-pressed': String(r === current), text: REVIEW_WORD[r], onclick: async () => {
      const before = [...seg.children].find((b) => b.getAttribute('aria-pressed') === 'true')?.dataset.r || 'unreviewed';
      paint(r);
      try { await onReview(r); } catch { paint(before); }
    } }));
  }
  return seg;
}

export function annotationsFor(result, ctl = null) {
  if (!result) return null;
  // null verdict: only "not checked yet" when the checker says it is pending or explains; otherwise there is nothing to mark.
  const spans = (result.spans || []).filter((s) => s.verdict !== 'supported' && !(s.verdict == null && (s.partial || (!s.pending && !s.message))));
  if (!spans.length) return el('div', { class: 'small muted', text: 'Checked against the file: nothing to flag.' });
  const flagged = spans.some((s) => s.verdict === 'contradicted' || s.verdict === 'out_of_date');
  return el('div', { class: 'annots' }, spans.map(annotation), ctl && flagged ? reviewControl(ctl.review || 'unreviewed', ctl.onReview) : null);
}

function annotation(s) {
  if (s.verdict == null) {
    return el('div', { class: 'annot a-notyet' }, el('div', { class: 'eyebrow', text: 'Not checked yet' }),
      el('div', { class: 'small' }, el('strong', { text: 'Their statement: ' }), s.text));
  }
  const v = VERDICT[s.verdict] || VERDICT.not_in_file;
  return el('div', { class: `annot a-${s.verdict}` },
    el('div', { class: 'eyebrow', text: v.word }),
    el('div', { class: 'small' }, el('strong', { text: 'Their statement: ' }), s.text),
    s.message ? el('div', { class: 'small' }, el('strong', { text: 'Your file: ' }), s.message) : null,
    (s.evidence || []).length ? el('div', { class: 'chips' }, s.evidence.map((e) => chip({ ...e.source, date: e.date || e.source?.date }))) : null);
}

// firm = true: the attorney's view. `checks` is the incoming-checks map; the provider's own view never has it.
export function threadView(messages, { firm = true, checks = null, reviews = {}, onReview = null, overrides = [], empty: emptyText = 'No messages yet.' } = {}) {
  const list = (messages || []).map(adaptMessage);
  if (!list.length) return empty(emptyText);
  return el('ol', { class: 'thread' }, list.map((m) => el('li', { class: `msg ${m.dir}` },
    el('div', { class: 'msg-h small muted', text: `${m.dir === 'in' ? (firm ? 'From the provider' : 'You') : 'From the firm'}${m.at ? `  |  ${fmtDateTime(m.at)}` : ''}` }),
    el('div', { class: 'msg-t', text: m.text }),
    firm && m.dir === 'out' ? overrideNote(groupOverrides(overrides.filter((o) => o.item === `message:${m.id}`))) : null,
    firm && m.dir === 'in' && checks ? annotationsFor(checks[`message:${m.id}`], onReview ? { review: reviews[`message:${m.id}`], onReview: (r) => onReview(`message:${m.id}`, r) } : null) : null)));
}
