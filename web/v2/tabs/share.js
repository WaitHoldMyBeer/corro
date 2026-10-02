// Share tab: three steps in three columns (provider, what they may see, what they will see) and a second level for
// the inbox, the thread and the share log. The sharing engine is web/js/share.js; this file only lays out its parts
// through the `layout` hook, so every behaviour (409 re-preview, 423 reason prompt, saved-not-sent, revoke, view as sent)
// is the verified one.
import { el } from '../../js/util.js';
import { renderShare, updateShare } from '../../js/share.js';
import { loadCss } from './_css.js';
import { customiseStrip, changeButton } from './_customise.js';
import { createCheckedEditor } from '../../js/write.js';
import { reasonPrompt, parseLocked } from '../../js/override.js';
import { api, keepMock } from '../../js/api.js';
import { toast, pill } from '../../js/util.js';

const PANES = [
  ['send', 'Prepare and send'],
  ['conv', 'Conversation'],
  ['inbox', 'Requests and replies'],
  ['log', 'Share log'],
];
let pane = 'send';   // survives the re-render that follows a send, an answer or a revoke

export default function mount(host, c, ctx) {
  loadCss('share.css');
  const w = el('div', { class: 'sh' });
  host.replaceChildren(el('div', { class: 'v2-page-h' }, el('h1', { text: 'Share' }), el('span', { class: 'muted', text: 'Nothing leaves the firm until you press Send on a preview you have read.' })), w);
  // The engine draws its own two-column page into `w` every time it re-renders (after a send, an answer, a revoke).
  // Each time, lift its parts out and lay them in three steps. The parts are moved, not copied, so the engine's
  // in-place updaters (which replaceWith the same nodes) keep working.
  const rerender = async () => { const before = leafTexts(w.querySelector('.sh-providerview')); renderShare(w, await ctx.reload(), ctx); highlight(w, before); await new Promise((r) => setTimeout(r, 0)); w.querySelector('.cz-input')?.focus(); };   // after the layout is rebuilt
  const arrange = () => {
    const old = w.firstElementChild;
    if (!old || !old.classList.contains('share-layout')) return;
    const parts = partsOf(old);
    if (parts) w.replaceChildren(layout(parts, ctx, rerender));
  };
  const mo = new MutationObserver(arrange);
  mo.observe(w, { childList: true });
  renderShare(w, c, ctx);
  arrange();
  return { update: () => updateShare(ctx.caseModel), destroy: () => mo.disconnect() };
}

function partsOf(old) {
  const [list, main] = old.children;
  const [head, cols, inbox, thread, replies, log] = main?.children || [];
  const ctl = cols?.querySelector('.share-ctl'), prev = cols?.querySelector('.share-prev');
  if (!list || !head || !ctl || !prev || !log) return null;
  const k = [...ctl.children];
  const send = k.pop(), items = k.pop();
  const [toggles, cover, ...rest] = k;
  const [preview, firmNote] = prev.children;
  return { list, head, toggles, cover, approvals: rest[0] || null, items, send, preview, firmNote, inbox, thread, replies, log };
}

// ---- customise: scope controls and the brief highlight of what a change did to the preview
const leafTexts = (root) => new Set([...(root?.querySelectorAll('*') || [])].filter((n) => !n.children.length && n.textContent.trim()).map((n) => n.textContent.trim()));
function highlight(w, before) {
  let tries = 0;
  const t = setInterval(() => {
    const root = w.querySelector('.sh-providerview .pv-page');
    if (!root && ++tries < 20) return;
    clearInterval(t);
    if (!root) return;
    for (const n of root.querySelectorAll('*')) if (!n.children.length && n.textContent.trim() && !before.has(n.textContent.trim())) { n.classList.add('sh-changed'); setTimeout(() => n.classList.remove('sh-changed'), 2600); }
  }, 150);
}
const SECTION_TARGET = { 'Cover note': { kind: 'cover_note', id: 'cover_note' } };
function addChange(sec, label, target) {
  const h = sec?.querySelector('.card-h');
  if (h && !h.querySelector('.cz-change')) h.append(changeButton(label, target));
}

function layout(p, ctx, rerender) {
  const idx = [...p.list.querySelectorAll('button')].findIndex((b) => b.classList.contains('on'));
  const provider = () => ctx.caseModel.providers?.[Math.max(idx, 0)];
  const cz = customiseStrip(ctx, provider, rerender);
  for (const [sec, title] of [[p.toggles, 'What they may see'], [p.cover, 'Cover note'], [p.approvals, 'Requests to this office'], [p.items, 'Items']]) addChange(sec, title, SECTION_TARGET[sec?.querySelector('h2')?.textContent] || { kind: 'section' });
  p.toggles?.querySelectorAll('label.toggle:not(.locked)').forEach((l) => { const name = l.querySelector('strong')?.textContent; if (name && !l.querySelector('.cz-change')) l.append(changeButton(name, { kind: 'category', id: l.getAttribute('for')?.replace('cat-', '') })); });
  // items: the rows are redrawn by the engine, so add the control to each new row. The id comes from the provider's own lists.
  const idFor = (text) => { const pv = provider(); const all = [...(pv.asks || []).map((a) => [a.id, a.text, 'ask']), ...(ctx.caseModel.timeline || []).map((u) => [u.id, u.label, 'update'])]; const t = text.replace(/^(Request|Status update|Removed): /, '').replace(/\.\.\.$/, ''); return all.find(([, l]) => l && l.startsWith(t)); };
  const decorate = () => p.items?.querySelectorAll('.item-row:not(.gone)').forEach((r) => { if (r.querySelector('.cz-change')) return; const label = r.querySelector('.small')?.textContent || 'item'; const hit = idFor(label); r.insertBefore(changeButton(label.slice(0, 40), hit ? { kind: hit[2], id: hit[0] } : { kind: 'item' }), r.lastElementChild); });
  if (p.items) { new MutationObserver(decorate).observe(p.items, { childList: true, subtree: true }); decorate(); }
  // the preview is redrawn on every change: give each of its blocks a control that scopes to that block's heading
  const decoratePreview = () => p.preview?.querySelectorAll('.pv-page > section, .pv-page > div > section').forEach((sct) => {
    const h = sct.querySelector('h2, h3'); if (!h || sct.querySelector(':scope > .cz-change, h2 + .cz-change, h3 + .cz-change')) return;
    const txt = h.textContent.toLowerCase();
    // a bills or records block is shared as a whole, so its control points at the category rather than at a line in it
    const cat = ['bills', 'records', 'coverage', 'attendance'].find((k) => txt.includes(k));
    h.after(changeButton(h.textContent.slice(0, 40), cat ? { kind: 'category', id: cat } : { kind: 'section' })); });
  if (p.preview) { new MutationObserver(decoratePreview).observe(p.preview, { childList: true, subtree: true }); decoratePreview(); }
  const bodies = {
    send: el('div', { class: 'sh-send' },
      el('div', { class: 'sh-col sh-may' },
        cz.node,
        step('2', 'What they may see', 'Switch categories on or off, then approve the wording of each request. Changes save as you go.'),
        p.toggles, p.cover, p.approvals, p.items),
      el('div', { class: 'sh-col sh-will' },
        step('3', 'What they will see', 'This is the provider\'s page, exactly as it will be frozen when you send.'),
        el('div', { class: 'sh-providerview', 'data-ai-skip': '' },
          el('div', { class: 'sh-pv-band' }, el('strong', { text: 'Provider\'s view' }), el('span', { text: 'read-only: nothing here can be edited' })),
          p.preview),
        el('div', { class: 'sh-sendbar' }, p.send),
        el('div', { class: 'sh-firmonly' }, el('div', { class: 'eyebrow', text: 'Firm only: not shown to the provider' }), p.firmNote))),
    inbox: el('div', { class: 'sh-stack' }, p.inbox, p.replies),
    conv: el('div', { class: 'sh-stack' }, p.thread, composer(ctx, provider, rerender)),
    log: el('div', { class: 'sh-stack' }, p.log),
  };
  const tabs = el('div', { class: 'sh-sub', role: 'tablist', 'aria-label': 'Share sections' });
  const show = (k) => {
    pane = k;
    if (k === 'conv') markSeen(ctx, provider());
    for (const [key, node] of Object.entries(bodies)) node.hidden = key !== k;
    [...tabs.children].forEach((b) => b.setAttribute('aria-selected', String(b.dataset.k === k)));
  };
  for (const [k, label] of PANES) tabs.append(el('button', { type: 'button', role: 'tab', 'data-k': k, text: label, onclick: () => show(k) }));
  show(pane);
  const who = el('div', { class: 'sh-col sh-who' }, step('1', 'Provider', 'Who needs attention first.'), p.list);
  // the engine swaps these nodes in place as data arrives, so watch their stable parents
  const redo = () => { const l = who.querySelector('.plist'); if (l) decorateList(l, ctx, show); };
  new MutationObserver(redo).observe(who, { childList: true }); redo();
  relabel(bodies.conv);
  tabs.querySelector('[data-k=conv]').append(unreadPill(ctx, provider()));
  return el('div', { class: 'sh-layout' },
    who,
    el('div', { class: 'sh-main' }, p.head, tabs, ...Object.values(bodies)));
}

function step(n, title, hint) {
  return el('header', { class: 'sh-step' }, el('span', { class: 'sh-n', 'aria-hidden': 'true', text: n }), el('div', {}, el('h2', { text: title }), el('p', { class: 'small muted', text: hint })));
}

// ---- conversation: labels, composer, unread, and a way into the provider's page
const fromProvider = (m) => ['in', 'incoming', 'provider', 'from_provider'].includes(m.direction) || m.from === 'provider';
const seenKey = (ctx, pv) => `v2:seen:${ctx.matterId}:${pv.contact.id}`;
const providerCount = (pv) => (pv.thread || pv.messages || []).filter(fromProvider).length;
function seen(ctx, pv) { try { return Number(localStorage.getItem(seenKey(ctx, pv)) || 0); } catch { return 0; } }
function markSeen(ctx, pv) { try { localStorage.setItem(seenKey(ctx, pv), String(providerCount(pv))); } catch { /* optional */ } }
const unreadOf = (ctx, pv) => Math.max(0, providerCount(pv) - seen(ctx, pv));
function unreadPill(ctx, pv) { const n = unreadOf(ctx, pv); return n && pane !== 'conv' ? pill(`${n} new`, 'st-contested') : document.createTextNode(''); }

// The engine says "From the provider"; the firm reads it as the provider's office.
function relabel(thread) {
  const fix = () => thread?.querySelectorAll('.msg-h').forEach((h) => { if (h.textContent.startsWith('From the provider') && !h.textContent.startsWith("From the provider's")) h.textContent = h.textContent.replace('From the provider', "From the provider's office"); });
  fix(); if (thread) new MutationObserver(fix).observe(thread, { childList: true, subtree: true });
}

// A way into each provider's page: their live link when a share is active, otherwise the preview.
function decorateList(list, ctx, show) {
  const provs = ctx.caseModel.providers || [];
  [...list.querySelectorAll('button')].forEach((b, i) => {
    const pv = provs[i]; if (!pv || b.nextElementSibling?.classList.contains('sh-open')) return;
    const live = (pv.shares || []).find((x) => x.state === 'active' && x.link_href);
    const n = unreadOf(ctx, pv);
    if (n) b.querySelector('span.small:last-child')?.append(pill(`${n} new message${n === 1 ? '' : 's'}`, 'st-contested'));
    b.after(live ? el('a', { class: 'btn small sh-open', href: new URL(keepMock(live.link_href), location.origin).href, target: '_blank', rel: 'noopener' }, 'Open provider\'s page')
      : el('button', { class: 'btn small sh-open', type: 'button', onclick: () => { b.click(); setTimeout(() => { show('send'); document.querySelector('.sh-providerview')?.scrollIntoView({ block: 'start' }); }, 80); } }, 'Open provider\'s preview'));
  });
}

function composer(ctx, provider, rerender) {
  const pv = provider();
  const ed = createCheckedEditor({ matterId: ctx.matterId, getAudience: () => ({ kind: 'provider', contact_id: pv.contact.id }), placeholder: 'Write to this provider. It is checked against the file and held if it would disclose something they must not see.', onChange: () => upd(), onResult: () => upd() });
  const send = el('button', { class: 'btn primary', type: 'button', disabled: true }, 'Send message');
  const note = el('span', { class: 'small muted' });
  const reasonBox = el('div', { hidden: true });
  function upd() { send.disabled = !ed.value.trim() || ed.blocked || ed.pending; note.textContent = ed.blocked ? 'A held sentence must be rewritten first.' : ''; }
  const go = async (reason) => {
    send.disabled = true;
    try { await api(`/api/matters/${ctx.matterId}/providers/${pv.contact.id}/messages`, { method: 'POST', body: { text: ed.value.trim(), override_reason: reason || null } }); await rerender(); }
    catch (err) { if (err.status === 423) { reasonBox.replaceChildren(reasonPrompt(parseLocked(err), (why) => { reasonBox.hidden = true; go(why); })); reasonBox.hidden = false; } else toast(`Not sent: ${err.message}`, 'error'); upd(); }
  };
  send.addEventListener('click', () => go(null));
  return el('section', { class: 'card sh-composer' }, el('header', { class: 'card-h' }, el('h2', { text: `Write to ${pv.contact.name}` }), el('span', { class: 'sub', text: 'goes to their page, after the same check as everything else' })), ed.root, reasonBox, el('div', { class: 'row' }, send, note));
}
