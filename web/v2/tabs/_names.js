// A stored file name shown as a title: the part after the folder and docket prefixes, words capitalised.
const ACRONYMS = new Set(['ime', 'mri', 'emg', 'ncv', 'eeg', 'ct', 'er', 'ems', 'pt', 'ot', 'dti', 'rfa', 'llc', 'pc']);

export function docTitle(name) {
  const base = String(name || '').replace(/\.[a-z0-9]{2,4}\.?$/i, '').split('__').pop() || String(name || '');
  const t = base.replace(/[-_]+/g, ' ').trim().split(' ').map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.replace(/^[a-z]/, (m) => m.toUpperCase()))).join(' ');
  return t || String(name || '');
}
