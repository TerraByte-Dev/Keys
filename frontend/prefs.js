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

import { ACTIONS, applyTheme, defaultBinds, getBinds, normalKey, setBinds, THEMES }
  from './app.js';
import { $, api, h, mod, slider, toast } from './ui.js';

const THEME_NOTE = {
  midnight: 'Anodized aluminium and a tungsten lamp. The default.',
  blueprint: 'The same instrument under drafting-table light.',
  phosphor: 'A P1 terminal tube. Green is the live colour here, not amber.',
  paper: 'Daylight. Print-dark ink on warm white, for a bright room.',
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
      h('strong', null, 'All notes off'), ' fires even while you are typing in a ',
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
      'no telemetry — the only thing it ever fetches is the release list, and only ',
      'when you press Check for updates.')));

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
