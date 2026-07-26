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

function add(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) add(el, kid);
    else el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* One screwed-down section of the panel. */
export function mod(title, aside, ...kids) {
  return h('section.mod', null,
    h('div.mod__head', null,
      h('span.mod__title', null, title),
      h('span.mod__rule'),
      aside ? h('span.mod__aside', null, aside) : null),
    ...kids);
}

export function field(label, valueEl, control) {
  return h('label.field', null,
    h('span.field__label', null, h('span', null, label), valueEl || null),
    control);
}

export function slider(opts) {
  const el = h('input', {
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
