/* Verovio, once.
 *
 * This used to live inside sheet.js, which was fine while the Practice panel was the
 * only thing that engraved. The roll engraves now too, and a second caller makes two
 * facts load-bearing that were previously just true:
 *
 * **There is one toolkit and `loadData` mutates it.** Verovio holds the parsed score
 * in the instance. Two callers loading two scores against one toolkit clobber each
 * other, and the symptom is the wrong music on the screen rather than an error. So
 * the currently-loaded score id is tracked here, beside the thing it describes, and
 * re-loading the same score is skipped.
 *
 * **The 7 MB of WebAssembly stays on demand.** songs.js and sheet.js both promise, in
 * writing, that someone who only ever plays along never downloads the engraver. That
 * promise survives exactly as long as the dynamic `import()` below is only reached
 * from a click. Do not hoist it to a top-level import.
 *
 * A leaf module on purpose: sheet.js already imports app.js, so putting this in
 * app.js would deepen a cycle. Nothing here imports anything of ours.
 */

let toolkit = null;          // the Verovio instance, built once
let loading = null;          // in-flight load, so two clicks make one download
let loadedId = '';           // which score is currently parsed into the toolkit
let measures = null;         // measureMap()'s answer for that score

/* Pages already engraved, newest last. A page is a synchronous render of ~200 ms, and a
   turn mid-song used to pay it in the frame the page changed -- the roll caps a frame's
   step at 0.1 s, so the song lost about 100 ms at every turn. The next page is engraved
   ahead, in idle time (prerenderPage), and the turn only swaps the SVG in.
   Keyed by score id and page, and emptied whenever a score is parsed: the element ids
   in a page are minted per load, so a page from an earlier load of the same score would
   carry ids the measure map no longer has. The options never change after the toolkit is
   built and svgViewBox makes the SVG scale to its box, so a resize or a fullscreen
   toggle leaves every page valid; anything that ever calls setOptions must empty this. */
const PAGES_KEPT = 4;
const pages = new Map();     // `${id}#${page}` -> SVG
const queued = new Set();    // keys waiting for an idle moment

function keep(key, svg) {
  pages.delete(key);
  pages.set(key, svg);
  while (pages.size > PAGES_KEPT) pages.delete(pages.keys().next().value);
}

/* Verovio's own options. Page width/height are in tenths of a staff space -- its unit,
   not pixels -- and the SVG then scales to whatever box we put it in. */
const VEROVIO_OPTS = {
  pageWidth: 2100,
  pageHeight: 2970,
  scale: 40,
  adjustPageHeight: true,
  breaks: 'auto',
  footer: 'none',
  header: 'none',
  spacingStaff: 8,
  svgViewBox: true,          // makes the SVG scale to its container instead of overflowing
  svgHtml5: true,
};

export async function verovio() {
  if (toolkit) return toolkit;
  if (loading) return loading;
  loading = (async () => {
    const { VerovioToolkit } = await import('./vendor/verovio.mjs');
    const createModule = (await import('./vendor/verovio-module.mjs')).default;
    const module = await createModule();
    toolkit = new VerovioToolkit(module);
    toolkit.setOptions(VEROVIO_OPTS);
    return toolkit;
  })();
  try {
    return await loading;
  } finally {
    loading = null;
  }
}

/**
 * Parse a score into the shared toolkit and report how many pages it came to.
 *
 * Cheap to call repeatedly: a score already in the toolkit is not fetched or parsed
 * again. That is what lets renderPage below take an id and simply be correct.
 */
export async function loadScore(id) {
  const tk = await verovio();
  if (loadedId === id) return tk.getPageCount() || 1;

  // .mxl is a zip, so the bytes go in as-is and Verovio sniffs the container.
  const raw = await (await fetch(`/api/scores/${id}/file`)).arrayBuffer();
  const bytes = new Uint8Array(raw);
  const isZip = bytes[0] === 0x50 && bytes[1] === 0x4b;
  // Cleared first: a failed load leaves the toolkit holding whatever it had, and a
  // stale id would then hand the previous score's pages to the next caller.
  loadedId = '';
  measures = null;          // element ids are minted per load, so the map dies with it
  pages.clear();            // ...and so do the pages engraved from it
  const ok = isZip
    ? tk.loadZipDataBuffer(raw)
    : tk.loadData(new TextDecoder().decode(bytes));
  if (!ok) throw new Error('Verovio could not read that file');
  loadedId = id;
  return tk.getPageCount() || 1;
}

/**
 * The SVG for one page of one score. 1-based, like Verovio.
 *
 * Takes the id rather than trusting the toolkit's current contents, because there are
 * two callers now and either can have loaded something else since. Asking by name is
 * the only version of this that cannot silently draw the wrong music.
 */
export async function renderPage(id, page) {
  await loadScore(id);
  const key = `${id}#${page}`;
  const svg = pages.get(key) ?? toolkit.renderToSVG(page);
  keep(key, svg);
  return svg;
}

/** The page's SVG if it is already engraved, else undefined. Never renders, never loads. */
export function cachedPage(id, page) {
  return loadedId === id ? pages.get(`${id}#${page}`) : undefined;
}

/**
 * Engrave a page ahead, when the browser is idle, so the turn to it costs nothing.
 *
 * Only ever for the score already in the toolkit: this never loads the engraver or a
 * score, so it cannot be what downloads 7 MB, and it touches no clock -- the render
 * happens in a moment the page has to spare, and the turn reads the result.
 */
export function prerenderPage(id, page) {
  const key = `${id}#${page}`;
  if (!toolkit || loadedId !== id || pages.has(key) || queued.has(key)) return;
  if (page < 1 || page > (toolkit.getPageCount() || 1)) return;
  queued.add(key);
  const idle = globalThis.requestIdleCallback ?? ((fn) => setTimeout(fn, 50));
  idle(() => {
    queued.delete(key);
    // Another score can have been parsed while this waited.
    if (loadedId === id && !pages.has(key)) keep(key, toolkit.renderToSVG(page));
  });
}

/**
 * Every measure of a score in document order: the id its `<g>` carries in the SVG
 * (`data-id`), the number written on it, and the page it lands on.
 *
 * This is the measure-level following ARCHITECTURE.md calls the honest route, and the
 * only toolkit queries it makes are structural -- which measures exist, what they are
 * numbered, which page holds each. Nothing here touches Verovio's millisecond timemap,
 * which disagrees with score.py's quarter-note timeline on repeats, pickups and grace
 * notes; the CLOCK stays ghost.js's, and this only says where its bars are printed.
 *
 * Built from the MEI Verovio already holds rather than by rendering every page: one
 * serialisation per load, then one page lookup per measure. Cached until the next load,
 * because the ids are generated per load and a stale map would point at nothing.
 */
export async function measureMap(id) {
  await loadScore(id);
  if (measures) return measures;
  const out = [];
  for (const [tag] of toolkit.getMEI().matchAll(/<measure\b[^>]*>/g)) {
    const mid = /xml:id="([^"]+)"/.exec(tag)?.[1];
    if (!mid) continue;
    const n = /\sn="([^"]*)"/.exec(tag)?.[1] ?? '';
    out.push({ id: mid, n, page: toolkit.getPageWithElement(mid) || 1 });
  }
  measures = out;
  return out;
}
