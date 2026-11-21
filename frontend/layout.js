/* Rearrangeable panels. Drag a panel by its header, resize it with the arrows: a quarter,
 * a half or all of the row (spans 3, 6, 12), a brick a click, never narrower than the
 * panel measures it needs where it is, and every click a change you can see (fit() and
 * stepFrom(), below).
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

// Quarter, half, full: lego bricks. Each click on an arrow is one step (12 -> 6 -> 3 and
// back), the bricks pair up into rows of 12 (3+3+6, 6+6, 3+3+3+3, 12), and fit() widens
// a panel you have not sized over columns left empty beside it, to a brick. A third and two
// thirds (0.14-0.15) are gone: from a full panel one click made two thirds, and two
// thirds sit with nothing but another third. Every panel has a quarter form (its mini
// tier, under 380px); a layout saved with a 4 or an 8 in it is from before (applySaved).
const SPANS = [3, 6, 12];
const DRAG_THRESHOLD = 5;   // px before a click becomes a drag
// Narrowing folds, it never grows: a step narrower that makes a panel more than this much
// taller is not offered (stepFrom). The same number as ui_check's FOLD_MAX.
const FOLD_MAX = 1.15;

// Must match .grid--packed's grid-auto-rows / row-gap 0 and .grid's column gap in
// style.css. Rows of 2px and no row gap, so a panel's slot is its height plus 14px
// rounded up to 2px: every vertical gap is 14-15px. 8px rows with a 14px gap between
// them rounded each slot up by as much as 21px, and gaps ran from 16 to 88px.
const ROW_PX = 2;
const GAP_PX = 14;
// How far past its content pack() will draw a card down to meet the panel under it. The
// holes it is for are the 64-82px left above a panel spanning two columns of different
// heights (Play, Stats, Tools as shipped). Past this it is not a tidy edge but a card
// with half a screen of nothing in it, and the hole is better shown than hidden.
const FILL_MAX_PX = 96;
// Card to card down a column (the 14px gap included), more than this is a hole.
const HOLE_PX = 24;

let saved = {};             // { viewId: [{id, span}, ...] } -- last known server state

export function primeLayout(state) {
  saved = (state?.settings?.ui?.layout) || {};
}

const slug = (s) => String(s || '').toLowerCase().trim()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'panel';

function panelId(col, seen) {
  const title = col.querySelector('.mod__title')?.textContent || '';
  let id = col.dataset.slot || slug(title);
  // Two panels with the same title in one view would otherwise share a slot.
  while (seen.has(id)) id += '-2';
  seen.add(id);
  return id;
}

/* A panel is a grid child whose own first child is a .mod -- the screwed-down section
   with a header to drag it by. A nested GRID of panels is not one: treating it as one
   gave it a drag handle it cannot use, an id stolen from the first panel inside it,
   and grid--packed's rows applied to a grid that was never ours.
   Or a named slot (data-slot) holding one: Practice's exercises, which is the shelf
   (a .mod) or an open run (a small grid of them) in the same place. Its name is the
   slot's, so the run's title does not move it, and its handle is the first header in
   it. The view says when it swapped what is inside (the 'layout:content' event). */
function isPanel(el) {
  if (el.classList.contains('grid')) return false;
  if (!(el.dataset.slot ? el.querySelector('.mod__head') : el.firstElementChild?.classList.contains('mod'))) return false;
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

/* What a child of the grid occupies as rendered: fit()'s inline span on a panel (and on
   the drag placeholder, which copies it), the class on anything else. */
function shownSpan(el) {
  const m = /span (\d+)/.exec(el.style.gridColumn || '');
  return m ? Number(m[1]) : spanOf(el);
}

/* ── fit: the measured minimum, and rows that fill ────────────────────────── */
/* The saved span is a preference, not an order. A panel has a width under which its
 * content no longer fits -- a row of twelve root buttons, a year of days, a slider with
 * its label -- and below it the panel either sticks out of its own box or squeezes its
 * text a word per line. The audit found that at 26 of 42 panels' quarter width.
 *
 * So each panel is measured: its .mod at min-content, and then laid out, with the panel
 * set to each candidate span's pixel width in turn so its container queries answer for
 * THAT width (a narrow panel wraps and hides things, which is what lets it be narrow),
 * the aside hidden (it truncates and goes first) and the resize arrows shown (they
 * appear on hover and must fit then). The smallest span it fits both ways is its
 * minSpan. It renders at max(saved, minSpan), inline, so the col-N class keeps the
 * preference: on a wider window the same panel goes back to it.
 *
 * Measured against the grid's own width, which makes a page pane just a narrower grid:
 * a panel in a narrow pane rises to what it needs, up to the whole pane. The
 * measurements (the minimum, and the panel's height at each span) are kept until the
 * grid's width changes or the panel's own content does, so a height-only tick -- Stats
 * refreshing, Timing painting -- costs a placement in arithmetic, not a hundred layouts.
 * "Its content does" is the card changing size (watch()), the card no longer matching
 * what was measured (pack() checks its height and that nothing runs past it), or a
 * pointer or focus arriving to use the arrows: a value filled in later can widen a
 * panel's minimum without changing its height at the width it shows.
 *
 * Then rows. A panel with empty columns to its right for its whole height -- a
 * half-width panel alone in its band, as Settings' MIDI input and Latency were -- is
 * widened over them, to the widest brick they make room for (a quarter alone in a row
 * goes full width; a half beside three empty columns stays a half, since three
 * quarters is not a brick and the three are a quarter's room). Render only, never
 * saved. Not grid-auto-flow: dense, which would fill the holes by taking panels out of
 * order -- tab order and slotUnder() both read the DOM.
 *
 * Except a panel you sized with its arrows (data-sized, saved with the layout). That one
 * renders at what you picked, and the columns beside it stay open for the next panel
 * you narrow to move up into. Widening it back would make the click that narrowed it
 * do nothing on screen, and make two panels side by side impossible to build where
 * every panel starts full width (Layers, every page pane).
 *
 * And a view may ask for more than a panel's content measures (data-min-span): Practice
 * does while an exercise run is open in its Exercises slot, which wants the whole row
 * whatever the shelf was sized to, and gives it back on 'Back to the shelf'.
 *
 * "Empty beside it" is decided on where the grid will actually put each panel
 * (place()), not on spans summed in DOM order: this is masonry, and a half-width panel
 * after a tall one lands beside it, under its neighbour. Counting DOM-order rows widened
 * Play's Click kit to full width under two columns of different heights -- 184k px2 of
 * hole where its half-width slot had 43k. */
/* Something in the card wider than its box: the card or one of its blocks holding more
   than its width (an overflow into the card's padding shows only on the block). A block
   that scrolls sideways on purpose is allowed to. */
const wider = (n) => n.clientWidth && n.scrollWidth > n.clientWidth + 1
  && !/auto|scroll/.test(getComputedStyle(n).overflowX);
const overflows = (mod) => [mod, ...mod.children].some(wider);
/* And, when measuring a span, a select cutting off the choice it shows: a select that may
   shrink (a quarter's Pedal mode) has no min-content to stop it, so it is asked directly.
   Only there -- pack() asking it of every select on every pass re-measured Jam, whose
   band selects change as it plays, every frame. */
const cutsOff = (mod) => overflows(mod) || [...mod.querySelectorAll('select')].some(wider);

/* Measuring a panel at a wider width than it shows clamps every sideways scroller in it
   back to the start: a quarter-width Activity jumped to January whenever the pointer came
   onto it (the arrows remeasure), and Chords & scales' ribbon to its first note. So what
   was scrolled is put back where it was. */
function keepScroll(col, measure) {
  const at = [...col.querySelectorAll('*')].filter((n) => n.scrollLeft > 0).map((n) => [n, n.scrollLeft]);
  const out = measure();
  for (const [n, x] of at) n.scrollLeft = x;
  return out;
}

function minSpan(col, px) {
  return keepScroll(col, () => measureMin(col, px));
}

function measureMin(col, px) {
  const mod = col.firstElementChild;
  const keep = [col.style.width, mod.style.width];
  col.classList.add('is-measuring');
  let found = SPANS[SPANS.length - 1];
  for (const s of SPANS.slice(0, -1)) {
    col.style.width = px(s) + 'px';
    mod.style.width = 'min-content';
    if (mod.getBoundingClientRect().width > px(s) + 1) continue;
    // And laid out at that width, nothing runs past its box: a grid of 1fr
    // fields reports a min-content under the width its labels then need (Click kit's
    // three gains, 186px of min-content that overflowed 256px).
    mod.style.width = keep[1];
    if (!cutsOff(mod)) { found = s; break; }
  }
  col.style.width = keep[0];
  mod.style.width = keep[1];
  col.classList.remove('is-measuring');
  return found;
}

function fit(grid) {
  // In focus the marked path is display:block and nothing here means anything.
  if (getComputedStyle(grid).display !== 'grid') return;
  const width = grid.clientWidth;
  if (!width) return;
  grid._fitW = width;
  const colW = (width - 11 * GAP_PX) / 12;
  const px = (n) => n * colW + (n - 1) * GAP_PX;

  unfill(grid);                               // natural heights for anything measured below
  const items = [];
  for (const el of grid.children) {
    if (el.classList.contains('is-dragging') || !el.getClientRects().length) continue;
    if (el.dataset.panel) {
      if (el._m?.width !== width) el._m = { width, min: minSpan(el, px), h: {}, fresh: true };
      const min = Math.max(el._m.min, Number(el.dataset.minSpan) || 0);
      el._minSpan = min;
      items.push({ el, panel: true, fixed: el.dataset.sized === '1', min,
        want: Math.min(12, Math.max(spanOf(el), min)) });
    } else {
      items.push({ el, panel: false, want: Math.min(12, shownSpan(el)) });
    }
  }

  // Each panel's height at a span, measured there (so this does not depend on what was
  // drawn last and cannot flip-flop between passes) and kept with its minimum.
  const heightAt = (el, span) => {
    if (!el.dataset.panel) return el.getBoundingClientRect().height;
    if (el._m.h[span] === undefined) {
      el._m.h[span] = keepScroll(el, () => {
        const keep = el.style.width;
        el.style.width = px(span) + 'px';
        const height = el.firstElementChild.getBoundingClientRect().height;
        el.style.width = keep;
        return height;
      });
    }
    return el._m.h[span];
  };
  const spots = arrange(items, heightAt);
  // What the arrows simulate their steps against (stepFrom).
  grid._plan = { items, heightAt, holes: narrowHoles(spots) };

  items.forEach(({ el, panel }, i) => {
    if (!panel) return;
    const span = spots[i].span;
    if (el._shown !== span) {
      el.style.gridColumn = 'span ' + span;
      el._shown = span;
    }
  });
  for (const { el, panel } of items) if (panel) paintGrip(el);
}

/* Where everything goes at the spans asked for, with the row fill: each panel you have
 * not sized, in order, widens over the columns to its right that stay empty for its whole
 * height -- to the widest brick that fits, never to a width that is not one (a half and
 * three free columns made a three-quarter panel). A widened panel is shorter, which moves
 * what is under it, so the placement is redone until nothing widens (at most four passes).
 * Pure arithmetic on measured heights, so the arrows can try a step before taking it. */
function arrange(items, heightAt) {
  const spans = items.map((it) => it.want);
  let spots = [];
  for (let pass = 0, grew = true; grew && pass < 4; pass++) {
    grew = false;
    spots = place(items.map(({ el }, i) => ({
      span: spans[i], rows: Math.ceil((heightAt(el, spans[i]) + GAP_PX) / ROW_PX) || 1,
    })));
    const taken = (c, a, b, self) => spots.some((sp) => sp !== self
      && sp.col <= c && c < sp.col + sp.span && sp.row < b && a < sp.row + sp.rows);
    spots.forEach((sp, i) => {
      if (!items[i].panel || items[i].fixed) return;
      let run = 0;
      while (sp.col + sp.span + run < 12 && !taken(sp.col + sp.span + run, sp.row, sp.row + sp.rows, sp)) run++;
      const to = [...SPANS].reverse().find((b) => b > sp.span && b <= sp.span + run);
      if (!to) return;
      sp.span = to;
      spans[i] = sp.span;
      grew = true;
    });
  }
  return spots;
}

/* How much of a placement is holes too narrow for any panel to go into: space down a
 * column (after pack()'s draw-down, which closes what is within FILL_MAX_PX) with fewer
 * than SPANS[0] empty columns side by side at its height. A wide hole beside a panel you
 * narrowed is room for the next one; a two-column slot between staggered panels is just
 * a hole, and a step that makes more of it is not offered (stepFrom). In 2px rows. */
function narrowHoles(spots) {
  const cap = FILL_MAX_PX / ROW_PX;
  const ends = spots.map((p) => {
    const end = p.row + p.rows;
    let below = Infinity;
    for (const q of spots) {
      if (q !== p && q.row >= end && q.col < p.col + p.span && p.col < q.col + q.span) below = Math.min(below, q.row);
    }
    return below - end <= cap ? below : end;
  });
  const gaps = Array.from({ length: 12 }, (_, c) => {
    const out = [];
    let y = 0;
    spots.map((p, i) => [p, ends[i]]).filter(([p]) => p.col <= c && c < p.col + p.span)
      .sort((a, b) => a[0].row - b[0].row)
      .forEach(([p, end]) => {
        if (p.row - y > (HOLE_PX - GAP_PX) / ROW_PX) out.push([y, p.row]);
        y = Math.max(y, end);
      });
    return out;
  });
  const empty = (c, r) => gaps[c].some(([a, b]) => a <= r && r < b);
  let area = 0;
  gaps.forEach((list, c) => list.forEach(([a, b]) => {
    const mid = (a + b) / 2;
    let w = 1;
    for (let l = c - 1; l >= 0 && empty(l, mid); l--) w++;
    for (let r = c + 1; r < 12 && empty(r, mid); r++) w++;
    if (w < SPANS[0]) area += b - a;
  }));
  return area;
}

/* Where CSS grid's auto-placement will put each item, so fit() can look at rows before
 * the browser lays them out: the sparse algorithm of css-grid-1 section 8.5 on 12 columns,
 * a cursor that only moves forward, each item at the first spot at or after it that is
 * free for its whole span. Rows are pack()'s 2px tracks. A spot can only open up where
 * something above ends, so a row that fits nothing jumps straight to the next end. */
function place(items) {
  const cols = Array.from({ length: 12 }, () => []);
  const busy = (c, a, b) => cols[c].some(([x, y]) => x < b && a < y);
  let row = 0;
  let col = 0;
  return items.map(({ span, rows }) => {
    for (;;) {
      if (col + span <= 12) {
        let free = true;
        for (let c = col; c < col + span && free; c++) free = !busy(c, row, row + rows);
        if (free) break;
        col++;
        continue;
      }
      let next = Infinity;
      for (const list of cols) for (const [, y] of list) if (y > row && y < next) next = y;
      row = next === Infinity ? row + 1 : next;
      col = 0;
    }
    for (let c = col; c < col + span; c++) cols[c].push([row, row + rows]);
    const spot = { row, col, span, rows };
    col += span;
    return spot;
  });
}

/* The span an arrow click asks for: one brick past what the panel SHOWS, not past its
 * saved class -- a half-width panel the row fill shows full width narrows to a half,
 * not to a quarter -- and never two: 12 -> 6 -> 3 and back, a click a step. A step
 * narrower that makes the panel more than FOLD_MAX taller is not one: narrowing folds,
 * it never grows, and in a page pane, where a quarter can be 160px, most panels would
 * stack instead. The step is placed first, as a sized panel, and taken only if the panel
 * then shows narrower (or wider) than now and the grid has no more too-narrow holes than
 * it has now. So every enabled arrow changes what you see, and an arrow with no such
 * step is disabled (paintGrip). */
const grows = (plan, col, s) => plan.heightAt(col, s) > FOLD_MAX * plan.heightAt(col, col._shown);

function stepFrom(col, delta) {
  const plan = col.parentElement?._plan;
  const i = plan ? plan.items.findIndex((it) => it.el === col) : -1;
  if (i < 0 || !col._shown) return undefined;
  const shown = col._shown;
  const s = delta < 0 ? [...SPANS].reverse().find((x) => x < shown) : SPANS.find((x) => x > shown);
  if (!s || s < plan.items[i].min || (delta < 0 && grows(plan, col, s))) return undefined;
  const trial = plan.items.map((it, j) => (j === i ? { ...it, want: s, fixed: true } : it));
  const spots = arrange(trial, plan.heightAt);
  const got = spots[i].span;
  return (delta < 0 ? got < shown : got > shown) && narrowHoles(spots) <= plan.holes ? s : undefined;
}

function paintGrip(col) {
  const [less, more] = col.querySelector('.mod__head > .mod__grip')?.querySelectorAll(':scope > .mod__btn') || [];
  if (!less || !more) return;
  const shown = col._shown || spanOf(col);
  const canLess = !!stepFrom(col, -1);
  const canMore = !!stepFrom(col, 1);
  less.disabled = !canLess;
  const next = [...SPANS].reverse().find((s) => s < shown);
  const word = next === 3 ? 'quarter' : 'half';
  const plan = col.parentElement?._plan;
  less.title = canLess ? `narrower: ${word}`
    : shown <= SPANS[0] ? 'a quarter is the narrowest'
      : next < (col._minSpan || 0) ? `a ${word} is too narrow for this panel here`
        : plan && grows(plan, col, next) ? `a ${word} would make this panel taller here`
          : 'narrower would leave a gap here';
  more.disabled = !canMore;
  more.title = canMore ? (SPANS.find((s) => s > shown) === 12 ? 'wider: full' : 'wider: half')
    : shown >= 12 ? 'full width' : 'wider would leave a gap here';
}

/* ── masonry ──────────────────────────────────────────────────────────────── */
/* Give every panel a row span matching its own content height.
 *
 * Without this the grid makes each panel as tall as the tallest one in its row, so a
 * short panel beside a tall one is a short panel inside a tall empty box -- 195px of
 * hollow, measured, in one case on Play. With it, panels stack up the columns
 * independently and there is nothing between them.
 *
 * There is no row gap (the 2px tracks would each carry one); instead every slot is the
 * content's height plus GAP_PX, rounded up to a whole track, so the space under each
 * panel is the same 14-15px as the space beside it.
 *
 * Then the one gap that leaves: two columns of different heights over a panel that
 * spans both. The full-width panel starts under the taller column, and the shorter one
 * ends in a hole (82px on Play, 64px on Stats). So a panel with nothing under it until
 * something further down is drawn to meet it: its card grows (--fill, style.css, added
 * under its content) to 14px above the next panel in its columns. The space it grows
 * into is empty by definition -- the next occupant of those columns is where it stops
 * -- so nothing moves. Measured from the natural heights each pass, the fills cleared first. Never by
 * more than FILL_MAX_PX: a card stretched hundreds of px past its content is the same
 * hole with a border round it, and it hid from every gap measurement there is.
 *
 * What it drew is noted on each child (_drawn), so watch() can tell its own echo -- a
 * card grown by --fill is a resize of exactly the element it observes -- from a real
 * change in what the panel holds. */
function pack(grid) {
  unfill(grid);
  for (const el of grid.children) {
    if (el.classList.contains('is-dragging')) continue;

    /* EVERY child, not just the managed panels.
     *
     * A view is allowed to drop a plain container in the grid -- Practice's exercise
     * shelf was one (a `div.grid.col-12`, before it became a slot panel) that isPanel()
     * rightly refused to make draggable. But "not a panel" was being read as "needs no row span", and
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
    const want = 'span ' + Math.max(1, Math.ceil((h + GAP_PX) / ROW_PX));
    // Written only when it changes. An unmanaged child is observed by ITSELF, so a
    // style write on every pass is a standing invitation to a resize-observe loop;
    // no write, no new observation, no loop.
    if (el.style.gridRowEnd !== want) el.style.gridRowEnd = want;
  }
  if (getComputedStyle(grid).display !== 'grid') return;

  const boxes = [];
  for (const el of grid.children) {
    if (el.classList.contains('is-dragging')) continue;
    const r = (el.dataset.panel ? el.firstElementChild : el)?.getBoundingClientRect();
    if (r?.height) boxes.push({ el, r });
  }
  let stale = false;
  for (const { el, r } of boxes) {
    el._drawn = { w: r.width, h: r.height };
    if (!el.dataset.panel) continue;
    // fit()'s measurements against what is drawn, once each after they were taken (so a
    // panel that overflows at every width cannot measure itself every frame).
    if (el._m?.fresh) el._m.fresh = false;
    else if (el._m && (Math.abs(r.height - (el._m.h[el._shown] ?? r.height)) > 1
        || overflows(el.firstElementChild))) {
      el._m = null;
      stale = true;
    }
    let below = Infinity;
    for (const o of boxes) {
      if (o.el === el || o.r.top < r.bottom - 1) continue;
      if (Math.min(o.r.right, r.right) - Math.max(o.r.left, r.left) > 2) below = Math.min(below, o.r.top);
    }
    const fill = below - GAP_PX - r.top;
    if (below === Infinity || fill - r.height <= 2 || fill - r.height > FILL_MAX_PX) continue;
    el.style.setProperty('--fill', (fill - r.height) + 'px');
    el.style.gridRowEnd = 'span ' + Math.ceil((fill + GAP_PX) / ROW_PX);
    el._drawn.h = fill;
  }
  if (stale) grid.repack?.();
}

function unfill(grid) {
  for (const el of grid.children) if (el.dataset.panel) el.style.removeProperty('--fill');
}

/* Panels change height on their own -- Stats refreshes, the loop station gains a
   layer, an instrument list finishes loading. Observing the CONTENT rather than the
   slot keeps the row span out of it: with align-items:start the content's height does
   not depend on the row span we just set from it. The card's --fill and fit()'s width
   DO change what is observed, so a resize to exactly what pack() last drew is our own
   echo and is dropped; anything else is the panel's content changing, and its
   measurements go with it. */
function watch(grid) {
  let queued = false;
  const repack = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fit(grid); pack(grid); });
  };
  const onResize = (entries) => {
    let real = grid.clientWidth !== grid._fitW;
    for (const { target, borderBoxSize } of entries) {
      const el = target.parentElement === grid ? target : target.parentElement;
      if (!el) continue;                      // a card swapped out of its slot (practice.js)
      const box = borderBoxSize?.[0];
      const d = el._drawn;
      if (d && box && Math.abs(box.inlineSize - d.w) < 0.5 && Math.abs(box.blockSize - d.h) < 0.5) continue;
      el._m = null;
      real = true;
    }
    if (real) repack();
  };
  // Torn down and rebuilt whenever the view replaces its panels, or this keeps
  // observing detached nodes and leaks one observer per refresh.
  grid._ro?.disconnect();
  grid._ro = new ResizeObserver(onResize);
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
    // On a custom page each column is its own scroller, so the one to hold still is
    // the column this grid lives in, not the stage.
    const stage = grid.closest('.page__col') || document.getElementById('stage');
    const top = stage ? stage.scrollTop : 0;
    const seen = new Set();
    const cols = [...grid.children].filter(isPanel);
    if (!cols.length) return;
    for (const col of cols) {
      col.dataset.panel = panelId(col, seen);
      col.dataset.defaultSpan = String(spanOf(col));
      addControls(grid, col, viewId);
      if (col.dataset.slot && !col._onContent) {
        // Its handle again, and the new card watched in place of the one that left.
        col._onContent = () => { addControls(grid, col, viewId); col._m = null; grid.repack = watch(grid); grid.repack(); };
        col.addEventListener('layout:content', col._onContent);
      }
    }
    applySaved(grid, viewId);
    // Opt in by class, not globally: nested grids (the exercise host in Practice) are
    // not managed here and 8px auto-rows would wreck them.
    grid.classList.add('grid--packed');
    grid.repack = watch(grid);
    fit(grid);
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
  grid._mo = new MutationObserver(() => {
    if (grid._layoutBusy || grid.querySelector('.is-dragging')) return;
    // Only when someone else replaced the panels, not when we reordered them.
    if ([...grid.children].some((el) => isPanel(el) && !el.dataset.panel)) {
      build();
    }
  });
  grid._mo.observe(grid, { childList: true });
}

/* The other half, called by the router on everything it unmounts. The window's resize
   listener closes over the grid, so a grid thrown away without this stays reachable --
   with the whole view around it -- and is repacked on every resize, forever. One per
   tab change was already a leak; a page mounts four views at a time. */
export function detachLayout(grid) {
  if (!grid) return;
  grid._ro?.disconnect();
  grid._mo?.disconnect();
  if (grid._onResize) window.removeEventListener('resize', grid._onResize);
  grid._ro = grid._mo = grid._onResize = grid.repack = null;
}

/* Order and width from settings. A panel the saved layout has never heard of goes back
   beside the panel its author wrote it after, which is what makes adding a panel safe.

   A width is taken only from a panel the user sized (sized: true), and only as a brick
   (SPANS). A layout saves every panel's width, so the rest are the widths their view
   authored when it was saved -- 0.15's 4+8 Tools, 4+4+4 Stats -- and they take what
   their view authors today instead, or a re-authored grid never reaches anyone who once
   dragged a panel (and an old 4+8 under the new rules drew as 3+9). A sized one from
   0.15, when the steps were thirds, moves to the brick under it (a third to a quarter,
   two thirds to a half), so a row the user built keeps its shape and its order. */
const MIGRATE = { 4: 3, 8: 6 };

function applySaved(grid, viewId) {
  const layout = saved[viewId];
  if (!Array.isArray(layout) || !layout.length) return;
  // The order mount() built, read before the appends below start shuffling grid.children.
  const authored = [...grid.children].filter((el) => el.dataset.panel);
  const byId = new Map(authored.map((el) => [el.dataset.panel, el]));

  const placed = new Set();
  for (const entry of layout) {
    const col = byId.get(entry.id);
    if (!col) continue;                       // a panel that no longer exists
    const span = MIGRATE[entry.span] ?? entry.span;
    if (entry.sized && SPANS.includes(span)) {
      setSpan(col, span);
      col.dataset.sized = '1';
    }
    grid.append(col);                         // saved panels first, in saved order
    placed.add(col);
  }
  // Appending every saved panel has just swept the unsaved ones to the FRONT -- they are
  // the only children nothing moved. That is how Effects, added to Play after this layout
  // was written, came out above Presets instead of under Touch response where Play puts
  // it. So each one is threaded back in after the saved panel it was authored after; only
  // a panel with no saved panel before it at all stays at the front.
  let anchor = null;
  for (const col of authored) {
    if (placed.has(col)) { anchor = col; continue; }
    if (anchor) anchor.after(col);
    else grid.prepend(col);
    anchor = col;
  }
}

function readLayout(grid) {
  return [...grid.children]
    .filter((el) => el.dataset.panel)
    .map((el) => ({ id: el.dataset.panel, span: spanOf(el), ...(el.dataset.sized ? { sized: true } : {}) }));
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
  if (!head || head.querySelector(':scope > .mod__grip')) return;

  // From what the panel shows, never below its measured minimum, and only to a width
  // that shows (stepFrom). The panel is sized from then on: fit() leaves it at it.
  // Measured afresh first, and again whenever the arrows come into view.
  const remeasure = () => { col._m = null; grid.repack?.(); };
  col.firstElementChild.addEventListener('pointerenter', remeasure);
  head.addEventListener('focusin', remeasure);
  const step = (delta) => {
    col._m = null;
    fit(grid);
    pack(grid);
    const next = stepFrom(col, delta);
    if (!next) return;
    setSpan(col, next);
    col.dataset.sized = '1';
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
  // Left button only, and never from a control inside the header. Not in focus either:
  // there is one panel on screen and nowhere to drag it.
  if (e.button !== 0 || e.target.closest('button, input, select, a')
      || document.body.classList.contains('is-focusing')) return;

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
      // The width the panel showed, not its saved preference, or the row reflows.
      placeholder.style.gridColumn = col.style.gridColumn;
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
    col._shown = 0;                           // the inline span went with the style
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
  if (!ids.length) { toast('Nothing to reset — panels are where they shipped', '', 2600); return; }
  const cleared = Object.fromEntries(ids.map((id) => [id, []]));
  saved = { ...saved, ...cleared };
  try {
    await api.post('/api/settings', { ui: { layout: cleared } });
    toast(viewId ? `${viewId} panels reset` : 'Panels back where they shipped', 'good');
    // The current view is already drawn from the old layout, so re-render it.
    location.reload();
  } catch (err) { toast(err.message, 'bad'); }
}
