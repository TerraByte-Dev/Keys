/* Rearrangeable panels. Drag a panel by its header, resize it with the arrows.
 *
 * Bolted on from the outside on purpose: no view knows this exists. It walks the grid
 * a view just built, identifies each panel by the title it already renders, and
 * applies a saved order and width. That means a new panel needs no registration and
 * cannot forget to opt in -- and if this module were deleted tomorrow, every view
 * would still render exactly as its author wrote it.
 *
 * Identity is the slug of the panel's own title. Not an index, because inserting a
 * panel would shuffle everyone's saved layout; not a hand-assigned id, because that is
 * a registry to keep in sync. A title that changes loses its saved position once,
 * which is the right price.
 *
 * The dragged panel goes `position: fixed` and follows the pointer while a placeholder
 * holds its slot in the grid, so the other panels reflow live underneath it. Moving
 * the element in the DOM as you drag instead would work, but the panel would jump
 * between slots rather than follow your hand, and following your hand is the whole
 * point of picking it up.
 */

import { $, api, h, toast } from './ui.js';

// Quarter, half, full. Nothing else, on purpose: every row then tiles exactly --
// 12, or 6+6, or 6+3+3, or 3+3+3+3 -- so there is no arrangement that leaves a ragged
// column at the end. Eleven sizes let a row come to eleven twelfths, which is where
// the leftover slivers came from. A saved layout holding one of the old spans falls
// back to the panel's shipped size, because applySaved only honours what is in here.
const SPANS = [3, 6, 12];
const DRAG_THRESHOLD = 5;   // px before a click becomes a drag

// Must match .grid--packed's grid-auto-rows and .grid's gap in style.css.
const ROW_PX = 8;
const GAP_PX = 14;

let saved = {};             // { viewId: [{id, span}, ...] } -- last known server state

export function primeLayout(state) {
  saved = (state?.settings?.ui?.layout) || {};
}

const slug = (s) => String(s || '').toLowerCase().trim()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'panel';

function panelId(col, seen) {
  const title = col.querySelector('.mod__title')?.textContent || '';
  let id = slug(title);
  // Two panels with the same title in one view would otherwise share a slot.
  while (seen.has(id)) id += '-2';
  seen.add(id);
  return id;
}

/* A panel is a grid child whose own first child is a .mod -- the screwed-down section
   with a header to drag it by. Practice's exercise host is `div.grid.col-12`, which
   matches "has a col- class" but is a nested GRID of panels, not a panel: treating it
   as one gave it a drag handle it cannot use, an id stolen from the first panel
   inside it, and grid--packed's 8px rows applied to a grid that was never ours. */
function isPanel(el) {
  if (el.classList.contains('grid')) return false;
  if (!el.firstElementChild?.classList.contains('mod')) return false;
  return [...el.classList].some((c) => /^col-\d+$/.test(c));
}

function spanOf(col) {
  for (const cls of col.classList) {
    const m = /^col-(\d+)$/.exec(cls);
    if (m) return Number(m[1]);
  }
  return 12;
}

function setSpan(col, span) {
  for (const cls of [...col.classList]) if (/^col-\d+$/.test(cls)) col.classList.remove(cls);
  col.classList.add('col-' + span);
}

/* ── masonry ──────────────────────────────────────────────────────────────── */
/* Give every panel a row span matching its own content height.
 *
 * Without this the grid makes each panel as tall as the tallest one in its row, so a
 * short panel beside a tall one is a short panel inside a tall empty box -- 195px of
 * hollow, measured, in one case on Play. With it, panels stack up the columns
 * independently and there is nothing between them.
 *
 * The +GAP terms are because gap applies between every one of the 8px row tracks a
 * panel spans, so the height a span of N buys is N*ROW + (N-1)*GAP. */
function pack(grid) {
  for (const el of grid.children) {
    if (el.classList.contains('is-dragging')) continue;

    /* EVERY child, not just the managed panels.
     *
     * A view is allowed to drop a plain container in the grid -- Practice puts the
     * exercise shelf in one, a `div.grid.col-12` that isPanel() rightly refuses to
     * make draggable. But "not a panel" was being read as "needs no row span", and
     * under grid-auto-rows:8px an element with no span is EIGHT PIXELS TALL, not
     * auto. The shelf was 183px of content occupying one 8px track and spilling
     * 161px straight over Sheet music, which is the stacking that had to be dragged
     * apart by hand.
     *
     * A managed panel is measured by its content (the .mod inside it); anything
     * else is measured by itself. Neither feeds back, because align-items:start
     * means an item's height is its content's height whatever span it is given. */
    const content = el.dataset.panel ? el.firstElementChild : el;
    if (!content) continue;
    const h = content.getBoundingClientRect().height;
    if (!h) continue;                       // hidden panel; leave it alone
    const want = 'span ' + Math.max(1, Math.ceil((h + GAP_PX) / (ROW_PX + GAP_PX)));
    // Written only when it changes. An unmanaged child is observed by ITSELF, so a
    // style write on every pass is a standing invitation to a resize-observe loop;
    // no write, no new observation, no loop.
    if (el.style.gridRowEnd !== want) el.style.gridRowEnd = want;
  }
}

/* Panels change height on their own -- Stats refreshes, the loop station gains a
   layer, an instrument list finishes loading. Observing the CONTENT rather than the
   slot is what stops this feeding back on itself: with align-items:start the content's
   height does not depend on the row span we just set from it. */
function watch(grid) {
  let queued = false;
  const repack = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; pack(grid); });
  };
  // Torn down and rebuilt whenever the view replaces its panels, or this keeps
  // observing detached nodes and leaks one observer per refresh.
  grid._ro?.disconnect();
  grid._ro = new ResizeObserver(repack);
  for (const el of grid.children) {
    // Same rule as pack(): a managed panel is watched by its content, anything else
    // by itself. Watching only the panels meant an unmanaged container that filled
    // in later -- the exercise shelf, once /api/exercises answered -- grew without
    // ever asking for its row span to be recomputed.
    const target = el.dataset.panel ? el.firstElementChild : el;
    if (target) grid._ro.observe(target);
  }
  if (!grid._onResize) {
    grid._onResize = () => grid.repack?.();
    window.addEventListener('resize', grid._onResize);
  }
  return repack;
}

/* ── public ───────────────────────────────────────────────────────────────── */
export function attachLayout(grid, viewId) {
  if (!grid || grid.dataset.layoutOn) return;
  grid.dataset.layoutOn = '1';
  if ([...grid.children].filter(isPanel).length < 2) return;

  const build = () => {
    // A rebuild changes every panel's height, and it lands after the view has already
    // restored its own scroll position -- Stats does exactly that. Without this, every
    // refresh while you are reading the bottom of the page throws you somewhere else.
    const stage = document.getElementById('stage');
    const top = stage ? stage.scrollTop : 0;
    const seen = new Set();
    const cols = [...grid.children].filter(isPanel);
    if (!cols.length) return;
    for (const col of cols) {
      col.dataset.panel = panelId(col, seen);
      col.dataset.defaultSpan = String(spanOf(col));
      addControls(grid, col, viewId);
    }
    applySaved(grid, viewId);
    // Opt in by class, not globally: nested grids (the exercise host in Practice) are
    // not managed here and 8px auto-rows would wreck them.
    grid.classList.add('grid--packed');
    grid.repack = watch(grid);
    pack(grid);
    if (stage && top) stage.scrollTop = top;
  };

  build();

  /* A view is allowed to replace its own panels -- Stats does exactly that every time
   * its numbers refresh. The new children arrive with no data-panel, no drag handler
   * and, fatally, no computed row span: with grid-auto-rows at 8px every one of them
   * collapses to 8px and the whole page piles up at the top, unmovable.
   *
   * So watch for it and rebuild, rather than making every view remember to tell us.
   * That is the same argument as attaching from the router in the first place: a view
   * that has to opt in is a view that will forget. */
  const mo = new MutationObserver(() => {
    if (grid._layoutBusy || grid.querySelector('.is-dragging')) return;
    // Only when someone else replaced the panels, not when we reordered them.
    if ([...grid.children].some((el) => isPanel(el) && !el.dataset.panel)) {
      build();
    }
  });
  mo.observe(grid, { childList: true });
}

/* Order and width from settings. Panels the saved layout has never heard of keep the
   position their author gave them, which is what makes adding a panel safe. */
function applySaved(grid, viewId) {
  const layout = saved[viewId];
  if (!Array.isArray(layout) || !layout.length) return;
  const byId = new Map([...grid.children]
    .filter((el) => el.dataset.panel)
    .map((el) => [el.dataset.panel, el]));

  for (const entry of layout) {
    const col = byId.get(entry.id);
    if (!col) continue;                       // a panel that no longer exists
    if (SPANS.includes(entry.span)) setSpan(col, entry.span);
    grid.append(col);                         // saved panels first, in saved order
  }
  // Anything not in the saved layout is new since it was written, and lands after --
  // visible rather than silently hidden or wedged into a stale slot.
}

function readLayout(grid) {
  return [...grid.children]
    .filter((el) => el.dataset.panel)
    .map((el) => ({ id: el.dataset.panel, span: spanOf(el) }));
}

let saveTimer = null;
function persist(grid, viewId) {
  const layout = readLayout(grid);
  saved = { ...saved, [viewId]: layout };
  clearTimeout(saveTimer);
  // Debounced: a width stepper is clicked several times in a row and each one would
  // otherwise be a request and a file write.
  saveTimer = setTimeout(() => {
    api.post('/api/settings', { ui: { layout: { [viewId]: layout } } })
      .catch(() => { /* a lost layout save is not worth interrupting anyone for */ });
  }, 500);
}

/* ── per-panel controls ───────────────────────────────────────────────────── */
function addControls(grid, col, viewId) {
  const head = col.querySelector('.mod__head');
  if (!head) return;

  const step = (delta) => {
    const i = SPANS.indexOf(spanOf(col));
    const next = SPANS[Math.max(0, Math.min(SPANS.length - 1, (i < 0 ? SPANS.length - 1 : i) + delta))];
    setSpan(col, next);
    // Narrower means taller, so the row span has to be recomputed -- but only after
    // the browser has reflowed the content at its new width.
    requestAnimationFrame(() => grid.repack?.());
    persist(grid, viewId);
  };

  head.append(h('div.mod__grip', { title: 'drag to rearrange' },
    h('button.mod__btn', {
      title: 'narrower',
      onclick: (e) => { e.stopPropagation(); step(-1); },
    }, '‹'),
    h('button.mod__btn', {
      title: 'wider',
      onclick: (e) => { e.stopPropagation(); step(1); },
    }, '›')));

  head.addEventListener('pointerdown', (e) => startDrag(e, grid, col, viewId));
}

/* ── drag ─────────────────────────────────────────────────────────────────── */
function startDrag(e, grid, col, viewId) {
  // Left button only, and never from a control inside the header.
  if (e.button !== 0 || e.target.closest('button, input, select, a')) return;

  const head = e.currentTarget;
  const startX = e.clientX;
  const startY = e.clientY;
  let dragging = false;
  let ghostDx = 0;
  let ghostDy = 0;
  let placeholder = null;

  const onMove = (ev) => {
    if (!dragging) {
      if (Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_THRESHOLD) return;
      dragging = true;
      const r = col.getBoundingClientRect();
      ghostDx = startX - r.left;
      ghostDy = startY - r.top;

      // A placeholder of the same span keeps the grid the same shape while the panel
      // is out of flow -- without it every other panel jumps the moment you lift one.
      // The placeholder is a col- child with no data-panel, which is exactly the
      // signature the rebuild watcher looks for. Tell it we are mid-drag.
      grid._layoutBusy = true;
      placeholder = h('div.col-' + spanOf(col) + '.layout__slot');
      // The same row span the panel had, or the grid closes up around the hole and
      // everything below it jumps the instant you lift a panel.
      placeholder.style.gridRowEnd = col.style.gridRowEnd || 'span 1';
      placeholder.style.minHeight = r.height + 'px';
      col.after(placeholder);

      Object.assign(col.style, {
        position: 'fixed', zIndex: '80', width: r.width + 'px', height: r.height + 'px',
        left: r.left + 'px', top: r.top + 'px', pointerEvents: 'none',
      });
      col.classList.add('is-dragging');
      document.body.classList.add('is-rearranging');
    }
    col.style.left = (ev.clientX - ghostDx) + 'px';
    col.style.top = (ev.clientY - ghostDy) + 'px';

    const target = slotUnder(grid, col, placeholder, ev.clientX, ev.clientY);
    if (target) {
      const { el, after } = target;
      if (after) el.after(placeholder);
      else el.before(placeholder);
    }
  };

  const onUp = () => {
    head.releasePointerCapture?.(e.pointerId);
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    if (!dragging) return;
    placeholder.replaceWith(col);
    grid._layoutBusy = false;
    col.removeAttribute('style');
    col.classList.remove('is-dragging');
    document.body.classList.remove('is-rearranging');
    grid.repack?.();
    persist(grid, viewId);
  };

  head.setPointerCapture?.(e.pointerId);
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

/* Which slot is the pointer over? Hit-tested against the laid-out siblings rather
   than elementFromPoint, because the dragged panel is fixed and on top of everything
   and would answer every query with itself. */
function slotUnder(grid, dragged, placeholder, x, y) {
  for (const el of grid.children) {
    if (el === dragged || el === placeholder) continue;
    const r = el.getBoundingClientRect();
    if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
    // Past the horizontal midpoint means "after", which reads correctly in a grid
    // that flows left to right and then wraps.
    return { el, after: (x - r.left) > r.width / 2 };
  }
  return null;
}

/* ── reset ────────────────────────────────────────────────────────────────── */
/* Settings deep-merges dicts, so posting `{layout: {}}` merges nothing and clears
   nothing -- the saved arrangement would survive its own reset button. Lists ARE
   replaced wholesale, so every view is explicitly set to an empty list instead. */
export async function resetLayout(viewId = null) {
  const ids = viewId ? [viewId] : Object.keys(saved);
  if (!ids.length) { toast('Nothing to reset -- panels are where they shipped', '', 2600); return; }
  const cleared = Object.fromEntries(ids.map((id) => [id, []]));
  saved = { ...saved, ...cleared };
  try {
    await api.post('/api/settings', { ui: { layout: cleared } });
    toast(viewId ? `${viewId} panels reset` : 'Panels back where they shipped', 'good');
    // The current view is already drawn from the old layout, so re-render it.
    location.reload();
  } catch (err) { toast(err.message, 'bad'); }
}
