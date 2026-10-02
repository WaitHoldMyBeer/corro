// Loads a stylesheet from web/v2/css once.
export function loadCss(name) {
  const href = new URL(`../css/${name}`, import.meta.url).href;
  if ([...document.styleSheets].some((s) => s.href === href) || document.querySelector(`link[data-v2css="${name}"]`)) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.href = href; l.dataset.v2css = name;
  document.head.append(l);
}
