// The one place that knows the server's shape (shared/assistant_contract.py). The panel and the
// renderers work on the normalised answer below, so a change on the server is a change here only.
//
//   answer = { conversationId, mode, blocks[], trace[], cost, notes[] }
//   block  = { type: 'heading', text } | { type: 'paragraph', sentences[] }
//          | { type: 'table', title, columns: string[], rows: [{ cells: string[], cites[] }] }
//          | { type: 'document', id, kind, title, href, entries[], groups[], totals }
//   sentence = { text, cites[], notInFile, unverified: string[] }
//   cite   = { ref: SourceRef, label, page, nodeId, text }      // nodeId: the graph's node id
//   entry  = { date, dateKind, provider, text, amount, group, cites[] }
//   step   = { n, tool, args, words, note, items, ms, cached, tokensIn, tokensOut, usd, error }
import { api, ApiError } from '../../../js/api.js';

const Q = new URLSearchParams(location.search);
// ?mock=1 mocks the whole app; ?assistant=mock mocks only the assistant, over the live matter and graph.
export const MOCK_ASSISTANT = Q.get('mock') === '1' || Q.get('mock') === 'empty' || Q.get('assistant') === 'mock';
export const MAX_CONTEXT = 12;                      // AssistantRequest.context_items max_length

const base = (matterId) => `/api/matters/${encodeURIComponent(matterId)}/assistant`;
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v) => (v == null ? '' : String(v));
const plural = (n, one, many = `${one}s`) => `${Number(n).toLocaleString('en-US')} ${n === 1 ? one : many}`;
const short = (v, n = 60) => { const s = typeof v === 'string' ? v : JSON.stringify(v); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };

// ---------------------------------------------------------------- request

const CONTEXT_KINDS = new Set(['card', 'passage', 'document', 'claim', 'node', 'record']);

// What the tray holds, in the server's ContextItem shape (it forbids extra members).
export function toContextItem(it) {
  const { kind, id, label, text, data, ...rest } = it;
  const key = id ?? rest.node_id ?? (kind && rest.clio_id != null ? `${kind}:${rest.clio_id}` : null);
  if (kind && !CONTEXT_KINDS.has(kind)) rest.picked_kind = kind;      // what it was on screen (task, conflict, figure…), kept for the server
  return {
    kind: CONTEXT_KINDS.has(kind) ? kind : (text ? 'passage' : (rest.node_id || key) ? 'node' : 'record'),
    id: key != null ? str(key).slice(0, 200) : null,
    label: label || rest.title ? str(label || rest.title).slice(0, 300) : null,
    text: text ? str(text).slice(0, 4000) : null,
    data: data && typeof data === 'object' ? data : (Object.keys(rest).length ? rest : null),
  };
}

// ---------------------------------------------------------------- normalising

function normCite(c) {
  if (!c || typeof c !== 'object') return null;
  const page = num(c.page);
  return {
    id: c.id, label: str(c.label || c.kind || 'Source'), page, nodeId: c.node_id != null ? str(c.node_id) : null, text: c.text || null,
    ref: c.href ? { kind: c.kind, clio_id: c.clio_id, label: str(c.label || c.kind), date: c.date ?? null, page, quote: c.quote ?? null, quote_verified: !!c.quote_verified, href: c.href } : null,
  };
}

// Reference ids to resolved citations, one per source and page.
function resolve(ids, table) {
  const seen = new Set(), out = [];
  for (const id of ids || []) {
    const c = table[id];
    if (!c) continue;
    const k = `${c.nodeId}|${c.page ?? ''}`;
    if (seen.has(k)) continue;
    seen.add(k); out.push(c);
  }
  return out;
}

const DATE_BASIS = { document: 'as printed on the page', record: 'date of the record' };

function normDocument(d, table) {
  return {
    type: 'document', id: d.id, kind: str(d.kind), title: str(d.title || 'Document'), href: d.href || null, pdfHref: d.pdf_href || null, createdAt: d.created_at || null,
    sources: new Set((d.entries || []).flatMap((e) => resolve(e.cite, table)).map((c) => c.nodeId).filter(Boolean)).size,
    entries: (d.entries || []).map((e) => ({
      date: e.date ? str(e.date).slice(0, 10) : null, dateKind: DATE_BASIS[e.date_basis] || str(e.date_basis || ''),
      provider: str(e.provider || ''), providerPrinted: e.provider_as_printed && e.provider_as_printed !== e.provider ? str(e.provider_as_printed) : '', text: str(e.what || ''), amount: num(e.amount), group: str(e.group || ''), kind: str(e.kind || ''),
      cites: resolve(e.cite, table),
    })),
    groups: (d.groups || []).map((g) => ({ label: str(g.label), entries: num(g.entries), first: g.first || null, last: g.last || null, total: num(g.total) })),
    totals: { entries: num(d.totals?.entries), amount: num(d.totals?.amount), undated: num(d.totals?.undated) },
  };
}

function normBlock(b, table) {
  if (!b || typeof b !== 'object') return null;
  if (b.type === 'heading') return { type: 'heading', text: str(b.text) };
  if (b.type === 'table') return { type: 'table', title: str(b.title || ''), columns: (b.columns || []).map(str), rows: (b.rows || []).map((r) => ({ cells: (r.cells || []).map(str), cites: resolve(r.cite, table) })) };
  if (b.type === 'document') return b.document ? normDocument(b.document, table) : null;
  return { type: 'paragraph', sentences: (b.sentences || []).map((s) => {
    const cites = resolve(s.cite, table);
    return { text: str(s.text), cites, notInFile: s.grounded === false || (!cites.length && !s.grounded), unverified: (s.figures_unverified || []).map(str) };
  }) };
}

const KIND_WORDS = { note: 'notes', communication: 'emails and calls', task: 'tasks', calendar_entry: 'calendar entries', expense: 'expenses', document: 'documents', contact: 'people', custom_field: 'matter fields' };
const DOC_WORDS = { medical_chronology: 'medical chronology', records_summary: 'summary of records', damages_summary: 'summary of damages' };

// The tool call in plain words, from its name and arguments. `names` resolves a record id to its title.
function words(tool, a, items, names) {
  // The model sometimes passes a bare number for a document: that is the node "document:<number>".
  const name = (id) => (id == null ? null : names?.[id] || names?.[String(id).includes(':') ? id : `document:${id}`] || null);
  const got = `: ${plural(items ?? 0, 'result')}`;
  const pages = Array.isArray(a.pages) && a.pages.length ? (a.pages.length > 1 ? `pages ${Math.min(...a.pages)}-${Math.max(...a.pages)} of ` : `page ${a.pages[0]} of `) : '';
  const range = a.date_from || a.date_to ? `, ${a.date_from || 'the start'} to ${a.date_to || 'now'}` : '';
  switch (tool) {
    case 'model': return 'thought about the request';
    case 'file_overview': return 'looked at what the file holds';
    case 'search_file': return `searched the file for “${short(a.query ?? '')}”${a.kinds?.length ? ` in ${a.kinds.map((k) => KIND_WORDS[k] || k).join(', ')}` : ''}${got}`;
    case 'get_claims': return `opened ${plural((a.ids || []).length, 'fact')} already established from the file`;
    case 'get_record': { const ids = a.ids || []; return ids.length === 1 && name(ids[0]) ? `opened ${short(name(ids[0]))}` : `opened ${plural(ids.length, 'record')}`; }
    case 'get_document': return `read ${pages}${short(name(a.id) || 'a document')}`;
    case 'list_records': return `listed ${KIND_WORDS[a.kind ?? a.record_kind] || 'records'}${a.contains ? ` containing “${short(a.contains, 40)}”` : ''}${range}${got}`;
    case 'case_figures': return 'looked up the case figures, as added in code';
    case 'app_guide': return `looked up how to do it in the application guide${a.query ? ` (“${short(a.query, 40)}”)` : ''}`;
    case 'differences': return `looked for where the notes and the documents differ${got}`;
    case 'agenda': return `looked at what is overdue, coming up and waiting on someone${got}`;
    case 'providers': return `listed the treating providers${got}`;
    case 'timeline': return `read the dated events in the file${a.provider ? ` for ${short(a.provider, 40)}` : ''}${range}${got}`;
    case 'graph_neighbours': return `followed the links from ${short(name(a.id) || 'a record')}${got}`;
    case 'compose_document': return `built the ${DOC_WORDS[a.document_kind || a.kind] || 'document'}${a.provider ? ` for ${short(a.provider, 40)}` : ''}${range}`;
    default: break;
  }
  const rest = Object.entries(a).slice(0, 2).map(([k, v]) => `${k.replace(/_/g, ' ')} ${short(v, 40)}`).join(', ');
  return `${tool.replace(/_/g, ' ')}${rest ? ` (${rest})` : ''}${items != null ? got : ''}`;
}

export function normStep(s, names) {
  if (!s || typeof s !== 'object') return null;
  const tool = str(s.tool || 'step'), args = s.arguments && typeof s.arguments === 'object' ? s.arguments : {};
  return {
    n: num(s.n), round: num(s.round), tool, args, words: words(tool, args, num(s.items), names), note: s.summary ? str(s.summary) : null,
    items: tool === 'model' ? null : num(s.items), ms: num(s.ms), cached: !!s.cached,
    tokensIn: num(s.input_tokens), tokensOut: num(s.output_tokens), usd: num(s.cost_usd), error: s.ok === false || s.error ? str(s.error || 'failed') : null,
  };
}

function tableOf(citations) {
  const table = {};
  for (const [id, c] of Object.entries(citations || {})) { const n = normCite(c); if (n) table[id] = n; }
  return table;
}

export function normAnswer(t) {
  const table = tableOf(t.citations), names = {};
  for (const n of Object.values(table)) if (n.nodeId) names[n.nodeId] = n.label;
  const u = t.usage || {};
  return {
    id: t.turn_id || null, conversationId: t.conversation_id || null, mode: t.mode || null, question: str(t.question || ''), at: t.at || null,
    sources: new Set(Object.values(table).map((n) => n.nodeId).filter(Boolean)).size,
    blocks: (t.blocks || []).map((b) => normBlock(b, table)).filter(Boolean),
    trace: (t.trace || []).map((s) => normStep(s, names)).filter(Boolean).map((s, i, all) => (s.tool === 'model' ? { ...s, words: i === all.length - 1 ? 'wrote the answer' : 'decided what to read next' } : s)),
    cost: { usd: num(u.cost_usd), tokensIn: num(u.input_tokens), tokensOut: num(u.output_tokens), cachedTokens: num(u.cached_tokens), ms: num(u.seconds) != null ? Math.round(u.seconds * 1000) : null,
      model: t.model || null, toolCalls: num(u.tool_calls), modelCalls: num(u.model_calls) },
    notes: (t.warnings || []).map(str),
  };
}

// ---------------------------------------------------------------- transport

async function post(url, body, signal, retried = false) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream, application/json' }, body: JSON.stringify(body), signal });
  if (res.status === 401 && !retried) { await api('/api/matters'); return post(url, body, signal, true); }   // api() owns the firm sign-in prompt
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  return res;
}

const ROUND_WORDS = { thinking: 'Deciding what to read', writing: 'Writing the answer', reading: 'Reading the file' };

// One server event (status, start, round, tool, document, block, done, error) as the view's event, or the final answer.
function onServerEvent(name, d, sink) {
  if (name === 'tool') { const step = normStep(d); if (step) sink.onEvent?.({ type: 'step', step }); }
  else if (name === 'round' || name === 'status') sink.onEvent?.({ type: 'status', text: ROUND_WORDS[d.state] || 'Reading the file' });
  else if (name === 'document') { const doc = d.block?.document || d.document; if (doc) sink.onEvent?.({ type: 'document', block: normDocument(doc, tableOf(d.citations)) }); }
  else if (name === 'block') { const block = normBlock(d.block, tableOf(d.citations)); if (block) sink.onEvent?.({ type: 'block', index: num(d.index), block }); }
  else if (name === 'start') sink.onEvent?.({ type: 'start', conversationId: d.conversation_id || null, model: d.model || null });
  else if (name === 'done') sink.final = normAnswer(d);
  else if (name === 'error') throw new ApiError(503, str(d.message || 'The assistant stopped.'));
}

async function readStream(res, sink) {
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '', name = '';
  const line = (raw) => {
    const l = raw.replace(/\r$/, '');
    if (!l) { name = ''; return; }
    if (l.startsWith(':')) return;                                   // keep-alive comment
    if (l.startsWith('event:')) { name = l.slice(6).trim(); return; }
    if (!l.startsWith('data:')) return;
    let d; try { d = JSON.parse(l.slice(5)); } catch { return; }
    onServerEvent(name, d, sink);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) { line(buf.slice(0, i)); buf = buf.slice(i + 1); }
  }
  if (buf) line(buf);
  if (!sink.final) throw new ApiError(502, 'The answer was cut off before it finished.');
  return sink.final;
}

// Ask the assistant. `onEvent` receives { type: 'start' | 'status' | 'step' } while it works.
export async function send(matterId, { message, mode, conversationId, context, fresh }, { onEvent, signal } = {}) {
  const req = { message, mode, conversation_id: conversationId || null, context_items: (context || []).slice(0, MAX_CONTEXT).map(toContextItem), stream: true };
  if (fresh) req.fresh = true;                       // work the answer out again instead of returning the saved one
  const sink = { onEvent, final: null };
  if (MOCK_ASSISTANT) {
    const { mockSend } = await import('./mock.js');
    return normAnswer(await mockSend(matterId, req, { signal, emit: (name, d) => onServerEvent(name, d, sink) }));
  }
  const res = await post(base(matterId), req, signal);
  if (/event-stream/.test(res.headers.get('content-type') || '') && res.body) return readStream(res, sink);
  return normAnswer(await res.json());
}

// ---------------------------------------------------------------- history, starting points, documents

const asTurn = (t) => ({ question: str(t.question || ''), mode: t.mode || 'fast', state: 'done', steps: [], blocks: [], answer: normAnswer(t) });

// Conversations on this matter, newest activity first: [{ id, title, updatedAt, turns, documents }].
export async function listConversations(matterId) {
  if (matterId == null) return [];   // no case is open (first run): nothing to ask the server
  const rows = MOCK_ASSISTANT ? (await import('./mock.js')).mockConversations() : await api(`${base(matterId)}/conversations`);
  return (Array.isArray(rows) ? rows : []).map((r) => ({ id: str(r.id), title: str(r.title || 'Untitled'), updatedAt: r.updated_at || r.created_at || null, turns: num(r.turns) ?? 0, documents: r.documents || [] }));
}

export async function getConversation(matterId, id) {
  const c = MOCK_ASSISTANT ? (await import('./mock.js')).mockConversation(id) : await api(`${base(matterId)}/conversations/${encodeURIComponent(id)}`);
  return { id: str(c.id), title: str(c.title || ''), turns: (c.turns || []).map(asTurn) };
}

export async function renameConversation(matterId, id, title) {
  if (MOCK_ASSISTANT) { (await import('./mock.js')).mockRename(id, title); return; }
  await api(`${base(matterId)}/conversations/${encodeURIComponent(id)}`, { method: 'PUT', body: { title } });
}

// Builds a document in code from the stored file, with no model call, and returns it normalised.
export async function buildDocument(matterId, kind) {
  const saved = MOCK_ASSISTANT ? await (await import('./mock.js')).mockBuild(matterId, kind) : await api(`${base(matterId)}/documents`, { method: 'POST', body: { kind } });
  return normDocument(saved.document, tableOf(saved.citations));
}

export async function deleteConversation(matterId, id) {
  if (MOCK_ASSISTANT) { (await import('./mock.js')).mockForget(id); return; }
  const res = await fetch(`${base(matterId)}/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
}

// The kinds of document the assistant can build, each with the request that asks for it: [{ kind, title, description, prompt }].
export async function documentKinds(matterId) {
  if (matterId == null) return [];   // no case is open (first run): nothing to ask the server
  let rows;
  try { rows = MOCK_ASSISTANT ? (await import('./mock.js')).mockKinds() : await api(`${base(matterId)}/document-kinds`); } catch { rows = []; }
  return (Array.isArray(rows) ? rows : []).filter((r) => r?.prompt).map((r) => ({ kind: str(r.kind), title: str(r.title || r.kind), description: str(r.description || ''), prompt: str(r.prompt) }));
}

// Documents the assistant made are stored by the server when the turn ends; these read them back.
export async function listDocuments(matterId) {
  if (matterId == null) return [];   // no case is open (first run): nothing to ask the server
  if (MOCK_ASSISTANT) return [];
  return api(`${base(matterId)}/documents`);
}

export async function getDocument(matterId, idOrHref) {
  const saved = await api(String(idOrHref).startsWith('/') ? idOrHref : `${base(matterId)}/documents/${encodeURIComponent(idOrHref)}`);
  const table = {};
  for (const [id, c] of Object.entries(saved.citations || {})) { const n = normCite(c); if (n) table[id] = n; }
  return normDocument(saved.document, table);
}

export async function documentIsSaved(href) {
  if (MOCK_ASSISTANT) return true;
  if (!href) return false;
  await api(href);
  return true;
}
