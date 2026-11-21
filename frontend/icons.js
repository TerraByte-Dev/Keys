/* Icons for the pages you build: a page in the nav is its icon, and with the nav folded
 * to icons it is ONLY its icon, so each one has to read at 18px on its own.
 *
 * Drawn like the tab icons in index.html -- viewBox 24, stroke 1.8, round caps and joins,
 * no fill except a dot or a notehead -- so a page sits in the nav as one of the tabs.
 * The inner markup only: icon() wraps it. A saved page names one by key (`page.icon`),
 * and pages.js validPages() holds the name against this list. */

export const ICONS = {
  note: { label: 'Note', svg: '<path d="M14 4v11.5"/><ellipse cx="10.5" cy="16.5" rx="3.5" ry="2.6" transform="rotate(-20 10.5 16.5)" fill="currentColor"/><path d="M14 4c1.5 2.2 4 2.6 4 5.5"/>' },
  beamed: { label: 'Beamed notes', svg: '<path d="M9 17V6l10-2v11"/><path d="M9 9l10-2"/><circle cx="6.5" cy="17.5" r="2.5" fill="currentColor"/><circle cx="16.5" cy="15.5" r="2.5" fill="currentColor"/>' },
  treble: { label: 'Treble clef', svg: '<path d="M12.5 21c1.7 0 2.4-1.2 2.1-2.6L11.4 3.6c-.3-1.3.9-1.9 1.7-.9 1.6 2 .6 4.6-2.2 6.8-2.7 2.1-3.7 3.7-3.2 5.8.5 2 2.5 3 4.5 2.7 2.1-.3 3.3-1.9 3-3.6-.3-1.6-1.7-2.5-3.2-2.3-1.6.2-2.5 1.5-2.2 2.8"/><circle cx="11" cy="20" r="1" fill="currentColor"/>' },
  bass: { label: 'Bass clef', svg: '<path d="M5 9.5a1.6 1.6 0 1 1 1.4 2.4"/><path d="M5.2 9.6C5.8 6.6 8 5 10.6 5c3 0 5 2.2 5 5.2 0 4.6-4.4 8-10 9.8"/><circle cx="18.6" cy="8" r=".9" fill="currentColor"/><circle cx="18.6" cy="12.6" r=".9" fill="currentColor"/>' },
  keys: { label: 'Piano keys', svg: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M9 5v14M15 5v14"/><path d="M7.4 5v7.5h3.2V5M13.4 5v7.5h3.2V5" fill="currentColor"/>' },
  metronome: { label: 'Metronome', svg: '<path d="M9.5 3h5l4 18h-13z"/><path d="M6.8 15.5h10.4"/><path d="M12 15.5l5-9"/><circle cx="17.2" cy="6.2" r="1" fill="currentColor"/>' },
  mic: { label: 'Microphone', svg: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21M9 21h6"/>' },
  headphones: { label: 'Headphones', svg: '<path d="M4 15v-3a8 8 0 0 1 16 0v3"/><rect x="3.5" y="14" width="4" height="6.5" rx="1.6"/><rect x="16.5" y="14" width="4" height="6.5" rx="1.6"/>' },
  guitar: { label: 'Guitar', svg: '<path d="M14.5 9.5L20 4"/><path d="M18.5 2.5l3 3"/><path d="M11.2 8.4c-1.4-.9-3.5-.7-4.6.8-.6.8-.6 1.7-1.5 2.3C3.4 12.6 2.6 15 4.2 17.6l2.2 2.2c2.6 1.6 5 .8 6.1-.9.6-.9 1.5-.9 2.3-1.5 1.5-1.1 1.7-3.2.8-4.6z"/><circle cx="10" cy="14" r="1.6"/><path d="M7 17l-1-1"/>' },
  drum: { label: 'Drum', svg: '<ellipse cx="12" cy="9" rx="8" ry="3"/><path d="M4 9v7c0 1.7 3.6 3 8 3s8-1.3 8-3V9"/><path d="M8 11.8V19M16 11.8V19M12 12v7"/><path d="M15 2.5l-3 4.5M20 3l-5.5 5"/>' },
  wave: { label: 'Waveform', svg: '<path d="M3 12h2M7 8v8M10.5 4v16M14 7v10M17.5 10v4M21 12h-1"/>' },
  sharp: { label: 'Sharp', svg: '<path d="M9.5 4v16M14.5 3v16"/><path d="M6 10l12-3.2M6 17l12-3.2"/>' },
  flat: { label: 'Flat', svg: '<path d="M8 3v18"/><path d="M8 20c3.5-1.6 8-3.8 8-7.2 0-2.6-2.6-3.6-5-2.4L8 12"/>' },
  fermata: { label: 'Fermata', svg: '<path d="M3.5 16a8.5 8.5 0 0 1 17 0"/><circle cx="12" cy="15" r="1.6" fill="currentColor"/>' },
  loop: { label: 'Loop', svg: '<path d="M17 3l3 3-3 3"/><path d="M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20"/><path d="M7 21l-3-3 3-3"/><path d="M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H4"/>' },
  stand: { label: 'Music stand', svg: '<path d="M4 4h16l-2 8H6z"/><path d="M12 12v9M8 21l4-3 4 3"/><path d="M8 7h8M9 9.5h6"/>' },
  sheet: { label: 'Sheet music', svg: '<path d="M6 3h9l4 4v14H6z"/><path d="M14.5 3v4.5H19"/><path d="M9 11h7M9 14h7M9 17h7"/><circle cx="11" cy="17" r="1.1" fill="currentColor"/>' },
  speaker: { label: 'Speaker', svg: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="14.5" r="3.5"/><circle cx="12" cy="7.2" r="1.3"/>' },
  star: { label: 'Star', svg: '<path d="M12 3.5l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>' },
  flame: { label: 'Flame', svg: '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.9 3.3-6 3.9-10.3 2.6 1.6 3.9 3.9 3.9 6.5 1-.7 1.6-1.9 1.9-3.2 2 1.9 3.3 4.4 3.3 7 0 3.6-2.6 6.2-6.5 6.2z"/><path d="M12 21c-1.6 0-2.6-1.1-2.6-2.6 0-1.6 1.3-2.6 2.6-4.2 1.3 1.6 2.6 2.6 2.6 4.2 0 1.5-1 2.6-2.6 2.6z"/>' },
  bolt: { label: 'Bolt', svg: '<path d="M13.5 2.5L5 13.5h6l-1 8 8.5-11h-6z"/>' },
  moon: { label: 'Moon', svg: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>' },
  heart: { label: 'Heart', svg: '<path d="M12 20s-7.5-4.5-7.5-10.2A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.8C19.5 15.5 12 20 12 20z"/>' },
  spark: { label: 'Spark', svg: '<path d="M10 3.5c.8 4.6 2.4 6.2 7 7-4.6.8-6.2 2.4-7 7-.8-4.6-2.4-6.2-7-7 4.6-.8 6.2-2.4 7-7z"/><path d="M18.5 15v5M16 17.5h5"/>' },
};

export const ICON_NAMES = Object.keys(ICONS);
export const DEFAULT_ICON = 'note';

/* Plain UI glyphs for menus (the page right-click menu), same stroke. Not offered in
   the picker and never valid as a page's icon. */
const GLYPHS = {
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
  type: '<path d="M5 7V5h14v2"/><path d="M12 5v14"/><path d="M9 19h6"/>',
  trash: '<path d="M4 7h16"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/><path d="M10 11v5M14 11v5"/>',
};

/* An <svg> for `name`, the default for anything unknown. Built through innerHTML on
   an SVG element, which parses its children in the SVG namespace. */
export function icon(name, cls = 'nav__icon') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = GLYPHS[name] || (ICONS[name] || ICONS[DEFAULT_ICON]).svg;
  return svg;
}
