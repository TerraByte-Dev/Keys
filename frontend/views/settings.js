/* Setup -- audio, MIDI, effects, and the diagnostics you want when something is off.
 *
 * The latency panel is deliberately blunt about what it is not measuring. Nothing in
 * software can measure MIDI-to-ear latency: WASAPI loopback taps the post-mix engine,
 * misses driver, DMA, DAC and the air entirely, and returns pure silence when the
 * output is exclusive-mode. Any app claiming a round-trip number is lying, so this one
 * reports the piece it can actually see and labels it. */

import { $, api, h, mod, slider, stat, toast } from '../ui.js';

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

      h('div.col-12', null, mod('Audio output', 'applies live -- the stream reopens',
        h('div.stats', { id: 'audio-stats', style: { marginBottom: '16px' } },
          stat(eng.buffer_ms ?? 'sys', 'Buffer ms',
               eng.exclusive ? `${eng.period_size ?? '--'} samples` : 'Windows decides',
               'stat__value--amber'),
          stat((eng.sample_rate ?? 0) / 1000 + 'k', 'Sample rate', '16-bit'),
          stat(eng.exclusive ? 'EXCL' : 'SHARED', 'WASAPI mode',
               eng.exclusive ? 'Keys owns the device' : 'shared with other apps')),

        h('div', { style: { display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '14px', alignItems: 'end' } },
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Output device')),
            h('select', { id: 'audio-device' }, h('option', null, 'loading...'))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Mode')),
            h('select', { id: 'audio-excl' },
              h('option', { value: '1', selected: eng.exclusive }, 'Exclusive (3 ms)'),
              h('option', { value: '0', selected: !eng.exclusive }, 'Shared (plays nice)'))),
          h('label.field', null,
            h('span.field__label', null, h('span', null, 'Buffer'),
              h('span.field__value', { id: 'audio-period-v' }, String(eng.period_size ?? 144))),
            h('select', { id: 'audio-period' },
              [144, 160, 192, 256, 384, 480].map((n) => h('option', {
                value: n, selected: n === (eng.period_size ?? 144),
              }, `${n} (${(n / (eng.sample_rate || 48000) * 1000).toFixed(2)} ms)`))))),

        h('div.btnrow', null,
          h('button.btn.btn--lg', { id: 'audio-apply' }, 'Apply'),
          h('button.btn', { id: 'audio-share' }, 'Share with Discord'),
          h('button.btn', { id: 'audio-lowlat' }, 'Lowest latency')),

        h('div.note.note--warn', { style: { marginTop: '14px' } },
          h('strong', null, 'Exclusive mode takes the output device away from every other app.'),
          ' That is not a bug and not a conflict -- it is how WASAPI buys the 3 ms. While ',
          'Keys is running in exclusive mode, Discord, your browser and everything else go ',
          'silent on that device. Three ways out, best first:'),
        h('div.note', { style: { marginTop: '8px' } },
          h('strong', null, '1. Pin Keys to a different output.'), ' If the piano goes to one ',
          'device and Discord to another, you keep 3 ms and keep voice chat. ',
          h('strong', null, '2. Switch to Shared.'), ' Everything coexists; Windows picks the ',
          'buffer, so expect roughly 10 ms instead of 3. Still very playable. ',
          h('strong', null, '3. Just close Keys'), ' when you are done -- the device comes ',
          'straight back.'),
        h('div.note', { style: { marginTop: '8px' } },
          '144 samples is this machine\'s measured floor; 128 is refused with "minimum period ',
          'is 144". Buffer size only does anything in exclusive mode. Raise it if you hear ',
          'crackling.'))),

      h('div.col-6', null, mod('Latency', 'the honest number', h('div.stats', { id: 'lat-stats' }),
        h('div.note', { style: { marginTop: '12px' } },
          h('strong', null, 'This is not end-to-end latency.'), ' It is the time from the ',
          'MIDI callback being entered to the synth call returning. It excludes USB ',
          'transfer, the audio buffer, DMA, the DAC and the air. Software cannot measure ',
          'the real figure. The buffer above is the part you can reason about.'))),

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
          h('strong', null, 'Eb4'), ', not D#4.'),
        h('label.field', { style: { marginTop: '14px' } },
          h('span.field__label', null, h('span', null, 'Practice idle timeout'),
            h('span.field__value', { id: 'idle-v' }, (s.idle_seconds ?? 12) + 's')),
          slider({
            min: 3, max: 60, step: 1, value: s.idle_seconds ?? 12,
            oninput: (v) => { $('#idle-v').textContent = v + 's'; },
            onchange: (v) => api.post('/api/settings', { idle_seconds: v }),
          })),
        h('div.note', null,
          'A gap longer than this stops the practice clock. It is what makes ',
          '"34 minutes" mean minutes playing.'))),

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

      h('div.col-12', null, mod('Event pipeline', 'MIDI callback to browser',
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
