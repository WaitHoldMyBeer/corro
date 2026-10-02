// The document the assistant made, shown as a PDF beside the conversation. The server renders the PDF
// on demand from the saved document; nothing is added to the matter's own documents. The browser's
// own PDF viewer draws it (a blob URL in an <iframe>), so there is no library here.
//
//   openDocument(doc, { mount, matterId, opener?, onClose? }) -> { close(), node }
//   closeDocument()
//   downloadDocument(doc, matterId)
//
// `doc` needs an `id`; title, kind, created time and entries are shown when it has them (a normalised
// document block from client.js and a row of the files list both work).
import { el, fmtMoney, fmtDateTime } from '../../../js/util.js';
import { api, ApiError } from '../../../js/api.js';
import { loadCss } from '../../tabs/_css.js';
import { MOCK_ASSISTANT, getDocument } from './client.js';

export const VIEWER_EVENT = 'assistant:viewer';      // window event, detail { id } on open and { id: null } on close
const WIDTH_KEY = 'v2.assistant.viewer.width';
const MIN_W = 380, DEFAULT_W = 520, STEP = 24;
const KIND = { medical_chronology: 'Medical chronology', records_summary: 'Summary of records', damages_summary: 'Summary of damages' };

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const plural = (n, one, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const dot = (...parts) => parts.filter(Boolean).join(' · ');
// The server names the kinds it can build (GET …/assistant/document-kinds); its titles win over the words above.
const kindTitles = new Map();
export function learnKinds(list) { for (const k of Array.isArray(list) ? list : []) if (k?.kind && k.title) kindTitles.set(String(k.kind), String(k.title)); }
export const kindWords = (k) => kindTitles.get(k) || KIND[k] || (k ? String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Document');

// What the viewer and the files list need from a document, whichever shape it arrived in.
export function describe(doc) {
  const list = Array.isArray(doc.entries) ? doc.entries : null;
  return {
    id: String(doc.id), title: String(doc.title || 'Document'), kind: doc.kind || '', createdAt: doc.createdAt || doc.created_at || null,
    conversationId: doc.conversationId || doc.conversation_id || null,
    entries: list, entryCount: list ? list.length : num(doc.entries) ?? num(doc.entry_count) ?? num(doc.totals?.entries),
    sourceCount: num(doc.citations) ?? num(doc.citation_count) ?? num(doc.sources),
    pages: num(doc.pages) ?? num(doc.page_count), pdfHref: doc.pdf_href || doc.pdfHref || null,
  };
}

export const pdfHref = (d, matterId) => d.pdfHref || `/api/matters/${encodeURIComponent(matterId)}/assistant/documents/${encodeURIComponent(d.id)}.pdf`;
const fileName = (title) => `${String(title).replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').trim().slice(0, 80) || 'document'}.pdf`;

// ---------------------------------------------------------------- the PDF

// Pages as the file itself declares them, when it says so in the clear.
async function countPages(blob) {
  if (blob.size > 20e6) return null;
  try { return (new TextDecoder('latin1').decode(await blob.arrayBuffer()).match(/\/Type\s*\/Page\b/g) || []).length || null; } catch { return null; }
}

async function fetchPdf(url, signal, retried = false) {
  const res = await fetch(url, { headers: { Accept: 'application/pdf' }, signal });
  if (res.status === 401 && !retried) { await api('/api/matters'); return fetchPdf(url, signal, true); }   // api() owns the firm sign-in prompt
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail || `the server answered ${res.status}`);
  }
  const raw = await res.blob();
  if ((await raw.slice(0, 5).text()) !== '%PDF-') throw new ApiError(502, 'the server did not return a PDF');
  const blob = raw.type === 'application/pdf' ? raw : new Blob([raw], { type: 'application/pdf' });
  return { blob, pages: num(Number(res.headers.get('x-page-count') || NaN)) ?? await countPages(blob) };
}

// Mock mode: a small PDF assembled by hand in the browser. Every word in it is a placeholder.
const mockLines = (n) => Array.from({ length: n }, (_, i) => `Placeholder entry ${i + 1}: placeholder words stand here in place of a line of the document.`);
function mockPdf(title, lines) {
  const esc = (s) => String(s).replace(/[^\x20-\x7e]/g, '?').replace(/([\\()])/g, '\\$1');
  const per = 28, pages = [];
  for (let i = 0; i < lines.length; i += per) pages.push(lines.slice(i, i + per));
  if (!pages.length) pages.push([]);
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${pages.map((_, i) => `${5 + i * 2} 0 R`).join(' ')}] /Count ${pages.length} >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>',
  ];
  pages.forEach((rows, p) => {
    const text = ['BT', '/F2 16 Tf', '72 724 Td', `(${esc(p ? `${title}, continued` : title)}) Tj`, '/F1 9 Tf', '0 -16 Td',
      '(Placeholder PDF made in the browser for mock mode. Nothing in it comes from a matter.) Tj', '0 -8 Td', ...rows.flatMap((r) => ['0 -21 Td', `(${esc(r)}) Tj`]), 'ET'].join('\n');
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents ${6 + p * 2} 0 R >>`);
    objs.push(`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);
  });
  let out = '%PDF-1.4\n';
  const at = objs.map((o, i) => { const pos = out.length; out += `${i + 1} 0 obj\n${o}\nendobj\n`; return pos; });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${at.map((n) => `${String(n).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new Blob([out], { type: 'application/pdf' });
}

async function mockFetch(d, signal) {
  await new Promise((resolve, reject) => { const t = setTimeout(resolve, 500); signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Stopped', 'AbortError')); }, { once: true }); });
  const blob = mockPdf(d.title, mockLines(Math.min(d.entryCount ?? 12, 60) || 12));
  return { blob, pages: await countPages(blob) };
}

const getPdf = (d, matterId, signal, download = false) => (MOCK_ASSISTANT ? mockFetch(d, signal) : fetchPdf(`${pdfHref(d, matterId)}${download ? '?download=1' : ''}`, signal));

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name, hidden: true });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function downloadDocument(doc, matterId) {
  const d = describe(doc);
  saveBlob((await getPdf(d, matterId, undefined, true)).blob, fileName(d.title));
}

// ---------------------------------------------------------------- the text, when the PDF cannot be drawn

async function entriesOf(d, matterId) {
  if (d.entries?.length) return d.entries;
  if (MOCK_ASSISTANT) return mockLines(Math.min(d.entryCount ?? 12, 60) || 12).map((text) => ({ text }));
  return (await getDocument(matterId, d.id)).entries;
}

function textBlocks(entries) {
  if (!entries.length) return el('p', { class: 'empty', text: 'This document has no entries.' });
  return el('ol', { class: 'av-text' }, entries.map((e) => el('li', { class: 'av-text-row' },
    el('span', { class: 'av-text-when', text: e.date ? String(e.date).slice(0, 10) : '' }),
    el('span', { class: 'av-text-what' }, e.provider ? el('strong', { text: `${e.provider} ` }) : null, String(e.text ?? e.what ?? ''),
      num(e.amount) != null ? el('span', { class: 'av-text-amt', text: ` ${fmtMoney(e.amount)}` }) : null))));
}

// ---------------------------------------------------------------- width

// Half the window at most, and never more than the row can give once the pane's neighbours keep their own minimum.
function maxW(mount) {
  let room = Infinity;
  const row = mount?.parentElement;
  if (row) {
    let taken = 0;
    for (const kid of row.children) {
      if (kid === mount) continue;
      const cs = getComputedStyle(kid);
      if (cs.display === 'none' || cs.position === 'absolute' || cs.position === 'fixed') continue;
      taken += parseFloat(cs.flexGrow) > 0 ? (parseFloat(cs.minWidth) || 0) : kid.getBoundingClientRect().width;
    }
    room = row.clientWidth - taken;
  }
  return Math.max(MIN_W, Math.floor(Math.min(window.innerWidth / 2, room)));
}
const clampW = (w, mount) => Math.round(Math.min(Math.max(w, MIN_W), maxW(mount)));
function savedW() { try { return num(Number(localStorage.getItem(WIDTH_KEY) || NaN)); } catch { return null; } }
function saveW(w) { try { localStorage.setItem(WIDTH_KEY, String(w)); } catch { /* private window: the width lasts for this visit */ } }

// ---------------------------------------------------------------- the pane

let current = null;
export function closeDocument(opts) { current?.close(opts); }

export function openDocument(doc, { mount, matterId, opener, onClose } = {}) {
  if (!doc || doc.id == null || !mount) return null;
  loadCss('assistant-viewer.css');
  const back = opener || document.activeElement;
  closeDocument({ restoreFocus: false });
  const d = describe(doc);
  let blob = null, blobUrl = null, ctl = null, closed = false, pages = d.pages, width = savedW() ?? DEFAULT_W;

  const title = el('h3', { class: 'av-title', tabindex: '-1', text: d.title });
  const meta = el('span', { class: 'av-meta' });
  const paintMeta = () => { meta.textContent = dot(kindWords(d.kind), d.createdAt ? `created ${fmtDateTime(d.createdAt)}` : null, pages ? plural(pages, 'page') : null, d.entryCount != null ? plural(d.entryCount, 'entry', 'entries') : null); };
  const dl = el('button', { type: 'button', class: 'btn sm', text: 'Download PDF', disabled: true, onclick: () => blob && saveBlob(blob, fileName(d.title)) });
  // A new tab reads the server's own page when there is one, so it outlives this pane; in mock mode it is the blob.
  const tab = el('button', { type: 'button', class: 'btn quiet sm', text: 'Open in new tab', disabled: true, onclick: () => window.open(MOCK_ASSISTANT ? blobUrl : pdfHref(d, matterId), '_blank', 'noopener') });
  const shut = el('button', { type: 'button', class: 'btn quiet sm', text: 'Close', title: 'Close (Esc)', onclick: () => close() });
  const body = el('div', { class: 'av-body' });
  const grip = el('div', { class: 'av-grip', role: 'separator', tabindex: '0', 'aria-orientation': 'vertical', 'aria-label': 'Resize the document pane', title: 'Drag to resize' });
  const node = el('section', { class: 'av-viewer', 'aria-label': `Document: ${d.title}` }, grip,
    el('header', { class: 'av-head' },
      el('div', { class: 'av-head-t' }, el('span', { class: 'av-eyebrow', text: 'Draft created by the assistant — firm-only' }), title, meta),
      el('div', { class: 'av-act' }, dl, tab, shut)),
    body);

  function applyW(w, keep) {
    width = clampW(w, mount);
    mount.style.flex = `0 0 ${width}px`; mount.style.width = `${width}px`;
    mount.style.setProperty('--av-w', `${width}px`); mount.parentElement?.style.setProperty('--av-w', `${width}px`);
    grip.setAttribute('aria-valuemin', String(MIN_W)); grip.setAttribute('aria-valuemax', String(maxW(mount))); grip.setAttribute('aria-valuenow', String(width));
    if (keep) saveW(width);
  }
  grip.addEventListener('pointerdown', (e) => {
    if (e.button) return;
    e.preventDefault();
    const x0 = e.clientX, w0 = mount.getBoundingClientRect().width || width;
    grip.setPointerCapture?.(e.pointerId); node.classList.add('av-dragging');      // the frame must not swallow the pointer
    const move = (m) => applyW(w0 + (x0 - m.clientX));
    const up = () => { grip.removeEventListener('pointermove', move); node.classList.remove('av-dragging'); saveW(width); };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up, { once: true }); grip.addEventListener('pointercancel', up, { once: true });
  });
  grip.addEventListener('keydown', (e) => {
    const by = { ArrowLeft: STEP, ArrowRight: -STEP }[e.key];
    if (e.key === 'Home') applyW(maxW(mount), true); else if (e.key === 'End') applyW(MIN_W, true); else if (by) applyW(width + by, true); else return;
    e.preventDefault();
  });
  const onResize = () => applyW(width);

  function free() { if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = null; blob = null; }

  function skeleton() {
    return el('div', { class: 'av-wait', role: 'status' }, el('span', { class: 'av-wait-t', text: 'Making the PDF…' }),
      el('div', { class: 'av-page v2-skel', 'aria-hidden': 'true' }, [62, 100, 94, 100, 88, 100, 71, 100, 96, 54].map((w) => el('div', { style: `width:${w}%` }))));
  }

  // The words of the document, under a note that says why the PDF is not on screen.
  async function showText(host) {
    host.replaceChildren(el('p', { class: 'muted', text: 'Reading the document…' }));
    try { const entries = await entriesOf(d, matterId); if (!closed) host.replaceChildren(textBlocks(entries)); }
    catch { if (!closed) host.replaceChildren(el('p', { class: 'muted', text: 'The text of the document could not be read either.' })); }
  }

  function fail(err) {
    const text = el('div', { class: 'av-text-host' });
    body.replaceChildren(el('div', { class: 'av-note', role: 'alert' },
      el('p', { class: 'error', text: `The PDF could not be made: ${err?.message || 'no reply from the server'}.` }),
      el('div', { class: 'av-note-act' }, el('button', { type: 'button', class: 'btn sm', text: 'Retry', onclick: load }),
        el('button', { type: 'button', class: 'btn quiet sm', text: 'Show the text instead', onclick: (e) => { e.currentTarget.remove(); showText(text); } }))), text);
  }

  async function load() {
    ctl?.abort(); ctl = new AbortController();
    dl.disabled = true; tab.disabled = true;
    node.setAttribute('aria-busy', 'true'); body.replaceChildren(skeleton());
    try {
      const got = await getPdf(d, matterId, ctl.signal);
      if (closed) return;
      free(); blob = got.blob; blobUrl = URL.createObjectURL(blob);
      pages = got.pages ?? pages; paintMeta();
      dl.disabled = false; tab.disabled = false;
      if (navigator.pdfViewerEnabled === false) {            // this browser has no PDF viewer of its own
        const text = el('div', { class: 'av-text-host' });
        body.replaceChildren(el('div', { class: 'av-note' }, el('p', { text: 'This browser cannot show a PDF inside the page. Download it, or read the text of the document below.' }),
          el('div', { class: 'av-note-act' }, el('button', { type: 'button', class: 'btn sm primary', text: 'Download PDF', onclick: () => saveBlob(blob, fileName(d.title)) }))), text);
        showText(text);
      } else {
        body.replaceChildren(el('iframe', { class: 'av-frame', title: `${d.title} (PDF)`, src: `${blobUrl}#view=FitH&navpanes=0` }));
      }
    } catch (err) {
      if (closed || err?.name === 'AbortError') return;
      fail(err);
    } finally { if (!closed) node.removeAttribute('aria-busy'); }
  }

  // Escape closes the pane unless something modal is in front of it (that closes first).
  const onKey = (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented || document.querySelector('dialog[open], [aria-modal="true"]:not([hidden])')) return;
    e.preventDefault(); close();
  };

  function close({ restoreFocus = true } = {}) {
    if (closed) return;
    closed = true; ctl?.abort();
    document.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize);
    mount.replaceChildren(); mount.classList.remove('av-open');
    for (const p of ['flex', 'width', '--av-w']) mount.style.removeProperty(p);
    mount.parentElement?.style.removeProperty('--av-w');
    free();
    if (current === handle) current = null;
    window.dispatchEvent(new CustomEvent(VIEWER_EVENT, { detail: { id: null } }));
    if (restoreFocus && back?.isConnected) back.focus?.();
    onClose?.();
  }

  const handle = { close, node };
  current = handle;
  mount.classList.add('av-open'); mount.replaceChildren(node);
  applyW(width); paintMeta();
  document.addEventListener('keydown', onKey); window.addEventListener('resize', onResize);
  window.dispatchEvent(new CustomEvent(VIEWER_EVENT, { detail: { id: d.id } }));
  title.focus({ preventScroll: true });
  load();
  return handle;
}
