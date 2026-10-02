// Stand-in for POST /api/matters/{id}/assistant (?mock=1 or ?assistant=mock), in the server's own
// shape (shared/assistant_contract.py) so the real adapter is what gets exercised. Every sentence is
// a placeholder; the only things taken from the open matter are source references and provider
// names found in the case model at run time, so chips open real sources and light real graph nodes.
import { api } from '../../../js/api.js';

const BASE = Date.UTC(2000, 0, 3);            // neutral placeholder dates
const day = (i) => new Date(BASE + i * 864e5).toISOString().slice(0, 10);

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) { reject(new DOMException('Stopped', 'AbortError')); return; }
  const t = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Stopped', 'AbortError')); }, { once: true });
});

// Every SourceRef-shaped object in the case model, documents first, one per record and page.
function collectRefs(model) {
  const seen = new Map();
  const walk = (v, depth) => {
    if (!v || typeof v !== 'object' || depth > 8 || seen.size > 400) return;
    if (Array.isArray(v)) { v.forEach((x) => walk(x, depth + 1)); return; }
    if (typeof v.href === 'string' && v.kind && v.clio_id != null) { const k = `${v.kind}:${v.clio_id}:${v.page ?? ''}`; if (!seen.has(k)) seen.set(k, v); return; }
    Object.values(v).forEach((x) => walk(x, depth + 1));
  };
  walk(model, 0);
  const refs = [...seen.values()];
  return refs.filter((r) => r.kind === 'document').concat(refs.filter((r) => r.kind !== 'document'));
}

function providerNames(model) {
  const names = (model.providers || []).map((p) => p?.contact?.name).filter(Boolean).slice(0, 6);
  const out = names.map((n, i) => (names.indexOf(n) === i && names.lastIndexOf(n) === i ? n : `${n} ${String.fromCharCode(65 + i)}`));
  return out.length ? out : ['Placeholder provider A', 'Placeholder provider B'];
}

export async function mockSend(matterId, req, { emit, signal } = {}) {
  const t0 = performance.now();
  const deep = req.mode === 'deep';
  const conversation = req.conversation_id || `mock-${Date.now()}`;
  emit('status', { state: 'reading' });
  emit('start', { conversation_id: conversation, turn_id: `turn-${Date.now()}`, mode: req.mode, model: 'placeholder-model' });
  let model = {};
  try { model = await api(`/api/matters/${encodeURIComponent(matterId)}/case`); } catch { /* an empty model still exercises the panel */ }
  const refs = collectRefs(model), providers = providerNames(model);

  const citations = {};
  const cite = (i) => {
    if (!refs.length) return [];
    const r = refs[i % refs.length], id = `ref-${i % refs.length}`;
    citations[id] = { id, claim_id: null, node_id: `${r.kind}:${r.clio_id}`, kind: r.kind, clio_id: r.clio_id, page: r.page ?? null, label: r.label, date: r.date ?? null,
      text: 'Placeholder statement.', quote: r.quote ?? null, quote_verified: !!r.quote_verified, href: r.href };
    return [id];
  };
  const nodeOf = (i) => (refs.length ? `${refs[i % refs.length].kind}:${refs[i % refs.length].clio_id}` : 'document:0');

  const wantsDoc = /chronolog|timeline/i.test(req.message), wantsTable = /provider|outstanding|whom|table/i.test(req.message);
  const pace = deep ? 520 : 240;
  const calls = [
    { tool: 'file_overview', arguments: {}, items: 8, summary: 'placeholder overview' },
    { tool: 'search_file', arguments: { query: req.message, limit: 20 }, items: Math.min(12, refs.length || 2), summary: 'placeholder matches' },
    { tool: 'get_document', arguments: { id: nodeOf(0), pages: deep ? [1, 2, 3, 4, 5, 6] : [1, 2] }, items: deep ? 6 : 2, summary: 'placeholder pages' },
    ...(deep ? [{ tool: 'get_document', arguments: { id: nodeOf(1), pages: [3, 4, 5] }, items: 3, summary: 'placeholder pages' },
      { tool: 'list_records', arguments: { kind: 'communication', limit: 40 }, items: 9, summary: 'placeholder records' }] : []),
    ...(wantsDoc ? [{ tool: 'compose_document', arguments: { document_kind: 'medical_chronology' }, items: deep ? 22 : 14, summary: 'entries built in code' }] : []),
  ];
  const trace = [];
  const note = (step) => { trace.push(step); emit('tool', step); };
  let n = 0;
  emit('round', { round: 1, state: 'thinking' });
  await sleep(pace, signal);
  note({ n: ++n, round: 1, tool: 'model', arguments: {}, items: calls.length, ms: pace, cached: false, ok: true, error: null, summary: 'Placeholder plan: read the overview, search, then open the pages.', input_tokens: 1200, output_tokens: 90, cost_usd: deep ? 0.004 : 0.001 });
  for (const [i, c] of calls.entries()) {
    await sleep(pace, signal);
    note({ n: ++n, round: i < 2 ? 0 : 1, ...c, ms: Math.round(8 + i * 6.5), cached: i === 1, ok: true, error: null, input_tokens: null, output_tokens: null, cost_usd: null });
  }
  emit('round', { round: 2, state: 'writing' });
  await sleep(pace * 2, signal);
  note({ n: ++n, round: 2, tool: 'model', arguments: {}, items: 0, ms: pace * 2, cached: false, ok: true, error: null, summary: 'Placeholder: wrote the answer.', input_tokens: 5200, output_tokens: 420, cost_usd: deep ? 0.012 : 0.003 });

  const blocks = [{ type: 'paragraph', sentences: [
    { text: 'Placeholder sentence one, drawn from a page in the file.', cite: cite(0), grounded: true, figures_unverified: [] },
    { text: 'Placeholder sentence two, supported by two sources.', cite: [...cite(1), ...cite(2)], grounded: true, figures_unverified: [] },
    { text: 'Placeholder sentence three, which no page in the file supports.', cite: [], grounded: false, figures_unverified: [] },
  ] }];
  if (wantsDoc) {
    const count = deep ? 22 : 14;
    const entries = Array.from({ length: count }, (_, i) => ({
      date: i === 9 ? null : day(i * 11), date_basis: i === 9 ? null : (i % 3 === 2 ? 'record' : 'document'), provider: providers[i % providers.length],
      what: `Placeholder entry ${i + 1}: what the record says happened on this date.`, provider_as_printed: i === 1 ? `${providers[i % providers.length]}, as printed` : null, amount: null, group: providers[i % providers.length], kind: 'treatment',
      source_label: null, page: null, cite: cite(i),
    }));
    blocks.push({ type: 'document', document: { id: `doc-${Date.now()}`, kind: 'medical_chronology', title: 'Medical chronology', created_at: new Date().toISOString(), ledger_version: 'placeholder',
      conversation_id: conversation, columns: [], entries, groups: [], totals: { entries: count, amount: null, undated: 1 }, href: `/api/matters/${matterId}/assistant/documents/placeholder` } });
  } else if (wantsTable) {
    blocks.push({ type: 'table', title: 'Placeholder table', columns: ['Provider', 'Records', 'Last entry'],
      rows: providers.map((p, i) => ({ cells: [p, `${3 + i} placeholder items`, day(i * 40)], cite: cite(i) })) });
  } else {
    blocks.push({ type: 'heading', text: 'Placeholder heading' }, { type: 'paragraph', sentences: [0, 1].map((i) => ({ text: i ? 'Placeholder point 2, with a figure of $0 in it.' : 'Placeholder point 1.', cite: cite(i + 3), grounded: true, figures_unverified: i ? ['$0'] : [] })) });
  }

  for (const [index, block] of blocks.entries()) {
    emit(block.type === 'document' ? 'document' : 'block', { index, block, citations });
    await sleep(60, signal);
  }
  const sum = (k) => trace.reduce((a, s) => a + (s[k] || 0), 0);
  const turn = { contract_version: '0.1.0', conversation_id: conversation, turn_id: `turn-${Date.now()}`, at: new Date().toISOString(), mode: req.mode, model: 'placeholder-model',
    question: req.message, ledger_version: 'placeholder', blocks, citations, trace,
    usage: { rounds: 2, tool_calls: calls.length, model_calls: 2, input_tokens: sum('input_tokens'), cached_tokens: 0, output_tokens: sum('output_tokens'), cost_usd: sum('cost_usd'),
      seconds: (performance.now() - t0) / 1000, model_seconds: 0, tool_ms: 0 },
    warnings: [] };
  if (req.quiet) return turn;
  if (!held.has(conversation)) held.set(conversation, { id: conversation, title: req.message.slice(0, 80), created_at: turn.at, turns: [] });
  const row = held.get(conversation);
  row.turns.push(turn); row.updated_at = turn.at;
  return turn;
}

// History and starting points for the stand-in: what was asked in this page load, and three generic kinds.
const held = new Map();
export const mockConversations = () => [...held.values()].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
  .map((c) => ({ id: c.id, title: c.title, created_at: c.created_at, updated_at: c.updated_at, turns: c.turns.length, documents: [] }));
export const mockConversation = (id) => held.get(id) || { id, title: '', turns: [] };
export const mockForget = (id) => held.delete(id);
export const mockRename = (id, title) => { const c = held.get(id); if (c) c.title = title; };
export async function mockBuild(matterId, kind) {
  const turn = await mockSend(matterId, { message: 'chronology', mode: 'fast', quiet: true }, { emit: () => {} });
  const document = turn.blocks.find((b) => b.type === 'document').document;
  const title = mockKinds().find((k) => k.kind === kind)?.title || document.title;
  return { document: { ...document, kind, title }, citations: turn.citations };
}
export const mockKinds = () => [
  { kind: 'medical_chronology', title: 'Medical chronology', description: 'Placeholder description.', prompt: 'Build a medical chronology' },
  { kind: 'records_summary', title: 'Summary of records', description: 'Placeholder description.', prompt: 'Summarise records by provider' },
  { kind: 'damages_summary', title: 'Summary of damages', description: 'Placeholder description.', prompt: 'Summarise the damages in a table' },
];
