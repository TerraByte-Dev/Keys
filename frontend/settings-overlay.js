/* The gear: everything about the APP rather than about the instrument.
 *
 * The Settings tab had grown to sixteen panels covering two unrelated things --
 * the rig you plug in (MIDI, audio, effects, SoundFonts) and the program you look
 * at (themes, shortcuts, updates, your data). Sixteen panels is a drawer, not a
 * page, and no amount of rearranging fixes a page that is about two subjects.
 *
 * So the rig keeps the tab, and the program moves behind a gear in the top rail --
 * where a gear means "this application" in every other program anyone uses.
 *
 * The panels themselves are the SAME builders the view used, imported from
 * prefs.js. Nothing is duplicated and nothing is re-styled: a panel is a panel
 * wherever it is mounted. They land outside `#stage`, so the layout system never
 * sees them and they get no drag grips -- correct, because a modal you can
 * rearrange is a modal you can break.
 */

import { aboutPanel, clockPanel, dataPanel, keysPanel, layoutPanel, themePanel,
         tutorialPanel } from './prefs.js';
import { $, h } from './ui.js';

const SECTIONS = [
  { id: 'appearance', label: 'Appearance', build: (ctx) => [themePanel(ctx)] },
  { id: 'shortcuts', label: 'Shortcuts', build: (ctx) => [keysPanel(ctx)] },
  { id: 'session', label: 'Session & layout', build: (ctx) => [clockPanel(ctx), layoutPanel()] },
  { id: 'data', label: 'Your data', build: () => [dataPanel()] },
  { id: 'tutorial', label: 'Tutorial', build: (ctx) => [tutorialPanel(ctx)] },
  { id: 'about', label: 'About & updates', build: (ctx) => [aboutPanel(ctx)] },
];

let overlay = null;
let section = SECTIONS[0].id;

export const settingsOpen = () => overlay !== null;

export function openSettings(ctx, sectionId = null) {
  if (overlay) { closeSettings(); return; }
  if (sectionId && SECTIONS.some((s) => s.id === sectionId)) section = sectionId;

  overlay = h('div.prefs', { id: 'prefs' },
    h('div.prefs__scrim', { onclick: closeSettings }),
    h('div.prefs__sheet', { role: 'dialog', 'aria-label': 'Settings' },
      h('div.prefs__head', null,
        h('span.prefs__title', null, 'Settings'),
        h('span.list__spacer'),
        h('button.prefs__close', { onclick: closeSettings, title: 'Close (Esc)' }, '✕')),
      h('div.prefs__body', null,
        h('nav.prefs__nav', { id: 'prefs-nav' }),
        h('div.prefs__panels', { id: 'prefs-panels' }))));

  document.body.append(overlay);
  paint(ctx);
  // The overlay owns the keyboard while it is up, the same way the tutorial does.
  document.addEventListener('keydown', onKey, true);
}

export function closeSettings() {
  document.removeEventListener('keydown', onKey, true);
  overlay?.remove();
  overlay = null;
}

function onKey(e) {
  if (e.key !== 'Escape') return;
  // Not while a shortcut is being captured -- there Escape means "never mind, keep
  // the binding you had", and the capture handler has already claimed it.
  if (document.querySelector('.bind__key.is-capturing')) return;
  e.preventDefault();
  e.stopPropagation();
  closeSettings();
}

function paint(ctx) {
  const nav = $('#prefs-nav');
  const host = $('#prefs-panels');
  if (!nav || !host) return;

  nav.replaceChildren(...SECTIONS.map((s) => h(
    'button.prefs__item' + (s.id === section ? '.is-on' : ''),
    { onclick: () => { section = s.id; paint(ctx); } }, s.label)));

  const current = SECTIONS.find((s) => s.id === section) || SECTIONS[0];
  host.replaceChildren(h('div.grid', null, ...current.build(ctx)));
  host.scrollTop = 0;
}
