// The conversation, held once and shown in two places: the side panel and the Assistant section.
// Both subscribe here, so a turn asked in one is the same turn in the other.
//
//   turn = { question, mode, state: 'running' | 'done' | 'error' | 'stopped', status, steps[], blocks[], answer, error, t0, firstEventMs }
//   events: 'reset' (the whole conversation changed), 'turn' (one was added), 'update' (a running turn moved),
//           'done' (a turn ended), 'busy', 'mode', 'context', 'history' (the list of conversations is stale)
import { api } from '../../../js/api.js';
import { send, getConversation, buildDocument, MAX_CONTEXT } from './client.js';

export const CREATED_EVENT = 'assistant:document-created';     // window event the files list listens for

export const chat = { conversationId: null, turns: [], mode: 'fast', context: [], busy: null, pendingDoc: null, canRefresh: true, getMatterId: () => null, matterId: null };
const subs = new Set();
const cache = new Map();          // conversation id -> turns, so switching back is instant

export function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
function emit(type, data) { for (const fn of [...subs]) { try { fn(type, data); } catch (err) { console.error('assistant view', err); } } }

// A conversation belongs to one case: opening another case starts clean.
let lastMatter = null;
function sameCase(id) {
  if (id != null && lastMatter != null && String(id) !== String(lastMatter)) { cache.clear(); chat.context = []; chat.pendingDoc = null; chat.busy?.abort(); chat.conversationId = null; chat.turns = []; emit('reset'); emit('context'); }
  if (id != null) lastMatter = id;
  return id;
}

export async function matterId() {
  const id = chat.getMatterId?.() ?? chat.matterId;
  if (id != null) return sameCase(id);
  const list = await api('/api/matters');          // the shell has not told us: ask the same way it does
  chat.matterId = [list.selected_matter_id, list.items?.[0]?.id].find((x) => x != null) ?? null;
  return sameCase(chat.matterId);
}
export const useMatter = (id) => { if (id != null) { chat.matterId = id; sameCase(id); } };

export function setMode(mode) { if (mode === 'fast' || mode === 'deep') { chat.mode = mode; emit('mode'); } }

// ---------------------------------------------------------------- context

const ctxKey = (it) => String(it.key ?? it.id ?? it.node_id ?? (it.kind && it.clio_id != null ? `${it.kind}:${it.clio_id}${it.page ? `#${it.page}` : ''}` : it.label));

// Adds picked items to what the assistant is given. Returns { added, full }.
export function addContext(items) {
  const have = new Set(chat.context.map(ctxKey));
  let added = 0, full = false;
  for (const it of [].concat(items || [])) {
    if (!it || typeof it !== 'object' || have.has(ctxKey(it))) continue;
    if (chat.context.length >= MAX_CONTEXT) { full = true; break; }
    have.add(ctxKey(it)); chat.context.push({ ...it }); added += 1;
  }
  emit('context');
  return { added, full };
}
export function removeContext(item) { chat.context = chat.context.filter((x) => x !== item); emit('context'); }
export function clearContext() { chat.context = []; emit('context'); }

// ---------------------------------------------------------------- turns

const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const firstDocument = (blocks) => (blocks || []).find((b) => b?.type === 'document') || null;

export async function ask(text, mode, { fresh = false } = {}) {
  const message = String(text || '').trim();
  if (!message || chat.busy) return null;
  if (mode) setMode(mode);
  const turn = { question: message, mode: chat.mode, state: 'running', status: 'Sending', steps: [], blocks: [], answer: null, error: null, t0: performance.now(), firstEventMs: null };
  chat.turns.push(turn);
  emit('turn', turn);
  const ctrl = new AbortController();
  chat.busy = ctrl; emit('busy');
  try {
    const id = await matterId();
    if (id == null) throw new Error('no matter is open');
    const answer = await send(id, { message, mode: turn.mode, conversationId: chat.conversationId, context: chat.context, fresh }, { signal: ctrl.signal, onEvent: (ev) => {
      if (turn.firstEventMs == null) turn.firstEventMs = Math.round(performance.now() - turn.t0);
      if (ev.type === 'start') { if (ev.conversationId) chat.conversationId = ev.conversationId; return; }
      if (ev.type === 'status') turn.status = ev.text;
      else if (ev.type === 'step') { turn.steps.push(ev.step); if (ev.step.tool !== 'model') turn.status = cap(ev.step.words); }
      else if (ev.type === 'document') { const had = turn.blocks.findIndex((b) => b?.type === 'document'); if (had >= 0) turn.blocks[had] = ev.block; else turn.blocks.push(ev.block); emit('document', ev.block); }
      else if (ev.type === 'block') { if (ev.block.type === 'document' && firstDocument(turn.blocks)) return; turn.blocks.push(ev.block); }
      emit('update', turn);
    } });
    turn.answer = answer; turn.state = 'done';
    if (answer.conversationId) chat.conversationId = answer.conversationId;
    const doc = firstDocument(answer.blocks);
    if (doc) { if (firstDocument(turn.blocks)?.id !== doc.id) emit('document', doc); window.dispatchEvent(new CustomEvent(CREATED_EVENT, { detail: { id: doc.id } })); }
  } catch (err) {
    turn.state = err?.name === 'AbortError' ? 'stopped' : 'error';
    turn.error = err?.message || 'no reply'; turn.errorStatus = err?.status ?? null;
    if (fresh && err?.status === 422) { chat.canRefresh = false; turn.again = true; }     // this server does not take `fresh` yet
  } finally {
    chat.busy = null;
    if (chat.conversationId) cache.set(chat.conversationId, chat.turns);
    emit('done', turn); emit('busy'); emit('history');
  }
  if (turn.again) return retry(turn);
  return turn.answer;
}

// Works an answer out again in place of the one shown (the server would otherwise return the saved one).
export function regenerate(turn) {
  if (chat.busy) return null;
  const i = chat.turns.indexOf(turn);
  if (i >= 0) chat.turns.splice(i, 1);
  emit('reset');
  return ask(turn.question, turn.mode, { fresh: chat.canRefresh });
}

// Builds one kind of document in code, with no model call, and shows it as a turn of its own.
export async function create(kind, title) {
  if (chat.busy) return null;
  const turn = { question: `Create: ${title}`, mode: chat.mode, state: 'running', status: 'Building the document from the file', steps: [], blocks: [], answer: null, error: null, t0: performance.now(), firstEventMs: null, built: true };
  chat.turns.push(turn);
  emit('turn', turn);
  chat.busy = new AbortController(); emit('busy');
  try {
    const doc = await buildDocument(await matterId(), kind);
    turn.answer = { blocks: [doc], trace: [], notes: [], sources: doc.sources, cost: { ms: Math.round(performance.now() - turn.t0), usd: null }, built: true };
    turn.state = 'done';
    emit('document', doc);
    window.dispatchEvent(new CustomEvent(CREATED_EVENT, { detail: { id: doc.id } }));
  } catch (err) {
    turn.state = 'error'; turn.error = err?.message || 'no reply';
  } finally {
    chat.busy = null;
    emit('done', turn); emit('busy');
  }
  return turn.answer;
}

export function stop() { chat.busy?.abort(); }

// Ask the same thing again in place of an answer that failed or was stopped.
export function retry(turn) {
  if (chat.busy) return null;
  const i = chat.turns.indexOf(turn);
  if (i >= 0) chat.turns.splice(i, 1);
  emit('reset');
  return ask(turn.question, turn.mode);
}

export function newConversation() {
  chat.busy?.abort();
  chat.conversationId = null; chat.turns = []; chat.pendingDoc = null;
  emit('reset');
}

// Shows a past conversation at once from what is held here, then brings it up to date from the server.
export async function openConversation(id) {
  if (!id || id === chat.conversationId) return;
  chat.busy?.abort();
  chat.conversationId = id;
  chat.turns = cache.get(id) || [];
  emit('reset', { loading: !cache.has(id) });
  try {
    const c = await getConversation(await matterId(), id);
    cache.set(id, c.turns);
    if (chat.conversationId === id && !chat.busy) { chat.turns = c.turns; emit('reset'); }
  } catch (err) {
    if (chat.conversationId === id && !chat.turns.length) emit('reset', { error: err?.message || 'could not be opened' });
  }
}

export function forget(id) { cache.delete(id); if (chat.conversationId === id) newConversation(); }
