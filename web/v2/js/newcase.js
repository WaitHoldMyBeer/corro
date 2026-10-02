// "New case": name it, then upload a zip, import from a connected system, or start empty.
// Nothing is unzipped in the browser; the server does that and reports progress.
import { el } from '../../js/util.js';
import { createEmptyCase, startZipImport, zipImportStatus, connectedMatters, importConnected } from '../tabs/_ingest-api.js';

const link = document.createElement('link');
link.rel = 'stylesheet'; link.href = new URL('../css/newcase.css', import.meta.url).href;
document.head.append(link);

const fmtSize = (b) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);
const REASONS = { 413: 'The file is larger than the server accepts.', 415: 'The server did not accept this kind of file.' };
const sentence = (t) => { const x = String(t || '').trim(); return x ? x.charAt(0).toUpperCase() + x.slice(1) + (/[.!?]$/.test(x) ? '' : '.') : ''; };

// The running import survives closing the dialog; the dashboard listens for these.
const emit = (detail) => window.dispatchEvent(new CustomEvent('newcase:importing', { detail }));

export function openNewCase({ onCreated } = {}) {
  const meta = { name: '', client: '', number: '' };
  let step = 'name';
  let job = null;            // { kind, status, matterId, error, partial }
  let closed = false;
  let zip = null;
  let lastFocus = document.activeElement;
  const dlg = el('dialog', { class: 'v2-dlg newcase', 'aria-labelledby': 'nc-title' });
  document.body.append(dlg);
  dlg.addEventListener('close', () => { closed = true; dlg.remove(); lastFocus?.focus?.(); });
  let downOnBackdrop = false;
  dlg.addEventListener('mousedown', (e) => { downOnBackdrop = e.target === dlg; });
  dlg.addEventListener('click', (e) => { if (e.target === dlg && downOnBackdrop) dlg.close(); });

  const head = (title, sub) => [el('h2', { id: 'nc-title', text: title }), sub ? el('p', { class: 'muted', text: sub }) : null];
  const close = () => dlg.close();
  const show = (...kids) => { dlg.replaceChildren(el('div', { class: 'nc-body' }, ...kids)); (dlg.querySelector('[data-first]') || dlg.querySelector('button, input'))?.focus(); };

  function viewName(error) {
    const name = el('input', { type: 'text', required: true, value: meta.name, 'data-first': '', autocomplete: 'off', id: 'nc-name' });
    const client = el('input', { type: 'text', value: meta.client, autocomplete: 'off', id: 'nc-client' });
    const number = el('input', { type: 'text', value: meta.number, autocomplete: 'off', id: 'nc-number' });
    const msg = el('p', { class: 'nc-err', role: 'alert', text: error || '' });
    const form = el('form', { class: 'nc-form', novalidate: true },
      ...head('New case'),
      el('label', { for: 'nc-name' }, 'Case name ', el('span', { class: 'muted', text: '(required)' })), name,
      el('label', { for: 'nc-client' }, 'Client name ', el('span', { class: 'muted', text: '(optional)' })), client,
      el('label', { for: 'nc-number' }, 'Case number ', el('span', { class: 'muted', text: '(optional)' })), number,
      msg,
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: close, text: 'Cancel' }), el('button', { class: 'btn primary', type: 'submit', text: 'Continue' })));
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      meta.name = name.value.trim(); meta.client = client.value.trim(); meta.number = number.value.trim();
      if (!meta.name) { msg.textContent = 'Give the case a name to continue.'; name.focus(); return; }
      viewChoose();
    });
    show(form);
  }

  function option(title, text, onclick) {
    return el('button', { class: 'nc-option', type: 'button', onclick }, el('strong', { text: title }), el('span', { class: 'muted', text }));
  }

  function viewChoose(error) {
    show(...head(`Fill "${meta.name}"`, 'Choose how the first documents get in. You can add more later from the Documents tab.'),
      el('p', { class: 'nc-err', role: 'alert', text: error || '' }),
      el('div', { class: 'nc-options' },
        option('Upload a zip of the file', 'A .zip of the case documents. The server unpacks and reads it.', () => viewZip()),
        option('Import from a connected system', 'Bring in a matter that already exists in your other database.', () => viewConnected()),
        option('Start empty', 'Create the case now and add documents later.', () => startEmpty())),
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: () => viewName(), text: 'Back' })));
  }

  async function startEmpty() {
    show(...head('Creating the case'), el('p', { class: 'muted', role: 'status', text: 'One moment.' }));
    try { const c = await createEmptyCase(meta); viewDone({ matterId: c.id, empty: true }); } catch (err) { viewFail(`The case was not created: ${err.message}`, startEmpty); }
  }

  function viewZip(error) {
    const input = el('input', { type: 'file', accept: '.zip,application/zip', hidden: true });
    const chosen = el('p', { class: 'small', role: 'status', text: zip ? `${zip.name}, ${fmtSize(zip.size)}` : 'No file chosen yet.' });
    const msg = el('p', { class: 'nc-err', role: 'alert', text: typeof error === 'string' ? error : '' });
    const go = el('button', { class: 'btn primary', type: 'button', disabled: !zip, onclick: () => runZip(), text: 'Create the case and import' });
    const take = (f) => {
      if (!f) return;
      if (!/\.zip$/i.test(f.name)) { zip = null; msg.textContent = 'That is not a .zip file. Choose a zip of the case documents.'; chosen.textContent = 'No file chosen yet.'; go.disabled = true; return; }
      zip = f; msg.textContent = ''; chosen.textContent = `${f.name}, ${fmtSize(f.size)}`; go.disabled = false;
    };
    const drop = el('div', { class: 'nc-drop', tabindex: '0', role: 'button', 'data-first': '', 'aria-label': 'Choose a zip file or drop it here' }, 'Drop the zip here, or ', el('span', { class: 'linkish', text: 'choose a file' }));
    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => take(e.dataTransfer.files[0]));
    input.addEventListener('change', () => { take(input.files[0]); input.value = ''; });
    show(...head('Upload a zip of the file', `The case "${meta.name}" is created when the upload starts.`), drop, input, chosen, msg,
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: () => viewChoose(), text: 'Back' }), go));
  }

  async function runZip() {
    job = { kind: 'zip', status: null, matterId: null, jobId: null };
    paintProgress('Uploading the zip');
    emit({ state: 'importing', name: meta.name });
    let started;
    try {
      started = await startZipImport(zip, meta);
      if (started.matterId == null || started.jobId == null) throw new Error('The server did not say which case it created.');
    } catch (err) {
      emit({ state: 'failed', name: meta.name });
      if (closed) return;
      const text = sentence(err.hasDetail ? err.message : (REASONS[err.status] || err.message));
      viewFail(`Nothing was created. ${text}`, runZip);
      return;
    }
    job.matterId = started.matterId; job.jobId = started.jobId;
    emit({ state: 'importing', name: meta.name, caseId: job.matterId });
    pollJob();
  }

  // Watch a started import. A few failed polls in a row are tolerated: the server may still be working.
  async function pollJob() {
    let misses = 0;
    try {
      for (;;) {
        let s;
        try { s = await zipImportStatus(job.matterId, job.jobId); misses = 0; } catch (err) { if (++misses >= 4) throw err; await new Promise((r) => setTimeout(r, 1000)); continue; }
        job.status = s;
        if (s.state === 'failed') throw new Error(s.error || 'The import stopped.');
        if (s.state === 'complete') break;
        if (!closed) paintProgress();
      }
      emit({ state: 'done', name: meta.name, caseId: job.matterId });
      if (!closed) viewDone({ matterId: job.matterId, status: job.status });
    } catch (err) {
      emit({ state: 'failed', name: meta.name, caseId: job.matterId });
      if (closed) return;
      viewFail(`The case "${meta.name}" was created but progress could not be read: ${sentence(err.message)} The import may still be running on the server. Documents read so far are in the case.`, pollJob, job.matterId);
    }
  }

  function paintProgress(label) {
    const s = job?.status;
    const total = s?.total || 0, done = s?.done || 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const bar = el('div', { class: 'nc-bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(total || 100), 'aria-valuenow': String(total ? done : 0), 'aria-label': 'Files read' },
      el('div', { class: `nc-fill${total ? '' : ' indet'}`, style: total ? `width:${pct}%` : null }));
    show(...head(`Importing "${meta.name}"`, 'You can close this window; the import keeps running and the case shows as importing.'),
      bar,
      el('p', { class: 'small', role: 'status' }, label || (total ? `${done} of ${total} files done${s.pages != null ? `, ${s.pages} pages` : ''}.` : 'Unpacking the zip.')),
      s?.current ? el('p', { class: 'small muted nc-clip', title: s.current, text: `Now reading: ${s.current}` }) : null,
      skippedList(s),
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn', type: 'button', onclick: close, text: 'Close and keep importing' })));
  }

  function skippedList(s) {
    if (!s || (!s.skipped.length && !s.duplicates.length && !s.kept.length)) return null;
    return el('div', { class: 'nc-skipped' },
      s.kept.length ? el('details', { open: true }, el('summary', { text: `${s.kept.length} ${s.kept.length === 1 ? 'file' : 'files'} kept but not read` }),
        el('ul', {}, s.kept.map((k) => el('li', {}, el('span', { class: 'nc-clip', title: k.name, text: k.name }), el('span', { class: 'muted small', text: ` ${k.reason}` }))))) : null,
      s.skipped.length ? el('details', { open: true }, el('summary', { text: `${s.skipped.length} ${s.skipped.length === 1 ? 'file' : 'files'} skipped` }),
        el('ul', {}, s.skipped.map((k) => el('li', {}, el('span', { class: 'nc-clip', title: k.name, text: k.name }), el('span', { class: 'muted small', text: ` ${k.reason}` }))))) : null,
      s.duplicates.length ? el('details', {}, el('summary', { text: `${s.duplicates.length} already in the file` }),
        el('ul', {}, s.duplicates.map((n) => el('li', {}, el('span', { class: 'nc-clip', title: n, text: n }))))) : null);
  }

  async function viewConnected() {
    show(...head('Import from a connected system'), el('p', { class: 'muted', role: 'status', text: 'Looking for matters to import.' }));
    let r;
    try { r = await connectedMatters(); } catch (err) { viewFail(`Could not reach the connected system: ${err.message}`, viewConnected); return; }
    if (!r.connected) {
      show(...head('Import from a connected system'), el('p', { text: 'No system is connected yet.' }),
        el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: () => viewChoose(), text: 'Back' }),
          el('a', { class: 'btn primary', href: '#/settings', onclick: close, text: 'Open Settings > Import' })));
      return;
    }
    const rows = r.items.length ? el('ul', { class: 'nc-matters' }, r.items.map((m) => el('li', {},
      el('div', {}, el('strong', { text: m.name }), m.client ? el('div', { class: 'muted small', text: m.client }) : null),
      el('button', { class: 'btn small primary', type: 'button', 'aria-label': `Import ${m.name}`, onclick: () => runConnected(m), text: 'Import' }))))
      : el('p', { text: 'Every matter in the connected system is already in the product.' });
    show(...head('Import from a connected system', 'Matters that are not in the product yet. An imported matter keeps the name it has there.'), rows,
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: () => viewChoose(), text: 'Back' })));
  }

  async function runConnected(m) {
    meta.name = m.name;
    show(...head(`Importing "${m.name}"`, 'Reading the matter from the connected system. This can take a minute.'),
      el('div', { class: 'nc-bar', role: 'progressbar', 'aria-label': 'Import in progress' }, el('div', { class: 'nc-fill indet' })),
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn', type: 'button', onclick: close, text: 'Close and keep importing' })));
    emit({ state: 'importing', name: m.name, caseId: m.id });
    try { await importConnected(m.id); emit({ state: 'done', name: m.name, caseId: m.id }); if (!closed) viewDone({ matterId: m.id }); }
    catch (err) { emit({ state: 'failed', name: m.name, caseId: m.id }); if (!closed) viewFail(`The import did not finish: ${err.message}`, () => runConnected(m)); }
  }

  function viewDone({ matterId, status, empty }) {
    const s = status;
    const nothing = s && s.total > 0 && s.storedCount === 0;
    show(...head(empty ? 'Case created' : nothing ? 'Case created, nothing stored' : 'Import finished', empty ? `"${meta.name}" is empty. Add documents from its Documents tab.` : null),
      nothing ? el('p', { role: 'status', text: 'The case was created but no file could be stored. The reasons are below.' }) : null,
      s && !nothing ? el('p', { role: 'status', text: `${s.done} of ${s.total} files ${s.waiting ? 'stored' : 'read'}${s.pages != null ? `, ${s.pages} pages` : ''}${s.skippedCount ? `, ${s.skippedCount} skipped` : ''}${s.duplicates.length ? `, ${s.duplicates.length} already in the file` : ''}.` }) : null,
      s?.waiting ? el('p', { class: 'nc-note', text: s.waiting }) : (s?.summary ? el('p', { text: s.summary }) : null),
      s ? skippedList(s) : null,
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: close, text: 'Close' }),
        el('button', { class: 'btn primary', type: 'button', 'data-first': '', onclick: () => { close(); onCreated?.(matterId); }, text: 'Open the case' })));
  }

  function viewFail(message, retry, matterId) {
    show(...head('This did not finish'), el('p', { class: 'nc-err', role: 'alert', text: message }),
      el('div', { class: 'nc-actions' }, el('button', { class: 'btn quiet', type: 'button', onclick: close, text: 'Close' }),
        matterId ? el('button', { class: 'btn', type: 'button', onclick: () => { close(); onCreated?.(matterId); }, text: 'Open the case anyway' }) : null,
        retry ? el('button', { class: 'btn primary', type: 'button', 'data-first': '', onclick: retry, text: matterId ? 'Check again' : 'Retry' }) : null));
  }

  dlg.showModal();
  viewName();
  return dlg;
}
