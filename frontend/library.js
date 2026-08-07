/* Your songs, on the screen you actually sit on.
 *
 * This is the third place the same list has appeared, and the first one you pass
 * without looking for it. The other two stay, because they are answering different
 * questions:
 *
 *   - `songs.js` is the drawer inside the roll -- the fastest way to change piece
 *     once you are already playing.
 *   - `sheet.js` is the engraver in Practice -- open a score, read it, hear the
 *     backend play it through FluidSynth.
 *   - this is the shelf: what do I have, and let me start one.
 *
 * Play hands the piece to the roll and goes full screen, which is one call --
 * startGhost does the rest, including the fullscreen request. It never touches
 * Verovio: a play-along has no notation to draw, so someone who only plays along
 * never downloads 7 MB of engraver. The Sheet button inside the roll is where that
 * cost is opted into, on purpose.
 */

import { $, api, fill, h, hms, mod, toast } from './ui.js';
import { startGhost } from './app.js';
import { failureNote, importScores } from './import-scores.js';

export function createLibrary() {
  let scores = [];
  let sig = null;
  let failed = [];

  const el = mod('Songs', h('span', { id: 'lib-count' }, ''),
    h('div.btnrow', { style: { marginBottom: '10px' } },
      h('button.btn', { id: 'lib-add' }, 'Import a MIDI or score…'),
      h('input', {
        type: 'file', id: 'lib-file', hidden: true, multiple: true,
        accept: '.mid,.midi,.musicxml,.mxl,.xml',
        onchange: (e) => importFiles(e.target.files),
      })),
    h('div.scroller', null, h('div.list', { id: 'lib-list' },
      h('div.empty', null, 'loading…'))),
    h('div.note', { style: { marginTop: '10px' } },
      h('strong', null, 'Play'), ' drops the piece onto the roll, full screen: it falls, ',
      'silently, and waits at each chord until you have played it. Once you are there, ',
      h('strong', null, 'Sheet'), ' shows the same piece engraved. Nothing is shipped ',
      'with Keys and nothing leaves your machine.'));

  async function load() {
    try {
      scores = (await api.get('/api/scores')).scores || [];
      // The reasons belong to the import run that produced them, not to whatever
      // reopened the panel an hour later. Dropping them has to move the signature too,
      // or paint() early-returns and the block they were drawn in stays on screen.
      if (failed.length) { failed = []; sig = null; }
      paint();
    } catch { /* the panel still explains itself with an empty shelf */ }
  }

  function paint() {
    const host = $('#lib-list');
    if (!host) return;
    const next = scores.map((s) => `${s.id}:${s.title}`).join('|');
    if (next === sig) return;
    sig = next;
    const count = $('#lib-count');
    if (count) count.textContent = scores.length ? `${scores.length} imported` : '';
    fill(host,
      failureNote(failed),
      scores.length ? scores.map(row) : h('div.empty', null, 'nothing imported yet'));
  }

  function row(s) {
    // No duration is stored, and it is honest arithmetic rather than a guess: an
    // unmarked score reports tempo 0, and 100 is the same fallback startGhost uses.
    const secs = s.quarters ? s.quarters / ((s.tempo || 100) / 60) : 0;
    return h('div.list__row', null,
      // .list__row has no overflow handling of its own, so a long title would push
      // the buttons off the end of the panel.
      h('span', {
        style: { flex: '1', minWidth: 0, overflow: 'hidden',
                 textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
        title: s.composer ? `${s.title} — ${s.composer}` : (s.title || s.name),
      }, s.title || s.name),
      secs ? h('span.mono', null, hms(secs)) : null,
      h('span.tag.tag--cyan', null, `${s.measures} bars`),
      (s.staves || []).length > 1 ? h('span.tag', null, '2 hands') : null,
      s.from_midi ? h('span.tag', null, 'midi') : null,
      (s.warnings || []).length
        ? h('span.tag.tag--amber', { title: s.warnings.join('\n') }, 'note')
        : null,
      h('button.btn', {
        title: 'Play along on the roll, full screen',
        onclick: () => play(s),
      }, 'Play'),
      h('button.btn', { title: 'Remove from the library', onclick: () => remove(s.id) }, '×'));
  }

  async function play(meta) {
    try {
      const payload = await api.get(`/api/scores/${meta.id}/notes`);
      startGhost(payload, meta);
    } catch (err) { toast(err.message, 'bad', 9000); }
  }

  async function remove(id) {
    try {
      scores = (await api.del(`/api/scores/${id}`)).scores || [];
      sig = null;
      paint();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function importFiles(files) {
    const picked = [...(files || [])];
    if (!picked.length) return;
    const solo = picked.length === 1;
    const busy = $('#lib-add');
    if (busy) busy.disabled = true;
    const run = await importScores(picked, (n, total) => {
      if (busy) busy.textContent = solo ? 'Reading…' : `Reading ${n}/${total}…`;
    });
    if (run.scores) scores = run.scores;
    failed = run.failed;
    if (busy) { busy.disabled = false; busy.textContent = 'Import a MIDI or score…'; }
    const input = $('#lib-file');
    if (input) input.value = '';          // so the same file can be picked again
    // A run where every file failed leaves `scores` byte-identical, so paint()'s
    // signature memo would early-return and the summary would never be drawn.
    sig = null;
    paint();
    // One toast for the run however big it is; the per-file reasons are in the block
    // at the top of the shelf.
    if (failed.length) {
      toast(solo ? failed[0] : `${failed.length} of ${picked.length} could not be imported`, 'bad', 9000);
    }
    // Straight onto the roll, the same as the drawer: importing a song IS asking to
    // play it. Picking forty is the opposite intent -- you are stocking the shelf --
    // so a batch plays nothing and leaves you looking at what landed.
    if (solo && run.landed) await play(run.landed);
  }

  return {
    el,
    async init() {
      $('#lib-add')?.addEventListener('click', () => $('#lib-file')?.click());
      await load();
    },
    refresh: load,
    destroy() { scores = []; sig = null; failed = []; },
  };
}
