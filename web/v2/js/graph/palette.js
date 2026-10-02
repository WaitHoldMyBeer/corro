// The colours the canvas paints with. The DOM around it is themed by CSS; a
// canvas is not, so these are read from the theme's custom properties on
// <html> (web/v2/css/themes.css) and re-read when the shell fires 'v2:theme'.
// Every value has a fallback, so the module also runs on a page with no theme.

const FALLBACK = {
  surface: '#ffffff', ink: '#14181f', label: '#3b4452',
  // record kinds: eight categorical hues in a fixed order, never cycled; a ninth kind takes `other`
  hues: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'],
  other: '#8a93a0', hub: '#5b6676',
  // relevance: one hue, weakest to strongest match
  ramp: ['#b7d3f6', '#6da7ec', '#2a78d6', '#184f95', '#0d1e42'],
};

function rgb(hex, fallback) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  const n = parseInt(m ? m[1] : fallback.slice(1), 16);
  return [n >> 16 & 255, n >> 8 & 255, n & 255];
}

export function readPalette() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fallback) => cs.getPropertyValue(name).trim() || fallback;
  return {
    surface: v('--n-0', FALLBACK.surface), ink: v('--n-900', FALLBACK.ink), label: v('--n-600', FALLBACK.label),
    hues: FALLBACK.hues.map((d, i) => v(`--g-${i + 1}`, d)),
    other: v('--g-other', FALLBACK.other), hub: v('--g-hub', FALLBACK.hub),
    ramp: FALLBACK.ramp.map((d, i) => rgb(v(`--g-r${i + 1}`, d), d)),
  };
}
