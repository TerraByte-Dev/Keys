/* One import, three panels.
 *
 * The drawer, the shelf and the engraver are deliberately three views of one library,
 * answering three different questions -- that part is fine. Each of them owning its own
 * COPY of the upload loop was not: bulk import was added to the drawer's copy and
 * shipped, and the shelf -- the one actually used -- still took a single file, because
 * a fix to one copy is invisible to the other two.
 *
 * Lives beside them rather than in ui.js because ui.js knows nothing about this app:
 * it is hyperscript, formatting and a generic fetch. This knows the /api/scores
 * contract and which extensions the backend can read, which is the opposite kind of
 * thing.
 */

import { h } from './ui.js';

const ACCEPT = /\.(mid|midi|musicxml|mxl|xml)$/i;

/* Send every picked file, ONE AT A TIME, each awaited. Do not turn this into
 * Promise.all. /api/scores is an `async def` with a fully synchronous body, so it runs
 * ON the uvicorn loop that also runs drain_loop() at 60 Hz -- firing N requests at once
 * does not get them handled at once, it gets them handled back to back with nothing
 * yielding in between, which converts N short hiccups in the note display into one
 * uninterrupted multi-second freeze.
 *
 * `onProgress(n, total)` fires before each file so the caller can count in its own
 * panel's words. Returns the server's newest score list -- or null when nothing landed,
 * so a caller whose whole batch failed keeps the list it already had -- the last score
 * to land, and one reason per file that did not.
 */
export async function importScores(files, onProgress) {
  const picked = [...(files || [])];
  const failed = [];
  let scores = null;
  let landed = null;
  for (const [i, file] of picked.entries()) {
    onProgress?.(i + 1, picked.length);
    if (!ACCEPT.test(file.name)) { failed.push(`${file.name} · not a MIDI or a score`); continue; }
    try {
      const res = await fetch('/api/scores', {
        method: 'POST',
        headers: { 'x-filename': file.name, 'content-type': 'application/octet-stream' },
        body: await file.arrayBuffer(),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      scores = body.scores || [];
      landed = body.score;
    } catch (err) {
      failed.push(`${file.name} · ${err.message}`);
    }
  }
  return { count: picked.length, scores, landed, failed };
}

/* The summary block itself, so the three panels cannot word the same failure three
 * ways; WHERE it goes is still each panel's own decision. */
export function failureNote(failed) {
  if (!failed.length) return null;
  return h('div.note.note--warn', { style: { overflowWrap: 'anywhere' } },
    h('strong', null, failed.length === 1
      ? '1 file did not import' : `${failed.length} files did not import`),
    failed.map((f) => h('div', null, f)));
}
