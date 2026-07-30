/* The tutorial.
 *
 * Started as six first-run cards. It is now the whole manual, because the app grew
 * a loop station, a score reader, a chord visualiser and four themes, and none of
 * that is guessable from looking at it.
 *
 * Chapters, not a slideshow. The contents list is always visible and every entry is
 * clickable, so this is equally the thing that runs once on first launch and the
 * thing you open from Settings when you want to know what the pedal modes do. A
 * linear tour you cannot re-enter in the middle is a tour nobody re-enters.
 *
 * Each chapter names a tab and the tutorial switches to it, so the thing being
 * described is behind the card while you read about it. The card sits ABOVE the
 * dock rather than centred, so the keyboard stays visible throughout.
 *
 * No masks, cut-outs or arrows pointing at elements. Those need element measuring
 * and a resize observer, and the design system has no tooltip arrow -- a card that
 * says where to look beats a spotlight that drifts two pixels off its target.
 */

import { api, h } from './ui.js';

const b = (t) => h('strong', null, t);

export const CHAPTERS = [
  {
    id: 'welcome', tab: null, title: 'What this is',
    body: [
      'A workspace, not a course. Nothing is locked, nothing is scored, and no lesson ',
      'is waiting. Keys opens, sounds good and gets out of the way.', h('br'), h('br'),
      'Six tabs down the left, or press ', b('1'), '–', b('6'), '. Every shortcut in ',
      'the app is rebindable in Settings.',
    ],
  },
  {
    id: 'play', tab: 'play', title: 'Play — the sounds',
    body: [
      'Presets are one click. Under them the browser holds every instrument in the ',
      'SoundFont — around 190 of them, grouped by family and filtering as you type. ',
      'Pianos, organs, guitars, strings, brass, leads, pads, world instruments and ',
      'drum kits.', h('br'), h('br'),
      b('Random'), ' picks one for you, which is the fastest way to find something ',
      'you would never have gone looking for.',
    ],
  },
  {
    id: 'touch', tab: 'play', title: 'Touch and highlighting',
    body: [
      b('Touch response'), ' changes how hard you have to hit for a loud note. A ',
      'weighted action and a light one want different curves; there is no correct ',
      'setting, only the one that stops fighting you.', h('br'), h('br'),
      'The ', b('scale highlighter'), ' lights the notes of a scale on the docked ',
      'keyboard. It changes nothing about what sounds — it is a map, not a filter.',
    ],
  },
  {
    id: 'layers', tab: 'layers', title: 'Splits and layers',
    body: [
      'The one worth knowing. A ', b('split'), ' puts one sound in your left hand and ',
      'another in your right — walking bass under a piano melody, on one keyboard. A ',
      b('layer'), ' puts two sounds on the same keys so they sound together, piano ',
      'with strings underneath.', h('br'), h('br'),
      'One click each at the top of the tab. The zone editor underneath is there when ',
      'you want six of them with your own ranges and volumes.',
    ],
  },
  {
    id: 'pedal', tab: 'layers', title: 'The pedal, four ways',
    body: [
      'Normally it is the damper, handled inside the synth. It can also be:', h('br'),
      b('Zone'), ' — hold it to switch which zones are live.', h('br'),
      b('Sostenuto'), ' — sustains only the notes already down when you press it, so ',
      'you can hold a bass note and play staccato over it.', h('br'),
      b('Hold'), ' — catches notes and lets them fade over a time you set, from a ',
      'moment to half a minute.',
    ],
  },
  {
    id: 'keyboard', tab: null, title: 'The keyboard never leaves',
    body: [
      'Docked at the bottom of every tab. It lights under your fingers as you play — ',
      'brighter the harder you hit — and you can click it with the mouse.', h('br'),
      h('br'),
      b('Esc'), ' is panic: all notes off, from anywhere, even while you are typing ',
      'in a box. It is the one shortcut that never stands aside.',
    ],
  },
  {
    id: 'roll', tab: null, title: 'The note roll',
    body: [
      'Press ', b('ROLL'), ' at the right of the strip above the keys, or ', b('V'), '. ',
      'The notes you play rise out of the keyboard — the falling-notes videos, upside ',
      'down.', h('br'), h('br'),
      'A bar’s length is how long you held the note and the gap above it is how ',
      'long you waited, so legato, staccato and a chord you rolled by accident all ',
      'become things you can see. In a split or a layer each zone gets its own colour.',
      h('br'), h('br'),
      b('F'), ' takes it full screen — the roll, the keyboard, a vignette and nothing ',
      'else. ', b('Esc'), ' brings the app back.',
    ],
  },
  {
    id: 'metronome', tab: 'tools', title: 'Tools — the click',
    body: [
      'Scheduled on the audio clock rather than a software timer, so it cannot drift ',
      'against what you hear. ', b('M'), ' toggles it from any tab.', h('br'), h('br'),
      'The ', b('tempo ramp'), ' climbs as you go. Start slower than feels necessary ',
      'and press ', b('Drop a step'), ' when you miss — that loop is what builds speed, ',
      'rather than playing fast badly.',
    ],
  },
  {
    id: 'theory', tab: 'tools', title: 'Chords & scales',
    body: [
      'Pick any root and any scale or chord and it shows you the shape — but the big ',
      'numbers are the ', b('gaps between the notes'), ', because that is the part you ',
      'can carry to a key you have not memorised.', h('br'), h('br'),
      'A major scale is W W H W W W H anywhere. A major chord is four keys then three; ',
      'minor is three then four. Standard fingering sits on the notes, with thumb ',
      'crossings marked in amber, and the chords that fit the key are underneath.',
    ],
  },
  {
    id: 'backing', tab: 'tools', title: 'Backing tracks',
    body: [
      'Paste any video link and it plays in the tab with its normal controls. Useful ',
      'for playing along, and for slowing something down to learn it.', h('br'), h('br'),
      'This is the only feature in Keys that reaches the network, and only once you ',
      'open a track.',
    ],
  },
  {
    id: 'loop', tab: 'play', title: 'The loop station',
    body: [
      'Record a few bars, and it plays back while you record the next part over it. ',
      'Five layers, each on its own channel with its own instrument — so a bass line, ',
      'a chord bed and a melody become an ensemble with yourself.', h('br'), h('br'),
      'It records from the same timestamps the app uses for everything else, so a take ',
      'is timed exactly as tightly as your playing is.',
    ],
  },
  {
    id: 'practice', tab: 'practice', title: 'Practice',
    body: [
      'A session clock and a shelf of exercises: scales and arpeggios in any key, one ',
      'hand or both, parallel or contrary motion, and sight reading.', h('br'), h('br'),
      'The clock counts time you spent ', b('playing'), ', not time with the app open. ',
      'Go quiet for a while and it stops; the gap is never counted. How long is up to ',
      'you in Settings.',
    ],
  },
  {
    id: 'sheet', tab: 'practice', title: 'Sheet music',
    body: [
      'Import a ', b('MusicXML'), ' file — .musicxml or .mxl — and Keys engraves and ',
      'plays it. Every notation program exports it, and a ', b('.mid'), ' works too: ',
      'one written by a notation program converts cleanly, which is most of the free ',
      'scores on the internet.', h('br'), h('br'),
      'The transport has play, pause, rewind, a tempo you can drag and a bar you can ',
      'click to jump anywhere in the piece.',
    ],
  },
  {
    id: 'stats', tab: 'stats', title: 'Stats',
    body: [
      'Everything Keys has noticed: which keys you actually use, which chords, what ',
      'key you play in, what time of day, how evenly, how hard.', h('br'), h('br'),
      'It updates while you play — no reloading. All of it is computed from notes you ',
      'already played, so it costs you nothing and asks you nothing.',
    ],
  },
  {
    id: 'yours', tab: 'settings', title: 'Making it yours',
    body: [
      'Every panel in the app drags by its header and resizes with the arrows on ',
      'hover, at a quarter, half or full width. The arrangement is per tab and saves ',
      'as you go, so put what you use at the top.', h('br'), h('br'),
      'The ', b('gear'), ' in the top-right corner holds everything about the app: ',
      'eleven ', b('themes'), ', rebindable ', b('shortcuts'), ', the session-clock ',
      'timeout, your data and this tutorial. The ', b('Sound'), ' tab is the rig — ',
      'MIDI in, audio out, effects and SoundFonts.',
    ],
  },
  {
    id: 'data', tab: 'settings', title: 'Your data, and updates',
    body: [
      'Everything Keys keeps is in one folder on this machine, listed in Settings with ',
      'its size, and each part can be deleted on its own. There is no account, no ',
      'server and no telemetry.', h('br'), h('br'),
      b('Check for updates'), ' is a button, never a background task. It is the only ',
      'request Keys makes that you did not ask for by pasting a link.',
    ],
  },
];

let overlay = null;
let index = 0;
let markSeen = true;

export function tourOpen() {
  return overlay !== null;
}

/** First run: opens at the beginning and records that it has been seen. */
export function startTour(ctx) {
  open(ctx, 0, true);
}

/** From Settings: may open at a chapter, and never re-marks anything. */
export function startTutorial(ctx, chapterId = null) {
  const at = Math.max(0, CHAPTERS.findIndex((c) => c.id === chapterId));
  open(ctx, chapterId ? at : 0, false);
}

function open(ctx, at, seen) {
  if (overlay) return;
  index = at;
  markSeen = seen;
  overlay = h('div.tour', { id: 'tour' },
    h('div.tour__scrim', { onclick: () => finish(ctx) }),
    h('div.tour__card', { id: 'tour-card' }));
  document.body.append(overlay);
  paint(ctx);
}

function paint(ctx) {
  const step = CHAPTERS[index];
  if (step.tab) location.hash = step.tab;

  const last = index === CHAPTERS.length - 1;
  document.getElementById('tour-card').replaceChildren(
    // The contents stays on screen at every step. Someone who opened this from
    // Settings to look up one thing should never have to click Next to find it.
    h('nav.tour__toc', null,
      h('div.tour__toclabel', null, 'Contents'),
      ...CHAPTERS.map((c, i) => h(
        'button.tour__tocitem' + (i === index ? '.is-on' : ''),
        { onclick: () => { index = i; paint(ctx); } }, c.title))),

    h('div.tour__main', null,
      h('div.tour__step', null, `${index + 1} of ${CHAPTERS.length}`),
      h('h2.tour__title', null, step.title),
      h('div.tour__body', null, ...step.body),
      h('div.tour__row', null,
        h('button.btn', { onclick: () => finish(ctx) }, markSeen ? 'Skip' : 'Close'),
        h('span.list__spacer'),
        index > 0
          ? h('button.btn', { onclick: () => { index -= 1; paint(ctx); } }, 'Back')
          : null,
        h('button.btn.btn--lg', {
          onclick: () => { if (last) finish(ctx); else { index += 1; paint(ctx); } },
        }, last ? 'Start playing' : 'Next'))));
}

function finish(ctx) {
  overlay?.remove();
  overlay = null;
  if (!markSeen) return;
  // Fire and forget. A failed write shows the tutorial once more, which is a better
  // failure than blocking the app on a settings round trip.
  api.post('/api/settings', { ui: { tour_seen: true } }).catch(() => {});
  if (ctx?.state?.settings?.ui) ctx.state.settings.ui.tour_seen = true;
}
