/* Sheet music: import a score, look at it, hear it.
 *
 * Verovio does the engraving. It is the one renderer that reads MusicXML and .mxl
 * natively AND hands back a machine-readable onset table, and it draws its glyphs as
 * SVG paths rather than webfont characters -- so a score renders with the wifi off,
 * which was a hard requirement rather than a preference. It is LGPL-3.0 and vendored
 * unmodified in vendor/; see vendor/README.md.
 *
 * The 7 MB WASM module is loaded ON DEMAND, the first time you open a score, and
 * never on app start. Someone who never touches sheet music never pays for it.
 *
 * Playback goes through the same FluidSynth sequencer the metronome and the loop
 * station use, from the note timeline the BACKEND parsed -- not from anything Verovio
 * produced. Two readers of the same file would be two chances to disagree, and the
 * backend's is the one a grader will eventually use.
 */

// `paint` is imported under another name: this module already has a local paint()
// for the library list, and the local declaration shadows the import -- so
// paint(slider) would silently repaint the list and leave the slider unfilled.
import { $, api, h, hms, mod, paint as paintSlider, slider, toast } from './ui.js';

let toolkit = null;          // the Verovio instance, built once
let loading = null;          // in-flight load, so two clicks make one download

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

async function verovio() {
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

export function createSheet(ctx) {
  let scores = [];
  let open = null;           // the score being read
  let pages = 1;
  let page = 1;
  let notes = null;          // the backend's timeline for the open score
  let listSig = null;
  let transport = null;      // the server's transport state, never our own guess

  const el = mod('Sheet music', 'bring your own',
    h('div.note', null,
      'Import a ', h('strong', null, '.musicxml'), ' or ', h('strong', null, '.mxl'),
      ' file and Keys will engrave it. Almost every notation program exports one: ',
      'MuseScore, Sibelius, Dorico, Finale, Noteflight, Flat. A ',
      h('strong', null, '.mid'), ' file is not sheet music -- it has no spelling, no ',
      'voices and no layout, and cannot tell E flat from D sharp.'),
    h('div.note', { style: { marginTop: '8px' } },
      'Nothing is shipped with Keys and nothing leaves your machine. Scores live in ',
      h('strong', null, 'scores/'), ' beside your practice history, where an update ',
      'cannot reach them.'),

    h('div.btnrow', { style: { margin: '12px 0' } },
      h('input', {
        type: 'file', id: 'sheet-file', accept: '.musicxml,.mxl,.xml',
        style: { flex: '1', minWidth: '200px' },
        onchange: (e) => importFile(e.target.files?.[0]),
      })),

    h('div.sheet', { id: 'sheet-stage', style: { display: 'none' } },
      h('div.sheet__bar', null,
        h('button.btn', { id: 'sheet-close' }, 'Close'),
        h('span.sheet__title', { id: 'sheet-title' }, ''),
        h('span.list__spacer'),
        h('button.btn', { id: 'sheet-prev' }, '‹'),
        h('span.sheet__page', { id: 'sheet-page' }, ''),
        h('button.btn', { id: 'sheet-next' }, '›')),

      h('div.transport', null,
        h('div.transport__row', null,
          h('button.btn', { id: 'tp-start', title: 'back to the beginning' }, '|‹'),
          h('button.btn', { id: 'tp-back', title: 'back four bars' }, '‹‹'),
          h('button.btn.btn--lg', { id: 'tp-play' }, 'Play'),
          h('button.btn', { id: 'tp-fwd', title: 'forward four bars' }, '››'),
          h('button.btn', { id: 'tp-stop' }, 'Stop'),
          h('span.transport__time', { id: 'tp-time' }, '0:00 / 0:00'),
          h('span.list__spacer'),
          h('label.field.transport__tempo', null,
            h('span.field__label', null, h('span', null, 'Tempo'),
              h('span.field__value', { id: 'tp-bpm-v' }, '100')),
            slider({
              min: 20, max: 240, step: 1, value: 100,
              oninput: (v) => { $('#tp-bpm-v').textContent = String(v); },
              onchange: (v) => send('tempo', { bpm: v }),
            }))),
        h('div.transport__scrub', { id: 'tp-scrub' },
          h('div.transport__fill', { id: 'tp-fill' }))),

      h('div.sheet__paper', { id: 'sheet-paper' })),

    h('div', { id: 'sheet-list' }));

  /* ── library ────────────────────────────────────────────────────────────── */
  async function load() {
    try {
      scores = (await api.get('/api/scores')).scores || [];
      paint();
    } catch { /* the panel still explains itself with an empty library */ }
  }

  async function importFile(file) {
    if (!file) return;
    const input = $('#sheet-file');
    try {
      const res = await fetch('/api/scores', {
        method: 'POST',
        headers: { 'x-filename': file.name, 'content-type': 'application/octet-stream' },
        body: await file.arrayBuffer(),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || res.statusText);
      scores = body.scores || [];
      listSig = null;
      paint();
      toast(`Imported ${body.score?.title || file.name}`, 'good');
      openScore(body.score.id);
    } catch (err) {
      toast(err.message, 'bad', 9000);
    } finally {
      if (input) input.value = '';       // so the same file can be picked again
    }
  }

  function paint() {
    const host = $('#sheet-list');
    if (!host) return;
    const sig = scores.map((s) => `${s.id}:${s.title}`).join('|');
    if (sig === listSig) return;
    listSig = sig;
    if (!scores.length) {
      host.replaceChildren(h('div.empty', null, 'no scores imported yet'));
      return;
    }
    host.replaceChildren(...scores.map((s) => h('div.track', {
      class: s.id === open?.id ? 'is-on' : '',
    },
      h('div.track__head', null,
        h('button.btn', { onclick: () => openScore(s.id) }, 'Open'),
        h('input.track__title', {
          type: 'text', value: s.title || s.name,
          onchange: (e) => api.post(`/api/scores/${s.id}`, { title: e.target.value })
            .then((r) => { scores = r.scores; listSig = null; paint(); })
            .catch((err) => toast(err.message, 'bad')),
        }),
        s.composer ? h('span.tag', null, s.composer) : null,
        h('span.tag.tag--cyan', null, `${s.measures} bars`),
        h('span.tag', null, `${s.notes} notes`),
        (s.warnings || []).length
          ? h('span.tag.tag--amber', { title: s.warnings.join('\n') }, 'note')
          : null,
        h('span.list__spacer'),
        h('button.btn', { onclick: () => remove(s.id) }, 'Remove')))));
  }

  async function remove(id) {
    if (open?.id === id) close();
    try {
      scores = (await api.del(`/api/scores/${id}`)).scores || [];
      listSig = null;
      paint();
    } catch (err) { toast(err.message, 'bad'); }
  }

  /* ── reading ────────────────────────────────────────────────────────────── */
  async function openScore(id) {
    const meta = scores.find((s) => s.id === id);
    if (!meta) return;
    open = meta;
    listSig = null;
    paint();
    $('#sheet-stage').style.display = '';
    $('#sheet-title').textContent = meta.title + (meta.composer ? ` -- ${meta.composer}` : '');
    $('#sheet-paper').replaceChildren(h('div.empty', null, 'engraving...'));

    let tk;
    try {
      tk = await verovio();
    } catch (err) {
      $('#sheet-paper').replaceChildren(h('div.empty', null,
        'the notation engine did not load: ' + err.message));
      return;
    }

    try {
      // .mxl is a zip, so the bytes go in as-is and Verovio sniffs the container.
      const raw = await (await fetch(`/api/scores/${id}/file`)).arrayBuffer();
      const bytes = new Uint8Array(raw);
      const isZip = bytes[0] === 0x50 && bytes[1] === 0x4b;
      const ok = isZip
        ? tk.loadZipDataBuffer(raw)
        : tk.loadData(new TextDecoder().decode(bytes));
      if (!ok) throw new Error('Verovio could not read that file');
      pages = tk.getPageCount() || 1;
      page = 1;
      draw();
      notes = (await api.get(`/api/scores/${id}/notes`)).notes || [];
      // Loads the score into the player and returns its length, so the transport shows
      // "0:00 / 0:34" straight away instead of "0:00 / 0:00" until you press something.
      await send('stop');
    } catch (err) {
      $('#sheet-paper').replaceChildren(h('div.empty', null, err.message));
    }
  }

  function draw() {
    if (!toolkit || !open) return;
    const paper = $('#sheet-paper');
    if (!paper) return;
    paper.innerHTML = toolkit.renderToSVG(page);
    const label = $('#sheet-page');
    if (label) label.textContent = `${page} / ${pages}`;
    $('#sheet-prev').disabled = page <= 1;
    $('#sheet-next').disabled = page >= pages;
  }

  function close() {
    if (open) send('stop');     // a shut panel must not keep playing
    open = null;
    notes = null;
    transport = null;
    listSig = null;
    const stage = $('#sheet-stage');
    if (stage) stage.style.display = 'none';
    paint();
  }

  /* ── the transport ────────────────────────────────────────────── */
  /* Every button is the same request, and the SERVER owns the transport state. This
     panel never decides what playing means -- which is exactly why pressing Play twice
     can no longer start a second copy of the piece on top of the first. */
  async function send(action, body) {
    if (!open) return;
    try {
      const res = await api.post(`/api/scores/${open.id}/transport/${action}`, body || {});
      transport = res.transport;
      paintTransport();
    } catch (err) { toast(err.message, 'bad'); }
  }

  function paintTransport() {
    const t = transport;
    if (!t || !$('#tp-play')) return;
    const playing = t.state === 'playing';
    $('#tp-play').textContent = playing ? 'Pause' : 'Play';
    $('#tp-play').classList.toggle('is-on', playing);
    $('#tp-time').textContent = `${hms(t.seconds)} / ${hms(t.total_seconds)}`;
    $('#tp-fill').style.width =
      (t.total ? Math.max(0, Math.min(100, (t.at / t.total) * 100)) : 0).toFixed(2) + '%';
    // Left alone while you are dragging it, or the 1 Hz frame fights your hand.
    const sl = document.querySelector('.transport__tempo input');
    if (sl && document.activeElement !== sl) {
      sl.value = Math.round(t.bpm);
      paintSlider(sl);
      $('#tp-bpm-v').textContent = String(Math.round(t.bpm));
    }
  }

  function wire() {
    $('#sheet-close').onclick = close;
    $('#sheet-prev').onclick = () => { if (page > 1) { page--; draw(); } };
    $('#sheet-next').onclick = () => { if (page < pages) { page++; draw(); } };

    $('#tp-play').onclick = () =>
      send(transport?.state === 'playing' ? 'pause' : 'play');
    $('#tp-stop').onclick = () => send('stop');
    $('#tp-start').onclick = () => send('seek', { at: 0 });
    // Four bars of four, which is what "back a bit" means at a piano.
    $('#tp-back').onclick = () => send('seek', { at: Math.max(0, (transport?.at || 0) - 16) });
    $('#tp-fwd').onclick = () => send('seek', { at: (transport?.at || 0) + 16 });

    // Click the bar to go there. A scrub IS a seek and a seek is a reschedule, so
    // there is nothing to drag against -- the events are already in the queue.
    $('#tp-scrub').onclick = (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      send('seek', { at: frac * (transport?.total || 0) });
    };
  }

  return {
    el,
    async init() { wire(); await load(); },

    /* The playhead rides the 1 Hz status frame; nothing polls. */
    status(s) {
      if (!open || !s.transport) return;
      // Only when it is OUR score: this panel can be open on one piece while the
      // player still holds another.
      if (s.transport.score_id === open.id) {
        transport = s.transport;
        paintTransport();
      }
    },

    destroy() {
      if (open) send('stop');
      open = null; notes = null; listSig = null; transport = null;
    },
  };
}
