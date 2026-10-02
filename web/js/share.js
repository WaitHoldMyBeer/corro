// Share screen: the attorney picks a provider, sets the category allowlist,
// previews exactly what will leave the firm, sends, and watches the share log.
import { api, keepMock } from './api.js';
import { el, empty, fmtDate, fmtDateTime, pill, section, toast } from './util.js';
import { renderProviderView, categoryLabel, firmBlock } from './providerview.js';
import { threadView, annotationsFor, overrideNote, groupOverrides } from './messages.js';
import { createCheckedEditor } from './write.js';
import { reasonPrompt, parseLocked } from './override.js';

const SHAREABLE = [
  ['status', 'Stage, open or closed, last activity date, and when the matter opened. No amounts, no reasoning.'],
  ['coverage', 'One band only: confirmed, being confirmed, or none established. No carrier, limit or reasoning.'],
  ['bills', 'This provider\'s own charges and balance.'],
  ['records', 'This provider\'s own records on file and requests.'],
  ['asks', 'Requests to this office, in the wording you approve below. Nothing is sent until you approve it.'],
  ['attendance', 'Appointments on the firm\'s calendar for this provider. It is the client\'s information: your call.'],
];
const NEVER = [
  ['valuation', 'Case value, offers, limits, liens, fee.'],
  ['strategy', 'Liability views, negotiation and litigation plans.'],
  ['other_party', 'Other providers and third parties.'],
  ['internal', 'Firm operations, staff notes, expenses.'],
];

let selected = null;
const hiddenLabels = new Map();   // contact id -> (item id -> label), so a removed item can be named for restore
let unreviewedBy = {};   // contact id -> number of provider texts the file contradicts that nobody has reviewed
let handle = null;     // set by the provider being shown, so a refresh can update it in place
const pendingChanges = new Map();   // contact id -> labels of changes saved but not yet sent
let lastSent = null;   // the link just issued, kept across the re-render that refreshes the log

// The firm is leaving this case: forget what was remembered about its providers.
export function resetShare() {
  selected = null; handle = null; lastSent = null; unreviewedBy = {}; overridesBy = {};
  hiddenLabels.clear(); pendingChanges.clear();
}

export function renderShare(host, c, ctx) {
  const providers = c.providers || [];
  if (!providers.length) {
    host.replaceChildren(section('Share with providers', null, empty('No treating providers were found on this matter. A provider appears here once a contact on the matter is recorded as a treating provider.')));
    return;
  }
  if (!providers.some((p) => p.contact.id === selected)) selected = providers[0].contact.id;
  const panel = providers.find((p) => p.contact.id === selected);
  const right = el('div', { class: 'share-main' });
  const list = providerList(host, c, ctx);
  host.replaceChildren(el('div', { class: 'share-layout' }, list, right));
  paintProvider(host, right, panel, c, ctx);
  handle = { list, ctx, host, c };
}

// Live refresh: swap in the provider list and the share log, and re-draw the
// preview only if the requests changed. Controls, note and typed text stay put.
export function setUnreviewed(map) {
  unreviewedBy = map || {};
  if (handle?.list && handle.host && handle.c) { const n = providerList(handle.host, handle.c, handle.ctx); handle.list.replaceWith(n); handle.list = n; }
}

export function updateShare(c) {
  if (!handle || !handle.panelUpdate) return;
  const fresh = c.providers.find((p) => p.contact.id === selected);
  if (!fresh) return;
  const newList = providerList(handle.host, c, handle.ctx);
  handle.list.replaceWith(newList);
  handle.list = newList;
  handle.panelUpdate(fresh, c);
}

// A reply is not the same as the request being closed, so a replied request is said to be replied, not "0 open".
function askPill(asks) {
  const open = asks.filter((a) => !a.reply).length;
  if (open) return pill(`${open} open request${open === 1 ? '' : 's'}`, 'st-assumed');
  const when = asks.map((a) => a.replied_at).filter(Boolean).sort().pop();
  const pl = pill(`${asks.length} request${asks.length === 1 ? '' : 's'}, replied${when ? ` ${fmtDate(when)}` : ''}`, '');
  pl.title = 'The provider has replied. The request stays open in the matter until its task is closed.';
  return pl;
}

function providerList(host, c, ctx) {
  return el('nav', { class: 'plist', 'aria-label': 'Providers' }, c.providers.map((p) => {
    const last = p.shares?.[0];
    return el('button', { type: 'button', class: p.contact.id === selected ? 'on' : '', onclick: () => { selected = p.contact.id; renderShare(host, c, ctx); } },
      el('strong', { text: p.contact.name }),
      el('span', { class: 'small muted', text: p.contact.role_text || 'Treating provider' }),
      el('span', { class: 'small' },
        unreviewedBy[p.contact.id] ? pill(`${unreviewedBy[p.contact.id]} to review`, 'st-contested') : null,
        p.asks?.length ? askPill(p.asks) : null,
        last ? (() => { const pl = pill(last.state === 'active' ? (last.open_count ? `opened ${last.open_count}x` : 'sent, not opened') : last.state, last.open_count ? 'ok' : ''); pl.title = 'Counts each time the link was fetched. A link preview in a chat app can count too.'; return pl; })() : pill('never shared', '')));
  }));
}

function paintProvider(host, right, panel, c, ctx) {
  const policy = structuredClone(panel.policy);
  let lastPreview = null;               // the view on screen: Send carries its content_hash
  const preview = el('div', { class: 'preview', 'aria-live': 'polite' });
  const warnHost = el('div', { class: 'warn-host' });

  const save = async () => {
    try {
      const saved = await api(`/api/matters/${ctx.matterId}/providers/${panel.contact.id}/policy`, { method: 'PUT', body: policy });
      panel.policy = saved;
    } catch (err) {
      toast(`Not saved: ${err.message}`, 'error');
      Object.assign(policy, structuredClone(panel.policy));
      throw err;
    }
  };
  const refresh = async () => {
    preview.classList.add('busy');
    try {
      const v = await api(panel.preview_href);
      lastPreview = v;
      const page = el('div', { class: 'pv-page' });
      renderProviderView(page, v, { preview: true });
      preview.replaceChildren(
        el('div', { class: 'pv-frame-bar' }, el('span', { class: 'dots', 'aria-hidden': 'true' }), el('span', { text: `What ${panel.contact.name} sees at their link (read-only)` })),
        page);
      firmNote.replaceChildren(firmBlock(v));
      paintItems(v);
      warnHost.replaceChildren(...coverageWarning(v));
    } catch (err) {
      preview.replaceChildren(el('p', { class: 'error', text: `Preview failed: ${err.message}` }));
    }
    preview.classList.remove('busy');
  };

  const labels = hiddenLabels.get(panel.contact.id) || hiddenLabels.set(panel.contact.id, new Map()).get(panel.contact.id);
  const itemsBox = el('div', { class: 'items-box' });
  const firmNote = el('div', { class: 'firm-note' });
  const paintItems = (v) => {
    const ids = policy.hidden_item_ids || [];
    for (const a of v.asks || []) labels.set(a.id, a.text);
    for (const u of v.updates || []) labels.set(u.id, u.label);
    const shown = [...(v.asks || []).map((a) => ({ id: a.id, label: a.text, kind: 'Request' })), ...(v.updates || []).map((u) => ({ id: u.id, label: u.label, kind: 'Status update' }))];
    const rows = [
      ...shown.map((it) => itemRow(it, false)),
      ...ids.map((id) => itemRow({ id, label: labels.get(id) || id, kind: 'Removed' }, true)),
    ];
    itemsBox.replaceChildren(...(rows.length ? [el('div', { class: 'eyebrow', text: 'Individual items' }), el('p', { class: 'small muted', text: 'Remove any single item from what this provider sees.' }), ...rows] : []));
  };
  const itemRow = (it, hidden) => el('div', { class: `item-row${hidden ? ' gone' : ''}` },
    el('span', { class: 'small grow' }, el('strong', { text: `${it.kind}: ` }), it.label.length > 90 ? `${it.label.slice(0, 88)}...` : it.label),
    el('button', { class: 'btn small', type: 'button', onclick: async () => {
      const before = policy.hidden_item_ids || [];
      policy.hidden_item_ids = hidden ? before.filter((x) => x !== it.id) : [...before, it.id];
      try { await save(); refresh(); } catch { policy.hidden_item_ids = before; }
    } }, hidden ? 'Restore' : 'Remove'));

  const toggle = (cat, desc, locked) => {
    const on = policy.allowed_categories.includes(cat);
    const input = el('input', { type: 'checkbox', checked: on, disabled: locked, id: `cat-${cat}` });
    input.addEventListener('change', async () => {
      const set = new Set(policy.allowed_categories);
      if (input.checked) set.add(cat); else set.delete(cat);
      policy.allowed_categories = [...set];
      try { await save(); } catch { input.checked = !input.checked; return; }
      refresh();
    });
    return el('label', { class: `toggle${locked ? ' locked' : ''}`, for: `cat-${cat}` }, input,
      el('span', {}, el('strong', { text: categoryLabel(cat) }), locked ? pill('never shared', '') : null, el('span', { class: 'small muted', text: desc })));
  };

  const msg = el('textarea', { rows: 3, placeholder: 'Optional note shown at the top of the provider\'s page', maxlength: 2000, 'aria-label': 'Cover note' });
  msg.value = policy.message || '';
  msg.addEventListener('change', async () => { policy.message = msg.value.trim() || null; try { await save(); refresh(); } catch { /* toast shown */ } });

  const linkBox = el('div', { class: 'linkbox' });
  if (lastSent && lastSent.contactId === panel.contact.id) {
    const url = lastSent.url;
    linkBox.append(el('p', { class: 'ok-line', text: `Sent. ${lastSent.items} item${lastSent.items === 1 ? '' : 's'} frozen at this moment.` }),
      el('div', { class: 'row' }, el('input', { type: 'text', readonly: true, value: url, 'aria-label': 'Provider link' }),
        el('button', { class: 'btn', type: 'button', onclick: () => navigator.clipboard?.writeText(url).then(() => toast('Link copied.')).catch(() => toast('Copy failed: select the link and copy it.', 'error')) }, 'Copy'),
        el('a', { class: 'btn', href: url, target: '_blank', rel: 'noopener' }, 'Open as the provider')));
  }
  const sendBtn = el('button', { class: 'btn primary', type: 'button' }, `Send to ${panel.contact.name}`);
  let armed = false;
  const reasonBox = el('div', { hidden: true });
  const pending = pendingChanges.get(panel.contact.id) || [];
  const pendingBox = pending.length ? el('div', { class: 'callout warn', role: 'status' }, el('strong', { text: 'Saved, not yet sent: ' }), pending.join('; '), '. Check the preview, then press Send.') : null;
  const reset = () => { sendBtn.disabled = false; armed = false; sendBtn.textContent = `Send to ${panel.contact.name}`; };

  async function doSend(overrideReason) {
    sendBtn.disabled = true;
    try {
      const entry = await api(`/api/matters/${ctx.matterId}/providers/${panel.contact.id}/share`, { method: 'POST',
        body: { preview_hash: lastPreview?.content_hash ?? null, override_reason: overrideReason || null } });
      pendingChanges.delete(panel.contact.id);
      lastSent = { contactId: panel.contact.id, url: new URL(keepMock(entry.link_href), location.origin).href, items: entry.item_count };
      renderShare(host, await ctx.reload(), ctx);
      return;
    } catch (err) {
      if (err.status === 409) {
        // what is on screen is no longer what would be sent: show the new preview and ask again
        toast(err.message || 'The case changed after this preview was drawn. Check the new preview and send again.', 'error');
        reasonBox.hidden = true; await refresh();
      } else if (err.status === 423) {
        askReason(parseLocked(err));
      } else toast(`Not sent: ${err.message}`, 'error');
    }
    reset();
  }

  // 423: text locked for this provider. The attorney may still send it, but must say why; the reason is logged, never shown to the provider.
  function askReason(detail) {
    reasonBox.replaceChildren(reasonPrompt(detail, (reason) => { reasonBox.hidden = true; doSend(reason); }));
    reasonBox.hidden = false;
  }

  sendBtn.addEventListener('click', () => {
    if (!armed) {
      armed = true; sendBtn.textContent = 'Confirm: send this exact preview';
      setTimeout(() => { if (!sendBtn.disabled) reset(); }, 6000);
      return;
    }
    doSend(null);
  });

  let logEl = shareLog(host, panel, c, ctx);
  let checks = null, rv = null;             // firm-only incoming checks and the attorney's review of each
  let repliesEl = repliesBlock(panel, checks, rv);
  let threadEl = threadBlock(panel, checks, rv);
  let inboxEl = inboxBlock(host, panel, ctx, checks, rv);
  const base = `/api/matters/${ctx.matterId}/providers/${panel.contact.id}/incoming-checks`;
  const draw = () => {
    const t = threadBlock(panel, checks, rv); threadEl.replaceWith(t); threadEl = t;
    const r = repliesBlock(panel, checks, rv); repliesEl.replaceWith(r); repliesEl = r;
    if (!inboxEl.contains(document.activeElement)) { const ib = inboxBlock(host, panel, ctx, checks, rv); inboxEl.replaceWith(ib); inboxEl = ib; }
  };
  const loadChecks = async () => {
    try { overridesBy[panel.contact.id] = await api(`/api/matters/${ctx.matterId}/providers/${panel.contact.id}/overrides`); } catch { /* an older server has no log: nothing to show */ }
    try {
      const got = await api(base);
      checks = got.checks || {};
      rv = { reviews: got.reviews || {}, onReview: async (key, review) => {
        const next = await api(`${base}/review`, { method: 'PUT', body: { item: key, review } });   // the server returns the object, updated
        rv.reviews = next.reviews || rv.reviews;
        ctx.refreshBadges?.();
      } };
      draw();
    } catch { /* the readings are an extra; the messages themselves still show */ }
  };
  setTimeout(() => {
    if (!handle) return;
    handle.panelUpdate = (fresh, c2) => {
      const asksChanged = JSON.stringify(fresh.asks) !== JSON.stringify(panel.asks);
      const logChanged = JSON.stringify(fresh.shares) !== JSON.stringify(panel.shares);
      panel.asks = fresh.asks; panel.shares = fresh.shares;
      if (JSON.stringify(fresh.thread) !== JSON.stringify(panel.thread)) { panel.thread = fresh.thread; const t = threadBlock(panel, checks, rv); threadEl.replaceWith(t); threadEl = t; loadChecks(); }
      if (JSON.stringify(fresh.requests) !== JSON.stringify(panel.requests) && !inboxEl.contains(document.activeElement)) { panel.requests = fresh.requests; const ib = inboxBlock(host, panel, ctx, checks, rv); inboxEl.replaceWith(ib); inboxEl = ib; loadChecks(); }
      if (asksChanged) { const r = repliesBlock(panel, checks, rv); repliesEl.replaceWith(r); repliesEl = r; loadChecks(); }
      if (logChanged) { const n = shareLog(host, panel, c2, ctx); logEl.replaceWith(n); logEl = n; }
      if (asksChanged) refresh();
    };
  }, 0);
  right.append(
    el('div', { class: 'share-head' },
      el('div', {}, el('h1', { text: panel.contact.name }), el('p', { class: 'muted small', text: [panel.contact.role_text, panel.last_contact ? `last contact ${panel.last_contact.display}` : null].filter(Boolean).join('  ·  ') }))),
    el('div', { class: 'share-cols' },
      el('div', { class: 'share-ctl' },
        section('What this provider may see', 'changes save as you toggle',
          ...SHAREABLE.map(([k, d]) => toggle(k, d, false)),
          el('div', { class: 'never' }, el('div', { class: 'eyebrow', text: 'Never on the list' }), ...NEVER.map(([k, d]) => toggle(k, d, true)))),
        section('Cover note', null, msg),
        approvalsBlock(panel, policy, save, refresh),
        section('Items', null, itemsBox),
        el('div', { class: 'send-box' }, pendingBox, warnHost, sendBtn, reasonBox, linkBox)),
      el('div', { class: 'share-prev' }, preview, firmNote)),
    inboxEl, threadEl, repliesEl, logEl);
  refresh();
  loadChecks();
}

function coverageWarning(v) {
  const out = [];
  if (v.coverage?.shared !== false && v.coverage?.band === 'not_established') {
    out.push(el('div', { class: 'callout warn', role: 'alert' },
      el('strong', { text: 'This sends "No coverage established". ' }),
      'A provider may stop treating, or press the patient for payment, when it reads that. Switch coverage off if you would rather say nothing.'));
  }
  return out;
}

let overridesBy = {};   // contact id -> the firm-only override log rows

function threadBlock(panel, checks, rv) {
  const thread = panel.thread || panel.messages || [];
  return section('Messages with this provider', thread.length ? `${thread.length}` : null,
    threadView(thread, { firm: true, checks, overrides: overridesBy[panel.contact.id] || [], reviews: rv?.reviews || {}, onReview: rv?.onReview, empty: 'No messages yet. Write one from the Write tab with this provider as the audience.' }));
}

// The inbox: what this provider asked, each with the server's suggested one-click answer, a checked reply, or decline.
function inboxBlock(host, panel, ctx, checks, rv) {
  const reqs = panel.requests || [];
  const open = reqs.filter((r) => r.state === 'open');
  const body = !reqs.length ? [empty('No requests from this provider yet. They can ask from their page.')] : reqs.map((r) => requestRow(host, panel, r, ctx, checks, rv));
  return section('Requests from this provider', open.length ? `${open.length} open` : null, ...body);
}

function requestRow(host, panel, r, ctx, checks, rv) {
  // A suggested action only saves the policy: it sends nothing. The change is marked until the attorney presses Send.
  const answer = async (action, text, reason) => {
    await api(`/api/matters/${ctx.matterId}/providers/${panel.contact.id}/requests/${encodeURIComponent(r.id)}/answer`, { method: 'POST', body: { action, ...(text ? { text } : {}), ...(reason ? { override_reason: reason } : {}) } });
    if (action === 'enable_category' || action === 'resend') {
      const list = pendingChanges.get(panel.contact.id) || [];
      list.push(r.suggested?.category ? `${categoryLabel(r.suggested.category)} switched on` : (r.suggested?.label || 'policy changed'));
      pendingChanges.set(panel.contact.id, list);
    }
    renderShare(host, await ctx.reload(), ctx);
  };
  const act = (action, text, reason) => answer(action, text, reason).catch((err) => toast(`Not saved: ${err.message}`, 'error'));
  const box = el('div', { class: `inbox-req st-${r.state}` },
    el('div', { class: 'ask-h' }, el('strong', { text: r.question || r.kind }), pill(r.state, r.state === 'open' ? 'st-assumed' : r.state === 'answered' ? 'ok' : 'st-stale')),
    r.text ? el('p', { class: 'small', text: r.text }) : null,
    checks ? annotationsFor(checks[`request:${r.id}`], rv ? { review: rv.reviews[`request:${r.id}`], onReview: (x) => rv.onReview(`request:${r.id}`, x) } : null) : null,
    el('div', { class: 'small muted', text: r.created_at ? `Asked ${fmtDateTime(r.created_at)}` : '' }));
  if (r.state !== 'open') {
    if (r.answer) box.append(el('div', { class: 'reply-done' }, el('div', { class: 'eyebrow', text: 'Your answer' }), el('p', { text: r.answer })));
    return box;
  }
  const actions = el('div', { class: 'row wrap' });
  const replyHost = el('div', { class: 'reply-host', hidden: true });
  const toggleReply = () => { replyHost.hidden = !replyHost.hidden; if (!replyHost.hidden && !replyHost.firstChild) buildReply(); };
  // A suggested reply opens the checked editor; the other suggestions save the policy and send nothing.
  if (r.suggested?.label) actions.append(el('button', { class: 'btn primary small', type: 'button', onclick: () => (r.suggested.action === 'reply' ? toggleReply() : act(r.suggested.action)) }, r.suggested.label));
  actions.append(
    r.suggested?.action === 'reply' ? null : el('button', { class: 'btn small', type: 'button', onclick: toggleReply }, 'Write a reply'),
    el('button', { class: 'btn small danger', type: 'button', onclick: () => act('decline') }, 'Decline'));
  function buildReply() {
    const ed = createCheckedEditor({
      matterId: ctx.matterId, getAudience: () => ({ kind: 'provider', contact_id: panel.contact.id }), placeholder: 'Your reply. It is checked against the file, and locked if it would disclose something this provider must not see.',
      onChange: () => upd(), onResult: () => upd(),
    });
    const send = el('button', { class: 'btn primary small', type: 'button', disabled: true }, 'Send reply');
    const note = el('span', { class: 'small muted' });
    function upd() { send.disabled = !ed.value.trim() || ed.blocked || ed.pending; note.textContent = ed.blocked ? 'A locked sentence must be rewritten first.' : ''; }
    const reasonHere = el('div', { hidden: true });
    const doReply = (reason) => answer('reply', ed.value.trim(), reason).catch((err) => {
      if (err.status === 423) { reasonHere.replaceChildren(reasonPrompt(parseLocked(err), (why) => { reasonHere.hidden = true; doReply(why); })); reasonHere.hidden = false; }
      else toast(`Not sent: ${err.message}`, 'error');
    });
    send.addEventListener('click', () => doReply(null));
    replyHost.append(reasonHere);
    replyHost.append(ed.root, el('div', { class: 'row' }, send, note));
  }
  box.append(actions, replyHost);
  return box;
}

// Per-request approval: each request is off until the attorney approves its wording. Shown only when the
// server's policy carries approved_asks (feature-detected), so an older server is unaffected.
function approvalsBlock(panel, policy, save, refresh) {
  if (policy.approved_asks === undefined) return null;
  const rows = (panel.asks || []).map((a) => {
    const approved = policy.approved_asks[a.id];
    const input = el('input', { type: 'text', value: approved ?? a.text, maxlength: 500, 'aria-label': 'Wording the provider will read' });
    const on = el('input', { type: 'checkbox', checked: approved != null, id: `appr-${a.id}` });
    const apply = async () => {
      const prev = { ...policy.approved_asks };
      const next = { ...policy.approved_asks };
      if (on.checked && input.value.trim()) next[a.id] = input.value.trim(); else delete next[a.id];
      policy.approved_asks = next;
      try { await save(); refresh(); } catch { policy.approved_asks = prev; on.checked = prev[a.id] != null; }
    };
    on.addEventListener('change', apply);
    input.addEventListener('change', () => { if (on.checked) apply(); });
    return el('div', { class: 'appr' },
      el('div', { class: 'small muted', text: `Firm task (internal, not sent): ${a.text}` }),
      el('div', { class: 'row' }, input, el('label', { class: 'small', for: `appr-${a.id}` }, on, ' Approve')));
  });
  return section('Requests to this office', 'off until you approve the wording',
    ...(rows.length ? rows : [empty('No open requests for this provider.')]));
}

function repliesBlock(panel, checks, rv) {
  const got = (panel.asks || []).filter((a) => a.reply);
  return section('Replies from this provider', got.length ? `${got.length}` : null,
    got.length ? el('ul', { class: 'replies' }, got.map((a) => el('li', {},
      el('div', { class: 'small muted', text: `Request: ${a.text.length > 110 ? `${a.text.slice(0, 108)}...` : a.text}` }),
      el('div', { class: 'reply-text', text: a.reply }),
      checks ? annotationsFor(checks[`reply:${a.id}`], rv ? { review: rv.reviews[`reply:${a.id}`], onReview: (x) => rv.onReview(`reply:${a.id}`, x) } : null) : null,
      el('div', { class: 'small muted', text: a.replied_at ? `Replied ${fmtDateTime(a.replied_at)}` : '' }))))
      : empty('No replies yet. Their answers to your requests appear here the moment they send them.'));
}

function shareLog(host, panel, c, ctx) {
  const shares = panel.shares || [];
  const rows = shares.map((s) => el('tr', {},
    el('td', { text: fmtDateTime(s.sent_at) }),
    el('td', { text: fmtDate(s.expires_at) || 'no expiry' }),
    el('td', {}, pill(s.state, s.state === 'active' ? 'ok' : 'st-stale')),
    el('td', { text: s.first_opened_at ? fmtDateTime(s.first_opened_at) : 'not opened' }),
    el('td', { text: String(s.open_count) }),
    el('td', {}, `${s.item_count} (${(s.categories || []).map(categoryLabel).join(', ')})`, overrideNote(groupOverrides((overridesBy[panel.contact.id] || []).filter((o) => o.share_id === s.id)))),
    el('td', { class: 'actions' },
      s.snapshot_href ? el('button', { class: 'btn small', type: 'button', onclick: () => showSnapshot(s) }, 'View as sent') : null,
      s.state === 'active' && s.revoke_href ? el('button', { class: 'btn small danger', type: 'button', onclick: async () => {
        if (!confirm('Revoke this link? The provider will no longer be able to open it.')) return;
        try { await api(s.revoke_href, { method: 'POST' }); renderShare(host, await ctx.reload(), ctx); } catch (err) { toast(`Not revoked: ${err.message}`, 'error'); }
      } }, 'Revoke') : null)));
  return section('Share log', 'an audit trail: what was sent, when, and whether it was opened',
    shares.length ? el('div', { class: 'table-wrap' }, el('table', { class: 'log' },
      el('thead', {}, el('tr', {}, ['Sent', 'Expires', 'State', 'First opened', 'Opens', 'Items'].map((h) => el('th', { text: h })), el('th', { text: '' }))), el('tbody', {}, rows)))
      : empty('Nothing has been sent to this provider yet.'));
}

async function showSnapshot(s) {
  const dlg = el('dialog', { class: 'drawer wide' });
  dlg.addEventListener('close', () => dlg.remove());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  const body = el('div', { class: 'drawer-body' }, el('p', { class: 'muted', text: 'Loading...' }));
  dlg.append(el('header', { class: 'drawer-h' }, el('div', {}, el('div', { class: 'eyebrow', text: `Sent ${fmtDateTime(s.sent_at)}` }), el('h2', { text: 'Exactly what was sent' })),
    el('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close() }, 'Close')), body);
  document.body.append(dlg);
  dlg.showModal();
  try { renderProviderView(body, await api(s.snapshot_href), { preview: true }); } catch (err) { body.replaceChildren(el('p', { class: 'error', text: err.message })); }
}
