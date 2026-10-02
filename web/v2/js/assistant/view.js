// The conversation as it is read: the lawyer's message in a quiet bubble on the right, the answer as
// plain text on the page, one quiet line under it that opens what was read. Used by the side panel
// and by the Assistant section; both draw from store.js. Text reaches the page through textContent only.
import { el, toast } from '../../../js/util.js';
import { openSources } from '../../../js/drawer.js';
import { chat, subscribe, ask, stop, retry, regenerate, create, setMode, removeContext, clearContext, matterId } from './store.js';
import { documentKinds, MOCK_ASSISTANT } from './client.js';
import { fmtMs, fmtUsd, copyText, citeName, marked, nodes, blockCites, traceBody, graphIcon } from './render.js';
import { showInGraph } from './graph.js';

const plural = (n, one, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const dot = (...parts) => parts.filter(Boolean).join(' · ');
const KIND = { medical_chronology: 'Medical chronology', records_summary: 'Summary of records', damages_summary: 'Summary of damages' };
const kindWords = (k) => KIND[k] || (k ? String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Document');
const MODE_TIP = { fast: 'Fast: uses what is already indexed. Usually a few seconds.', deep: 'Deep: re-reads the pages it needs. Usually tens of seconds; costs more.' };
const FALLBACK_STARTS = ['What is still outstanding, and from whom?', 'Build a medical chronology', 'Summarise the records by provider', 'Summarise the damages in a table'];
const MOST_REFS = 4;

// ---------------------------------------------------------------- one answer

// Sources are numbered once per answer, in the order first cited.
function numbering() {
  const seen = new Map();
  return (c) => { const k = `${c.nodeId}|${c.page ?? ''}|${c.ref?.href ?? ''}`; if (!seen.has(k)) seen.set(k, seen.size + 1); return seen.get(k); };
}

function refChip(c, n) {
  const name = citeName(c);
  return c.ref
    ? el('button', { type: 'button', class: 'ac-ref', title: name, 'aria-label': `Source ${n}: ${name}`, text: String(n), onclick: () => openSources([c.ref]) })
    : el('span', { class: 'ac-ref', title: name, text: String(n) });
}

function refs(list, number) {
  if (!list.length) return null;
  const row = el('span', { class: 'ac-refs' }, list.slice(0, MOST_REFS).map((c) => refChip(c, number(c))));
  if (list.length > MOST_REFS) {
    const rest = list.slice(MOST_REFS);
    rest.forEach(number);                                    // keep the numbering stable whether or not they are opened
    const more = el('button', { type: 'button', class: 'ac-ref more', title: 'Show the other sources', text: `+${rest.length}` });
    more.addEventListener('click', () => more.replaceWith(...rest.map((c) => refChip(c, number(c)))));
    row.append(more);
  }
  return row;
}

function sentence(s, number) {
  const m = marked(s.text, s.unverified);
  return [
    el('span', { class: s.notInFile ? 'as-sent nif' : 'as-sent', title: s.notInFile ? 'No page or record in the file supports this sentence.' : null }, m.nodes),
    s.notInFile ? el('span', { class: 'as-nif', text: 'not in the file' }) : null,
    m.missing.length ? el('span', { class: 'as-nif warn', text: `not found in its sources: ${m.missing.join(', ')}` }) : null,
    refs(s.cites, number), ' ',
  ];
}

function tableBlock(b, number) {
  const sourced = b.rows.some((r) => r.cites.length);
  return el('div', { class: 'ac-table', tabindex: '0', role: 'region', 'aria-label': b.title || 'Table' },
    b.title ? el('div', { class: 'ac-table-t', text: b.title }) : null,
    el('table', { class: 'v2-table as-table' },
      el('thead', {}, el('tr', {}, b.columns.map((c) => el('th', { scope: 'col', text: c })), sourced ? el('th', { scope: 'col', text: 'Source' }) : null)),
      el('tbody', {}, b.rows.map((r) => el('tr', {}, r.cells.map((c) => el('td', { class: /^[$-]?\d[\d,.\s%]*$/.test(c) ? 'num' : null, text: c })), sourced ? el('td', {}, refs(r.cites, number)) : null)))));
}

// A document the assistant made: one card in the conversation, the document itself opens beside it.
function documentCard(b, view) {
  const open = el('button', { type: 'button', class: 'btn sm primary', text: 'Open' });
  open.addEventListener('click', () => view.openDocument(b, open));
  const pdf = el('button', { type: 'button', class: 'btn sm', text: 'Download PDF' });
  pdf.addEventListener('click', async () => {
    pdf.disabled = true;
    try { const m = await import('./viewer.js'); await m.downloadDocument(b, await matterId()); }
    catch (err) { toast(`The PDF could not be made: ${err?.message || 'not available'}`, 'error'); }
    pdf.disabled = false;
  });
  return el('div', { class: 'ac-doc' },
    el('div', { class: 'ac-doc-i', 'aria-hidden': 'true' }),
    el('div', { class: 'ac-doc-t' }, el('strong', { text: b.title }), el('span', { text: dot(kindWords(b.kind), `${plural(b.entries.length, 'entry', 'entries')}, ${plural(b.sources ?? 0, 'source')}`) })),
    el('div', { class: 'ac-doc-a' }, open, pdf));
}

function renderBlocks(blocks, view) {
  const number = numbering();
  return blocks.filter(Boolean).map((b) => {
    if (b.type === 'heading') return el('h3', { class: 'ac-h', text: b.text });
    if (b.type === 'table') return tableBlock(b, number);
    if (b.type === 'document') return documentCard(b, view);
    return el('p', { class: 'ac-p' }, b.sentences.map((s) => sentence(s, number)));
  });
}

function plainText(answer) {
  return answer.blocks.map((b) => {
    if (b.type === 'heading') return b.text;
    if (b.type === 'table') return [b.title, b.columns.join('\t'), ...b.rows.map((r) => r.cells.join('\t'))].filter(Boolean).join('\n');
    if (b.type === 'document') return `[${b.title}: ${plural(b.entries.length, 'entry', 'entries')}]`;
    return b.sentences.map((s) => s.text).join(' ');
  }).join('\n\n');
}

function turnView(turn, view) {
  const statusText = el('span', {});
  const status = el('p', { class: 'ac-status', role: 'status' }, el('span', { class: 'as-spin', 'aria-hidden': 'true' }), statusText);
  const body = el('div', { class: 'ac-blocks' });
  const foot = el('div', { class: 'ac-foot' });
  const node = el('section', { class: 'ac-turn' },
    el('div', { class: 'ac-user' }, el('div', { class: 'ac-bubble', text: turn.question })),
    el('article', { class: 'ac-answer' }, status, body, foot));
  let shown = -1;

  function paint() {
    if (turn.state === 'running') {
      statusText.textContent = turn.status || 'Reading the file';
      if (turn.blocks.length !== shown) { shown = turn.blocks.length; body.replaceChildren(...renderBlocks(turn.blocks, view)); }
      return;
    }
    if (turn.state !== 'done') {
      status.className = turn.state === 'stopped' ? 'ac-status' : 'ac-status error';
      const modelDown = turn.errorStatus === 503 || turn.errorStatus === 502 || /not available|model/i.test(turn.error || '');
      status.replaceChildren(turn.state === 'stopped' ? 'Stopped. ' : (turn.built ? `The document could not be built: ${turn.error} `
        : (modelDown ? 'The assistant\u2019s model is not answering; documents and saved answers still work. ' : `The assistant could not answer: ${turn.error} `)),
        el('button', { type: 'button', class: 'as-link', text: 'Try again', onclick: () => (turn.built ? null : retry(turn)) }));
      if (turn.built) status.lastChild.remove();
      status.title = turn.error || '';
      body.replaceChildren(); foot.replaceChildren();
      return;
    }
    const a = turn.answer;
    status.remove();
    // The server says so when the model could not be reached and the answer was put together in code.
    const down = a.notes.find((n) => /^the model could not be reached/i.test(n));
    body.replaceChildren(...renderBlocks(a.blocks, view));
    if (down) body.prepend(el('p', { class: 'ac-warn', role: 'status' }, 'The assistant\u2019s model is not answering, so this was assembled in code from the file\u2019s search index. Documents and saved answers still work. ',
      el('button', { type: 'button', class: 'as-link', text: 'Try again', onclick: () => regenerate(turn) })));
    if (!a.blocks.length) body.append(el('p', { class: 'ac-p muted', text: 'Nothing came back for this request.' }));
    a.notes.filter((n) => n !== down).forEach((n) => body.append(el('p', { class: 'as-note', text: n })));
    if (a.built) { foot.replaceChildren(el('span', { text: dot('Built in code from the file', fmtMs(a.cost.ms), 'no model call') })); return; }
    const lit = nodes(a.blocks.flatMap(blockCites));
    const read = el('details', { class: 'ac-read' }, el('summary', { text: dot(a.sources ? `Read ${plural(a.sources, 'source')}` : 'No sources read', fmtMs(a.cost.ms), fmtUsd(a.cost.usd)) }));
    read.addEventListener('toggle', () => { if (read.open && read.children.length < 2) read.append(traceBody(a.trace, a.cost)); });
    const copy = el('button', { type: 'button', class: 'ac-act', text: 'Copy' });
    copy.addEventListener('click', async () => { const ok = await copyText(plainText(a)); copy.textContent = ok ? 'Copied' : 'Not copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1600); });
    const again = chat.canRefresh ? el('button', { type: 'button', class: 'ac-act', text: 'Regenerate', title: 'Work this answer out again', onclick: () => regenerate(turn) }) : null;
    foot.replaceChildren(read, el('span', { class: 'ac-acts' }, copy, again,
      lit.length ? el('button', { type: 'button', class: 'ac-act', title: `Light ${plural(lit.length, 'source')} in the graph`, onclick: async () => { const said = await showInGraph(lit); if (said) toast(said); view.afterGraph?.(); } }, graphIcon(), 'Show in graph') : null));
  }
  paint();
  return { node, paint };
}

// ---------------------------------------------------------------- the view

// opts: { variant: 'page' | 'panel', openDocument(doc, opener), getCase?(), afterGraph?() }
export function conversationView(opts = {}) {
  const view = { openDocument: opts.openDocument || (() => {}), afterGraph: opts.afterGraph };
  const turns = el('div', { class: 'ac-turns' });
  const greet = el('p', { class: 'ac-greet', text: 'What do you need from this file?' });
  const starts = el('div', { class: 'ac-starts' });
  const empty = el('div', { class: 'ac-empty' }, greet, el('p', { class: 'ac-greet-s', text: 'Every sentence is tied to the page it came from. Documents open beside the conversation.' }), starts);
  const note = el('p', { class: 'ac-status', hidden: true });
  const log = el('div', { class: 'ac-log', tabindex: '0', role: 'log', 'aria-label': 'Conversation' }, el('div', { class: 'ac-col' }, empty, note, turns));

  const trayList = el('ul', { class: 'as-tray-l' });
  const trayTitle = el('span', { class: 'as-tray-t' });
  const tray = el('div', { class: 'as-tray', hidden: true }, el('div', { class: 'as-tray-h' }, trayTitle, el('button', { type: 'button', class: 'as-link', text: 'Clear all', onclick: clearContext })), trayList);

  const input = el('textarea', { class: 'ac-input', rows: '1', placeholder: 'Ask about this file, or ask for a document', 'aria-label': 'Message to the assistant' });
  const modes = ['fast', 'deep'].map((m) => el('button', { type: 'button', class: 'ac-mode', 'data-mode': m, title: MODE_TIP[m], text: m === 'fast' ? 'Fast' : 'Deep', onclick: () => setMode(m) }));
  const sendBtn = el('button', { type: 'submit', class: 'btn sm primary ac-send', text: 'Send' });
  const form = el('form', { class: 'ac-composer' }, input, el('div', { class: 'ac-composer-b' }, el('div', { class: 'ac-modes', role: 'group', 'aria-label': 'How thoroughly to read' }, modes), sendBtn));
  const node = el('div', { class: `ac ac-${opts.variant || 'page'}` }, log,
    el('div', { class: 'ac-bottom' }, el('div', { class: 'ac-col' }, tray, form,
      el('p', { class: 'ac-firm', text: `Answers are drafts for the firm and are never shared with a provider.${MOCK_ASSISTANT ? ' Placeholder answers.' : ''}` }))));

  const views = new Map();
  const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 168)}px`; };
  const toEnd = () => { log.scrollTop = log.scrollHeight; };
  const nearEnd = () => log.scrollHeight - log.scrollTop - log.clientHeight < 120;

  function submit() {
    if (chat.busy) { stop(); return; }
    const text = input.value;
    if (!text.trim()) return;
    input.value = ''; grow();
    ask(text);
  }
  input.addEventListener('input', grow);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });

  function paintAll(info) {
    views.clear();
    turns.replaceChildren(...chat.turns.map((t) => { const v = turnView(t, view); views.set(t, v); return v.node; }));
    empty.hidden = chat.turns.length > 0 || !!info?.loading;
    note.hidden = !(info?.loading || info?.error);
    note.className = info?.error ? 'ac-status error' : 'ac-status';
    note.textContent = info?.error ? `This conversation could not be opened: ${info.error}` : 'Opening the conversation…';
    requestAnimationFrame(toEnd);
  }
  function paintBusy() { sendBtn.textContent = chat.busy ? 'Stop' : 'Send'; sendBtn.classList.toggle('primary', !chat.busy); sendBtn.setAttribute('aria-label', chat.busy ? 'Stop the assistant' : 'Send'); }
  function paintMode() { modes.forEach((b) => b.setAttribute('aria-pressed', b.dataset.mode === chat.mode ? 'true' : 'false')); }
  function paintTray() {
    const n = chat.context.length;
    tray.hidden = !n;
    trayTitle.textContent = `In context: ${plural(n, 'item')}`;
    trayList.replaceChildren(...chat.context.map((it) => {
      const name = `${it.label || it.title || it.kind || 'Item'}${it.page ? `, p. ${it.page}` : ''}`;
      const srcs = it.href ? [{ kind: it.kind, clio_id: it.clio_id, label: it.label, page: it.page, href: it.href }] : (it.source_refs || []).filter((r) => r?.href);
      return el('li', { class: 'as-ctx' }, it.kind ? el('span', { class: 'as-ctx-k', text: String(it.kind).replace(/_/g, ' ') }) : null,
        srcs.length ? el('button', { type: 'button', class: 'as-ctx-l', title: `Open the source of ${name}`, text: name, onclick: () => openSources(srcs, name) }) : el('span', { class: 'as-ctx-l', title: name, text: name }),
        el('button', { type: 'button', class: 'as-ctx-x', 'aria-label': `Remove ${name} from context`, title: 'Remove', text: '×', onclick: () => removeContext(it) }));
    }));
  }

  // Starting points: the kinds of document the server can build, and what the case itself says is open.
  async function paintStarts() {
    let list = [];
    try { list = (await documentKinds(await matterId())).slice(0, 6).map((k) => ({ label: k.title, kind: k.kind, hint: k.description })); } catch { /* the plain one below still shows */ }
    const agenda = opts.getCase?.()?.agenda;
    const open = agenda ? (agenda.overdue?.length || 0) + (agenda.waiting?.length || 0) : 0;
    if (!list.length) list = FALLBACK_STARTS.slice(1).map((t) => ({ label: t, prompt: t }));      // an older server without the list of kinds: ask instead
    list.push(open ? { label: `What is overdue or waiting on someone (${open})`, prompt: 'What is overdue, what is waiting on someone else, and who has to act next?' } : { label: FALLBACK_STARTS[0], prompt: FALLBACK_STARTS[0] });
    starts.replaceChildren(...list.map((s) => el('button', { type: 'button', class: 'ac-start', title: s.hint || null, onclick: () => { input.focus({ preventScroll: true }); if (s.kind) create(s.kind, s.label); else ask(s.prompt); } },
      s.kind ? el('span', { class: 'ac-start-k', text: 'Create' }) : null, el('span', { text: s.label }))));
  }

  const off = subscribe((type, data) => {
    if (type === 'reset') paintAll(data);
    else if (type === 'turn') { empty.hidden = true; note.hidden = true; const v = turnView(data, view); views.set(data, v); turns.append(v.node); requestAnimationFrame(toEnd); }
    else if (type === 'update' || type === 'done') { const pin = nearEnd(); views.get(data)?.paint(); if (pin) requestAnimationFrame(toEnd); }
    else if (type === 'busy') paintBusy();
    else if (type === 'mode') paintMode();
    else if (type === 'context') paintTray();
  });
  paintAll(); paintBusy(); paintMode(); paintTray(); paintStarts();

  return { node, focus: () => input.focus({ preventScroll: true }), destroy: off };
}
