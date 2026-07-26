/* The first-run tour.
 *
 * Six cards, skippable, shown once. It covers only the things that actually proved
 * non-obvious to the first person who used this app -- above all what a "layer" is,
 * which was genuinely useful and completely opaque until someone said it in words.
 *
 * Not a view: it overlays whatever is up, and each step may switch tabs so you see the
 * thing being described behind the card. It sits ABOVE the dock rather than centred on
 * the screen, so the keyboard stays visible while step 4 talks about it.
 *
 * No masks, cut-outs or arrows pointing at elements. Those need element measuring and a
 * resize observer, and the design system has no tooltip arrow -- a card that says where
 * to look is worth more than a spotlight that drifts two pixels off its target.
 */

import { api, h } from './ui.js';

const STEPS = [
  {
    tab: null,
    title: 'This is a workspace, not a lesson',
    body: [
      'Keys opens, sounds good, and gets out of the way. There is no course, nothing ',
      'locked, and nothing keeping score. Six tabs along the left, or press ',
      h('strong', null, '1'), '–', h('strong', null, '6'), '.',
    ],
  },
  {
    tab: 'play',
    title: 'Play — where the sounds are',
    body: [
      'Presets are one click. Underneath them the browser has every sound in the ',
      'SoundFont, filtering as you type — pianos, organs, strings, synths, drum kits. ',
      'Clicking a key on screen auditions whatever you currently have loaded.',
    ],
  },
  {
    tab: 'layers',
    title: 'Layers — the one worth knowing',
    body: [
      'A ', h('strong', null, 'split'), ' puts one sound in your left hand and another ',
      'in your right: walking bass under a piano melody, on one keyboard. A ',
      h('strong', null, 'layer'), ' puts two sounds on the same keys so they sound ',
      'together — piano with strings underneath. One click each at the top of this tab.',
    ],
  },
  {
    tab: null,
    title: 'The keyboard never leaves',
    body: [
      'It is docked at the bottom of every tab. It lights under your fingers as you ',
      'play, brighter the harder you hit, and you can click it with the mouse. ',
      h('strong', null, 'Esc'), ' is panic — all notes off, from anywhere.',
    ],
  },
  {
    tab: 'tools',
    title: 'Tools — the metronome, for now',
    body: [
      'Scheduled on the audio clock rather than a timer, so it cannot drift against ',
      'what you hear. It has a tempo ramp that climbs as you get it clean. ',
      h('strong', null, 'M'), ' toggles it from any tab.',
    ],
  },
  {
    tab: 'practice',
    title: 'Practice and Stats',
    body: [
      'Practice is a session clock and a shelf of exercises — scales and arpeggios in ',
      'any key, one hand or both, and sight reading. Stats is everything Keys has ',
      'noticed about your playing: which keys, which chords, what time of day, how ',
      'evenly. ', h('strong', null, 'It is stored in keys.db next to the app and never ',
      'leaves this machine.'),
    ],
  },
];

let overlay = null;
let index = 0;

export function tourOpen() {
  return overlay !== null;
}

export function startTour(ctx) {
  if (overlay) return;
  index = 0;
  overlay = h('div.tour', { id: 'tour' },
    h('div.tour__scrim', { onclick: () => finish(ctx) }),
    h('div.tour__card', { id: 'tour-card' }));
  document.body.append(overlay);
  paint(ctx);
}

function paint(ctx) {
  const step = STEPS[index];
  if (step.tab) location.hash = step.tab;

  const last = index === STEPS.length - 1;
  document.getElementById('tour-card').replaceChildren(
    h('div.tour__step', null, `${index + 1} of ${STEPS.length}`),
    h('h2.tour__title', null, step.title),
    h('div.tour__body', null, ...step.body),
    h('div.tour__dots', null, STEPS.map((_, i) =>
      h('i.tour__dot' + (i === index ? '.is-on' : '')))),
    h('div.tour__row', null,
      h('button.btn', { onclick: () => finish(ctx) }, 'Skip'),
      h('span.list__spacer'),
      index > 0
        ? h('button.btn', { onclick: () => { index -= 1; paint(ctx); } }, 'Back')
        : null,
      h('button.btn.btn--lg', {
        onclick: () => { if (last) finish(ctx); else { index += 1; paint(ctx); } },
      }, last ? 'Start playing' : 'Next')));
}

function finish(ctx) {
  overlay?.remove();
  overlay = null;
  // Fire and forget. A failed write shows the tour once more, which is a better
  // failure than blocking the app on a settings round trip.
  api.post('/api/settings', { ui: { tour_seen: true } }).catch(() => {});
  if (ctx?.state?.settings?.ui) ctx.state.settings.ui.tour_seen = true;
}
