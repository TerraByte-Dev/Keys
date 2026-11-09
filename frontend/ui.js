/* Shared helpers. Deliberately tiny -- there is no framework here and there does not
 * need to be. Views build DOM once in mount() and mutate specific nodes afterwards;
 * nothing re-renders a subtree sixty times a second. */

/* Hyperscript. h('div.mod', {onclick}, 'text', childNode) */
export function h(spec, props = null, ...kids) {
  const [tag, ...classes] = String(spec).split('.');
  const el = document.createElement(tag || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (props && (props.nodeType || Array.isArray(props) || typeof props === 'string')) {
    kids.unshift(props);
    props = null;
  }
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  add(el, kids);
  return el;
}

/* Replace a host's children the way h() adds them.
 *
 * `replaceChildren()` looks like the same thing and is not: it takes Nodes or
 * STRINGS, so a conditional child that resolves to null is stringified and the
 * word "null" appears on the page. That is exactly what Practice printed above
 * its exercise shelf for anyone who had not run an exercise yet. h() has always
 * skipped null children; this makes the same rule available when clearing. */
export function fill(host, ...kids) {
  if (!host) return host;
  host.replaceChildren();
  add(host, kids);
  return host;
}

function add(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) add(el, kid);
    else el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* One screwed-down section of the panel. Any info() inside it, however deep, is lifted
 * out of the body and into an (i) on the header, so a panel is as tall as its controls
 * and the explanation is one click away. */
export function mod(title, aside, ...kids) {
  const el = h('section.mod', null,
    h('div.mod__head', null,
      h('span.mod__title', null, title),
      h('span.mod__rule'),
      aside ? h('span.mod__aside', null, aside) : null),
    ...kids);
  const infos = $$('.info', el);
  if (infos.length) el.firstChild.insertBefore(tip(...infos), el.querySelector('.mod__rule'));
  return el;
}

/* The way into focus, for a panel's header: EXIT's corners and lettering, at the size of
 * the header's other buttons. Sits at the far right (style.css .mod__focus), so the
 * resize arrows that appear on hover land beside it rather than pushing it about. */
export function focusBtn(onclick, title) {
  const svg = new DOMParser().parseFromString(
    '<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9" viewBox="0 0 12 12" fill="none" '
    + 'stroke="currentColor" stroke-width="1.4" aria-hidden="true">'
    + '<path d="M4.2 1H1v3.2M11 4.2V1H7.8M7.8 11H11V7.8M1 7.8V11h3.2"/></svg>', 'image/svg+xml').documentElement;
  return h('button.mod__focus', { type: 'button', title, onclick: (e) => { e.stopPropagation(); onclick(); } },
    svg, 'FOCUS');
}

/* An explanation: what a panel is for, how to use it, why it works the way it does.
 * Not a warning and not a live readout -- those stay on the panel as a plain .note,
 * because they are what you need without asking. */
export function info(...kids) {
  return h('div.note.info', null, ...kids);
}

/* An (i) that opens `kids` in a popover. mod() builds one from a panel's info() blocks;
 * a card with its own title (Layers' builders, the exercise shelf) calls it directly.
 *
 * A native popover, so light dismiss and Esc come free, and it sits in the top layer
 * where no panel's overflow can clip it. popoverTargetElement rather than a click
 * handler: with a handler, a click on the (i) while open is a light dismiss on
 * pointerdown and a reopen on click. */
export function tip(...kids) {
  const pop = h('div.tip__pop', { popover: 'auto' }, ...kids);
  const btn = h('button.tip', { type: 'button', title: 'What is this?', 'aria-label': 'What is this?' }, 'i');
  btn.popoverTargetElement = pop;
  // Inside a panel header, which starts a drag on pointerdown. Selecting text is not that.
  pop.addEventListener('pointerdown', (e) => e.stopPropagation());
  // Fixed to the viewport, so it would float in place while the panel scrolls away.
  const close = () => pop.hidePopover();
  pop.addEventListener('beforetoggle', (e) => {
    if (e.newState === 'open') {
      pop.classList.remove('is-placed');
      addEventListener('scroll', close, { capture: true, passive: true });
    } else {
      removeEventListener('scroll', close, { capture: true });
    }
  });
  // Measured only once it is open: a closed popover has no size.
  pop.addEventListener('toggle', (e) => {
    if (e.newState !== 'open') return;
    const r = btn.getBoundingClientRect();
    const w = pop.offsetWidth, ht = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.left - 10, innerWidth - w - 8)) + 'px';
    pop.style.top = (r.bottom + 6 + ht > innerHeight - 8
      ? Math.max(8, r.top - 6 - ht) : r.bottom + 6) + 'px';
    pop.classList.add('is-placed');
  });
  return h('span.tip__wrap', null, btn, pop);
}

export function field(label, valueEl, control) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label), valueEl || null),
    control);
}

export function slider(opts) {
  const el = h('input', {
    // Optional, and skipped by h() when absent. Present so a caller can declare the id
    // where the control is built instead of walking the DOM to stamp it on afterwards.
    id: opts.id ?? null,
    type: 'range', min: opts.min, max: opts.max, step: opts.step ?? 1, value: opts.value,
    oninput: (e) => { paint(e.target); opts.oninput?.(Number(e.target.value)); },
    onchange: (e) => opts.onchange?.(Number(e.target.value)),
  });
  paint(el);
  return el;
}

/* The filled portion of a range track is not stylable in CSS alone -- feed it a
 * percentage so the track gradient can render the fill. */
export function paint(input) {
  const min = Number(input.min || 0), max = Number(input.max || 100);
  const pct = max === min ? 0 : ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty('--pct', pct.toFixed(2) + '%');
  // A knob's arc is the same number. Every programmatic write in the app already
  // routes through here -- eight call sites across app.js, play.js and layers.js --
  // so one hook means no caller has to know which control it is holding. A separate
  // paintKnob() would leave all eight drawing a stale angle beside a correct number.
  input._knob?.(pct);
}

/* A dial for a mix amount. Drop-in for slider(): same opts bag, same oninput/onchange
 * contract, and the <input type=range> underneath is real -- invisible, but keyboard
 * operable, and three places in the app reach through a .field to it by hand.
 *
 * Draws no numeral of its own. The readout is the .field__value span every call site
 * already builds; a knob that printed its own number would put two of them, in two
 * fonts, on every control. */
export function knob(opts) {
  const input = slider(opts);
  input.className = 'knob__input';

  // h() is createElement, which cannot build SVG. Kept local rather than teaching h()
  // a namespace branch -- this is the only SVG in the shared helpers.
  const svg = (tag, attrs) => {
    const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const arc = svg('circle', { cx: 23, cy: 23, r: 18, class: 'knob__arc' });
  const dial = svg('svg', { viewBox: '0 0 46 46', class: 'knob__dial', 'aria-hidden': 'true' });
  dial.append(svg('circle', { cx: 23, cy: 23, r: 18, class: 'knob__track' }), arc);

  const el = h('span.knob', { title: opts.title || null }, input, dial);
  // Written onto the INPUT, because that is what paint() is handed.
  input._knob = (pct) => arc.style.setProperty('--turn', pct.toFixed(2));
  // slider() already ran paint() before this hook existed, so the first draw has to
  // happen here or the dial sits at zero until the control is first touched.
  paint(input);

  // Vertical drag. The gesture writes the input and dispatches the events the browser
  // would have, so there is exactly one code path: the oninput/onchange timing below
  // is literally slider()'s own listeners firing. Sites that POST on release keep
  // doing so, and sites that mutate at 60 Hz keep doing that.
  el.addEventListener('pointerdown', (e) => {
    if (e.target === input) return;          // let the range handle its own events
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const min = Number(input.min || 0), max = Number(input.max || 100);
    const step = Number(input.step) || 1;
    const startY = e.clientY, startVal = Number(input.value);
    // 160px of travel covers the whole range -- the same feel as the 46px dial being
    // roughly three and a half of its own diameters, which is what hardware does.
    const span = (max - min) / 160;
    const move = (ev) => {
      const raw = startVal + (startY - ev.clientY) * span;
      const snapped = Math.round(raw / step) * step;
      input.value = String(Math.min(max, Math.max(min, snapped)));
      input.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const up = (ev) => {
      el.releasePointerCapture(ev.pointerId);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      input.dispatchEvent(new Event('change', { bubbles: true }));
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
  });
  return el;
}

export function toggle(label, checked, onchange) {
  return h('label.toggle', null,
    h('input', { type: 'checkbox', checked, onchange: (e) => onchange(e.target.checked) }),
    h('span.toggle__track'),
    label);
}

export function stat(value, label, note, cls = '') {
  return h('div.stat', null,
    h('div.stat__value' + (cls ? '.' + cls : ''), null, value),
    h('div.stat__label', null, label),
    note ? h('div.stat__note', null, note) : null);
}

/* ── formatting ───────────────────────────────────────────────────────────── */
export function hms(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h_ = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h_ ? `${h_}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
            : `${m}:${String(sec).padStart(2, '0')}`;
}

export function humanMinutes(seconds) {
  const m = Math.round((seconds || 0) / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
export const noteName = (n) => `${NAMES[n % 12]}${Math.floor(n / 12) - 1}`;

/* ── server ───────────────────────────────────────────────────────────────── */
async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, body) => request('POST', p, body),
  del: (p) => request('DELETE', p),
};

/* ── toasts ───────────────────────────────────────────────────────────────── */
export function toast(message, kind = '', ms = 3800) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = h('div.toast' + (kind ? '.toast--' + kind : ''), null, message);
  host.append(el);
  setTimeout(() => {
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity = '0';
    el.style.transform = 'translateX(14px)';
    setTimeout(() => el.remove(), 320);
  }, ms);
}
