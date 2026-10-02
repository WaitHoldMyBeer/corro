// Upload button, drop zone and one progress line per upload on the Documents tab.
import { el } from '../../js/util.js';
import { startUpload, getIngestion, afterComplete } from './_ingest-api.js';

const STAGE = { stored: 'Stored', fingerprint: 'Comparing with pages already known', read: 'Reading', reconcile: 'Checking against the file', done: 'Done' };
const usd = (n) => (n == null ? null : `$${Number(n).toFixed(Number(n) < 1 ? 4 : 2)}`);

function progressText(g) {
  const n = g.counts || {};
  return `${STAGE[g.stage] || 'Working'}. ${n.pages_total ?? '?'} pages in the file, ${n.pages_skipped ?? 0} already known (no cost), ${n.pages_text ?? 0} read as text, ${n.pages_model ?? 0} read by the model, ${n.claims_added ?? 0} claims added${n.cost_usd != null ? `, cost ${usd(n.cost_usd)}` : ''}.`;
}

export function uploadControl(ctx, onDone) {
  const lines = el('ul', { class: 'tab-uploads', 'aria-live': 'polite' });
  const input = el('input', { type: 'file', multiple: true, accept: 'application/pdf,image/png,image/jpeg', hidden: true });
  const drop = el('div', { class: 'tab-drop', tabindex: '0', role: 'button', 'aria-label': 'Upload documents: choose files or drop them here' },
    el('span', { text: 'Drop a PDF or image here, or ' }), el('span', { class: 'linkish', text: 'choose a file' }));

  async function run(file) {
    const status = el('div', { class: 'small', text: `Uploading ${file.name}` });
    const li = el('li', { class: 'tab-upload' }, el('strong', { text: file.name }), status);
    lines.prepend(li);
    try {
      let g = await startUpload(ctx, file);
      while (g.state !== 'complete') {
        if (g.state === 'failed') throw new Error(g.error || 'The document could not be read.');
        status.textContent = progressText(g);
        await new Promise((r) => setTimeout(r, 400));
        g = await getIngestion(ctx, g.id);
      }
      if (g.state === 'failed') throw new Error(g.error);
      li.classList.add('done');
      status.textContent = g.duplicate_of ? `Already on file as ${g.duplicate_of.name}. Nothing was read, so nothing was spent.` : (g.summary || progressText(g));
      if (!afterComplete(ctx, g)) await ctx.reload?.();
      onDone();
    } catch (err) {
      li.classList.add('failed');
      status.textContent = `Not added: ${err.message}`;
    }
  }
  const take = (files) => [...files].forEach(run);
  input.addEventListener('change', () => { take(input.files); input.value = ''; });
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => take(e.dataTransfer.files));
  return el('div', { class: 'tab-upload-wrap' }, drop, input, lines);
}
