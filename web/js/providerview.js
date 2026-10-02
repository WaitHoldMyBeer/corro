// Renders a ProviderView. Used twice: as the attorney's live preview and on the
// provider's own link, so what the attorney checks is what the provider sees.
import { el, empty, fmtDate, fmtDateTime, fmtMoney, pill, section, dayWords } from './util.js';
import { chips } from './drawer.js';
import { threadView } from './messages.js';

const CATEGORY_LABEL = {
  status: 'Status', bills: 'Bills', records: 'Records', attendance: 'Attendance', asks: 'Requests to your office',
  coverage: 'Coverage', valuation: 'Valuation', strategy: 'Strategy', other_party: 'Other parties', internal: 'Internal',
};
export const categoryLabel = (c) => CATEGORY_LABEL[c] || c;

const STATE_WORDS = { unknown: 'Not included', requested: 'Requested', partial: 'Partly received', received: 'Received' };
const SIGNAL_WORDS = {
  unknown: 'No appointments on the firm\'s calendar', recorded: 'Past appointments on the firm\'s calendar', scheduled: 'An upcoming appointment is on the firm\'s calendar',
};

function kv(label, value) {
  if (value == null || value === '') return null;
  return [el('dt', { text: label }), el('dd', { text: value })];
}

function statusBlock(v, firm) {
  const rows = [];
  if (v.shared_categories.includes('status')) {
    rows.push(
      el('div', { class: 'pv-fact' }, el('div', { class: 'eyebrow', text: 'Case status' }),
        v.alive ? [el('div', { class: 'big', text: v.alive.display }), v.alive.detail ? el('p', { class: 'muted', text: v.alive.detail }) : null, null]
          : el('div', { class: 'big muted', text: 'Not recorded' })),
      el('div', { class: 'pv-fact' }, el('div', { class: 'eyebrow', text: 'Stage' }),
        v.stage ? [el('div', { class: 'big', text: v.stage.display }), el('p', { class: 'muted small', text: 'As recorded by the firm' })]
          : el('div', { class: 'big muted', text: 'Not recorded' })));
  }
  const cov = v.coverage;
  const band = cov?.shared === false ? 'off' : (cov?.band || 'unknown');
  rows.push(el('div', { class: `pv-fact cov cov-${band}` }, el('div', { class: 'eyebrow', text: 'Coverage behind the case' }),
    el('div', { class: 'big', text: cov?.display || 'Not shared by the firm' }),
    cov?.shared === false ? null : el('p', { class: 'small muted', text: cov?.note || 'A status note from the firm. Not a statement of the amount available and not a promise of payment.' }),
    firm && cov?.sources?.length ? el('p', { class: 'small' }, 'Basis (firm only): ', chips(cov.sources)) : null));
  return el('div', { class: 'pv-facts' }, rows);
}

function asksBlock(v, opts) {
  if (!v.shared_categories.includes('asks')) return null;
  const body = v.asks.length ? v.asks.map((a) => askItem(a, opts)) : [empty('The firm has no open requests for your office.')];
  const open = v.asks.filter((a) => !a.reply).length;
  return section('What the firm needs from your office', v.asks.length ? `${open} open of ${v.asks.length}` : null, ...body);
}

function askItem(a, opts) {
  const meta = [a.due ? `Requested by ${fmtDate(a.due)}` : null, a.times_asked > 1 ? `Asked ${a.times_asked} times` : null, a.last_asked ? `Last asked ${fmtDate(a.last_asked)}` : null].filter(Boolean).join('  ·  ');
  const box = el('div', { class: 'ask' },
    el('div', { class: 'ask-h' }, el('p', { class: 'ask-text', text: a.text }), 
      ),
    meta ? el('p', { class: 'muted small', text: meta }) : null);
  if (a.reply) {
    box.append(el('div', { class: 'reply-done' }, el('div', { class: 'eyebrow', text: `Your reply${a.replied_at ? `, ${fmtDateTime(a.replied_at)}` : ''}` }), el('p', { text: a.reply })));
    return box;
  }
  if (opts.preview) return box;           // the preview is read-only: no controls, only what is shown to the provider
  const ta = el('textarea', { rows: 3, placeholder: 'Reply to the firm', 'aria-label': 'Reply to the firm', maxlength: 4000 });
  const send = el('button', { class: 'btn primary', type: 'button' }, 'Send reply');
  const msg = el('span', { class: 'small muted', role: 'status' });
  send.addEventListener('click', async () => {
    const text = ta.value.trim();
    if (!text) { msg.textContent = 'Write a reply first.'; return; }
    send.disabled = true; msg.textContent = 'Sending...';
    try { await opts.onReply(a.id, text); } catch (err) { msg.textContent = `Not sent: ${err.message}`; send.disabled = false; }
  });
  box.append(el('div', { class: 'reply' }, ta, el('div', { class: 'row' }, send, msg)));
  return box;
}

function recordsBlock(v, firm) {
  const cards = [];
  if (v.shared_categories.includes('records')) {
    const r = v.records;
    cards.push(el('div', { class: 'card inner' }, el('h3', { text: 'Your records on file' }),
      r ? [pill(STATE_WORDS[r.state || 'unknown'] || r.state, `rs-${r.state || 'unknown'}`),
        el('dl', { class: 'meta' }, kv('Pages held', r.pages ? String(r.pages) : null), kv('First requested', fmtDate(r.first_requested)), kv('Last received', fmtDate(r.last_received))),
        (r.state || 'unknown') === 'unknown' ? el('p', { class: 'muted small', text: 'Not included in this update.' }) : null,
        firm ? chips(r.sources) : null] : empty('Not recorded.')));
  }
  if (v.shared_categories.includes('bills')) {
    const b = v.bills;
    cards.push(el('div', { class: 'card inner' }, el('h3', { text: 'Your bills on file' }),
      b ? [pill(STATE_WORDS[b.state || 'unknown'] || b.state, `rs-${b.state || 'unknown'}`),
        el('dl', { class: 'meta' }, kv('Billed total', fmtMoney(b.billed_total)), kv('Lines', b.line_count ? String(b.line_count) : null), kv('Last service date', fmtDate(b.last_service_date))),
        (b.state || 'unknown') === 'unknown' ? el('p', { class: 'muted small', text: 'Not included in this update.' }) : null,
        firm ? chips(b.sources) : null] : empty('Not recorded.')));
  }
  if (v.shared_categories.includes('attendance')) {
    const a = v.attendance;
    cards.push(el('div', { class: 'card inner' }, el('h3', { text: 'Appointments on the firm\'s calendar' }),
      a ? [el('p', { text: SIGNAL_WORDS[a.signal] || a.signal }),
        el('dl', { class: 'meta' }, kv('Most recent', fmtDate(a.last_visit)), kv('Next', fmtDate(a.next_visit)), kv('Appointments listed', a.visits ? String(a.visits) : null)),
        el('p', { class: 'muted small', text: 'From the firm\'s calendar. This is not your own scheduling record, and it says nothing about attendance.' })] : empty('Not recorded.')));
  }
  return cards.length ? el('div', { class: 'grid-3' }, cards) : null;
}

function messagesBlock(v, opts) {
  const kinds = v.request_kinds;                         // {kind: question}
  const thread = v.thread || v.messages;
  if (!kinds && !Array.isArray(thread)) return null;     // an older server sends neither
  if (opts.preview && !(v.requests || []).length && !(Array.isArray(thread) && thread.length)) return null;   // nothing to show in a read-only preview
  const body = [];
  const mine = (v.requests || []);
  if (mine.length) {
    body.push(el('ul', { class: 'my-requests' }, mine.map((r) => el('li', {},
      el('div', {}, el('strong', { text: r.question || r.kind }), ' ', pill(r.state === 'open' ? 'waiting for the firm' : r.state, r.state === 'answered' ? 'ok' : '')),
      r.text ? el('div', { class: 'small', text: r.text }) : null,
      r.answer ? el('div', { class: 'reply-done' }, el('div', { class: 'eyebrow', text: `The firm replied${r.answered_at ? `, ${fmtDateTime(r.answered_at)}` : ''}` }), el('p', { text: r.answer })) : null))));
  }
  if (Array.isArray(thread) && thread.length) body.push(threadView(thread, { firm: false }));
  if (!opts.preview && (opts.onRequest || opts.onAsk)) {
    let chosen = null;
    const buttons = Object.entries(kinds || {}).map(([k, q]) => el('button', { class: 'btn small', type: 'button', 'aria-pressed': 'false',
      onclick: (e) => { chosen = chosen === k ? null : k; buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === chosen))); ta.placeholder = chosen ? 'Add details (optional)' : 'Write a message to the firm'; } }, q));
    buttons.forEach((b, i) => { b.dataset.k = Object.keys(kinds)[i]; });
    const ta = el('textarea', { rows: 3, placeholder: 'Write a message to the firm', 'aria-label': 'Message to the firm', maxlength: 2000 });
    const send = el('button', { class: 'btn primary', type: 'button' }, 'Send to the firm');
    const msg = el('span', { class: 'small muted', role: 'status' });
    send.addEventListener('click', async () => {
      const text = ta.value.trim();
      if (!chosen && !text) { msg.textContent = 'Choose a question or write a message first.'; return; }
      send.disabled = true; msg.textContent = 'Sending...';
      try { await (chosen ? opts.onRequest({ kind: chosen, text }) : opts.onAsk(text)); } catch (err) { msg.textContent = `Not sent: ${err.message}`; send.disabled = false; }
    });
    body.push(el('div', { class: 'ask-firm' }, el('div', { class: 'eyebrow', text: 'Ask the firm' }),
      kinds ? el('div', { class: 'row wrap' }, buttons) : null, ta, el('div', { class: 'row' }, send, msg)));
  }
  return section('Messages with the firm', null, ...body);
}

function updatesBlock(v, opts) {
  if (!v.shared_categories.includes('status')) return null;
  const items = v.updates.map((u) => {
    const fresh = opts.lastVisit && u.date && u.date > opts.lastVisit.slice(0, 10);
    return el('li', {}, el('span', { class: 'when', text: fmtDate(u.date) }), el('span', { class: 'what', text: u.label }), fresh ? pill('new', 'ok') : null);
  });
  return section('Status changes', opts.lastVisit ? `since your last visit, ${fmtDateTime(opts.lastVisit)}, marked new` : null,
    items.length ? el('ul', { class: 'timeline' }, items) : empty('No status changes recorded yet.'));
}

function firmBlock(v) {
  const counts = Object.entries(v.withheld_counts || {}).filter(([, n]) => n > 0);
  const notShared = ['status', 'bills', 'records', 'attendance', 'asks', 'coverage'].filter((c) => !v.shared_categories.includes(c));
  return el('div', { class: 'preview-foot' },
    v.warnings?.length ? el('ul', { class: 'warnings' }, v.warnings.map((w) => el('li', { text: w }))) : null,
    el('p', { class: 'small' }, el('strong', { text: 'Withheld from this provider: ' }),
      counts.length ? counts.map(([c, n]) => `${n} ${categoryLabel(c).toLowerCase()}`).join(', ') : 'nothing counted',
      notShared.length ? `. Categories switched off: ${notShared.map((c) => categoryLabel(c).toLowerCase()).join(', ')}.` : '.'));
}

export { firmBlock };

export function renderProviderView(host, v, opts = {}) {
  const preview = !!opts.preview;
  host.replaceChildren(...[
    el('div', { class: 'pv-head' },
      el('div', {}, el('div', { class: 'eyebrow', text: 'Case update' }),
        el('h1', { text: v.provider?.name || 'Provider' }),
        el('p', { class: 'muted', text: `Patient: ${v.client_name}` }),
        (v.from_name || v.firm_name || v.attorney_name) ? el('p', { class: 'muted small', text: `From ${[v.from_name || v.firm_name, v.attorney_name].filter(Boolean).join(', ')}${v.attorney_contact ? `  ·  ${v.attorney_contact}` : ''}` }) : null),
      v.generated_at ? el('p', { class: 'muted small', text: `Prepared ${fmtDateTime(v.generated_at)}${v.expires_at ? `  ·  link expires ${fmtDate(v.expires_at)}` : ''}` }) : null),
    v.message ? el('div', { class: 'note-from-firm' }, el('div', { class: 'eyebrow', text: 'Note from the firm' }), el('p', { text: v.message })) : null,
    statusBlock(v, preview),
    messagesBlock(v, opts),
    asksBlock(v, { ...opts, preview }),
    recordsBlock(v, preview),
    updatesBlock(v, opts),
    el('p', { class: 'pv-footer small muted', text: 'A courtesy status update from the firm about your patient\'s claim. It is not legal advice.' }),
    ].filter(Boolean));
}
