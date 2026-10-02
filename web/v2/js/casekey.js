// Per-browser remembered views are kept per case: a Tasks filter, a list sort or a conversation cache for one case
// must never show in another. Use caseKey('tasks:filter') instead of a bare key; the shell sets the current case.
let current = null;
export const setCurrentCase = (id) => { current = id; };
export const currentCase = () => current;
export const caseKey = (name, id = current) => `v2:${id ?? 'none'}:${name}`;
