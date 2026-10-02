// Lights cited sources in the case-file graph, through the handle the graph module exposes.
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (get, tries = 40) => { for (let i = 0; i < tries; i++) { const v = get(); if (v) return v; await wait(100); } return null; };

// The shell's addresses are #/c/<case id>/<tab>; the older #/<tab> form still opens the current case.
export const onTab = (name) => new RegExp(`^#/?(c/[^/]+/)?${name}(\\?|$)`).test(location.hash);
export function tabHash(name) { const m = location.hash.match(/^#\/c\/[^/?]+\//); return `${m ? m[0] : '#/'}${name}`; }

// The graph on screen; the Graph tab is opened when there is none.
async function graphHandle() {
  let mod = null;
  try { mod = await import('../graph/index.js'); } catch { return null; }
  if (typeof mod.currentGraph !== 'function') return null;
  const live = () => (document.querySelector('.gx') ? mod.currentGraph() : null);
  if (live()) return live();
  if (!onTab('graph')) location.hash = tabHash('graph');
  return until(live);
}

// `cites` carry { nodeId, label }. Returns a sentence saying what happened, for the caller to show.
export async function showInGraph(cites) {
  const list = (cites || []).filter((c) => c?.nodeId);
  if (!list.length) return null;
  const ids = list.map((c) => c.nodeId);
  const g = await graphHandle();
  if (!g || typeof (g.highlightNodes || g.highlight) !== 'function') return 'The graph is not on this page yet. Open the Graph tab and try again.';
  try { await g.ready; } catch { /* the graph reports its own errors in place */ }
  const n = (g.highlightNodes || g.highlight).call(g, ids, { label: list.length === 1 ? list[0].label : `${list.length} cited sources` });
  if (ids.length === 1 && typeof g.focusNode === 'function') g.focusNode(ids[0]);
  document.querySelector('.gx')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return n ? `${n} source${n === 1 ? '' : 's'} lit in the graph. Type in its search box or press Escape there to clear.` : 'None of these sources is on the graph.';
}
