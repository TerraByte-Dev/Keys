/* Backing tracks -- a shelf of YouTube links, with a player attached.
 *
 * Two constraints shape everything here.
 *
 * **The audio device.** In WASAPI exclusive mode Keys owns the output and the browser
 * gets silence, so a backing track looks broken when it is merely blocked. The panel
 * checks, says so, and offers the one-click switch rather than letting you conclude
 * the feature does not work.
 *
 * **YouTube's terms.** Embedding their player and driving it through the IFrame API is
 * allowed; separating the audio, overlaying the video and caching it are not. So the
 * two things a musician wants -- pitch shift and independent tempo -- are off the
 * table, except for setPlaybackRate, which is a documented player control and moves
 * pitch with speed like a tape machine. Slow practice still works; it just transposes.
 *
 * The API script is fetched the first time you open a track and never before. Keys
 * makes no network request until you ask it to, and this is the only place it can.
 */

import { $, api, h, hms, mod, slider, toast } from './ui.js';

const API_SRC = 'https://www.youtube.com/iframe_api';
const RATES = [0.5, 0.75, 0.9, 1, 1.25, 1.5];

let apiReady = null;

/* One promise for the whole page. YT calls a single global when it loads, so a second
   concurrent request has to wait on the first rather than overwrite the callback. */
function loadApi() {
  if (apiReady) return apiReady;
  apiReady = new Promise((resolve, reject) => {
    if (window.YT?.Player) { resolve(window.YT); return; }
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT); };
    const tag = document.createElement('script');
    tag.src = API_SRC;
    tag.onerror = () => reject(new Error('could not reach YouTube -- are you online?'));
    document.head.append(tag);
  });
  return apiReady;
}

export function createBacking() {
  let tracks = [];
  let exclusive = true;
  let player = null;
  let currentId = null;
  let watch = null;          // the A/B loop watcher
  let listSig = null;

  const el = mod('Backing tracks', 'play along with something',
    h('div.note', null,
      'Paste a YouTube link and it goes on the shelf. Set loop points to grind eight ',
      'bars of a solo without hunting the scrubber, slow it down to learn it, and write ',
      'the key and tempo next to it so you do not work them out twice.'),

    h('div.note.note--warn', { id: 'yt-excl', style: { display: 'none' } },
      h('strong', null, 'Keys owns the speakers right now.'),
      ' Exclusive mode is where the 3 ms comes from, and it means the browser gets ',
      'silence -- a backing track will play with no sound. ',
      h('button.btn', { id: 'yt-share', style: { marginLeft: '8px' } }, 'Switch to shared'),
      ' Latency goes to roughly 10 ms, which is still very playable.'),

    h('div.btnrow', { style: { margin: '12px 0' } },
      h('input', {
        type: 'text', id: 'yt-url', placeholder: 'paste a YouTube link',
        style: { flex: '1', minWidth: '220px' },
        onkeydown: (e) => { if (e.key === 'Enter') add(); },
      }),
      h('button.btn', { onclick: add }, 'Add to shelf')),

    h('div.yt', { id: 'yt-stage', style: { display: 'none' } },
      h('div.yt__frame', null, h('div', { id: 'yt-player' })),
      h('div.yt__side', null,
        h('div.yt__title', { id: 'yt-title' }, ''),
        h('div.btnrow', null,
          h('button.btn', { id: 'yt-play' }, 'Play'),
          h('button.btn', { id: 'yt-a' }, 'Set A'),
          h('button.btn', { id: 'yt-b' }, 'Set B'),
          h('button.btn', { id: 'yt-clear' }, 'Clear loop')),
        h('div.yt__loop', { id: 'yt-loop' }, 'no loop set'),
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Speed'),
            h('span.field__value', { id: 'yt-rate-v' }, '1x')),
          h('div.btnrow', { id: 'yt-rates' },
            RATES.map((r) => h('button.btn', {
              'data-rate': r, onclick: () => setRate(r),
            }, r === 1 ? '1x' : `${r}x`)))),
        h('div.note', null,
          'YouTube changes pitch with speed, like slowing a record. At 0.75x everything ',
          'sounds a fourth or so low -- fine for learning the shapes, not for playing ',
          'along in the original key.'))),

    h('div', { id: 'yt-list' }));

  /* ── shelf ──────────────────────────────────────────────────────────────── */
  function apply(res) {
    if (!res) return;
    if (res.tracks) tracks = res.tracks;
    if ('exclusive' in res) exclusive = res.exclusive;
    if (res.error) toast(res.error, 'bad');
    paint();
  }

  async function add() {
    const url = $('#yt-url').value.trim();
    if (!url) return;
    try {
      const res = await api.post('/api/backing', { url });
      apply(res);
      if (!res.error) { $('#yt-url').value = ''; toast('On the shelf', 'good'); }
    } catch (err) { toast(err.message, 'bad'); }
  }

  const patch = (id, body) => api.post(`/api/backing/${id}`, body)
    .then(apply).catch((err) => toast(err.message, 'bad'));

  async function remove(id) {
    if (currentId === id) closePlayer();
    try { apply(await api.del(`/api/backing/${id}`)); }
    catch (err) { toast(err.message, 'bad'); }
  }

  function paint() {
    $('#yt-excl').style.display = exclusive ? '' : 'none';

    const sig = tracks.map((t) => `${t.id}:${t.title}:${t.key}:${t.bpm}:${t.loop_a}:${t.loop_b}`).join('|');
    if (sig === listSig) return;
    listSig = sig;

    const host = $('#yt-list');
    if (!tracks.length) {
      host.replaceChildren(h('div.empty', null, 'nothing on the shelf yet'));
      return;
    }
    host.replaceChildren(...tracks.map((t) => h('div.track', {
      id: 'tr-' + t.id, class: t.id === currentId ? 'is-on' : '',
    },
      h('div.track__head', null,
        h('button.btn', { onclick: () => open(t) }, t.id === currentId ? 'Open' : 'Play'),
        h('input.track__title', {
          type: 'text', value: t.title,
          onchange: (e) => patch(t.id, { title: e.target.value }),
        }),
        looped(t) ? h('span.tag.tag--amber', null,
          `loop ${hms(t.loop_a)}-${hms(t.loop_b)}`) : null,
        h('span.list__spacer'),
        h('input.track__meta', {
          type: 'text', value: t.key, placeholder: 'key',
          onchange: (e) => patch(t.id, { key: e.target.value }),
        }),
        h('input.track__meta', {
          type: 'number', value: t.bpm || '', placeholder: 'bpm',
          onchange: (e) => patch(t.id, { bpm: e.target.value || 0 }),
        }),
        h('button.btn', { onclick: () => remove(t.id) }, 'Remove')),
      h('input.track__notes', {
        type: 'text', value: t.notes, placeholder: 'notes -- what you are working on here',
        onchange: (e) => patch(t.id, { notes: e.target.value }),
      }))));
  }

  const looped = (t) => (t.loop_b || 0) - (t.loop_a || 0) > 0.5;
  const track = (id) => tracks.find((t) => t.id === id);

  /* ── player ─────────────────────────────────────────────────────────────── */
  async function open(t) {
    $('#yt-stage').style.display = '';
    $('#yt-title').textContent = t.title;
    currentId = t.id;
    listSig = null;
    paint();
    markRate(t.rate || 1);
    showLoop(t);

    let YT;
    try { YT = await loadApi(); }
    catch (err) { toast(err.message, 'bad', 8000); return; }

    if (player) {
      player.loadVideoById({ videoId: t.video, startSeconds: t.loop_a || 0 });
      player.setPlaybackRate(t.rate || 1);
      return;
    }
    player = new YT.Player('yt-player', {
      videoId: t.video,
      // origin is required for the API to talk to the frame at all; 127.0.0.1 is a
      // legitimate origin as far as the player is concerned.
      playerVars: { origin: location.origin, rel: 0, modestbranding: 1,
                    start: Math.floor(t.loop_a || 0) },
      events: {
        onReady: () => { player.setPlaybackRate(t.rate || 1); startWatch(); },
        onStateChange: (e) => {
          const btn = $('#yt-play');
          if (btn) btn.textContent = e.data === 1 ? 'Pause' : 'Play';
        },
        onError: (e) => playerError(e.data, track(currentId)),
      },
    });
  }

  /* Plenty of music is embed-blocked by the rights holder, and the player's own
     "Video unavailable" card does not say which of the several reasons applies. It
     reads as the app being broken, which it is not -- the link is simply not one you
     can play here. */
  function playerError(code, t) {
    const why = {
      2: 'that video id is malformed',
      5: 'the player could not start -- try reloading the page',
      100: 'that video is private, deleted, or does not exist',
      101: 'the owner does not allow this one to be played outside YouTube',
      150: 'the owner does not allow this one to be played outside YouTube',
    }[code] || `the player returned error ${code}`;
    toast(`Cannot play it here: ${why}`, 'bad', 9000);
    const host = $('#yt-loop');
    if (host && t) {
      host.replaceChildren(
        h('span', null, why, ' '),
        h('a', { href: t.url || `https://youtu.be/${t.video}`, target: '_blank',
                 rel: 'noreferrer' }, 'open it on YouTube'));
    }
  }

  function closePlayer() {
    stopWatch();
    currentId = null;
    listSig = null;
    if (player?.stopVideo) { try { player.stopVideo(); } catch { /* already gone */ } }
    $('#yt-stage').style.display = 'none';
  }

  /* The A/B loop. A 10 Hz poll on a video scrubber, which is nothing like musical
     timing -- the rule against setInterval is about notes, and this is a seek. */
  function startWatch() {
    stopWatch();
    watch = setInterval(() => {
      const t = track(currentId);
      if (!t || !player?.getCurrentTime) return;
      const now = player.getCurrentTime();
      const el2 = $('#yt-now');
      if (el2) el2.textContent = hms(now);
      if (looped(t) && now >= t.loop_b) player.seekTo(t.loop_a, true);
    }, 100);
  }

  function stopWatch() {
    if (watch) clearInterval(watch);
    watch = null;
  }

  function showLoop(t) {
    const host = $('#yt-loop');
    if (!host) return;
    host.replaceChildren(
      looped(t)
        ? h('span', null, 'looping ', h('strong', null, hms(t.loop_a)), ' to ',
            h('strong', null, hms(t.loop_b)), ' · at ')
        : h('span', null, 'no loop set · at '),
      h('span', { id: 'yt-now' }, '0:00'));
  }

  function setRate(r) {
    const t = track(currentId);
    if (!t) return;
    player?.setPlaybackRate?.(r);
    markRate(r);
    patch(t.id, { rate: r });
  }

  function markRate(r) {
    $('#yt-rate-v').textContent = r === 1 ? '1x' : `${r}x`;
    for (const b of document.querySelectorAll('#yt-rates .btn')) {
      b.classList.toggle('is-on', Number(b.dataset.rate) === r);
    }
  }

  function wire() {
    $('#yt-play').onclick = () => {
      if (!player) return;
      if (player.getPlayerState?.() === 1) player.pauseVideo();
      else player.playVideo();
    };
    $('#yt-a').onclick = () => mark('loop_a');
    $('#yt-b').onclick = () => mark('loop_b');
    $('#yt-clear').onclick = () => {
      const t = track(currentId);
      if (t) patch(t.id, { loop_a: 0, loop_b: 0 }).then(() => showLoop(track(currentId)));
    };
    $('#yt-share').onclick = async () => {
      try {
        await api.post('/api/audio', { exclusive: false });
        exclusive = false;
        paint();
        toast('Shared mode -- the browser can make sound again', 'good');
      } catch (err) { toast(err.message, 'bad'); }
    };
  }

  function mark(which) {
    const t = track(currentId);
    if (!t || !player?.getCurrentTime) return;
    const at = player.getCurrentTime();
    // Setting A past B is a mistake, not an instruction to play backwards.
    const body = which === 'loop_a'
      ? { loop_a: at, loop_b: Math.max(t.loop_b || 0, at) }
      : { loop_b: at, loop_a: Math.min(t.loop_a || 0, at) };
    patch(t.id, body).then(() => { listSig = null; paint(); showLoop(track(currentId)); });
  }

  return {
    el,
    async init() {
      wire();
      try { apply(await api.get('/api/backing')); }
      catch { /* the shelf is a preference list; the app works without it */ }
    },
    status(s) {
      const now = !!s.engine?.exclusive;
      if (now !== exclusive) { exclusive = now; paint(); }
    },
    destroy() {
      stopWatch();
      player = null;
      currentId = null;
      listSig = null;
    },
  };
}
