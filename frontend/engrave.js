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
  return toolkit.renderToSVG(page);
}
