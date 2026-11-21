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

export const MAX_PAGES = 6;
export const PAGE_VIEWS = ['play', 'practice', 'layers', 'tools'];
const VIEW_TITLES = { play: 'Play', practice: 'Practice', layers: 'Layers', tools: 'Tools' };

/* slug = what layout.js derives from the panel's title (its data-panel), or the
   data-slot of a nested grid that is not a layout panel (Practice's exercise shelf).
   min = the narrowest the panel can render, in px, in its narrow layout (style.css
   `@container panel (max-width: 440px)`): what the builder holds a pane's width against.
   ui_check measures every one in the real DOM, so a panel that grows cannot leave its
   number behind. The shelf has none -- it is a grid of cards that reflows to any width. */
export const PANELS = [
  { view: 'play', slug: 'instruments', title: 'Instruments', min: 230 },
  { view: 'play', slug: 'effects', title: 'Effects', min: 210 },
  { view: 'play', slug: 'scale-highlighter', title: 'Scale highlighter', min: 250 },
  { view: 'play', slug: 'tempo', title: 'Tempo (metronome)', min: 160 },
  { view: 'play', slug: 'meter', title: 'Meter', min: 150 },
  { view: 'play', slug: 'tempo-ramp', title: 'Tempo ramp', min: 220 },
  { view: 'play', slug: 'click-kit', title: 'Click kit', min: 190 },
  { view: 'play', slug: 'loop-station', title: 'Loop station', min: 220 },
  { view: 'practice', slug: 'jam', title: 'Jam', min: 240 },
  { view: 'practice', slug: 'songs', title: 'Songs', min: 180 },
  { view: 'practice', slug: 'exercises', title: 'Exercises' },
  { view: 'practice', slug: 'now', title: 'Now', min: 150 },
  { view: 'practice', slug: 'timing', title: 'Timing', min: 180 },
  { view: 'layers', slug: 'layout', title: 'Layout', min: 160 },
  { view: 'layers', slug: 'build-one', title: 'Build one', min: 280 },
  { view: 'layers', slug: 'zone-editor', title: 'Zone editor', min: 190 },
  { view: 'tools', slug: 'touch-response', title: 'Touch response', min: 220 },
  { view: 'tools', slug: 'pedal', title: 'Pedal', min: 240 },
  { view: 'tools', slug: 'chords-scales', title: 'Chords & scales', min: 220 },
  { view: 'tools', slug: 'chords-in-this-key', title: 'Chords in this key', min: 230 },
  { view: 'tools', slug: 'backing-tracks', title: 'Backing tracks', min: 260 },
];

const panelsOf = (view) => PANELS.filter((p) => p.view === view).map((p) => p.slug);

/* What "Start from" fills the builder with. Solo practice is two across, not three: a
   third of a 1280 window gave Jam a 344px pane, and Jam in it ran 1711px tall. */
export const STARTERS = {
  solo: {
    label: 'Solo practice',
    cols: 2,
    panes: [
      { view: 'practice', panels: ['jam'] },
      { view: 'play', panels: ['scale-highlighter', 'tempo'] },
      { view: 'tools', panels: ['chords-scales'] },
    ],
  },
  everything: {
    label: 'Everything',
    cols: 2,
    panes: PAGE_VIEWS.map((view) => ({ view, panels: panelsOf(view) })),
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
      panes.push({ view, panels });
    }
    if (!panes.length) continue;
    ids.add(id);
    const name = typeof p.name === 'string' ? p.name.trim().slice(0, 40) : '';
    out.push({
      id,
      name: name || `Page ${out.length + 1}`,
      cols: [1, 2, 3].includes(p.cols) ? p.cols : 2,
      panes,
    });
  }
  return out;
}

export async function savePages(list) {
  return api.post('/api/settings', { ui: { pages: list } });
}

/* ── the page itself ──────────────────────────────────────────────────────── */
/* A header strip and the panes. Columns as asked, as many rows as the panes need --
   which makes four panes at two columns the 2x2. Each pane scrolls on its own.

   One row fills the stage. More than one cannot all fit: shared out of a 720px window
   they were 120-190px slivers, each with its own scrollbar. So every row gets at least
   ROW_MIN and the page scrolls past that. One column is not a grid at all but a stack:
   each pane as tall as what it holds, and the page the only scroller. */
const ROW_MIN = 420;

export function pageShell(page, { onEdit }) {
  const n = page.panes.length;
  const cols = Math.max(1, Math.min(page.cols, n));
  const rows = Math.ceil(n / cols);
  const stack = cols === 1 && n > 1;
  const panes = page.panes.map((p) => ({
    ...p,
    el: h('div.page__pane', { 'data-view': p.view }),
  }));
  const el = h('div.page' + (stack ? '.page--stack' : ''), { 'data-page': page.id },
    h('div.page__head', null,
      h('span.page__name', null, page.name),
      h('span.page__sub', null, page.panes.map((p) => VIEW_TITLES[p.view]).join(' · ')),
      h('span.list__spacer'),
      h('button.btn.page__edit', { onclick: onEdit, title: 'Change what is on this page' },
        'Edit')),
    h('div.page__panes', {
      style: {
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gridTemplateRows: stack ? 'none'
          : rows === 1 ? 'minmax(0, 1fr)' : `repeat(${rows}, minmax(${ROW_MIN}px, 1fr))`,
      },
    }, panes.map((p) => p.el)));
  return { el, panes };
}

const COL = /^col-\d+$/;

/* How wide a pane's panel grid comes out with this many columns in a stage this wide:
   the page's side padding (style.css .stage:has(> .page), 16px each side), the 14px
   between panes, then the pane's own 4px right padding and its 8px scrollbar. */
export function paneWidth(stageWidth, cols) {
  return (stageWidth - 32 - (cols - 1) * 14) / cols - 4 - 8;
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
    cols: page?.cols || 2,
    order: [],                        // views, in pane order
    picked: Object.fromEntries(PAGE_VIEWS.map((v) => [v, new Set()])),
  };
  const load = (src) => {
    draft.cols = src.cols;
    draft.order = src.panes.map((p) => p.view);
    for (const v of PAGE_VIEWS) draft.picked[v] = new Set();
    for (const p of src.panes) draft.picked[p.view] = new Set(p.panels);
  };
  load(page || STARTERS.solo);

  const nameIn = h('input.page__namein', {
    id: 'pb-name', type: 'text', maxlength: '40',
    placeholder: page ? page.name : `Page ${count + 1}`, value: draft.name,
    oninput: (e) => { draft.name = e.target.value; },
  });
  const starters = h('div.seg', { id: 'pb-start' });
  const checks = h('div.pb__groups', { id: 'pb-panels' });
  const colsSeg = h('div.seg', { id: 'pb-cols' });
  const orderList = h('div.pb__order', { id: 'pb-order' });
  const note = h('div.note.pb__note', { id: 'pb-note', hidden: true });
  const warn = h('div.note.pb__warn', { id: 'pb-warn', hidden: true });
  const foot = h('div.btnrow.pb__foot', { id: 'pb-foot' });

  function paint() {
    starters.replaceChildren(...Object.entries(STARTERS).map(([key, s]) =>
      h('button.seg__btn' + (key === draft.start ? '.is-on' : ''), {
        'data-start': key, onclick: () => { load(s); draft.start = key; paint(); },
      }, s.label)));
    checks.replaceChildren(...PAGE_VIEWS.map((view) =>
      h('div.pb__group', null,
        h('div.pb__gtitle', null, VIEW_TITLES[view]),
        PANELS.filter((p) => p.view === view).map((p) =>
          h('label.toggle.pb__check', null,
            h('input', {
              type: 'checkbox', 'data-view': view, 'data-slug': p.slug,
              checked: draft.picked[view].has(p.slug),
              onchange: (e) => tick(view, p.slug, e.target.checked),
            }),
            h('span.toggle__track'), p.title)))));
    colsSeg.replaceChildren(...[1, 2, 3].map((n) =>
      h('button.seg__btn' + (n === draft.cols ? '.is-on' : ''), {
        onclick: () => { if (n !== draft.cols) draft.start = null; draft.cols = n; paint(); },
      }, String(n))));
    orderList.replaceChildren(...(draft.order.length
      ? draft.order.map((view, i) => h('div.pb__row', { 'data-view': view },
        h('span.pb__pos', null, String(i + 1)),
        h('span.pb__rname', null, VIEW_TITLES[view]),
        h('span.pb__rsub', null, `${draft.picked[view].size} panel${draft.picked[view].size > 1 ? 's' : ''}`),
        h('span.list__spacer'),
        h('button.btn.btn--sm', { title: 'earlier', disabled: i === 0, onclick: () => move(i, -1) }, '↑'),
        h('button.btn.btn--sm', {
          title: 'later', disabled: i === draft.order.length - 1, onclick: () => move(i, 1),
        }, '↓')))
      : [h('div.empty', null, 'Tick a panel above and its tab becomes a pane here.')]));
    paintWarn();
  }

  /* A panel narrower than its own minimum cannot be laid out, only clipped (a pane does
     not scroll sideways), so say so while the columns are being chosen -- against the
     window as it is at each click, which is the one the page will open in. Repainted
     with everything else rather than on resize: a listener here would need a teardown
     the builder does not have. */
  function paintWarn() {
    const stage = document.getElementById('stage');
    const cols = Math.max(1, Math.min(draft.cols, draft.order.length));
    const room = stage ? paneWidth(stage.getBoundingClientRect().width, cols) : Infinity;
    const tight = PANELS.filter((p) => p.min && draft.picked[p.view].has(p.slug) && p.min > room);
    warn.hidden = !tight.length;
    if (!tight.length) return;
    const names = tight.map((p) => p.title);
    const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
    warn.textContent = `${list} ${names.length > 1 ? 'need' : 'needs'} a wider column than `
      + `${cols} across gives at this window size (${Math.floor(room)}px). Fewer columns, or a wider window.`;
  }

  function tick(view, slug, on) {
    const set = draft.picked[view];
    if (on) set.add(slug); else set.delete(slug);
    if (set.size && !draft.order.includes(view)) draft.order.push(view);
    if (!set.size) draft.order = draft.order.filter((v) => v !== view);
    draft.start = null;               // your own now, not the starter's
    note.hidden = true;
    paint();
  }

  function move(i, by) {
    const j = i + by;
    if (j < 0 || j >= draft.order.length) return;
    [draft.order[i], draft.order[j]] = [draft.order[j], draft.order[i]];
    draft.start = null;
    paint();
  }

  function save() {
    const panes = draft.order.map((view) => ({
      view, panels: panelsOf(view).filter((s) => draft.picked[view].has(s)),
    }));
    if (!panes.length) {
      note.hidden = false;
      note.textContent = 'Pick at least one panel for the page to show.';
      return;
    }
    onSave({
      id: page?.id || `p${Date.now().toString(36)}`,
      name: draft.name.trim().slice(0, 40) || (page ? page.name : `Page ${count + 1}`),
      cols: draft.cols,
      panes,
    });
  }

  // Filtered: the DOM's own replaceChildren prints a null child as the text "null".
  function paintFoot(asking = false) {
    foot.replaceChildren(...[
      h('button.btn.btn--lg', { id: 'pb-save', onclick: save }, page ? 'Save' : 'Make the page'),
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
  return h('div.page__builder', null,
    mod(page ? `Edit ${page.name}` : 'New page', 'panels from any tab, side by side',
      h('label.field', null, h('span.field__label', null, h('span', null, 'Name')), nameIn),
      h('div.pb__line', null, h('span.pb__lbl', null, 'Start from'), starters),
      h('div.pb__lbl', null, 'Panels'),
      checks,
      h('div.pb__line', null, h('span.pb__lbl', null, 'Columns'), colsSeg),
      warn,
      h('div.pb__lbl', null, 'Panes, in order'),
      orderList,
      note,
      foot));
}
