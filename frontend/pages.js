/* Pages -- tabs you build yourself, out of the panels the four working tabs already have.
 *
 * A tester soloing over Jam wanted the scale highlighter and Chords & scales on screen
 * beside it, and they live in Play and Tools. Rather than move panels between tabs
 * (every tab has a reason to be shaped the way it is), a page MOUNTS several whole views
 * side by side and shows only the panels you picked from each.
 *
 * Each view at most once per page, and that rule is what makes this cheap: every view
 * module is a singleton with module-scoped state and document-wide ids, so two copies of
 * one in a document would collide, but one each of four different ones never does. The
 * views mount exactly as they do on their own tab and do not know they are on a page.
 *
 * The panel list is a static registry rather than read off the DOM, because the builder
 * has to offer panels of views that are not mounted. ui_check mounts each view and holds
 * this list against the real data-panel slugs, so it cannot quietly drift.
 *
 * Saved as `ui.pages` in settings. Lists replace whole there, so every save posts the
 * complete list, and every load goes through validPages() -- a hand-edited or stale
 * file loses the bad entries, never the app. */

import { api, h, mod } from './ui.js';
import { DEFAULT_ICON, ICONS, ICON_NAMES, icon } from './icons.js';

export const MAX_PAGES = 6;
export const PAGE_VIEWS = ['play', 'practice', 'layers', 'tools', 'stats'];
const VIEW_TITLES = { play: 'Play', practice: 'Practice', layers: 'Layers', tools: 'Tools', stats: 'Stats' };

/* slug = what layout.js derives from the panel's title (its data-panel), or the
   data-slot of a nested grid that is not a layout panel (Practice's exercise shelf).
   min = the narrowest the panel can render, in px, in its narrow layout (style.css
   `@container panel (max-width: 440px)`): what the builder holds a pane's width against.
   ui_check measures every one in the real DOM, so a panel that grows cannot leave its
   number behind. The shelf has none -- it is a grid of cards that reflows to any width. */
export const PANELS = [
  { view: 'play', slug: 'instruments', title: 'Instruments', min: 210 },
  { view: 'play', slug: 'effects', title: 'Effects', min: 170 },
  { view: 'play', slug: 'scale-highlighter', title: 'Scale highlighter', min: 230 },
  { view: 'play', slug: 'tempo', title: 'Tempo (metronome)', min: 160 },
  { view: 'play', slug: 'meter', title: 'Meter', min: 150 },
  { view: 'play', slug: 'tempo-ramp', title: 'Tempo ramp', min: 220 },
  { view: 'play', slug: 'click-kit', title: 'Click kit', min: 190 },
  { view: 'play', slug: 'loop-station', title: 'Loop station', min: 220 },
  { view: 'practice', slug: 'jam', title: 'Jam', min: 240 },
  { view: 'practice', slug: 'songs', title: 'Songs', min: 180 },
  { view: 'practice', slug: 'exercises', title: 'Exercises', min: 160 },
  { view: 'practice', slug: 'now', title: 'Now', min: 130 },
  { view: 'practice', slug: 'timing', title: 'Timing', min: 180 },
  { view: 'layers', slug: 'layout', title: 'Layout', min: 160 },
  { view: 'layers', slug: 'build-one', title: 'Build one', min: 220 },
  { view: 'layers', slug: 'zone-editor', title: 'Zone editor', min: 190 },
  { view: 'tools', slug: 'touch-response', title: 'Touch response', min: 200 },
  { view: 'tools', slug: 'pedal', title: 'Pedal', min: 280 },
  { view: 'tools', slug: 'chords-scales', title: 'Chords & scales', min: 200 },
  { view: 'tools', slug: 'chords-in-this-key', title: 'Chords in this key', min: 210 },
  { view: 'tools', slug: 'backing-tracks', title: 'Backing tracks', min: 260 },
  { view: 'stats', slug: 'all-time', title: 'All time', min: 160 },
  { view: 'stats', slug: 'activity', title: 'Activity', min: 160 },
  { view: 'stats', slug: 'keys-you-have-played', title: 'Keys you have played', min: 220 },
  { view: 'stats', slug: 'what-key-you-play-in', title: 'What key you play in', min: 220 },
  { view: 'stats', slug: 'note-circle', title: 'Note circle', min: 160 },
  { view: 'stats', slug: 'chords', title: 'Chords', min: 160 },
  { view: 'stats', slug: 'chord-qualities', title: 'Chord qualities', min: 190 },
  { view: 'stats', slug: 'intervals', title: 'Intervals', min: 170 },
  { view: 'stats', slug: 'when-you-practise', title: 'When you practise', min: 210 },
  { view: 'stats', slug: 'which-day', title: 'Which day', min: 180 },
  { view: 'stats', slug: 'velocity', title: 'Velocity', min: 190 },
  { view: 'stats', slug: 'notes-per-minute', title: 'Notes per minute', min: 230 },
  { view: 'stats', slug: 'session-length', title: 'Session length', min: 180 },
  { view: 'stats', slug: 'sounds-you-reach-for', title: 'Sounds you reach for', min: 220 },
  { view: 'stats', slug: 'sight-reading', title: 'Sight reading', min: 200 },
];

const panelsOf = (view) => PANELS.filter((p) => p.view === view).map((p) => p.slug);

/* What "Start from" fills the builder with. Solo practice is two across, not three: a
   third of a 1280 window gave Jam a 344px pane, and Jam in it ran 1711px tall. Jam has
   its column to itself; the two panels you solo with stack in the other. Everything
   puts Stats under Practice and Tools, which is the shorter column of the two. */
const all = (view, col) => ({ view, col, panels: panelsOf(view) });
export const STARTERS = {
  solo: {
    label: 'Solo practice',
    cols: 2,
    panes: [
      { view: 'practice', col: 0, panels: ['jam'] },
      { view: 'play', col: 1, panels: ['scale-highlighter', 'tempo'] },
      { view: 'tools', col: 1, panels: ['chords-scales'] },
    ],
  },
  everything: {
    label: 'Everything',
    cols: 2,
    panes: [all('play', 0), all('layers', 0), all('practice', 1), all('tools', 1), all('stats', 1)],
  },
  blank: { label: 'Blank', cols: 2, panes: [] },
};

/* ── validation ───────────────────────────────────────────────────────────── */
export function validPages(raw) {
  const out = [];
  const ids = new Set();
  for (const p of Array.isArray(raw) ? raw : []) {
    if (out.length >= MAX_PAGES) break;
    if (!p || typeof p !== 'object') continue;
    const id = typeof p.id === 'string' && /^[a-z0-9-]{1,24}$/.test(p.id) ? p.id : null;
    if (!id || id === 'new' || ids.has(id)) continue;
    const panes = [];
    const seen = new Set();
    for (const pane of Array.isArray(p.panes) ? p.panes : []) {
      const view = pane?.view;
      if (!PAGE_VIEWS.includes(view) || seen.has(view)) continue;
      const known = panelsOf(view);
      const panels = [...new Set(Array.isArray(pane.panels) ? pane.panels : [])]
        .filter((s) => known.includes(s));
      if (!panels.length) continue;
      seen.add(view);
      panes.push({ view, panels, col: pane.col });
    }
    if (!panes.length) continue;
    ids.add(id);
    const name = typeof p.name === 'string' ? p.name.trim().slice(0, 40) : '';
    const cols = [1, 2, 3].includes(p.cols) ? p.cols : 2;
    // A page from before columns (0.14, 0.15) was a grid filled row by row, so pane i
    // stood in column i % cols: that is the column it keeps, in the order it had.
    panes.forEach((pane, i) => {
      if (!(Number.isInteger(pane.col) && pane.col >= 0 && pane.col < cols)) pane.col = i % cols;
    });
    out.push({
      id,
      name: name || `Page ${out.length + 1}`,
      // A page from before icons, or one naming an icon this build does not draw.
      icon: ICON_NAMES.includes(p.icon) ? p.icon : DEFAULT_ICON,
      cols,
      panes,
    });
  }
  return out;
}

export async function savePages(list) {
  return api.post('/api/settings', { ui: { pages: list } });
}

/* ── the page itself ──────────────────────────────────────────────────────── */
/* A header strip and the columns. A column is a stack: its panes top to bottom, each
   as tall as what it holds, and the column the one thing that scrolls -- each column
   on its own, so a long Play beside a short Jam never drags Jam out of view. There are
   no rows across columns. A grid of rows (0.14, 0.15) made four panes at two columns a
   2x2 whose second row started wherever the taller pane above it ended: quarters.

   A column nothing was put in is left out rather than drawn empty, so three columns
   chosen for two panes is two columns as wide as the stage allows. */
export function columnsOf(page) {
  const used = [...new Set(page.panes.map((p) => p.col))].sort((a, b) => a - b);
  return used.map((c) => page.panes.filter((p) => p.col === c));
}

export function pageShell(page, { onEdit }) {
  const columns = columnsOf(page).map((list) => list.map((p) => ({
    ...p,
    el: h('div.page__pane', { 'data-view': p.view }),
  })));
  const panes = columns.flat();
  const el = h('div.page', { 'data-page': page.id },
    h('div.page__head', null,
      icon(page.icon, 'page__icon'),
      h('span.page__name', null, page.name),
      h('span.page__sub', null, page.panes.map((p) => VIEW_TITLES[p.view]).join(' · ')),
      h('span.list__spacer'),
      h('button.btn.page__edit', { onclick: onEdit, title: 'Change what is on this page' },
        'Edit')),
    h('div.page__panes', { style: { gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` } },
      columns.map((list, i) => h('div.page__col', { 'data-col': String(i) }, list.map((p) => p.el)))));
  return { el, panes };
}

const COL = /^col-\d+$/;

/* How wide a pane's panel grid comes out with this many columns in a stage this wide:
   the page's side padding (style.css .stage:has(> .page), 14px each side), the 10px
   between columns, then the column's own 4px scrollbar gutter -- which with the 10px
   is the 14px between one column's panels and the next's. */
export function paneWidth(stageWidth, cols) {
  return (stageWidth - 28 - (cols - 1) * 10) / cols - 4;
}

/* Before layout.js sees the pane: panels default to the pane's full width. A pane is a
   third of a window at most, and the spans a view was authored with assumed all of it.
   A width you set with the arrows is saved per page-pane and wins over this; layout.js
   still raises any panel to what it measures it needs in this pane, up to all of it. */
export function fullWidth(pane) {
  for (const grid of pane.querySelectorAll(':scope > .grid')) {
    for (const col of grid.children) {
      if (col.classList.contains('grid')) continue;
      for (const c of [...col.classList]) if (COL.test(c)) col.classList.remove(c);
      col.classList.add('col-12');
    }
  }
}

/* After layout.js has named the panels: hide every one not picked. Hidden, not removed --
   the view still owns them, and its status() still finds the nodes it paints. */
export function pickPanels(pane, panels) {
  const want = new Set(panels);
  for (const grid of pane.querySelectorAll(':scope > .grid')) {
    for (const col of grid.children) {
      const id = col.dataset.panel || col.dataset.slot;
      if (!id) continue;
      col.classList.toggle('is-unpicked', !want.has(id));
    }
    grid.repack?.();
  }
}

/* ── the builder ──────────────────────────────────────────────────────────── */
/* In the stage, no browser dialogs. `page` is null for a new one. */
export function pageBuilder(page, { count, onSave, onCancel, onDelete }) {
  const draft = {
    start: page ? null : 'solo',      // the starter the draft still matches, if any
    name: page?.name || '',
    icon: page?.icon || DEFAULT_ICON,
    cols: page?.cols || 2,
    columns: [],                      // views, per column, top to bottom
    byCols: {},                       // the last arrangement seen at each column count
    picked: Object.fromEntries(PAGE_VIEWS.map((v) => [v, new Set()])),
  };
  const load = (src) => {
    draft.byCols = {};
    draft.cols = src.cols;
    draft.columns = Array.from({ length: src.cols }, (_, c) =>
      src.panes.filter((p) => p.col === c).map((p) => p.view));
    for (const v of PAGE_VIEWS) draft.picked[v] = new Set();
    for (const p of src.panes) draft.picked[p.view] = new Set(p.panels);
  };
  const inPage = () => draft.columns.flat();
  load(page || STARTERS.solo);

  const nameIn = h('input.page__namein', {
    id: 'pb-name', type: 'text', maxlength: '40',
    placeholder: page ? page.name : `Page ${count + 1}`, value: draft.name,
    oninput: (e) => { draft.name = e.target.value; },
  });
  const starters = h('div.seg', { id: 'pb-start' });
  const icons = h('div.pb__icons', { id: 'pb-icon', role: 'radiogroup', 'aria-label': 'Icon' });
  const checks = h('div.pb__groups', { id: 'pb-panels' });
  const colsSeg = h('div.seg', { id: 'pb-cols' });
  const orderList = h('div.pb__order', { id: 'pb-order' });
  const note = h('div.note.pb__note', { id: 'pb-note', hidden: true });
  const warn = h('div.note.pb__warn', { id: 'pb-warn', hidden: true });
  const foot = h('div.btnrow.pb__foot', { id: 'pb-foot' });

  function paint() {
    icons.replaceChildren(...ICON_NAMES.map((name) =>
      h('button.pb__icon' + (name === draft.icon ? '.is-on' : ''), {
        type: 'button', role: 'radio', 'data-icon': name, title: ICONS[name].label,
        'aria-checked': name === draft.icon ? 'true' : 'false',
        onclick: () => { draft.icon = name; paint(); },
      }, icon(name, 'pb__iconsvg'))));
    starters.replaceChildren(...Object.entries(STARTERS).map(([key, s]) =>
      h('button.seg__btn' + (key === draft.start ? '.is-on' : ''), {
        'data-start': key, onclick: () => { load(s); draft.start = key; paint(); },
      }, s.label)));
    // A long list (Stats has 15) takes two columns of the grid and splits into two,
    // so no one group sets the height of the lot.
    checks.replaceChildren(...PAGE_VIEWS.map((view) => [view, PANELS.filter((p) => p.view === view)])
      .map(([view, list]) =>
      h('div.pb__group' + (list.length > 9 ? '.pb__group--wide' : ''), null,
        h('div.pb__gtitle', null, VIEW_TITLES[view]),
        list.map((p) =>
          h('label.toggle.pb__check', null,
            h('input', {
              type: 'checkbox', 'data-view': view, 'data-slug': p.slug,
              checked: draft.picked[view].has(p.slug),
              onchange: (e) => tick(view, p.slug, e.target.checked),
            }),
            h('span.toggle__track'), p.title)))));
    colsSeg.replaceChildren(...[1, 2, 3].map((n) =>
      h('button.seg__btn' + (n === draft.cols ? '.is-on' : ''), {
        onclick: () => { if (n !== draft.cols) { draft.start = null; setCols(n); } paint(); },
      }, String(n))));
    paintColumns();
    paintWarn();
  }

  /* The page in miniature: its columns side by side, each pane a card in its column,
     with arrows that move it the way they point. A column with nothing in it is shown
     (it is somewhere a pane can go), but the page leaves it out. */
  function paintColumns() {
    orderList.style.setProperty('--cols', String(draft.cols));
    if (!inPage().length) {
      orderList.replaceChildren(h('div.empty.pb__none', null, 'Tick a panel above and its tab becomes a pane here.'));
      return;
    }
    const last = draft.cols - 1;
    const arrow = (dir, title, off, fn) => h('button.btn.btn--sm.pb__mv', {
      type: 'button', 'data-move': dir, title, 'aria-label': title, disabled: off, onclick: fn,
    }, { left: '←', right: '→', up: '↑', down: '↓' }[dir]);
    // Filtered: the DOM's own replaceChildren prints a null child as the text "null".
    orderList.replaceChildren(...draft.columns.map((list, c) => h('div.pb__col', { 'data-col': String(c) },
      ...[draft.cols > 1 ? h('div.pb__chead', null, `Column ${c + 1}`) : null,
        ...(list.length ? list.map((view, i) => {
          const names = PANELS.filter((p) => p.view === view && draft.picked[view].has(p.slug)).map((p) => p.title);
          return h('div.pb__row', { 'data-view': view },
            h('div.pb__rtext', null,
              h('span.pb__rname', null, VIEW_TITLES[view]),
              h('span.pb__rsub', { title: names.join(', ') }, names.join(' · '))),
            h('div.pb__arrows', null, ...[
              draft.cols > 1 ? arrow('left', 'Move to the column on the left', c === 0, () => shift(c, i, -1)) : null,
              arrow('up', 'Move up', i === 0, () => move(c, i, -1)),
              arrow('down', 'Move down', i === list.length - 1, () => move(c, i, 1)),
              draft.cols > 1 ? arrow('right', 'Move to the column on the right', c === last, () => shift(c, i, 1)) : null,
            ].filter(Boolean)));
        }) : [h('div.pb__hole', null, 'Empty: left off the page')])].filter(Boolean))));
  }

  /* A count seen before, with the same panes on the page, gets back the arrangement it
     had: 2 -> 1 -> 2 is a round trip, not a reshuffle. A new count keeps the panes in
     reading order -- down the first column, then the next -- and shares them out as
     evenly as they go, the first columns taking one more: Play + Layers | Practice +
     Tools + Stats is Play + Layers | Practice + Tools | Stats at three, and four panes
     are 2 + 1 + 1, never a column left empty while there are panes to fill it. */
  function setCols(n) {
    const flat = inPage();
    draft.byCols[draft.cols] = draft.columns.map((l) => [...l]);
    const seen = draft.byCols[n];
    draft.cols = n;
    const same = (a, b) => a.length === b.length && a.every((v) => b.includes(v));
    if (seen && same(seen.flat(), flat)) { draft.columns = seen.map((l) => [...l]); return; }
    const base = Math.floor(flat.length / n), extra = flat.length % n;
    let at = 0;
    draft.columns = Array.from({ length: n }, (_, c) => {
      const run = base + (c < extra ? 1 : 0);
      return flat.slice(at, (at += run));
    });
  }

  /* A panel narrower than its own minimum cannot be laid out, only clipped (a pane does
     not scroll sideways), so say so while the columns are being chosen -- against the
     window as it is at each click, which is the one the page will open in. Repainted
     with everything else rather than on resize: a listener here would need a teardown
     the builder does not have. */
  function paintWarn() {
    const stage = document.getElementById('stage');
    const cols = Math.max(1, draft.columns.filter((l) => l.length).length);
    const room = stage ? paneWidth(stage.getBoundingClientRect().width, cols) : Infinity;
    const tight = PANELS.filter((p) => p.min && draft.picked[p.view].has(p.slug) && p.min > room);
    warn.hidden = !tight.length;
    if (!tight.length) return;
    const names = tight.map((p) => p.title);
    const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
    warn.textContent = `${list} ${names.length > 1 ? 'need' : 'needs'} a wider column than `
      + `${cols} across ${cols > 1 ? 'give' : 'gives'} at this window size (${Math.floor(room)}px). `
      + 'Fewer columns, or a wider window.';
  }

  function tick(view, slug, on) {
    const set = draft.picked[view];
    if (on) set.add(slug); else set.delete(slug);
    // A new pane goes to the foot of the column with the fewest in it.
    if (set.size && !inPage().includes(view)) {
      const lens = draft.columns.map((l) => l.length);
      draft.columns[lens.indexOf(Math.min(...lens))].push(view);
    }
    if (!set.size) draft.columns = draft.columns.map((l) => l.filter((v) => v !== view));
    draft.start = null;               // your own now, not the starter's
    note.hidden = true;
    paint();
  }

  // Up or down inside its column.
  function move(c, i, by) {
    const list = draft.columns[c];
    const j = i + by;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    draft.start = null;
    paint();
  }

  // Into the next column over, at the same height in it where it can be.
  function shift(c, i, by) {
    const to = draft.columns[c + by];
    if (!to) return;
    const [view] = draft.columns[c].splice(i, 1);
    to.splice(Math.min(i, to.length), 0, view);
    draft.start = null;
    paint();
  }

  function save() {
    const panes = draft.columns.flatMap((list, col) => list.map((view) => ({
      view, col, panels: panelsOf(view).filter((s) => draft.picked[view].has(s)),
    })));
    if (!panes.length) {
      note.hidden = false;
      note.textContent = 'Pick at least one panel for the page to show.';
      return;
    }
    onSave({
      id: page?.id || `p${Date.now().toString(36)}`,
      name: draft.name.trim().slice(0, 40) || (page ? page.name : `Page ${count + 1}`),
      icon: draft.icon,
      cols: draft.cols,
      panes,
    });
  }

  // Filtered: the DOM's own replaceChildren prints a null child as the text "null".
  function paintFoot(asking = false) {
    foot.replaceChildren(...[
      h('button.btn.btn--primary', { id: 'pb-save', onclick: save }, page ? 'Save' : 'Make the page'),
      h('button.btn', { id: 'pb-cancel', onclick: onCancel }, 'Cancel'),
      h('span.list__spacer'),
      page && !asking
        ? h('button.btn', { id: 'pb-delete', onclick: () => paintFoot(true) }, 'Delete page')
        : null,
      page && asking
        ? h('span.pb__ask', null, 'Delete this page? ',
            h('button.btn', { id: 'pb-yes', onclick: onDelete }, 'Yes'),
            h('button.btn', { id: 'pb-no', onclick: () => paintFoot(false) }, 'No'))
        : null].filter(Boolean));
  }

  paint();
  paintFoot();
  // Header and footer fixed, the middle scrolling (style.css .pb__body).
  return h('div.page__builder', null,
    mod(page ? `Edit ${page.name}` : 'New page', 'panels from any tab, side by side',
      h('div.pb__body', null,
        h('div.pb__top', null,
          h('label.field', null, h('span.field__label', null, h('span', null, 'Name')), nameIn),
          h('div.pb__line', null, h('span.pb__lbl', null, 'Start from'), starters),
          h('div.pb__line', null, h('span.pb__lbl', null, 'Columns'), colsSeg)),
        h('div.pb__lbl', null, 'Icon'),
        icons,
        h('div.pb__lbl', null, 'Panels'),
        checks,
        h('div.pb__lbl', null, 'Where each pane goes'),
        warn,
        orderList,
        note),
      foot));
}
