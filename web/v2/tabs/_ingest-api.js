// The one place that knows the upload and ingestion routes.
// Real: POST /documents/upload (multipart, field `file`), then GET /ingestions/{id}.
// With ?mock=1 nothing leaves the browser: a short invented run stands in for the server.
const MOCK = ['1', 'empty'].includes(new URLSearchParams(location.search).get('mock'));
const base = (ctx) => `/api/matters/${ctx.matterId}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const fakeRuns = new Map();
function fakeStep(file, id, tick) {
  const total = 12;
  const stages = ['stored', 'fingerprint', 'read', 'reconcile', 'done'];
  const stage = stages[Math.min(tick, 4)];
  const done = tick >= 4;
  const known = done ? 9 : Math.min(tick * 3, 9);
  return {
    id, name: file.name, document_id: -1000 - id, origin: 'uploaded', state: done ? 'complete' : 'running', stage,
    counts: { pages_total: total, pages_skipped: known, pages_identical: known, pages_text: done ? 2 : 0, pages_model: done ? 1 : 0, claims_added: done ? 4 : 0, cards_touched: done ? 1 : 0, model_calls: done ? 1 : 0, cost_usd: done ? 0.02 : 0, seconds: tick },
    duplicate_of: null, error: null,
    summary: done ? 'Placeholder run: 9 of 12 pages were already known and cost nothing, 2 were read as text, 1 by the model. 4 claims added.' : null,
    saving: done ? { from_scratch_cost_usd: 0.09, actual_cost_usd: 0.02, saved_usd: 0.07 } : null,
  };
}

export async function startUpload(ctx, file) {
  if (MOCK) { const id = Date.now() % 100000; fakeRuns.set(id, { file, tick: 0 }); return fakeStep(file, id, 0); }
  const body = new FormData(); body.append('file', file);
  const res = await fetch(`${base(ctx)}/documents/upload`, { method: 'POST', body });
  if (!res.ok) { let d = res.statusText; try { const j = await res.json(); d = typeof j.detail === 'string' ? j.detail : d; } catch { /* keep */ } throw new Error(d); }
  return res.json();
}

export async function getIngestion(ctx, id) {
  if (MOCK) { await sleep(700); const r = fakeRuns.get(id); r.tick += 1; return fakeStep(r.file, id, r.tick); }
  return ctx.api(`${base(ctx)}/ingestions/${id}`);
}

// In mock mode the server's list does not change, so the finished document is added to the local copy.
export function afterComplete(ctx, run) {
  if (!MOCK || !ctx.caseModel?.documents) return false;
  ctx.caseModel.documents.unshift({ id: run.document_id, name: run.name, folder: null, page_count: run.counts.pages_total, has_text_layer: true,
    received_at: new Date().toISOString(), source: { kind: 'document', clio_id: run.document_id, label: run.name, href: '/x' }, origin: 'uploaded' });
  return true;
}

// ---- new case: create, import from a zip, import from a connected system.
// Shapes follow the route owners' messages; every field name is read here and nowhere else.
import { api } from '../../js/api.js';

const SKIPPED = ['skipped'];
const KEPT = ['not_read'];
const DUPES = ['duplicate', 'already_in_file'];
function normStatus(r) {
  // Real shape (ingest): files{total,done,...}, pages{total,...}, current{name}, items[{name,outcome,reason}], model, summary.
  // The mock below produces the same shape through this one function.
  const items = Array.isArray(r.items) ? r.items : [];
  const f = r.files || {};
  const waiting = r.model === 'unavailable' || (f.waiting_to_be_read || 0) > 0;
  return {
    state: r.state === 'complete' ? 'complete' : r.state === 'failed' ? 'failed' : r.state === 'queued' ? 'queued' : 'running',
    total: f.total ?? 0,
    done: f.done ?? 0,
    skippedCount: f.skipped ?? items.filter((i) => SKIPPED.includes(i.outcome)).length,
    skipped: items.filter((i) => SKIPPED.includes(i.outcome)).map((i) => ({ name: i.name || i.path || '', reason: i.reason || '' })),
    kept: items.filter((i) => KEPT.includes(i.outcome)).map((i) => ({ name: i.name || i.path || '', reason: i.reason || '' })),
    storedCount: (f.stored ?? 0) + (f.already_in_file ?? 0) + (f.not_read ?? 0),
    duplicates: items.filter((i) => DUPES.includes(i.outcome)).map((i) => i.name || i.path || ''),
    pages: r.pages?.total ?? null,
    current: r.current?.name || null,
    matterId: r.matter_id ?? null,
    summary: r.summary || null,
    waiting: waiting ? (r.summary || 'Everything is stored. Some documents are waiting to be read.') : null,
    error: r.error || null,
  };
}

const mockJobs = new Map();
const MOCK_FILES = Array.from({ length: 12 }, (_, i) => `Invented file ${String(i + 1).padStart(2, '0')}.pdf`);
function mockStatus(job) {
  job.tick += 1;
  const done = Math.min(12, job.tick);
  const finished = done >= 12 && job.tick >= 13;
  const items = [];
  if (done >= 6) items.push({ name: MOCK_FILES[4], outcome: 'skipped', reason: 'Not a supported file type' });
  if (done >= 9) items.push({ name: MOCK_FILES[8], outcome: 'skipped', reason: 'Password protected' });
  if (done >= 10) items.push({ name: MOCK_FILES[2], outcome: 'already_in_file', reason: null });
  return normStatus({
    state: finished ? 'complete' : 'running', matter_id: job.matterId, files: { total: 12, done, stored: Math.max(0, done - items.length), already_in_file: items.filter((i) => i.outcome === 'already_in_file').length, skipped: items.filter((i) => i.outcome === 'skipped').length, not_read: 0, waiting_to_be_read: finished ? 9 : 0 },
    pages: { total: done * 7 }, current: finished ? null : { name: MOCK_FILES[Math.min(done, 11)] }, items, model: finished ? 'unavailable' : 'used',
    summary: finished ? 'Placeholder summary: 12 files, 9 stored and waiting to be read because the reading service is unavailable.' : null,
  });
}

export async function createEmptyCase(meta) {
  if (MOCK) { await sleep(300); return { id: 9001 }; }
  const r = await api('/api/matters', { method: 'POST', body: { name: meta.name, client_name: meta.client || undefined, number: meta.number || undefined } });
  return { id: r.id ?? r.matter_id };
}

export async function startZipImport(file, meta) {
  if (MOCK) { await sleep(300); const id = Date.now() % 100000; mockJobs.set(id, { tick: 0, matterId: 9002 }); return { jobId: id, matterId: 9002 }; }
  const fd = new FormData();
  fd.append('file', file); fd.append('name', meta.name);
  if (meta.client) fd.append('client_name', meta.client);
  if (meta.number) fd.append('number', meta.number);
  const res = await fetch('/api/matters/import/zip', { method: 'POST', body: fd });
  if (!res.ok) { let d = res.statusText; try { const j = await res.json(); d = typeof j.detail === 'string' ? j.detail : d; } catch { /* keep */ } const e = new Error(d); e.status = res.status; e.hasDetail = d !== res.statusText; throw e; }
  const j = await res.json();
  return { jobId: j.id, matterId: j.matter_id ?? null };
}

export async function zipImportStatus(matterId, jobId) {
  if (MOCK) { await sleep(600); return mockStatus(mockJobs.get(jobId)); }
  return normStatus(await api(`/api/matters/${matterId}/imports/${jobId}`));
}

// Matters in the connected account that are not in the product yet.
export async function connectedMatters() {
  if (MOCK) { await sleep(200); return { connected: true, items: [{ id: 7001, name: 'Invented matter A', client: 'Invented client' }, { id: 7002, name: 'Invented matter B', client: null }] }; }
  const r = await api('/api/matters');
  return { connected: !!r.connected, items: (r.items || []).filter((m) => m.source === 'clio' && !m.synced_at).map((m) => ({ id: m.id, name: m.display_number ? `${m.display_number} ${m.description || ''}`.trim() : (m.description || String(m.id)), client: m.client_name || null })) };
}

export async function importConnected(id) {
  if (MOCK) { await sleep(900); return { id }; }
  await api(`/api/matters/${id}/import`, { method: "POST" });
  return { id };
}
