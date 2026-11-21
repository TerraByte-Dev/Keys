/* The rail as the window's title bar.

   In the app window keys.py cuts Windows' caption off and hands the page three calls
   (window.pywebview.api.minimize / toggle_maximize / close). When they are there, the
   rail becomes the title bar: `app-region: drag` on the rail (style.css) gives Windows
   the drag, double-click-to-maximise, Snap and the system menu, and this module only
   draws the three buttons at the right. In a browser tab, headless, or with Windows'
   own title bar chosen in settings, the api is absent and nothing here runs -- the rail
   stays exactly what it always was. */

const ICONS = {
  min: '<path d="M1 5.5h9"/>',
  max: '<rect x="1.5" y="1.5" width="8" height="8" rx="1"/>',
  restore: '<rect x="1.5" y="3.5" width="6" height="6" rx="1"/><path d="M3.5 3.5V2.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-1"/>',
  close: '<path d="M1.5 1.5l8 8M9.5 1.5l-8 8"/>',
};

const svg = (body) =>
  `<svg width="11" height="11" viewBox="0 0 11 11" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

function windowApi() {
  const api = window.pywebview?.api;
  return api && ['minimize', 'toggle_maximize', 'close'].every((k) => typeof api[k] === 'function')
    ? api : null;
}

/* Maximised fills the work area exactly; nothing else does. Read from the page, so a
   maximise done by Windows (double-click, Win+Up, Snap) updates the button too. */
const looksMaximized = () =>
  Math.abs(window.innerWidth - screen.availWidth) <= 2 &&
  Math.abs(window.innerHeight - screen.availHeight) <= 2;

function mount(api) {
  const rail = document.querySelector('.rail');
  if (!rail || rail.querySelector('.winctl')) return;
  document.documentElement.classList.add('is-titlebar');

  const box = document.createElement('div');
  box.className = 'winctl';
  box.innerHTML =
    `<button class="winctl__btn" data-win="min" title="Minimise" aria-label="Minimise">${svg(ICONS.min)}</button>` +
    `<button class="winctl__btn" data-win="max" title="Maximise" aria-label="Maximise">${svg(ICONS.max)}</button>` +
    `<button class="winctl__btn winctl__btn--close" data-win="close" title="Close" aria-label="Close">${svg(ICONS.close)}</button>`;
  rail.append(box);

  const maxBtn = box.querySelector('[data-win="max"]');
  const paintMax = (isMax) => {
    maxBtn.innerHTML = svg(isMax ? ICONS.restore : ICONS.max);
    maxBtn.title = isMax ? 'Restore' : 'Maximise';
    maxBtn.setAttribute('aria-label', maxBtn.title);
    document.documentElement.classList.toggle('is-maximized', isMax);
  };
  paintMax(looksMaximized());
  window.addEventListener('resize', () => paintMax(looksMaximized()));

  box.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-win]');
    if (!btn) return;
    btn.blur();   // a focused title-bar button would eat the next Space meant for the app
    if (btn.dataset.win === 'min') api.minimize();
    else if (btn.dataset.win === 'max') api.toggle_maximize().then((m) => paintMax(!!m)).catch(() => {});
    else api.close();
  });
  // A double-click on a button is two clicks, not a maximise of the window.
  box.addEventListener('dblclick', (e) => e.stopPropagation());
}

export function initTitlebar() {
  const api = windowApi();
  if (api) { mount(api); return; }
  window.addEventListener('pywebviewready', () => {
    const late = windowApi();
    if (late) mount(late);
  }, { once: true });
}
