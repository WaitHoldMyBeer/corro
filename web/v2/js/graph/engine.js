// The canvas: one requestAnimationFrame loop that sleeps when nothing moves.
// Everything a frame touches lives in typed arrays allocated here, once; the
// frame itself allocates nothing. Every animation eases toward a target, so a
// new keystroke only moves the targets and never queues work.

const TAU = Math.PI * 2;
const EASE_MS = 110;         // time constant of score, fade and label easing
const CAM_MS = 90;           // time constant of the camera
const GROW_MS = 440;         // a search link's run from the bar to its node
const STAGGER_MS = 14;       // delay per rank, so the best match leaves first
const STAGGER_MAX = 250;
const RING = 8;              // passages shown around one node
const ITEM_CAP = 96;         // passages shown at once
const ACT_CAP = 400;         // shown plus still fading out
const LABEL_CAP = 64;
const NONE = 0xffffffff;
const ENTER_MS = 900, ENTER_CAP = 24, ENTER_HOLD = 90;   // a new node's flight in, and frames its ring stays afterwards
const NODE_BASE = 0.34, NODE_BASE_COMPACT = 0.26, NODE_ZOOM = 1.12, NODE_MAX = 1.8;   // drawn radius = world radius x min(MAX, BASE + ZOOM x scale)

// The relevance spectrum: one hue, weakest to strongest match, as 64 ready-made colour strings.
const LUT = new Array(64);
export function setRamp(stops) {                 // stops: [[r, g, b], ...] from the palette
  for (let i = 0; i < 64; i++) {
    const f = (i / 63) * (stops.length - 1), a = Math.min(stops.length - 2, f | 0), t = f - a;
    const c = (j) => Math.round(stops[a][j] + (stops[a + 1][j] - stops[a][j]) * t);
    LUT[i] = `rgb(${c(0)},${c(1)},${c(2)})`;
  }
}
setRamp([[183, 211, 246], [109, 167, 236], [42, 120, 214], [24, 79, 149], [13, 30, 66]]);
export const spectrumCss = () => `linear-gradient(90deg, ${LUT[0]}, ${LUT[21]}, ${LUT[42]}, ${LUT[63]})`;
// Relevance to shade. Squared, because the matches of a one-word query sit close together near the top
// of the scale and would otherwise all draw equally dark; the floor keeps the weakest match visible.
export const shade = (rel) => 0.12 + 0.88 * rel * rel;
export const relColor = (rel) => LUT[Math.max(0, Math.min(63, (shade(rel) * 63.999) | 0))];

const RING_COS = new Float32Array(RING), RING_SIN = new Float32Array(RING);
for (let s = 0; s < RING; s++) { const a = Math.PI / 2 + Math.PI / RING + s * TAU / RING; RING_COS[s] = Math.cos(a); RING_SIN[s] = Math.sin(a); }

export function createEngine(canvas, model, { compact = false, labelFont = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' } = {}) {
  const ctx = canvas.getContext('2d');
  const { n, m, x: wx, y: wy, r: baseR, kind, kinds, edges, edgeCount, realEdgeCount, iparent, iord, bySize } = model;
  const K = kinds.length;
  const LABEL_CHARS = compact ? 26 : 32;
  const FONT = `500 13px ${labelFont}`, FONT_HIT = `650 13px ${labelFont}`;

  let W = 1, H = 1, dpr = 1;
  let reduced = false;
  let cInk = '#14181f', cLabel = '#3b4452', cSurface = '#ffffff';
  // camera: screen = world * k + t (x also carries the stage's stretch)
  let k = 1, tx = 0, ty = 0, kT = 1, txT = 0, tyT = 0, kFit = 1, txFit = 0, tyFit = 0, stretch = 1, userView = false;
  // where the search links start, in canvas px
  let oX = 0, oY = 0, oHalf = 0;
  let ringGap = 12;          // px between a node's edge and its ring of claims
  let padTop = 64, padBottom = 44;                 // px kept clear for the search bar and the legend

  const sx = new Float32Array(n), sy = new Float32Array(n), sr = new Float32Array(n);
  const nT = new Float32Array(n), nS = new Float32Array(n), nNext = new Float32Array(n);
  const nGrow = new Float32Array(n), nDelay = new Float32Array(n);
  const nPad = new Float32Array(n), nCnt = new Uint8Array(n), ringMask = new Uint8Array(n);
  const order = new Uint16Array(n);
  let orderN = 0;
  let linkFloor = 0.45;      // opacity of the weakest search link; lower when a query matches many nodes, so strong links stand out
  const lblT = new Uint8Array(n), lblA = new Float32Array(n), lblW = new Float32Array(n);
  const lblText = new Array(n);
  const bx0 = new Float32Array(LABEL_CAP), by0 = new Float32Array(LABEL_CAP), bx1 = new Float32Array(LABEL_CAP), by1 = new Float32Array(LABEL_CAP);

  const iT = new Float32Array(m), iS = new Float32Array(m);
  const iSlot = new Uint8Array(m), iAct = new Uint8Array(m);
  const act = new Uint32Array(ACT_CAP), sel = new Uint32Array(ITEM_CAP);
  let actN = 0;

  const kOn = new Uint8Array(K).fill(1), kA = new Float32Array(K).fill(1);
  let recede = 0, recedeT = 0;
  let hoverNode = -1, hoverItem = -1;
  const enterIdx = new Uint16Array(ENTER_CAP);
  let enterN = 0, enterT = 0, enterHold = 0;

  // frame-time probe: cost of a frame on the main thread, and the gap between frames
  const ST = 1024, stCpu = new Float32Array(ST), stGap = new Float32Array(ST);
  let stN = 0, raf = 0, lastTs = 0, dead = false, dirty = false, settled = false, sized = false;

  ctx.font = FONT;
  for (let i = 0; i < n; i++) {
    const s = model.labels[i];
    lblText[i] = s.length > LABEL_CHARS ? `${s.slice(0, LABEL_CHARS - 1).trimEnd()}…` : s;
    lblW[i] = ctx.measureText(lblText[i]).width;
  }

  function request() { if (!raf && !dead) raf = requestAnimationFrame(frame); }
  function invalidate() { dirty = true; request(); }

  function fit() {
    const b = model.bounds;
    const padSide = 48;
    const bw = b.x1 - b.x0 || 1, bh = b.y1 - b.y0 || 1;
    const availW = Math.max(40, W - 2 * padSide), availH = Math.max(40, H - padTop - padBottom);
    // The layout has no geometric meaning, so it is stretched (within limits) to use the stage's shape.
    kFit = availH / bh; stretch = availW / bw / kFit;
    if (stretch < 0.5) { stretch = 0.5; kFit = availW / (bw * stretch); } else if (stretch > 4.5) stretch = 4.5;
    txFit = padSide + (availW - bw * kFit * stretch) / 2 - b.x0 * kFit * stretch;
    tyFit = padTop + (availH - bh * kFit) / 2 - b.y0 * kFit;
    if (!userView) { kT = kFit; txT = txFit; tyT = tyFit; }      // the camera glides to the new fit; the first frame snaps
  }

  function resize(w, h, ratio, top, bottom) {
    if (top != null) padTop = top;
    if (bottom != null) padBottom = bottom;
    W = Math.max(1, w); H = Math.max(1, h); dpr = ratio || 1;
    fit();
    const pw = Math.round(W * dpr), ph = Math.round(H * dpr);
    if (sized && pw === canvas.width && ph === canvas.height) { invalidate(); return; }   // same backing store: nothing to reallocate
    sized = true; canvas.width = pw; canvas.height = ph;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Sizing a canvas clears it. Redraw now, inside the resize, so no blank frame is ever shown.
    step(0); draw();
    invalidate();
  }

  function setOrigin(x, y, half) { oX = x; oY = y; oHalf = half; invalidate(); }
  function setReducedMotion(v) { reduced = !!v; invalidate(); }
  function setColours(c) { cInk = c.ink; cLabel = c.label; cSurface = c.surface; invalidate(); }

  // ---- search results in: only targets move ----
  function dropFaded() {
    let wr = 0;
    for (let a = 0; a < actN; a++) {
      const id = act[a];
      if (iT[id] === 0) { iAct[id] = 0; iS[id] = 0; } else act[wr++] = id;
    }
    actN = wr;
  }

  function apply(res) {
    const on = !!(res && res.active);
    recedeT = on ? 1 : 0;
    orderN = on ? res.nodeHits : 0;
    if (orderN) linkFloor = Math.max(0.2, Math.min(0.45, 16 / orderN));
    for (let j = 0; j < orderN; j++) {
      const i = res.nodeOrder[j];
      order[j] = i;
      nNext[i] = shade(res.nodeRel[i]);
      if (nT[i] === 0) {                             // newly matched: send a link out, best match first
        if (nS[i] < 0.004) nGrow[i] = 0;
        nDelay[i] = Math.min(STAGGER_MAX, j * STAGGER_MS);
      }
    }
    for (let i = 0; i < n; i++) { nT[i] = nNext[i]; nNext[i] = 0; }

    for (let a = 0; a < actN; a++) iT[act[a]] = 0;
    ringMask.fill(0); nCnt.fill(0);
    if (on) {
      let selN = 0;
      for (let j = 0; j < res.itemHits && selN < ITEM_CAP; j++) {
        const id = res.itemOrder[j], p = iparent[id];
        if (nCnt[p] >= RING) continue;
        nCnt[p]++; sel[selN++] = id;
      }
      for (let s = 0; s < selN; s++) {               // a passage already on screen keeps its place
        const id = sel[s];
        if (!iAct[id]) continue;
        const p = iparent[id], bit = 1 << iSlot[id];
        if (ringMask[p] & bit) continue;
        ringMask[p] |= bit; iT[id] = shade(res.itemRel[id]); sel[s] = NONE;
      }
      for (let s = 0; s < selN; s++) {
        const id = sel[s];
        if (id === NONE) continue;
        const p = iparent[id];
        let slot = iord[id] % RING;
        while (ringMask[p] & (1 << slot)) slot = (slot + 1) % RING;
        if (!iAct[id]) {
          if (actN >= ACT_CAP) dropFaded();
          if (actN >= ACT_CAP) { nCnt[p]--; continue; }
          iAct[id] = 1; act[actN++] = id; iS[id] = 0;
        }
        ringMask[p] |= 1 << slot; iSlot[id] = slot; iT[id] = shade(res.itemRel[id]);
      }
    }
    invalidate();
  }

  function setKind(ki, on) { kOn[ki] = on ? 1 : 0; invalidate(); }

  // ---- camera ----
  function zoomAt(factor, cx, cy) {
    const next = Math.max(kFit * 0.6, Math.min(kFit * 9, kT * factor));
    const f = next / kT;
    txT = cx - (cx - txT) * f; tyT = cy - (cy - tyT) * f; kT = next;
    userView = true; invalidate();
  }
  function panBy(dx, dy) { tx += dx; ty += dy; txT += dx; tyT += dy; userView = true; invalidate(); }
  function resetView() { userView = false; kT = kFit; txT = txFit; tyT = tyFit; invalidate(); }

  // ---- hover ----
  function itemX(id) { const p = iparent[id]; return sx[p] + RING_COS[iSlot[id]] * (sr[p] + ringGap); }
  function itemY(id) { const p = iparent[id]; return sy[p] + RING_SIN[iSlot[id]] * (sr[p] + ringGap); }

  // Returns true when what is under the pointer changed. Read hover() afterwards.
  function pointAt(px, py) {
    let hn = -1, hi = -1, best = 1e9;
    for (let a = 0; a < actN; a++) {
      const id = act[a];
      if (iT[id] === 0) continue;
      const dx = itemX(id) - px, dy = itemY(id) - py, d = dx * dx + dy * dy;
      if (d < 49 && d < best) { best = d; hi = id; }
    }
    if (hi < 0) {
      for (let i = 0; i < n; i++) {
        if (!kOn[kind[i]]) continue;
        const dx = sx[i] - px, dy = sy[i] - py, rr = sr[i] + 5, d = dx * dx + dy * dy;
        if (d < rr * rr && d < best) { best = d; hn = i; }
      }
    }
    if (hn === hoverNode && hi === hoverItem) return false;
    hoverNode = hn; hoverItem = hi; invalidate();
    return true;
  }
  // The node (or the parent of the statement) under a point, without changing what is hovered. -1 when none.
  function hit(px, py) {
    for (let a = 0; a < actN; a++) {
      const id = act[a];
      if (iT[id] === 0) continue;
      const dx = itemX(id) - px, dy = itemY(id) - py;
      if (dx * dx + dy * dy < 49) return iparent[id];
    }
    let hn = -1, best = 1e9;
    for (let i = 0; i < n; i++) {
      if (!kOn[kind[i]]) continue;
      const dx = sx[i] - px, dy = sy[i] - py, rr = sr[i] + 5, d = dx * dx + dy * dy;
      if (d < rr * rr && d < best) { best = d; hn = i; }
    }
    return hn;
  }
  // Brings one node to the middle (full tab only: the compact map always shows everything) and rings it.
  function centreOn(i) {
    if (!compact) {
      if (kT < kFit * 1.7) kT = kFit * 1.7;
      txT = W / 2 - wx[i] * kT * stretch; tyT = (padTop + H - padBottom) / 2 - wy[i] * kT;
      userView = true;
    }
    hoverNode = i; hoverItem = -1; invalidate();
  }
  function enter(list) {
    enterN = Math.min(ENTER_CAP, list.length);
    for (let j = 0; j < enterN; j++) enterIdx[j] = list[j];
    enterT = 0; enterHold = ENTER_HOLD; invalidate();
  }
  function getView() { return userView ? { k: kT, tx: txT, ty: tyT } : null; }
  function setView(v) { if (v) { k = kT = v.k; tx = txT = v.tx; ty = tyT = v.ty; userView = true; invalidate(); } }

  function setHoverNode(i) { if (hoverNode !== i || hoverItem !== -1) { hoverNode = i; hoverItem = -1; invalidate(); } }

  // ---- one frame ----
  function near(cur, target, a) { return Math.abs(target - cur) < 0.003 ? target : cur + (target - cur) * a; }

  function step(dt) {
    let moving = false;
    const snap = reduced || !settled;                // the first frame draws the resting picture outright: nothing fades in on load
    const a = snap ? 1 : 1 - Math.exp(-dt / EASE_MS);
    const ca = snap ? 1 : 1 - Math.exp(-dt / CAM_MS);
    settled = true;

    if (k !== kT || tx !== txT || ty !== tyT) {
      k += (kT - k) * ca; tx += (txT - tx) * ca; ty += (tyT - ty) * ca;
      if (Math.abs(kT - k) < kT * 0.0005 && Math.abs(txT - tx) < 0.05 && Math.abs(tyT - ty) < 0.05) { k = kT; tx = txT; ty = tyT; }
      moving = true;
    }
    const zoom = k / kFit;
    // Radii come in world units; they are drawn larger than true scale when the whole file is on screen, so a
    // record stays a readable dot, and stop growing once zoomed in far enough.
    const rk = Math.min(NODE_MAX, (compact ? NODE_BASE_COMPACT : NODE_BASE) + NODE_ZOOM * k);
    ringGap = Math.max(9, Math.min(18, 7 + 16 * k));
    const kx = k * stretch;
    for (let i = 0; i < n; i++) { sx[i] = wx[i] * kx + tx; sy[i] = wy[i] * k + ty; }
    if (enterN) {                                    // nodes new in this version travel in from beyond the edge of the stage
      enterT = reduced ? 1 : Math.min(1, enterT + dt / ENTER_MS);
      const inv = 1 - enterT, far = (W > H ? W : H) * inv * inv * inv;
      for (let j = 0; j < enterN; j++) {
        const i = enterIdx[j], vx = sx[i] - W / 2, vy = sy[i] - H / 2, len = Math.sqrt(vx * vx + vy * vy) || 1;
        sx[i] += vx / len * far; sy[i] += vy / len * far;
      }
      if (enterT >= 1 && enterHold-- <= 0) enterN = 0;
      moving = true;
    }

    if (recede !== recedeT) { recede = near(recede, recedeT, a); moving = true; }
    for (let j = 0; j < K; j++) if (kA[j] !== kOn[j]) { kA[j] = near(kA[j], kOn[j], a); moving = true; }

    for (let i = 0; i < n; i++) {
      const t = nT[i];
      if (nS[i] !== t) { nS[i] = near(nS[i], t, a); moving = true; }
      if (t > 0) {
        if (nDelay[i] > 0 && !reduced) { nDelay[i] -= dt; moving = true; }
        else if (nGrow[i] < 1) { nGrow[i] = reduced ? 1 : Math.min(1, nGrow[i] + dt / GROW_MS); moving = true; }
      } else if (nS[i] === 0) nGrow[i] = 0;
      const pad = nCnt[i] ? 1 : 0;
      if (nPad[i] !== pad) { nPad[i] = near(nPad[i], pad, a); moving = true; }
      const rr = baseR[i] * rk;
      sr[i] = (rr < 2.4 ? 2.4 : rr) * (1 + 0.22 * Math.min(1, nS[i] * 8));
    }

    let wr = 0;
    for (let q = 0; q < actN; q++) {
      const id = act[q];
      if (iS[id] !== iT[id]) { iS[id] = near(iS[id], iT[id], a); moving = true; }
      if (iT[id] === 0 && iS[id] === 0) iAct[id] = 0; else act[wr++] = id;
    }
    actN = wr;

    // which labels show: matches by rank while searching, otherwise the weightiest; never overlapping
    lblT.fill(0);
    let boxes = 0;
    const room = W * H / 20000;                       // labels the stage has room for, by area
    const cap = Math.min(LABEL_CAP, recedeT ? (compact ? 9 : 16) : Math.round(Math.min(compact ? 22 : 34, room) * Math.min(1.9, zoom)));
    if (hoverNode >= 0) boxes = place(hoverNode, boxes);
    if (recedeT) for (let j = 0; j < orderN && boxes < cap; j++) boxes = place(order[j], boxes);
    else for (let j = 0; j < n && boxes < cap; j++) boxes = place(bySize[j], boxes);
    for (let i = 0; i < n; i++) if (lblA[i] !== lblT[i]) { lblA[i] = near(lblA[i], lblT[i], a); moving = true; }
    return moving;
  }

  // A label is centred under its node, pulled inward where it would run off the side of the stage.
  function labelX(i) { const half = lblW[i] / 2 + 8, x = sx[i]; return x < half ? half : (x > W - half ? W - half : x); }
  function labelY(i) { return sy[i] + sr[i] + 4 + nPad[i] * (ringGap + 4); }

  function place(i, boxes) {
    if (lblT[i] || !kOn[kind[i]]) return boxes;
    const cx = labelX(i), top = labelY(i), half = lblW[i] / 2 + 4;
    if (sx[i] < 0 || sx[i] > W || top < 0 || top > H - padBottom + 6) return boxes;   // off stage, or under the legend
    const x0 = cx - half, x1 = cx + half, y0 = top - 2, y1 = top + 16;
    for (let b = 0; b < boxes; b++) if (x0 < bx1[b] && x1 > bx0[b] && y0 < by1[b] && y1 > by0[b]) return boxes;
    bx0[boxes] = x0; bx1[boxes] = x1; by0[boxes] = y0; by1[boxes] = y1;
    lblT[i] = 1;
    return boxes + 1;
  }

  function link(i) {
    const g = nGrow[i], s = nS[i];
    if (g <= 0 || s <= 0.004) return;
    const inv = 1 - g, e = 1 - inv * inv * inv;
    const px = sx[i], py = sy[i];
    let off = (px - oX) * 0.24;
    if (off > oHalf) off = oHalf; else if (off < -oHalf) off = -oHalf;
    const ox = oX + off;
    const cx = ox + (px - ox) * 0.1, cy = oY + (py - oY) * 0.74;
    // the first e of the curve, by de Casteljau
    const ax = ox + (cx - ox) * e, ay = oY + (cy - oY) * e;
    const bx = cx + (px - cx) * e, by = cy + (py - cy) * e;
    const qx = ax + (bx - ax) * e, qy = ay + (by - ay) * e;
    const col = LUT[(s * 63.999) | 0];
    const lit = i === hoverNode;                     // the link of the node under the pointer, or of the hovered result row
    ctx.globalAlpha = Math.min(1, s * 8) * (lit ? 1 : linkFloor + (1 - linkFloor) * s) * kA[kind[i]];
    ctx.strokeStyle = col;
    ctx.lineWidth = 0.6 + 2.2 * s + (lit ? 1.2 : 0);
    ctx.beginPath(); ctx.moveTo(ox, oY); ctx.quadraticCurveTo(ax, ay, qx, qy); ctx.stroke();
    if (g < 1) { ctx.fillStyle = col; ctx.beginPath(); ctx.arc(qx, qy, 1.4 + 2 * s, 0, TAU); ctx.fill(); }
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = 'round';

    // structural links, one batch
    if (edgeCount) {
      ctx.strokeStyle = cInk; ctx.lineWidth = 1;
      for (let pass = 0; pass < 2; pass++) {        // links Clio holds, then the fainter ones that only group the layout
        ctx.globalAlpha = (pass ? 0.07 : 0.17) * (1 - 0.72 * recede);
        ctx.beginPath();
        for (let e = pass ? realEdgeCount * 2 : 0, end = (pass ? edgeCount : realEdgeCount) * 2; e < end; e += 2) {
          const a = edges[e], b = edges[e + 1];
          if (kA[kind[a]] < 0.5 || kA[kind[b]] < 0.5) continue;
          ctx.moveTo(sx[a], sy[a]); ctx.lineTo(sx[b], sy[b]);
        }
        ctx.stroke();
      }
    }

    // search links: those fading out first, then weakest to strongest so the darkest sits on top
    for (let i = 0; i < n; i++) if (nT[i] === 0 && nS[i] > 0) link(i);
    for (let j = orderN - 1; j >= 0; j--) link(order[j]);

    // the dimmer link from a node to each of its matching passages
    ctx.strokeStyle = LUT[30]; ctx.lineWidth = 1;
    for (let q = 0; q < actN; q++) {
      const id = act[q], p = iparent[id];
      const al = Math.min(1, iS[id] * 8) * Math.max(0, (nGrow[p] - 0.75) * 4) * kA[kind[p]];
      if (al <= 0.01) continue;
      ctx.globalAlpha = al * 0.45;
      ctx.beginPath(); ctx.moveTo(sx[p], sy[p]); ctx.lineTo(itemX(id), itemY(id)); ctx.stroke();
    }

    // every node, batched by kind
    ctx.strokeStyle = cSurface; ctx.lineWidth = 1.5;
    for (let j = 0; j < K; j++) {
      const al = kA[j] * (1 - 0.8 * recede);
      if (al < 0.01) continue;
      ctx.globalAlpha = al; ctx.fillStyle = kinds[j].color;
      const list = kinds[j].nodes;
      ctx.beginPath();
      for (let q = 0; q < list.length; q++) {
        const i = list[q], r = sr[i];
        if (sx[i] < -r || sx[i] > W + r || sy[i] < -r || sy[i] > H + r) continue;
        ctx.moveTo(sx[i] + r, sy[i]); ctx.arc(sx[i], sy[i], r, 0, TAU);
      }
      ctx.fill(); ctx.stroke();
    }

    // matching nodes come forward at full colour
    ctx.strokeStyle = cSurface; ctx.lineWidth = 2;
    for (let i = 0; i < n; i++) {
      const s = nS[i];
      if (s <= 0.004) continue;
      ctx.globalAlpha = Math.min(1, s * 8) * kA[kind[i]];
      ctx.fillStyle = kinds[kind[i]].color;
      ctx.beginPath(); ctx.arc(sx[i], sy[i], sr[i], 0, TAU); ctx.fill(); ctx.stroke();
    }

    // matching passages: small dots, shaded by their own relevance
    ctx.strokeStyle = cSurface; ctx.lineWidth = 1.25;
    for (let q = 0; q < actN; q++) {
      const id = act[q], p = iparent[id], s = iS[id];
      const al = Math.min(1, s * 8) * Math.max(0, (nGrow[p] - 0.75) * 4) * kA[kind[p]];
      if (al <= 0.01) continue;
      ctx.globalAlpha = al; ctx.fillStyle = LUT[(s * 63.999) | 0];
      ctx.beginPath(); ctx.arc(itemX(id), itemY(id), id === hoverItem ? 5.5 : 2.6 + 2 * s, 0, TAU); ctx.fill(); ctx.stroke();
    }

    if (hoverNode >= 0) {
      ctx.globalAlpha = 1; ctx.strokeStyle = cInk; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(sx[hoverNode], sy[hoverNode], sr[hoverNode] + 3, 0, TAU); ctx.stroke();
    }
    if (enterN) {                                    // a ring marks what has just arrived
      ctx.globalAlpha = enterT < 1 ? 1 : Math.min(1, enterHold / 30); ctx.strokeStyle = LUT[44]; ctx.lineWidth = 2;
      ctx.beginPath();
      for (let j = 0; j < enterN; j++) { const i = enterIdx[j]; ctx.moveTo(sx[i] + sr[i] + 5, sy[i]); ctx.arc(sx[i], sy[i], sr[i] + 5, 0, TAU); }
      ctx.stroke();
    }

    // labels: a surface halo under the text keeps them legible over links
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.lineJoin = 'round';
    ctx.strokeStyle = cSurface; ctx.lineWidth = 3.5;
    ctx.font = FONT; ctx.fillStyle = cLabel;
    for (let i = 0; i < n; i++) {
      if (lblA[i] <= 0.02 || nS[i] > 0.004 || i === hoverNode) continue;
      ctx.globalAlpha = lblA[i] * kA[kind[i]];
      const yy = labelY(i);
      const xx = labelX(i);
      ctx.strokeText(lblText[i], xx, yy); ctx.fillText(lblText[i], xx, yy);
    }
    ctx.font = FONT_HIT; ctx.fillStyle = cInk;
    for (let i = 0; i < n; i++) {
      if (lblA[i] <= 0.02 || !(nS[i] > 0.004 || i === hoverNode)) continue;
      ctx.globalAlpha = lblA[i] * kA[kind[i]];
      const yy = labelY(i);
      const xx = labelX(i);
      ctx.strokeText(lblText[i], xx, yy); ctx.fillText(lblText[i], xx, yy);
    }
    ctx.globalAlpha = 1;
  }

  function frame(ts) {
    raf = 0;
    if (dead) return;
    const t0 = performance.now();
    let dt = lastTs ? ts - lastTs : 16.7;
    stGap[stN % ST] = lastTs ? dt : 0;               // 0 marks the first frame after a sleep
    if (dt > 50) dt = 50;
    lastTs = ts; dirty = false;
    const moving = step(dt);
    draw();
    stCpu[stN % ST] = performance.now() - t0;
    stN++;
    if (moving || dirty) request(); else lastTs = 0;
  }

  // Frame numbers since the last reset. Allocates, so it is never called from the loop.
  function stats(reset = false) {
    const count = Math.min(stN, ST);
    const cpu = Array.from(stCpu.subarray(0, count)).sort((p, q) => p - q);
    const gap = Array.from(stGap.subarray(0, count)).filter((g) => g > 0).sort((p, q) => p - q);
    const pct = (arr, f) => (arr.length ? +arr[Math.min(arr.length - 1, Math.floor(arr.length * f))].toFixed(2) : null);
    const out = {
      frames: count,
      cpuMs: { median: pct(cpu, 0.5), p95: pct(cpu, 0.95), max: pct(cpu, 1) },
      frameGapMs: { median: pct(gap, 0.5), p95: pct(gap, 0.95), max: pct(gap, 1) },
      over20ms: gap.filter((g) => g > 20).length,
      over34ms: gap.filter((g) => g > 34).length,
    };
    if (reset) { stN = 0; stCpu.fill(0); stGap.fill(0); }
    return out;
  }

  // One frame run by hand, dt ms after the last: for tests and the benchmark, where requestAnimationFrame
  // is throttled (a background tab). Returns the frame's cost in ms and whether anything is still moving.
  function tick(dt) {
    const t0 = performance.now();
    const moving = step(dt);
    draw();
    tickOut.ms = performance.now() - t0; tickOut.moving = moving;
    return tickOut;
  }
  const tickOut = { ms: 0, moving: false };

  function destroy() { dead = true; if (raf) cancelAnimationFrame(raf); raf = 0; }

  return {
    resize, setOrigin, setReducedMotion, setColours, apply, setKind, zoomAt, panBy, resetView, pointAt, hit, centreOn, enter, getView, setView, setHoverNode, stats, tick, destroy, invalidate,
    hover: () => (hoverItem >= 0 ? { item: hoverItem, node: iparent[hoverItem] } : (hoverNode >= 0 ? { item: -1, node: hoverNode } : null)),
    nodeScreen: (i) => ({ x: sx[i], y: sy[i], r: sr[i] }),
    itemScreen: (id) => ({ x: itemX(id), y: itemY(id), r: 5 }),
    isIdle: () => !raf,
  };
}
