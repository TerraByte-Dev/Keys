/* The parts of Settings that are about you rather than about the hardware:
 * how it looks, what the keys do, when the clock stops, and what Keys has kept.
 *
 * Split out of views/settings.js because that file is the MIDI-and-audio panel and
 * these four have nothing to do with it. Each export returns a finished `.col-N`
 * element, so the view stays a list of panels.
 *
 * Two things here are deliberate rather than convenient:
 *   - The theme swatches carry `data-theme` themselves, so each one renders in the
 *     palette it is offering. Nothing lists colours twice; tune a theme in
 *     style.css and its swatch follows.
 *   - Every delete is two clicks with the count in between, and the second click is
 *     the only one that sends anything. No modal, no confirm() -- a native dialog
 *     blocks the page and is the one thing a keyboard app must never do while a
 *     note is sounding.
 */

import { ACTIONS, applyTheme, defaultBinds, getBinds, normalKey, rollSpeed, setBinds,
         setRollSpeed, THEMES } from './app.js';
import { resetLayout } from './layout.js';
import { CHAPTERS, startTutorial } from './tour.js';
import { $, api, fill, h, mod, slider, stat, toast } from './ui.js';

const THEME_NOTE = {
  midnight: 'Anodized aluminium and a tungsten lamp. The default.',
  blueprint: 'The same instrument under drafting-table light.',
  phosphor: 'A P1 terminal tube. Green is the live colour here, not amber.',
  paper: 'Daylight. Print-dark ink on warm white, for a bright room.',
  ultraviolet: 'Electric violet over midnight indigo.',
  synthwave: 'Magenta neon and cyan on deep violet.',
  crimson: 'Red neon on charred maroon.',
  tangerine: 'Hot orange on scorched black.',
  ice: 'Cyan-white over deep navy.',
  gold: 'Champagne gold. Quiet and expensive.',
  slate: 'Steel blue. The flattest thing here.',
};

/* ── appearance ─────────────────────────────────────────────────────────────── */
export function themePanel(ctx) {
  const current = ctx.state?.settings?.ui?.theme || 'midnight';

  const pick = async (name) => {
    applyTheme(name);                     // instant; the save is just persistence
    for (const el of document.querySelectorAll('.themecard')) {
      el.classList.toggle('is-on', el.dataset.theme === name);
    }
    try {
      await api.post('/api/settings', { ui: { theme: name } });
      if (ctx.state?.settings?.ui) ctx.state.settings.ui.theme = name;
    } catch (err) { toast(err.message, 'bad'); }
  };

  return h('div.col-6', null, mod('Appearance', 'applies as you click',
    h('div.themes', null, THEMES.map((name) => h(
      'button.themecard' + (name === current ? '.is-on' : ''),
      { 'data-theme': name, onclick: () => pick(name) },
      h('div.themecard__strip', null,
        h('i', { style: { background: 'var(--panel-2)' } }),
        h('i', { style: { background: 'var(--ink)' } }),
        h('i', { style: { background: 'var(--amber)' } }),
        h('i', { style: { background: 'var(--cyan)' } }),
        h('i', { style: { background: 'var(--key-white)' } })),
      h('span.themecard__name', null, name),
      h('span.themecard__note', null, THEME_NOTE[name] || '')))),
    h('div.note', { style: { marginTop: '12px' } },
      'The keyboard, the lamps and the charts all read from the same palette, so a ',
      'theme changes the whole instrument rather than the chrome around it.')));
}

/* ── the session clock ──────────────────────────────────────────────────────── */
export function clockPanel(ctx) {
  const value = ctx.state?.settings?.idle_seconds ?? 12;

  return h('div.col-6', null, mod('Session clock', 'when to stop counting',
    h('label.field', null,
      h('span.field__label', null, h('span', null, 'Stop the clock after'),
        h('span.field__value', { id: 'idle-v' }, fmtIdle(value))),
      slider({
        min: 3, max: 300, step: 1, value,
        oninput: (v) => { $('#idle-v').textContent = fmtIdle(v); },
        onchange: async (v) => {
          try {
            await api.post('/api/settings', { idle_seconds: v });
            if (ctx.state?.settings) ctx.state.settings.idle_seconds = v;
          } catch (err) { toast(err.message, 'bad'); }
        },
      })),
    h('div.note', { style: { marginTop: '10px' } },
      'TODAY counts time you spent ', h('strong', null, 'playing'), ', not time with ',
      'the app open. Go this long without a note and the clock stops; the next note ',
      'starts it again. The gap itself is never counted, so a coffee break cannot ',
      'inflate a day.'),
    h('div.note', { style: { marginTop: '8px' } },
      'Short is honest but choppy on slow pieces, since a held chord sends nothing ',
      'while it rings. Long is smoother and quietly generous. ',
      h('strong', null, '12 seconds'), ' is the default.')));
}

const fmtIdle = (s) => (s >= 60
  ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  : `${s}s`);

/* ── shortcuts ──────────────────────────────────────────────────────────────── */
let capturing = null;          // action id currently listening for a key

export function keysPanel(ctx) {
  const groups = [...new Set(ACTIONS.map((a) => a.group))];

  const el = h('div.col-6', null, mod('Shortcuts', 'click a key to change it',
    h('div.binds', { id: 'binds' },
      groups.flatMap((g) => [
        h('div.binds__head', null, g),
        ...ACTIONS.filter((a) => a.group === g).map((a) =>
          h('div.bind', { 'data-id': a.id },
            h('span.bind__label', null, a.label),
            h('button.bind__key', { onclick: (e) => capture(a.id, e.target, ctx) },
              keyLabel(getBinds()[a.id])))),
      ])),
    h('div.btnrow', { style: { marginTop: '12px' } },
      h('button.btn', { onclick: () => restore(ctx) }, 'Back to the defaults')),
    h('div.note', { style: { marginTop: '10px' } },
      h('strong', null, 'Stop everything'), ' fires even while you are typing in a ',
      'box — it is the one shortcut you need most at the moment you are least ',
      'able to reach for the mouse. The rest stand aside for text fields.')));

  return el;
}

function keyLabel(key) {
  if (!key) return '—';
  return key.length === 1 ? key.toUpperCase() : key;
}

function capture(id, btn, ctx) {
  if (capturing) return;
  capturing = id;
  btn.classList.add('is-capturing');
  btn.textContent = 'press a key';

  const done = (key) => {
    document.removeEventListener('keydown', onKey, true);
    capturing = null;
    btn.classList.remove('is-capturing');
    btn.textContent = keyLabel(key ?? getBinds()[id]);
  };

  const onKey = (e) => {
    // Capture phase and stopped here, or the app's own handler would act on the
    // very key you are trying to reassign.
    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Tab') return;                    // leave a way out by keyboard
    const key = normalKey(e.key);
    if (key === 'Escape' && getBinds()[id] !== 'Escape') { done(null); return; }

    const clash = ACTIONS.find((a) => a.id !== id && getBinds()[a.id] === key);
    const next = { ...getBinds(), [id]: key };
    // A key can only mean one thing, so taking it from another action leaves that
    // one unbound rather than silently shadowed.
    if (clash) {
      delete next[clash.id];
      toast(`${keyLabel(key)} was ${clash.label} — that one is now unset`, 'warn', 5000);
    }
    save(next, ctx);
    done(key);
    if (clash) repaint();
  };

  document.addEventListener('keydown', onKey, true);
}

function repaint() {
  const binds = getBinds();
  for (const row of document.querySelectorAll('.bind')) {
    const btn = row.querySelector('.bind__key');
    if (btn && !btn.classList.contains('is-capturing')) {
      btn.textContent = keyLabel(binds[row.dataset.id]);
    }
  }
}

async function save(map, ctx) {
  const applied = setBinds(map);
  // Replaced, not merged: Settings deep-merges dicts, so an unbound action would
  // keep its old key forever if this were sent as a patch of only what changed.
  const payload = Object.fromEntries(ACTIONS.map((a) => [a.id, applied[a.id] || '']));
  try {
    await api.post('/api/settings', { keys: payload });
    if (ctx.state?.settings) ctx.state.settings.keys = payload;
  } catch (err) { toast(err.message, 'bad'); }
}

function restore(ctx) {
  save(defaultBinds(), ctx);
  repaint();
  toast('Shortcuts back to the defaults', 'good');
}

/* ── your data ──────────────────────────────────────────────────────────────── */
export function dataPanel() {
  const el = h('div.col-12', null, mod('Your data', 'kept on this machine only',
    h('div.data__where', { id: 'data-where' }),
    h('div.data__rows', { id: 'data-rows' }),
    h('div.note', { style: { marginTop: '12px' } },
      'None of this has ever left your computer. Keys has no account, no server and ',
      'no telemetry — the only thing it ever fetches is the public release list, to ',
      'see whether there is a newer version. That happens when Keys opens and when you ',
      'press Check for updates, it sends nothing about you, and the first of the two ',
      'can be switched off in ', h('strong', null, 'About & updates'), '.')));

  load();
  return el;
}

async function load() {
  try {
    const inv = await api.get('/api/data');
    const where = $('#data-where');
    if (where) {
      where.replaceChildren(
        h('span.data__path', null, inv.data_dir),
        h('span.data__size', null, `${(inv.db_bytes / 1048576).toFixed(1)} MB database`));
    }
    const host = $('#data-rows');
    if (host) host.replaceChildren(...inv.items.map(row));
  } catch (err) { toast(err.message, 'bad'); }
}

function row(item) {
  const el = h('div.datarow', null,
    h('div.datarow__main', null,
      h('span.datarow__label', null, item.label),
      h('span.datarow__note', null, item.note)),
    h('span.datarow__detail', null, item.detail),
    h('div.datarow__act', null,
      h('button.btn' + (item.count ? '' : '.is-disabled'), {
        disabled: !item.count,
        onclick: (e) => arm(el, item, e.target),
      }, item.id === 'layout' || item.id === 'settings' ? 'Reset' : 'Delete')));
  return el;
}

/* Two clicks, and the first one only changes the label. The count is in the
   confirm text because "Delete practice history" and "Delete 35 sessions and
   11,138 notes" are different decisions. */
function arm(rowEl, item, btn) {
  const original = btn.textContent;
  const timer = setTimeout(() => disarm(), 6000);

  function disarm() {
    clearTimeout(timer);
    rowEl.classList.remove('is-armed');
    btn.textContent = original;
    btn.onclick = (e) => arm(rowEl, item, e.target);
    cancel.remove();
  }

  const cancel = h('button.btn', { onclick: disarm }, 'Keep it');
  rowEl.classList.add('is-armed');
  btn.textContent = `Yes — ${item.detail}`;
  btn.onclick = async () => {
    clearTimeout(timer);
    btn.disabled = true;
    try {
      const res = await api.post('/api/data/reset',
                                 { what: item.id, confirm: item.id });
      const n = res.removed?.[item.id] ?? 0;
      toast(`${item.label}: ${n.toLocaleString()} removed`, 'good');
      const host = $('#data-rows');
      if (host) host.replaceChildren(...res.items.map(row));
      const where = $('#data-where');
      if (where) {
        where.replaceChildren(
          h('span.data__path', null, res.data_dir),
          h('span.data__size', null, `${(res.db_bytes / 1048576).toFixed(1)} MB database`));
      }
    } catch (err) {
      toast(err.message, 'bad');
      btn.disabled = false;
      disarm();
    }
  };
  btn.after(cancel);
}


/* ── about, and the only request Keys makes on its own behalf ──────────────── */
/* Three buttons, and none of them presses the next one: Check, then Download, then
 * Restart and install. Each step happens because you asked for it in so many words,
 * which is why this is a sequence rather than an "Update now" that does all three.
 *
 * The download lives in the backend and outlives this panel — close the overlay
 * mid-transfer and it keeps going. So the panel asks the server what is happening
 * instead of assuming it started whatever it is showing. That question goes to our
 * own process on localhost; the promise at the bottom of the panel is about GitHub
 * and is untouched by it. */

let poll = null;                       // interval id, and only while one is running

export function aboutPanel(ctx) {
  const st = ctx.state || {};
  // The overlay rebuilds a section by replacing its children, so a poll left over
  // from the last time About was open would be painting into a detached node.
  stopPoll();

  const el = h('div.col-6', null, mod('About', `version ${st.version || '?'}`,
    h('div.stats', null,
      stat(st.version || '?', 'Version', st.frozen ? 'installed build' : 'source checkout'),
      stat(st.frozen ? 'EXE' : 'PY', 'Running as',
           st.frozen ? 'from the installer' : 'from keys.py')),
    h('div.btnrow', { style: { marginTop: '12px' } },
      h('button.btn.btn--lg', { id: 'upd-check', onclick: () => checkUpdate(!!st.frozen) },
        'Check for updates')),
    h('div', { id: 'upd-result', style: { marginTop: '10px' } }),
    // Said before you press anything rather than after the backend has refused: a
    // checkout has no installed copy to replace, and offering a Download button that
    // can only fail is the panel discovering that on your behalf.
    st.frozen ? null : h('div.note', { style: { marginTop: '10px' } },
      'You are running from source, so an update here is a ', h('strong', null, 'git pull'),
      ' rather than a download — there is no installed copy for Keys to replace. ',
      'Checking still works, and the result links to the release.'),
    h('label.toggle', { style: { marginTop: '12px' } },
      h('input', {
        type: 'checkbox',
        checked: ctx.state?.settings?.ui?.update_check_on_launch !== false,
        onchange: async (e) => {
          const on = e.target.checked;
          try {
            await api.post('/api/settings', { ui: { update_check_on_launch: on } });
            if (ctx.state?.settings?.ui) ctx.state.settings.ui.update_check_on_launch = on;
            toast(on ? 'Keys will look for updates when it opens'
                     : 'Keys will only look when you press the button', 'good', 2600);
          } catch (err) { toast(err.message, 'bad'); }
        },
      }),
      h('span.toggle__track'), 'Look for updates when Keys opens'),
    h('div.note', { style: { marginTop: '10px' } },
      'That is the only thing Keys does on the network without you pressing something: ',
      'one request for the public release list, once, when it opens. If there is a ',
      'newer version the gear grows a dot and so does this row — nothing downloads and ',
      'nothing installs. Turn it off and Keys touches the network only when you press ',
      h('strong', null, 'Check for updates'), '.'),
    h('div.note', { style: { marginTop: '8px' } },
      h('strong', null, 'Nothing here updates on its own.'), ' Downloading is a button, ',
      'installing is a second button, and the swap happens when you close the app ',
      'because you told it to. There is no timer and no background install.')));

  if (st.frozen) resume();
  return el;
}

async function checkUpdate(frozen) {
  const btn = $('#upd-check');
  const host = $('#upd-result');
  btn.disabled = true;
  btn.textContent = 'Checking...';
  host.replaceChildren();
  try {
    const r = await api.post('/api/update/check', {});
    if (r.error) {
      host.append(h('div.note.note--warn', null, r.error));
    } else if (r.newer) {
      host.append(h('div.note.note--warn', null,
        h('strong', null, `${r.latest} is available.`), ` You are on ${r.current}. `,
        h('a', { href: r.url, target: '_blank', rel: 'noreferrer' }, 'Open the release'),
        r.download_name ? ` (${r.download_name}, ${mb(r.download_size)} MB)` : ''));
      // Above the release notes, which can be two thousand characters of changelog.
      if (frozen && r.download) {
        host.append(h('div.btnrow', { style: { marginTop: '10px' } },
          h('button.btn.btn--lg', { onclick: (e) => startDownload(e.target, r) },
            `Download ${mb(r.download_size)} MB`)));
      }
      if (r.notes) host.append(h('div.note', { style: { marginTop: '8px' } }, r.notes));
    } else {
      host.append(h('div.note', null,
        `Up to date — ${r.current}`,
        r.latest && r.latest !== r.current ? ` (latest published: ${r.latest})` : ''));
    }
  } catch (err) {
    host.append(h('div.note.note--warn', null, err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check for updates';
  }
}

/* Step two. The backend hands the transfer to a worker thread and answers at once, so
   this returns long before the 55 MB does — the progress comes from the poll. */
async function startDownload(btn, r) {
  btn.disabled = true;
  try {
    await api.post('/api/update/download', {});
  } catch (err) {
    // The refusal names itself -- a source checkout, an application directory this
    // account cannot write, or one already running. All three are worth reading and
    // none of them is a bug, so the message goes on screen as it arrived.
    btn.disabled = false;
    warn(err.message);
    return;
  }
  showProgress({ received: 0, total: r.download_size || 0 });
  startPoll();
}

async function cancelDownload(btn) {
  btn.disabled = true;
  try {
    await api.post('/api/update/cancel', {});
  } catch (err) {
    btn.disabled = false;
    warn(err.message);
  }
  // Nothing is repainted here on purpose. The poll sees the state leave `downloading`
  // and decides what the panel says, so there is one writer whoever caused the change.
}

/* Step three, and the last thing this page does. */
async function applyUpdate() {
  stopPoll();
  fill($('#upd-result'), h('div.note', null,
    h('strong', null, 'Installing.'), ' Keys will close and reopen in a moment. The ',
    'window going away — and this page going dead with it — is the swap working, not ',
    'a crash.'));
  try {
    await api.post('/api/update/apply', {});
  } catch (err) {
    // The server is meant to vanish mid-request, so a dropped connection is the
    // success signal: fetch rejects with a TypeError and nothing else here does. A
    // thrown HTTP status came from a server still alive enough to refuse, and that is
    // a real failure that has to give the button back.
    if (err instanceof TypeError) return;
    showStaged();
    warn(err.message);
  }
}

/* A download you started before closing the overlay is still going. Only the two
   states with something left to do are restored — a stale error from an attempt you
   already walked away from should not be sitting there waiting to greet you. */
async function resume() {
  let s;
  try { s = await api.get('/api/update/status'); } catch { return; }
  if (s.state === 'downloading' || s.state === 'cancelling') { showProgress(s); startPoll(); }
  else if (s.state === 'staged') showStaged();
}

function startPoll() {
  stopPoll();
  // 1 Hz, which is the rate every other moving number in this app already runs at --
  // the status heartbeat. On a 55 MB asset that is a couple of dozen updates, and a
  // download bar is nothing like musical timing.
  poll = setInterval(tick, 1000);
  // Checking again would rebuild #upd-result and take the readout with it.
  const btn = $('#upd-check');
  if (btn) btn.disabled = true;
}

function stopPoll() {
  if (poll) clearInterval(poll);
  poll = null;
  const btn = $('#upd-check');
  if (btn) btn.disabled = false;
}

async function tick() {
  let s;
  try {
    s = await api.get('/api/update/status');
  } catch (err) {
    stopPoll();
    warn(err.message);
    return;
  }
  // Checked after the await rather than before, because nothing clears this interval
  // when the overlay closes or another section paints over us: the readout having
  // gone is the only signal there is, and an interval that outlives its panel runs
  // until the tab does.
  const label = $('#upd-pct');
  if (!label) { stopPoll(); return; }

  if (s.state === 'downloading') {
    label.textContent = progressText(s);
    $('#upd-bar').style.width = pct(s) + '%';
    return;
  }
  // The backend publishes this the instant you press Cancel. The worker itself cannot
  // notice until the in-flight read returns — up to the 30 s socket timeout — and for
  // that whole time the old panel went on counting up over a transfer already told to
  // stop, with the button disabled and nothing saying why.
  if (s.state === 'cancelling') {
    label.textContent = 'Cancelling — waiting for the transfer to stop.';
    return;
  }
  stopPoll();
  if (s.state === 'staged') showStaged();
  else if (s.state === 'error') fill($('#upd-result'),
    h('div.note.note--warn', null, s.error || 'the download did not finish'));
  else fill($('#upd-result'),
    h('div.note', null, 'Download cancelled. Nothing on disk was changed.'));
}

function showProgress(s) {
  fill($('#upd-result'),
    h('div.note', { id: 'upd-pct' }, progressText(s)),
    h('div.bar', { style: { marginTop: '8px' } },
      h('div.bar__fill', { id: 'upd-bar', style: { width: pct(s) + '%' } })),
    h('div.btnrow', { style: { marginTop: '10px' } },
      h('button.btn', { onclick: (e) => cancelDownload(e.target) }, 'Cancel')));
}

function showStaged() {
  fill($('#upd-result'),
    h('div.note', null,
      h('strong', null, 'Downloaded and ready.'), ' Installing closes Keys and reopens ',
      'it on the new version — a few seconds with the window gone. Your practice ',
      'history, settings and recordings are untouched: they live in a different folder ',
      'from the application, which is why that is a fact rather than a hope.'),
    h('div.btnrow', { style: { marginTop: '10px' } },
      h('button.btn.btn--lg', { onclick: applyUpdate }, 'Restart and install')));
}

/* Update trouble stays in the panel instead of going to a toast. You are reading it
   after a decision you made on purpose, and a toast is gone before you have finished. */
function warn(message) {
  const host = $('#upd-result');
  if (host) host.append(h('div.note.note--warn', { style: { marginTop: '8px' } }, message));
}

const mb = (bytes) => ((bytes || 0) / 1048576).toFixed(1);
const pct = (s) => (s.total ? Math.round((s.received / s.total) * 100) : 0);
const progressText = (s) =>
  `Downloading — ${pct(s)}%, ${mb(s.received)} of ${mb(s.total)} MB`;

/* ── panel layout ─────────────────────────────────────────────────────────── */
export function layoutPanel() {
  return h('div.col-6', null, mod('Panel layout', null,
    h('div.note', null,
      'Every panel drags by its header and resizes with the arrows that appear when ',
      'you hover it, at a quarter, half or full width. The arrangement is per tab and ',
      'saves as you go, so put what you actually use at the top.'),
    h('div.btnrow', { style: { marginTop: '12px' } },
      h('button.btn', { onclick: () => resetLayout() },
        'Put every tab back the way it shipped'))));
}

/* ── the tutorial ─────────────────────────────────────────────────────────── */
export function tutorialPanel(ctx) {
  return h('div.col-12', null, mod('Tutorial', `${CHAPTERS.length} chapters`,
    h('div.note', null,
      'The whole manual, and the same thing that runs on first launch. Every chapter ',
      'is one click from every other, so it is also the place to look one thing up.'),
    h('div.btnrow', { style: { marginTop: '12px' } },
      h('button.btn.btn--lg', { onclick: () => startTutorial(ctx) },
        'Start from the beginning')),
    h('div.tour__jump', { style: { marginTop: '12px' } },
      CHAPTERS.map((c) => h('button.btn.btn--sm', {
        onclick: () => startTutorial(ctx, c.id),
      }, c.title)))));
}


/* ── the note roll ────────────────────────────────────────────────────────── */
export function rollPanel(ctx) {
  const value = ctx.state?.settings?.ui?.roll_speed ?? rollSpeed();

  return h('div.col-6', null, mod('Note roll', 'how fast it scrolls',
    h('label.field', null,
      h('span.field__label', null, h('span', null, 'Speed'),
        h('span.field__value', { id: 'roll-speed-v' }, rate(value))),
      slider({
        min: 40, max: 240, step: 5, value,
        oninput: (v) => {
          setRollSpeed(v);                       // live, so you can hear... see it
          $('#roll-speed-v').textContent = rate(v);
        },
        onchange: async (v) => {
          try {
            await api.post('/api/settings', { ui: { roll_speed: v } });
            if (ctx.state?.settings?.ui) ctx.state.settings.ui.roll_speed = v;
          } catch (err) { toast(err.message, 'bad'); }
        },
      })),
    h('div.note', { style: { marginTop: '10px' } },
      'Pixels per second, not a crossing time — so full screen shows ',
      h('strong', null, 'more of your playing'), ' rather than the same amount going ',
      'faster. Slower is easier to read back; faster keeps more of the keyboard clear.'),
    /* The one place both knobs are explained together. A play-along has two, they are
       independent, and confusing them is the obvious mistake: this is where someone
       looking for "make it slower" will arrive, and half the time they want the other
       one. Deliberately NOT a third slider — seconds of lookahead is just height over
       speed, and shipping a control that sets the same number twice is the confusion
       itself. */
    h('div.note', { style: { marginTop: '8px' } },
      'In ', h('strong', null, 'ghost mode'), ' this is the ', h('strong', null, 'reading'),
      ' knob and it changes nothing about the music — it decides how far ahead you can ',
      'see. The ', h('strong', null, 'Tempo'), ' slider on the ghost bar is the ',
      h('strong', null, 'practice'), ' knob: it decides how fast the piece actually goes. ',
      'Slowing the tempo therefore buys reading time twice over, because fewer notes ',
      'fall into the same stretch of screen.'),
    h('div.note', { style: { marginTop: '8px' } },
      h('strong', null, 'F'), ' takes the roll full screen with the lights down. ',
      h('strong', null, 'Esc'), ' brings the app back.')));
}

/* Named `rate`, not `window`. A module-scoped `function window()` shadows the
   global for the whole file, which is the kind of bug that shows up much later in
   something unrelated. */
function rate(px) {
  return `${px} px/s`;
}
