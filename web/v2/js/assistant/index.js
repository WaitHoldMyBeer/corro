// The assistant beside the work: a slim panel that slides in from the right over whatever tab is
// open and shows the current conversation. The full section (history, files, the document viewer)
// is web/v2/tabs/assistant.js; both draw the same conversation from store.js.
//
//   mountAssistant({ getMatterId })   once, from the shell
//   openPanel() / closePanel() / togglePanel()
//   addContext(items)                 items picked on the page: { kind, id, label, text?, source_refs?, … }
//   ask(text, mode)                   mode: 'fast' | 'deep' (defaults to the switch)
import { el, toast } from '../../../js/util.js';
import { chat, ask as storeAsk, addContext as storeAdd, newConversation, matterId } from './store.js';
import { conversationView } from './view.js';
import { MAX_CONTEXT } from './client.js';
import { onTab, tabHash } from './graph.js';

const MAC = /Mac|iPhone|iPad/.test(navigator.platform || '');
const KEY_HINT = MAC ? '⌘ .' : 'Ctrl .';
const inSection = () => onTab('assistant');

const state = { mounted: false, open: false, opener: null };
const ui = {};

export function openPanel() {
  if (!state.mounted) mountAssistant();
  if (inSection()) { document.querySelector('.asx .ac-input')?.focus(); return; }     // the section already shows the conversation
  if (state.open) { ui.view.focus(); return; }
  matterId().catch(() => {});                      // a different case than last time starts a fresh conversation
  state.open = true;
  state.opener = document.activeElement instanceof HTMLElement && !ui.panel.contains(document.activeElement) ? document.activeElement : ui.openBtn;
  ui.panel.inert = false;
  ui.panel.classList.add('open');
  document.body.classList.add('as-push');
  ui.panel.setAttribute('aria-hidden', 'false');
  ui.openBtn?.setAttribute('aria-expanded', 'true');
  ui.view.focus();
}

export function closePanel() {
  if (!state.open) return;
  state.open = false;
  ui.panel.classList.remove('open');
  document.body.classList.remove('as-push');
  ui.panel.setAttribute('aria-hidden', 'true');
  ui.panel.inert = true;
  ui.openBtn?.setAttribute('aria-expanded', 'false');
  (state.opener?.isConnected ? state.opener : ui.openBtn)?.focus?.({ preventScroll: true });
}

export const togglePanel = () => (state.open ? closePanel() : openPanel());

// Called by the drag-in tool: adds items to what the assistant is given, and shows the panel.
export function addContext(items) {
  if (!state.mounted) mountAssistant();
  const { added, full } = storeAdd(items);
  if (full) toast(`The assistant takes ${MAX_CONTEXT} items at a time. Remove one to add another.`);
  openPanel();
  return added;
}

export const getContext = () => chat.context.map((x) => ({ ...x }));

export function ask(text, mode) {
  if (!state.mounted) mountAssistant();
  openPanel();
  return storeAsk(text, mode);
}

function toSection(doc) {
  if (doc) chat.pendingDoc = doc;
  closePanel();
  if (!inSection()) location.hash = tabHash('assistant');
}

export function mountAssistant({ getMatterId, button = true } = {}) {
  if (getMatterId) chat.getMatterId = getMatterId;
  if (state.mounted) return api_;
  state.mounted = true;

  ui.view = conversationView({ variant: 'panel', openDocument: (doc) => toSection(doc) });
  const full = el('button', { type: 'button', class: 'btn quiet sm', title: 'History, files and documents', text: 'Open in Assistant', onclick: () => toSection(null) });
  const fresh = el('button', { type: 'button', class: 'btn quiet sm', title: 'Start a new chat', text: 'New chat', onclick: () => { newConversation(); ui.view.focus(); } });
  const close = el('button', { type: 'button', class: 'btn sm as-close', title: `Close (Esc or ${KEY_HINT})`, 'aria-label': 'Close the assistant', onclick: closePanel }, 'Close', el('span', { class: 'as-x', 'aria-hidden': 'true', text: '×' }));
  ui.panel = el('aside', { class: 'as-panel', id: 'as-panel', 'aria-label': 'Assistant', 'aria-hidden': 'true', 'data-ai-skip': true },
    el('header', { class: 'as-h' }, el('h2', { text: 'Assistant' }), el('span', { class: 'as-grow' }), fresh, full, close), ui.view.node);
  ui.panel.inert = true;
  document.body.append(ui.panel);

  const top = button ? (document.getElementById('v2-actions') || document.querySelector('.v2-top')) : null;
  if (top) {
    ui.openBtn = el('button', { type: 'button', class: 'btn sm as-open', 'aria-controls': 'as-panel', 'aria-expanded': 'false', title: `Open the assistant (${KEY_HINT})`, onclick: togglePanel },
      'Assistant', el('kbd', { class: 'as-kbd', text: KEY_HINT }));
    top.append(ui.openBtn);
  }

  // Items picked on the page arrive by a direct call; this catches any that were sent before the panel existed.
  window.addEventListener('aiifier:context', (e) => { if (e.detail?.items?.length) addContext(e.detail.items); });
  import('../aiifier/index.js').then((m) => { const held = m.takeQueued?.(); if (held?.length) addContext(held); }).catch(() => { /* no picker on this page */ });
  window.addEventListener('hashchange', () => { if (inSection()) closePanel(); });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key === '.') { e.preventDefault(); togglePanel(); return; }
    if (e.key === 'Escape' && state.open && ui.panel.contains(document.activeElement) && !document.querySelector('dialog[open]')) { e.preventDefault(); closePanel(); }
  });
  return api_;
}

const api_ = { openPanel, closePanel, togglePanel, addContext, getContext, ask };
// Other modules and the console reach the panel without importing it.
window.assistant = api_;
