/* Setup -- audio, MIDI, effects, and the diagnostics you want when something is off.
 *
 * The latency panel is deliberately blunt about what it is not measuring. Nothing in
 * software can measure MIDI-to-ear latency: WASAPI loopback taps the post-mix engine,
 * misses driver, DMA, DAC and the air entirely, and returns pure silence when the
 * output is exclusive-mode. Any app claiming a round-trip number is lying, so this one
 * reports the piece it can actually see and labels it. */

import { $, api, h, mod, slider, stat, toast } from '../ui.js';
import { resetLayout } from '../layout.js';
import { CHAPTERS, startTutorial } from '../tour.js';
import { clockPanel, dataPanel, keysPanel, themePanel } from '../prefs.js';

export default {
  async mount(root, ctx) {
    const st = ctx.state;
    const eng = st.engine || {};
    const midi = st.midi || {};
    const s = st.settings || {};

    root.append(h('div.grid', null,
      h('div.col-6', null, mod('MIDI input', midi.connected ? 'connected' : 'not connected',
        h('div.list', { id: 'port-list' },
          (midi.ports || []).length
            ? midi.ports.map((p, i) => h('div.list__row', {
                onclick: async () => {
                  const res = await api.post(`/api/midi/open/${i}`);
                  toast(res.ok ? `Listening on ${p}` : 'Could not open that port',
                        res.ok ? 'good' : 'bad');
                },
              },
                h('span.mono', null, String(i)),
                h('span', null, p),
                h('span.list__spacer'),
                i === midi.port_index ? h('span.tag.tag--amber', null, 'open') : null))
            : [h('div.empty', null, 'no MIDI inputs found')]),
        midi.error ? h('div.note.note--warn', { style: { marginTop: '10px' } }, midi.error) : null,
        h('div.note', { style: { marginTop: '10px' } },
          'The port is watched and reopened automatically, so unplugging the piano and ',
          'plugging it back in does not need a restart. If Windows sees no MIDI device ',
          'at all, that is a driver problem -- run ', h('strong', null, 'tools/midi_probe.py'),
          ', which needs no venv and no packages.'))),

      h('div.col-6', null, mod('Latency', 'the honest number', h('div.stats', { id: 'lat-stats' }),
        h('div.note', { style: { marginTop: '12px' } },
          h('strong', null, 'This is not end-to-end latency.'), ' It is the time from the ',
          'MIDI callback being entered to the synth call returning. It excludes USB ',
          'transfer, the audio buffer, DMA, the DAC and the air. Software cannot measure ',
          'the real figure. The buffer above is the part you can reason about.'))),

      h('div.col-12', null, mod('Audio output', 'applies live -- the stream reopens',
        h('div.stats', { id: 'audio-stats', style: { marginBottom: '16px' } },
          stat(eng.buffer_ms != null ? `${eng.buffer_ms} ms` : '~10 ms', 'Delay',
               eng.exclusive ? `${eng.period_size ?? '--'} sample buffer`
                             : 'Windows picks the buffer',
               'stat__value--amber'),
          stat((eng.sample_rate ?? 0) / 1000 + 'k', 'Sample rate', '16-bit'),
          stat(eng.exclusive ? 'Exclusive' : 'Shared', 'Device mode',
               eng.exclusive ? 'Keys owns the device' : 'shared with other apps')),

        h('div', { style: { display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '14px', alignItems: 'end' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Output device')),
            h('select', { id: 'audio-device' }, h('option', null, 'loading...'))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Mode')),
            h('select', { id: 'audio-excl' },
              h('option', { value: '0', selected: !eng.exclusive }, 'Shared -- everything works'),
              h('option', { value: '1', selected: eng.exclusive }, 'Exclusive -- 3 ms, Keys only'))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Buffer'),
              h('span.field__value', { id: 'audio-period-v' }, String(eng.period_size ?? 144))),
            h('select', { id: 'audio-period' },
              [144, 160, 192, 256, 384, 480].map((n) => h('option', {
                value: n, selected: n === (eng.period_size ?? 144),
              }, `${n} (${(n / (eng.sample_rate || 48000) * 1000).toFixed(2)} ms)`))))),

        h('div.btnrow', null,
          h('button.btn.btn--lg', { id: 'audio-apply' }, 'Apply'),
          h('button.btn', { id: 'audio-share' }, 'Play nicely with everything'),
          h('button.btn', { id: 'audio-lowlat' }, 'Lowest latency (takes the device)')),

        h('div.note', { style: { marginTop: '14px' } },
          h('strong', null, 'Shared is the default, on purpose.'), ' Keys coexists with ',
          'Spotify, Discord, a browser and everything else. Windows owns the buffer in ',
          'this mode, which is about 10 ms -- inside the range a piano action already ',
          'spans between a soft and a hard keystroke.'),
        h('div.note.note--warn', { style: { marginTop: '8px' } },
          h('strong', null, 'Exclusive mode takes the output device away from every other app.'),
          ' Not turns them down -- silent. Spotify stops, Discord stops, a browser reports ',
          'an audio rendering error. That is not a bug and not a conflict; it is how WASAPI ',
          'buys the 3 ms, and it is why it is not the default.'),
        h('div.note', { style: { marginTop: '8px' } },
          h('strong', null, 'Want 3 ms without losing anything?'), ' Pin Keys to an output ',
          'Windows is not using -- an audio interface, headphones on a second endpoint, or ',
          'an HDMI device you are not watching. The piano goes there exclusively, everything ',
          'else keeps the default device, and nothing is taken from you.'),
        h('div.note', { style: { marginTop: '8px' } },
          '144 samples is this machine\'s measured floor; 128 is refused with "minimum period ',
          'is 144". Buffer size only does anything in exclusive mode. Raise it if you hear ',
          'crackling.'))),

      

      themePanel(ctx),
      clockPanel(ctx),
      keysPanel(ctx),

      h('div.col-6', null, mod('Reading key', null,
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Key signature')),
          h('select', {
            onchange: (e) => api.post('/api/settings', { ui: { key_signature: e.target.value } })
              .then(() => toast('Notes will spell to ' + e.target.value, 'good', 1800)),
          }, (st.keys || ['C']).map((k) => h('option', {
            value: k, selected: k === (s.ui?.key_signature || 'C'),
          }, k)))),
        h('div.note', null,
          'Sets how notes are spelled everywhere: in E flat major, MIDI 63 reads ',
          h('strong', null, 'Eb4'), ', not D#4.'))),

      h('div.col-6', null, mod('Reverb', null,
        fx('reverb', 'room', 0, 1, 0.01, s.reverb?.room ?? 0.3),
        fx('reverb', 'damping', 0, 1, 0.01, s.reverb?.damping ?? 0.4),
        fx('reverb', 'width', 0, 100, 1, s.reverb?.width ?? 6),
        fx('reverb', 'level', 0, 1, 0.01, s.reverb?.level ?? 0.55))),

      h('div.col-6', null, mod('Chorus', null,
        fx('chorus', 'level', 0, 10, 0.1, s.chorus?.level ?? 1.2),
        fx('chorus', 'speed', 0.29, 5, 0.01, s.chorus?.speed ?? 0.4),
        fx('chorus', 'depth', 0, 21, 0.1, s.chorus?.depth ?? 6),
        fx('chorus', 'nr', 0, 20, 1, s.chorus?.nr ?? 3))),

      h('div.col-6', null, mod('Startup sound', 'what Keys opens with',
        h('label.field', null,
          h('span.field__label', null, h('span', null, 'Preset')),
          h('select', {
            onchange: (e) => api.post(`/api/presets/${e.target.value}/startup`)
              .then(() => toast(`Keys will open with ${e.target.selectedOptions[0].textContent}`,
                                'good', 2600))
              .catch((err) => toast(err.message, 'bad')),
          }, (st.presets || []).map((p) => h('option', {
            value: p.id, selected: p.id === (s.preset || 'grand-piano'),
          }, `${p.name}${p.zones?.length > 1 ? `  (${p.zones.length} zones)` : ''}`)))),
        h('div.note', null,
          'Loading a preset in ', h('strong', null, 'Play'), ' does not change this. ',
          'The keyboard is one instrument end to end unless you say otherwise.'))),

      h('div.col-12', null, mod('SoundFonts', null,
        h('div.list', null, (st.soundfonts || []).map((sf) => h('div.list__row', null,
          h('span', null, sf.file),
          h('span.list__spacer'),
          h('span.mono', null, (sf.size / 1048576).toFixed(1) + ' MB'),
          sf.loaded ? h('span.tag.tag--green', null, 'loaded') : h('span.tag', null, 'on disk')))),
        h('div.note', { style: { marginTop: '10px' } },
          'Drop more .sf2 / .sf3 files into ', h('strong', null, 'soundfonts/'),
          ' and pick them per zone. Salamander Grand will not work here -- it is SFZ, ',
          'and FluidSynth cannot load SFZ.'))),

      

      dataPanel(),

      h('div.col-6', null, mod('About', `version ${st.version || '?'}`,
        h('div.stats', null,
          stat(st.version || '?', 'Version', st.frozen ? 'installed build' : 'source checkout'),
          stat(st.frozen ? 'EXE' : 'PY', 'Running as',
               st.frozen ? 'from the installer' : 'from keys.py')),
        h('div.btnrow', { style: { marginTop: '12px' } },
          h('button.btn.btn--lg', { id: 'upd-check', onclick: checkUpdate },
            'Check for updates')),
        h('div', { id: 'upd-result', style: { marginTop: '10px' } }),
        h('div.note', { style: { marginTop: '10px' } },
          'Keys checks only when you press that button -- never on launch, never on a ',
          'timer, never in the background. The request sends nothing but a GET for the ',
          'public release list.'))),

      h('div.col-3', null, mod('Panel layout', null,
        h('div.note', null,
          'Every panel can be dragged by its header and resized with the arrows that ',
          'appear when you hover it. The arrangement is per tab and saved as you go, ',
          'so put the things you actually use at the top.'),
        h('div.btnrow', { style: { marginTop: '12px' } },
          h('button.btn', { onclick: () => resetLayout() },
            'Put every tab back the way it shipped')))),

      h('div.col-6', null, mod('Tutorial', `${CHAPTERS.length} chapters`,
        h('div.note', null,
          'The whole manual, and the same thing that runs on first launch. Every ',
          'chapter is one click from every other, so it is also the place to look ',
          'one thing up.'),
        h('div.btnrow', { style: { marginTop: '12px' } },
          h('button.btn.btn--lg', { onclick: () => startTutorial(ctx) },
            'Start from the beginning')),
        h('div.tour__jump', { style: { marginTop: '12px' } },
          CHAPTERS.map((c) => h('button.btn.btn--sm', {
            onclick: () => startTutorial(ctx, c.id),
          }, c.title))))),

      h('div.col-12', null, mod('Events', 'engine to browser',
        h('div.stats', { id: 'hub-stats' }),
        h('div.note', { style: { marginTop: '12px' } },
          'Dropped frames mean the UI fell behind and the queue shed its oldest events. ',
          'That is by design -- the callback never blocks. The held-note list is resent ',
          'every second from the engine, so the display corrects itself.'))),
    ));

    wireAudio(ctx);
    this.status?.(ctx.status || {}, ctx);
  },

  status(s) {
    const lat = s.hub?.latency;
    const host = $('#lat-stats');
    if (host && lat) {
      host.replaceChildren(
        stat(lat.n ? lat.median_us + 'us' : '--', 'Callback median', `n=${lat.n || 0}`,
             'stat__value--cyan'),
        stat(lat.n ? lat.p95_us + 'us' : '--', 'p95'),
        stat(lat.n ? lat.max_us + 'us' : '--', 'Worst'));
    }
    const hub = $('#hub-stats');
    if (hub && s.hub) {
      hub.replaceChildren(
        stat((s.hub.events_total || 0).toLocaleString(), 'Events'),
        stat(s.hub.queue_depth ?? 0, 'Queue depth', `limit ${s.hub.queue_limit}`),
        stat(s.hub.dropped ?? 0, 'Dropped',
             s.hub.dropped ? 'UI fell behind' : 'none', s.hub.dropped ? 'stat__value--amber' : ''));
    }
  },
};

async function checkUpdate() {
  const btn = $('#upd-check');
  const host = $('#upd-result');
  btn.disabled = true;
  btn.textContent = 'Checking...';
  host.replaceChildren();
  try {
    const r = await api.post('/api/update/check', {});
    if (r.error) {
      host.append(h('div.note.note--warn', null, r.error));
    } else if (r.newer) {
      host.append(h('div.note.note--warn', null,
        h('strong', null, `${r.latest} is available.`), ` You are on ${r.current}. `,
        h('a', { href: r.url, target: '_blank', rel: 'noreferrer' }, 'Open the release'),
        r.download_name ? ` (${r.download_name}, ${(r.download_size / 1048576).toFixed(0)} MB)` : ''));
      if (r.notes) host.append(h('div.note', { style: { marginTop: '8px' } }, r.notes));
    } else {
      host.append(h('div.note', null,
        `Up to date -- ${r.current}`,
        r.latest && r.latest !== r.current ? ` (latest published: ${r.latest})` : ''));
    }
  } catch (err) {
    host.append(h('div.note.note--warn', null, err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check for updates';
  }
}

async function wireAudio(ctx) {
  try {
    const res = await api.get('/api/audio/devices');
    const sel = $('#audio-device');
    sel.replaceChildren(...res.devices.map((d) => h('option', {
      value: d, selected: d === res.current,
    }, d === 'default' ? 'System default' : d)));
  } catch {
    $('#audio-device').replaceChildren(h('option', { value: 'default' }, 'System default'));
  }

  const apply = async (patch, label) => {
    const btn = $('#audio-apply');
    btn.disabled = true;
    btn.textContent = 'Reopening...';
    try {
      const res = await api.post('/api/audio', patch);
      for (const w of res.warnings || []) toast(w, 'bad', 9000);
      if (res.ok) {
        toast(label || 'Audio reopened', 'good');
        ctx.state.engine = res.engine;
        await ctx.refresh();
      }
    } catch (err) {
      toast(err.message, 'bad', 8000);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Apply';
    }
  };

  $('#audio-apply').onclick = () => apply({
    device: $('#audio-device').value,
    exclusive: $('#audio-excl').value === '1',
    period_size: Number($('#audio-period').value),
  });

  // The two shortcuts, because "I want Discord back" and "I want it tight" are the
  // only two things anyone actually wants from this panel.
  $('#audio-share').onclick = () => apply(
    { exclusive: false }, 'Shared mode -- other apps can use this device again');
  $('#audio-lowlat').onclick = () => apply(
    { exclusive: true, period_size: 144 }, 'Exclusive, 144 samples -- 3.00 ms');
}

function fx(group, key, min, max, step, value) {
  const id = `fx-${group}-${key}`;
  return h('label.field', null,
    h('span.field__label', null, h('span', null, key),
      h('span.field__value', { id }, String(value))),
    slider({
      min, max, step, value,
      oninput: (v) => { $('#' + id).textContent = String(v); },
      onchange: (v) => api.post('/api/settings', { [group]: { [key]: v } }),
    }));
}
