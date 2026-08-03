/* The songs drawer -- where music comes in, on the screen you play it on.
 *
 * Importing used to live three tabs away in Sheet music, and a .mid dropped there
 * came back as a page of engraved notation. That is the right answer if you wanted
 * to READ it and the wrong one every other time: a MIDI you found in order to learn
 * a song has no business being turned into a score you did not ask for, on a screen
 * you have to leave to start playing.
 *
 * So the drawer sits inside the roll, and a file dropped into it lands on the roll --
 * silent, falling, ready. Sheet music still engraves; it is just no longer the front
 * door.
 *
 * Two things worth knowing:
 *
 * **It never touches Verovio.** The engraver is 7 MB of WebAssembly loaded on demand,
 * and nothing here needs it -- a roll has no notation to draw. Someone who only ever
 * plays along never downloads it.
 *
 * **Drop works on the whole roll, not just the drawer.** Aiming for a 260px panel is
 * a worse target than the screen you are already looking at, and the roll has nothing
 * else a drop could mean.
 */

import { $, api, h, toast } from './ui.js';

const ACCEPT = /\.(mid|midi|musicxml|mxl|xml)$/i;

export function createSongs(onPlay) {
  let scores = [];
  let open = false;
  let sig = null;
  let playingId = '';

  const panel = $('#songs');
  const tab = $('#songs-tab');
  const list = $('#songs-list');

  async function load() {
    try {
      scores = (await api.get('/api/scores')).scores || [];
      paint();
    } catch { /* the drawer still explains itself with an empty list */ }
  }

  function paint() {
    if (!list) return;
    const next = scores.map((s) => `${s.id}:${s.title}:${s.id === playingId}`).join('|');
    if (next === sig) return;
    sig = next;
    if (!scores.length) {
      list.replaceChildren(h('div.empty', null, 'nothing imported yet'));
      return;
    }
    list.replaceChildren(...scores.map((s) => h('div.song', {
      class: s.id === playingId ? 'is-on' : '',
    },
      h('button.song__go', { onclick: () => play(s) },
        h('span.song__name', null, s.title || s.name),
        h('span.song__meta', null,
          `${s.measures} bars`,
          s.staves && s.staves.length > 1 ? ' · 2 hands' : ' · 1 staff',
          s.from_midi ? ' · MIDI' : '')),
      h('button.song__x', {
        title: 'Remove from the library', onclick: (e) => { e.stopPropagation(); remove(s.id); },
      }, '×'))));
  }

  async function play(meta) {
    try {
      const payload = await api.get(`/api/scores/${meta.id}/notes`);
      playingId = meta.id;
      sig = null;
      paint();
      onPlay(payload, meta);
      setOpen(false);          // get out of the way of the thing you just started
    } catch (err) {
      toast(err.message, 'bad', 9000);
    }
  }

  async function remove(id) {
    try {
      scores = (await api.del(`/api/scores/${id}`)).scores || [];
      if (playingId === id) playingId = '';
      sig = null;
      paint();
    } catch (err) { toast(err.message, 'bad'); }
  }

  async function importFile(file) {
    if (!file) return;
    if (!ACCEPT.test(file.name)) {
      toast(`${file.name} is not a MIDI or a score`, 'bad');
      return;
    }
    const busy = $('#songs-add');
    if (busy) { busy.disabled = true; busy.textContent = 'Reading…'; }
    try {
      const res = await fetch('/api/scores', {
        method: 'POST',
        headers: { 'x-filename': file.name, 'content-type': 'application/octet-stream' },
        body: await file.arrayBuffer(),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      scores = body.scores || [];
      sig = null;
      paint();
      // Straight onto the roll. Importing a song IS asking to play it; making you
      // find it in a list you just watched it appear in is a step for nobody.
      if (body.score) await play(body.score);
    } catch (err) {
      toast(err.message, 'bad', 9000);
    } finally {
      if (busy) { busy.disabled = false; busy.innerHTML = 'Import a MIDI&hellip;'; }
      const input = $('#songs-file');
      if (input) input.value = '';        // so the same file can be picked again
    }
  }

  function setOpen(want) {
    open = want === undefined ? !open : !!want;
    panel?.toggleAttribute('hidden', !open);
    tab?.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('is-drawer', open);
    if (open) load();
    return open;
  }

  /* ---- wiring ---------------------------------------------------------- */
  tab?.addEventListener('click', () => setOpen());
  $('#songs-close')?.addEventListener('click', () => setOpen(false));
  $('#songs-add')?.addEventListener('click', () => $('#songs-file')?.click());
  $('#songs-file')?.addEventListener('change', (e) => importFile(e.target.files?.[0]));

  // Drop anywhere on the roll. dragover must be cancelled or the browser navigates
  // to the file instead, which looks exactly like the app crashing.
  const roll = $('#roll');
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  roll?.addEventListener('dragover', (e) => { stop(e); document.body.classList.add('is-dropping'); });
  roll?.addEventListener('dragleave', (e) => { stop(e); document.body.classList.remove('is-dropping'); });
  roll?.addEventListener('drop', (e) => {
    stop(e);
    document.body.classList.remove('is-dropping');
    importFile(e.dataTransfer?.files?.[0]);
  });

  return {
    toggle: setOpen,
    isOpen: () => open,
    refresh: load,
    /* The roll tells us what it is playing, so the list can mark it. */
    setPlaying(id) {
      if (playingId === id) return;
      playingId = id || '';
      sig = null;
      paint();
    },
  };
}
