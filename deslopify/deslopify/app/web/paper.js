/* The paper itself, drawn in the page.
 *
 * The browser's own PDF viewer is a closed box: nothing on this page can see a
 * selection made inside it, so nothing could explain one. PDF.js draws each
 * page onto a canvas and lays the page's text over it, invisible but
 * selectable, which makes the paper as selectable as the companion.
 *
 * Pages are drawn only near the screen and let go when far from it: a
 * sixty-page paper at two pixels per point would otherwise hold a gigabyte of
 * canvases. The text layers are kept, because they are small and they are what
 * a search for a passage looks through.
 *
 * Nothing here may wait for ever. A paper on this computer opens in well under
 * a second; one that has not opened after OPEN_MS will not, and the page says
 * so and offers to try again rather than spinning. A drawing that stops
 * working later — its worker gone, a page that will not render, the browser
 * out of canvas memory after a long session — is reported through `onBroken`,
 * so that the page can draw the paper afresh instead of showing blank pages
 * until it is reloaded (Tancredi's report of 2026-10-08).
 *
 * It can be zoomed (Tancredi, 2026-10-23): 100% is the width it fits, and
 * the buttons, a pinch on a trackpad or Ctrl and the wheel make it smaller
 * or larger — drawn afresh at that size, not stretched. One level for every
 * paper, kept in this browser. A page wider than its column then scrolls
 * sideways within it. */

import { authHeaders, h } from './dom.js';

const BASE = '/vendor/pdfjs/';
const MAX_WIDTH = 900;          // px: wider than this is harder to read, not easier
const NEAR = '1400px 0px';      // draw pages this close to the screen
const FAR = '5000px 0px';       // and let go of those further than this
const OPEN_MS = 45000;          // to load the reader, open the file and its pages
const DRAW_MS = 30000;          // to draw one page
// Larger than this, a canvas is refused by some browsers (Safari's limit): a
// page zoomed far in is drawn at a lower pixel density instead.
const MAX_PIXELS = 16_777_216;
export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 4;
// The steps the buttons take; a pinch goes anywhere in between.
const ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
const ZOOM_KEY = 'unpacman.pdfZoom';    // localStorage

function keptZoom() {
  let z = 1;
  try { z = Number(localStorage.getItem(ZOOM_KEY)) || 1; } catch { /* storage off */ }
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}
// Asked for in pieces this large when the file has to be read in pieces: from
// this computer, one large request is faster than many small ones, and it
// leaves the browser's few connections to the server free for everything else.
const CHUNK = 1 << 20;

// Promise.withResolvers, for browsers from before it existed.
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// `promise`, or a rejection saying `what` once `ms` have passed.
function within(promise, ms, what) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(what)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

let lib = null;
async function pdfjs() {
  if (!lib) {
    const loaded = await import(`${BASE}pdf.min.mjs`);
    loaded.GlobalWorkerOptions.workerSrc = `${BASE}pdf.worker.min.mjs`;
    lib = loaded;
  }
  return lib;
}

/* `onBroken(reason)` hears, once, that the drawing has stopped working; `signal`
 * (an AbortSignal) gives up opening, or lets go of the document once open. */
export async function openViewer(root, url, { onBroken = () => {}, signal = null } = {}) {
  const pdf = await within(pdfjs(), OPEN_MS, 'the PDF reader could not be loaded');
  const task = pdf.getDocument({
    url,
    // With the page's key, as every request of the page's own (dom.js): a
    // session cookie lost or replaced must not leave the paper blank.
    httpHeaders: authHeaders(),
    cMapUrl: `${BASE}cmaps/`, cMapPacked: true,
    standardFontDataUrl: `${BASE}standard_fonts/`,
    wasmUrl: `${BASE}wasm/`,
    rangeChunkSize: CHUNK,
    isEvalSupported: false, enableXfa: false,
  });
  const abandon = () => { task.destroy(); };
  signal?.addEventListener('abort', abandon, { once: true });

  const holder = h('div', { class: 'pdf-pages' });
  const pages = [];
  let doc;
  try {
    doc = await within((async () => {
      const opened = await task.promise;
      for (let n = 1; n <= opened.numPages; n++) {
        const page = await opened.getPage(n);
        const canvas = h('canvas', { 'aria-hidden': 'true' });
        const text = h('div', { class: 'textLayer' });
        const el = h('div', { class: 'pdf-page', 'data-page': String(n) }, canvas, text);
        pages.push({ n, page, base: page.getViewport({ scale: 1 }), el, canvas, text,
                     scale: 0, drawn: 0, texted: 0, task: null, ready: null, wanted: false });
        holder.append(el);
      }
      return opened;
    })(), OPEN_MS, 'it took too long to open');
  } catch (err) {
    abandon();
    throw signal?.aborted ? new Error('it was closed') : err;
  }
  root.append(holder);

  // Reported once: the page decides what to do about it.
  let broken = false;
  let failures = 0;
  function fail(err) {
    console.error(err);
    failures += 1;
    // A worker that is gone fails every page from now on; anything else is
    // given a second chance before the drawing is called broken.
    const gone = /destroy|terminat|worker/i.test(String(err?.message || err));
    if (!broken && (gone || failures >= 2)) {
      broken = true;
      onBroken(String(err?.message || err));
    }
  }

  let width = 0;                 // the width the pages fit: the column, at most MAX_WIDTH
  let zoom = keptZoom();
  function layout(force = false) {
    const w = Math.min(holder.clientWidth || MAX_WIDTH, MAX_WIDTH);
    if (!w || (!force && Math.abs(w - width) < 2)) return false;
    width = w;
    for (const p of pages) {
      p.scale = zoom * width / p.base.width;
      p.el.style.width = `${Math.floor(p.base.width * p.scale)}px`;
      p.el.style.height = `${Math.floor(p.base.height * p.scale)}px`;
      // What PDF.js's text layer sizes itself by.
      p.el.style.setProperty('--total-scale-factor', String(p.scale));
      p.el.style.setProperty('--scale-round-x', '1px');
      p.el.style.setProperty('--scale-round-y', '1px');
    }
    // Wider than the column: the pages scroll sideways in it (app.css).
    holder.classList.toggle('zoomed', pages.some(p => p.base.width * p.scale > holder.clientWidth + 1));
    return true;
  }

  async function draw(p) {
    if (broken || (p.drawn === p.scale && p.texted === p.scale)) return;
    p.task?.cancel();
    const viewport = p.page.getViewport({ scale: p.scale });
    const ratio = Math.min(window.devicePixelRatio || 1, 2,
                           Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)));
    const scale = p.scale;
    // Drawn on a canvas of its own and put in place when done, so that the
    // page keeps its last drawing (stretched to its new size by app.css)
    // rather than going blank while it is drawn again.
    const canvas = h('canvas', { 'aria-hidden': 'true' });
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    // No context means the browser has no canvas memory left to give: let go
    // of every page that is not on screen, and ask once more.
    let context = canvas.getContext('2d');
    if (!context) {
      pages.filter(q => q !== p && !q.wanted).forEach(release);
      context = canvas.getContext('2d');
      if (!context) {
        const err = new Error('the browser has no room left to draw pages');
        fail(err);
        p.ready?.reject(err);
        return;
      }
    }
    const task = p.page.render({
      canvasContext: context, viewport,
      transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null,
    });
    p.task = task;
    try {
      await within(task.promise, DRAW_MS, `page ${p.n} would not draw`);
      const old = p.canvas;
      old.replaceWith(canvas);
      old.width = 0;
      old.height = 0;
      p.canvas = canvas;
      p.drawn = scale;
      failures = 0;
    } catch (err) {
      canvas.width = 0;
      canvas.height = 0;
      if (err?.name !== 'RenderingCancelledException') {
        task.cancel();
        fail(err);
        p.ready?.reject(err);
      }
      return;
    } finally {
      if (p.task === task) p.task = null;
    }
    if (p.texted !== scale) {
      p.text.replaceChildren();
      try {
        const layer = new pdf.TextLayer({
          textContentSource: p.page.streamTextContent(), container: p.text, viewport,
        });
        await within(layer.render(), DRAW_MS, `the text of page ${p.n} would not load`);
      } catch (err) {
        fail(err);
        p.ready?.reject(err);
        return;
      }
      p.texted = scale;
      p.ready?.resolve();
    }
  }

  function release(p) {
    if (!p.drawn) return;
    p.task?.cancel();
    p.canvas.width = 0;
    p.canvas.height = 0;
    p.drawn = 0;
  }

  // Which pages are near enough to draw, and which far enough to let go of,
  // is measured against whatever scrolls them: the window, or a pane of the
  // side-by-side view. Measured against the window while in a pane, a page
  // just below the pane's edge would count as far away, and every page would
  // be drawn only once it was already on screen.
  let scroller = null;
  let near = null;
  let far = null;
  function observe() {
    near?.disconnect();
    far?.disconnect();
    near = new IntersectionObserver((entries) => {
      for (const e of entries) {
        const p = pages[Number(e.target.dataset.page) - 1];
        p.wanted = e.isIntersecting;
        if (p.wanted) draw(p);
      }
    }, { root: scroller, rootMargin: NEAR });
    far = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) release(pages[Number(e.target.dataset.page) - 1]);
      }
    }, { root: scroller, rootMargin: FAR });
    for (const p of pages) { near.observe(p.el); far.observe(p.el); }
  }

  layout();
  observe();

  // A narrower column (the notes opening beside it, a window resized) means
  // new sizes for every page, and a redraw for the ones on screen.
  let pending = 0;
  const resized = new ResizeObserver(() => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      if (layout()) pages.filter(p => p.wanted).forEach(draw);
    }, 160);
  });
  resized.observe(holder);

  // Zoomed, the point being looked at stays where it was on the screen: under
  // the pointer for a pinch, else the middle of what shows.
  const zoomed = new Set();      // who shows the level (app.js zoomControls)
  function anchorAt(at) {
    const box = scroller ? scroller.getBoundingClientRect() : { top: 0, height: window.innerHeight };
    const hb = holder.getBoundingClientRect();
    const x = at?.x ?? hb.left + holder.clientWidth / 2;
    const y = at?.y ?? box.top + box.height / 2;
    let p = pages[0];
    for (const q of pages) {
      if (q.el.getBoundingClientRect().top <= y) p = q;
      else break;
    }
    const r = p.el.getBoundingClientRect();
    return { p, x, y, fx: (x - r.left) / r.width, fy: (y - r.top) / r.height };
  }

  function setZoom(next, at = null) {
    const z = Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next)) * 100) / 100;
    if (z === zoom || broken) return;
    const a = anchorAt(at);
    zoom = z;
    try { localStorage.setItem(ZOOM_KEY, String(z)); } catch { /* this page only */ }
    layout(true);
    const r = a.p.el.getBoundingClientRect();
    const dy = r.top + a.fy * r.height - a.y;
    if (scroller) scroller.scrollTop += dy;
    else window.scrollBy(0, dy);
    holder.scrollLeft += r.left + a.fx * r.width - a.x;
    pages.filter(p => p.wanted).forEach(draw);
    zoomed.forEach(fn => fn(z));
  }

  // A pinch on a trackpad (Ctrl and the wheel, as browsers send it — or
  // Safari's own gesture events): shown at once by stretching the pages, then
  // drawn afresh at the new size a moment after it stops.
  let pinch = null;
  let gesture = 0;               // Safari: the level its pinch started from
  function pinchTo(z, x, y) {
    if (broken) return;
    pinch = pinch || { timer: 0 };
    Object.assign(pinch, { z: Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z)), x, y });
    const hb = holder.getBoundingClientRect();
    holder.style.transformOrigin = `${x - hb.left}px ${y - hb.top}px`;
    holder.style.transform = `scale(${pinch.z / zoom})`;
    clearTimeout(pinch.timer);
    pinch.timer = setTimeout(endPinch, 160);
  }
  function endPinch() {
    if (!pinch) return;
    const { z, x, y, timer } = pinch;
    clearTimeout(timer);
    pinch = null;
    holder.style.transform = '';
    holder.style.transformOrigin = '';
    setZoom(z, { x, y });
  }
  holder.addEventListener('wheel', (e) => {
    if (!e.ctrlKey || gesture) return;
    e.preventDefault();
    const d = Math.max(-50, Math.min(50, e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY));
    pinchTo((pinch?.z ?? zoom) * Math.exp(-d / 250), e.clientX, e.clientY);
  }, { passive: false });
  holder.addEventListener('gesturestart', (e) => { e.preventDefault(); gesture = pinch?.z ?? zoom; });
  holder.addEventListener('gesturechange', (e) => {
    e.preventDefault();
    if (gesture) pinchTo(gesture * e.scale, e.clientX, e.clientY);
  });
  holder.addEventListener('gestureend', (e) => { e.preventDefault(); gesture = 0; endPinch(); });

  function current() {
    const box = scroller?.getBoundingClientRect();
    const mid = box ? box.top + box.height / 2 : window.innerHeight / 2;
    let best = pages[0];
    for (const p of pages) {
      const r = p.el.getBoundingClientRect();
      if (r.top <= mid) best = p;
      else break;
    }
    return best?.n || 1;
  }

  return {
    pages: doc.numPages,
    element: holder,
    current,
    // The zoom level (1: the width the pages fit), changed by a step, to a
    // level, or by a pinch; `onZoom(fn)` hears every change, until called off.
    zoom: () => zoom,
    setZoom: (z) => setZoom(z),
    zoomStep(dir) {
      const next = dir > 0 ? ZOOMS.find(s => s > zoom + 0.001) ?? ZOOM_MAX
        : [...ZOOMS].reverse().find(s => s < zoom - 0.001) ?? ZOOM_MIN;
      setZoom(next);
    },
    onZoom(fn) {
      zoomed.add(fn);
      return () => zoomed.delete(fn);
    },
    // The element the pages now scroll in, or null for the window.
    setScroller(el) {
      if (el === scroller) return;
      scroller = el || null;
      observe();
    },
    scrollToPage(n, { smooth = false } = {}) {
      const p = pages[Math.min(Math.max(1, n), pages.length) - 1];
      p.el.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
    },
    // Resolves once a page's text can be searched — drawing it if need be —
    // or with null if it cannot be.
    async textOf(n) {
      const p = pages[n - 1];
      if (!p || broken) return null;
      if (p.texted !== p.scale) {
        p.ready = p.ready || deferred();
        draw(p);
        try { await within(p.ready.promise, DRAW_MS * 2, `page ${n} has no text`); }
        catch { return null; }
        finally { p.ready = null; }
      }
      return p.text;
    },
    // Whether the drawing still answers: its worker is asked for page 1's text
    // (a round trip, where most of PDF.js answers from its own cache). Asked
    // when the page is shown again after a while: a browser may stop the
    // worker of a tab left alone for long, and the paper would stay blank.
    async check() {
      if (broken) return false;
      try {
        const page = await within(doc.getPage(1), 8000, 'the paper stopped answering');
        await within(page.getTextContent(), 8000, 'the paper stopped answering');
        return true;
      } catch (err) {
        if (!broken) {
          broken = true;
          onBroken(String(err?.message || err));
        }
        return false;
      }
    },
    destroy() {
      clearTimeout(pinch?.timer);
      zoomed.clear();
      near?.disconnect();
      far?.disconnect();
      resized.disconnect();
      signal?.removeEventListener('abort', abandon);
      pages.forEach(p => p.task?.cancel());
      // Through its loading task: PDF.js 6 documents have no destroy() of
      // their own, and calling one threw — leaving the page on the view
      // before, when another paper's PDF was opened (2026-10-23).
      task.destroy();
    },
  };
}
