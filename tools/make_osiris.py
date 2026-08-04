"""Build a SoundFont from the Osiris Piano SFZ sample set.

    .venv\\Scripts\\python.exe tools\\make_osiris.py --src <path-to-Osiris_Piano>
    .venv\\Scripts\\python.exe tools\\make_osiris.py --src ... --stereo --rate 48000

**Why this exists.** The `whisper` velocity curve is an attenuator and a low-pass on a
font that holds one recording of each note (see `CURVES` in backend/engine.py). It is
not a soft piano and cannot become one, because "soft" is a property of the recording:
felt, or the soft pedal, lengthens the hammer-string contact and kills the upper
partials before a microphone is involved. The only fix is different samples.

Osiris Piano is a worn Yamaha C2 recorded at half-stick with **the soft pedal down and
very low dynamics**, by Versilian Studios and Karoryfer, released **CC0-1.0**. It is
what the app was missing. It ships as SFZ + FLAC, which FluidSynth cannot load, so
this converts it.

**Why a script instead of the Polyphone GUI.** A GUI session is not a build step. The
preset shelf is generated from a table (`make_presets.py`) precisely so that "how did
this get made" has an answer in the repo, and a 200 MB binary asset deserves that more
than a 500-byte JSON does, not less. Run it again and you get the same font.

**The size problem, stated plainly.** FLAC's compression does not survive into SF2 --
the format stores raw PCM. The 26.6 MB of FLAC in the repo is 206 MB of 16-bit stereo
PCM at 48 kHz. So this exposes the three levers that actually matter and prints what
each costs, rather than picking for you:

  --mono / --stereo   halves it. SF2 stores stereo as two linked mono samples.
  --rate              48000 native. This instrument is DARK -- almost nothing above
                      a few kHz survives the soft pedal -- so downsampling is cheaper
                      here than it would be on a bright grand.
  --tail              seconds of decay to keep. The mean sample runs 10.4 s; a
                      practice instrument rarely needs the last five, and the release
                      envelope covers the join.

`--sf3` writes Ogg Vorbis samples instead of raw PCM, which FluidSynth 2.5.7 reads and
which is about 10x smaller again. No external converter is involved: libsndfile is
already here to decode the FLAC and it encodes Vorbis too. The cost is decode time at
load -- roughly 170 ms per MB -- so an SF3 belongs on a font that loads when you pick
the preset rather than at boot.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import soundfile as sf
except ImportError:  # pragma: no cover
    print("  this tool needs soundfile to decode FLAC:")
    print("    .venv\\Scripts\\pip install soundfile")
    raise SystemExit(1)


# --- reading the SFZ -----------------------------------------------------------
@dataclass
class Region:
    sample: Path
    lokey: int
    hikey: int
    root: int
    release: float
    velocity: int          # which layer this region belongs to: 1 or 2
    one_shot: bool = False

    @property
    def name(self) -> str:
        """A name that survives SF2's 20-byte field.

        `Piano_UC_MicA_C3_vl1` truncates to `Piano_UC_MicA_C3_vl` -- which drops the
        one character that distinguishes the two velocity layers, so both samples come
        out identically named and every dump of the font is unreadable.

        Note also that Osiris names an octave below scientific pitch: its `C3` is MIDI
        60, middle C. That is not an error to correct, it is their convention, and the
        SFZ's own pitch_keycenter is authoritative -- but it will mislead anyone who
        compares a rendered middle C against the file called C4, so the key number
        goes in the name.
        """
        stem = self.sample.stem
        note = stem.split("_MicA_")[-1].split("_")[0] if "_MicA_" in stem else stem[:6]
        return f"UC{self.root:03d}_{note}_v{self.velocity}"


def parse_map(path: Path, velocity: int, base: Path) -> list[Region]:
    """Read one of Osiris's mapping includes.

    They are the simplest possible SFZ: a flat list of <region> with sample, lokey,
    hikey, pitch_keycenter and ampeg_release. No round robins -- Osiris was authored
    without them, which is the one thing that would not survive this conversion.

    `base` is the directory of the MASTER program, not of this include. SFZ resolves
    sample paths against the file that starts the chain, so "../UC/A/..." inside
    Programs/modules/mappings/ still means <root>/UC/A. Getting that wrong looks like
    every sample being missing at once.
    """
    out: list[Region] = []
    cur: dict[str, str] = {}
    text = path.read_text("utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        if line.startswith("<region>"):
            if cur.get("sample"):
                out.append(_region(base, cur, velocity))
            cur = {}
            line = line[len("<region>"):].strip()
        for k, v in re.findall(r"(\w+)=([^\s]+)", line):
            cur[k] = v
    if cur.get("sample"):
        out.append(_region(base, cur, velocity))
    return out


def _region(base: Path, d: dict[str, str], velocity: int) -> Region:
    rel = d["sample"].replace("\\", "/")
    return Region(
        sample=(base / rel).resolve(),
        lokey=int(d.get("lokey", 0)),
        hikey=int(d.get("hikey", 127)),
        # One region (middle C) omits pitch_keycenter entirely. SFZ's default for a
        # missing keycenter is 60, and lokey is also 60 there, so both readings agree
        # -- but only by luck, so be explicit rather than rely on it.
        root=int(d["pitch_keycenter"]) if "pitch_keycenter" in d
        else int(d.get("lokey", 60)),
        release=float(d.get("ampeg_release", 0.6)),
        velocity=velocity,
        # The top twelve regions are one_shot: they play to the end whatever the key
        # does, because at that end of the piano the whole note is shorter than a
        # release envelope and gating it just truncates it. Dropping this opcode is
        # inaudible in a file listing and audible on the top octave.
        one_shot=d.get("loop_mode") == "one_shot",
    )


# --- audio ---------------------------------------------------------------------
def load_sample(path: Path, mono: bool, rate: int, tail: float) -> tuple[np.ndarray, int]:
    """Decode, optionally fold to mono, resample, and trim -- returns int16."""
    data, sr = sf.read(str(path), dtype="float64", always_2d=True)
    if mono:
        data = data.mean(axis=1, keepdims=True)

    if rate and rate != sr:
        # Linear resampling. Good enough here and deliberately dependency-free: this
        # instrument's energy sits low (the soft pedal removes most of what a better
        # kernel would protect), and the alternative is another wheel to install.
        n_out = int(round(data.shape[0] * rate / sr))
        x_old = np.arange(data.shape[0])
        x_new = np.linspace(0, data.shape[0] - 1, n_out)
        data = np.stack([np.interp(x_new, x_old, data[:, c])
                         for c in range(data.shape[1])], axis=1)
        sr = rate

    if tail:
        keep = int(sr * tail)
        if data.shape[0] > keep:
            data = data[:keep].copy()
            # Fade the cut so it does not click. The zone's release envelope takes
            # over from here.
            fade = min(int(sr * 0.25), keep // 4)
            if fade > 1:
                data[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]

    peak = np.max(np.abs(data)) or 1.0
    if peak > 1.0:
        data /= peak
    return (data * 32767.0).astype("<i2"), sr


# --- levelling -----------------------------------------------------------------
def loudness(pcm: np.ndarray) -> float:
    """RMS of the first second, which tracks what you hear better than the peak.

    A piano note's peak is one hammer transient and can be several dB out from how
    loud the note actually seems; the body of the note is what the ear averages.
    """
    x = pcm.astype("float64") / 32768.0
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = min(len(x), 48000)
    return float(np.sqrt(np.mean(x[:n] ** 2))) if n else 0.0


def level_trend(keys: list[int], levels: list[float], window: int = 9) -> list[float]:
    """A running median of loudness across the keyboard.

    The median is the point: it follows the real slope of the instrument (a piano is
    genuinely louder in the middle than at the top) while ignoring the one note that
    was struck differently, which is what a mean would smear across its neighbours.

    Nine diatonic samples, chosen by measuring rather than taste. What a listener
    notices is the STEP BETWEEN ADJACENT KEYS, not a slow slope across the keyboard,
    and the 90th-percentile step between neighbours came out: uncorrected 5.6 dB,
    window 5 -> 2.3 dB, window 9 -> 1.4 dB, window 13 -> 1.6 dB. Wider than 9 starts
    fitting the keyboard's own slope into the correction and gets worse again.
    """
    out = []
    for i in range(len(levels)):
        lo = max(0, i - window // 2)
        hi = min(len(levels), i + window // 2 + 1)
        out.append(float(np.median(levels[lo:hi])))
    return out


# FluidSynth's own scale for initialAttenuation, measured against this build rather
# than taken from the spec: 200 units rendered exactly -8.0 dB and 600 units exactly
# -24.0 dB, so it is 0.04 dB per unit, not the 0.1 the SF2 spec implies. Negative
# values are honoured and boost, which the spec's 0..1440 range does not promise.
ATTEN_DB_PER_UNIT = 0.04


def attenuation_units(gain_db: float) -> int:
    return int(round(-gain_db / ATTEN_DB_PER_UNIT))


def match_levels(samples: list[tuple[int, np.ndarray]], limit_db: float,
                 normalised: bool) -> dict[int, float]:
    """Per-sample gain, in dB, so no note jumps out of its neighbours.

    Two separate things are being corrected here and only one of them is Osiris's
    fault.

    **The recording's own inconsistency.** It was cut at deliberately very low
    dynamics, where the difference between one strike and the next is a large
    fraction of the signal. On the quiet layer around middle C, D4 came out 12.2 dB
    below C4 -- reported by a player as notes that "do not carry that sound", which
    is exactly what that is. What gets corrected is deviation from a SMOOTH TREND,
    never deviation from a flat line: a piano is genuinely not flat across 88 keys
    and must not be made so. What it also is not is 12 dB down on one note.

    **FluidSynth peak-normalises every Ogg sample in an SF3.** Verified: rendered
    level tracked rms/peak across six keys with a spread of 1.00x, and scaling the
    PCM changed nothing at all while the identical change in an uncompressed SF2
    landed to within 0.1 dB. So in an SF3 the note-to-note balance is not the
    recording's -- it is whatever each sample's rms-to-peak ratio happens to be,
    which is arbitrary. `normalised` says to undo that.

    Which is why this returns dB for the ATTENUATION GENERATOR rather than a factor
    to multiply the samples by. Attenuation is applied by the voice, after the
    loader has had its way with the sample data. Scaling the PCM is normalised
    straight back out.
    """
    keys = [k for k, _ in samples]
    rms = [loudness(p) for _, p in samples]
    peak = [float(np.max(np.abs(p.astype("float64") / 32768.0)) or 1.0) for _, p in samples]
    # What the synth will actually render at, up to a constant.
    natural = [r / pk if normalised else r for r, pk in zip(rms, peak)]
    target = level_trend(keys, rms)

    raw = []
    for nat, tgt in zip(natural, target):
        raw.append(20 * np.log10(tgt / nat) if nat > 0 and tgt > 0 else 0.0)
    # Centre on the median so the font's overall loudness is left alone and only the
    # spread between notes changes.
    mid = float(np.median(raw))
    out: dict[int, float] = {}
    for k, db in zip(keys, raw):
        out[k] = float(max(-limit_db, min(limit_db, db - mid)))
    return out


# --- writing the SF2 -----------------------------------------------------------
# Generator operators used here.
GEN_KEYRANGE, GEN_VELRANGE = 43, 44
GEN_RELEASEVOLENV, GEN_PAN = 38, 17
GEN_SAMPLEMODES, GEN_ROOTKEY, GEN_SAMPLEID = 54, 58, 53
GEN_ATTENUATION = 48

MONO, RIGHT, LEFT = 1, 2, 4


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def timecents(seconds: float) -> int:
    """SF2 stores envelope times as 1200*log2(sec). Floor at the spec's -12000."""
    if seconds <= 0:
        return -12000
    return max(-12000, min(8000, int(round(1200 * np.log2(seconds)))))


class SF2Writer:
    """Just enough of the SoundFont 2.04 spec to emit one instrument.

    The parts that bite, all learned by loading the output back into FluidSynth:
      * every one of phdr/pbag/inst/ibag/shdr ends with a TERMINAL record that is
        counted in the chunk size and points one past the real data;
      * sample data needs 46 zero frames after each sample, or a voice that runs off
        the end reads its neighbour;
      * within a zone, the generator that references the target (instrument, or
        sampleID) must come LAST, and keyRange must come FIRST.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.samples: list[tuple[str, np.ndarray, int, int, int, int]] = []
        self.pcm: list[np.ndarray] = []
        self.frames = 0

    def add_sample(self, name: str, pcm: np.ndarray, rate: int, root: int,
                   stype: int = MONO, link: int = 0) -> int:
        start = self.frames
        self.pcm.append(pcm)
        self.pcm.append(np.zeros(46, dtype="<i2"))
        self.frames += len(pcm) + 46
        self.samples.append((name[:19], pcm, rate, root, stype, link))
        return len(self.samples) - 1, start

    # -- SF3 --------------------------------------------------------------------
    # SF3 is SF2 with each sample stored as its own Ogg Vorbis stream instead of raw
    # PCM, and the sample type flagged 0x10. FluidSynth has read it since 1.1.7 and
    # the binary this app ships (2.5.7, linked against libsndfile 1.2.2 with Vorbis)
    # does -- verified by loading one and rendering non-silence.
    #
    # Two details the spec is quiet about and that cost an afternoon each:
    #   * start/end in shdr become BYTE offsets into the smpl chunk, not frame counts;
    #   * there are no 46 frames of padding between compressed samples -- the streams
    #     are self-delimiting, and inserting silence corrupts the offsets.
    SF3_COMPRESSED = 0x10

    def build_sf3_smpl(self, compression: float) -> tuple[bytes, list[tuple[int, int]]]:
        """Each sample as its own Ogg stream, concatenated.

        `compression` is libsndfile's scale and it runs the way the name says, not the
        way "quality" would: **0.0 is the largest and best, 1.0 the smallest and
        worst.** Measured on a 3 s tone: 0.0 -> 11,866 B, 1.0 -> 8,760 B. Naming this
        parameter "quality" and passing it straight through would inverse the knob for
        whoever used it next.
        """
        import io
        blobs, spans = [], []
        pos = 0
        for _name, pcm, rate, _root, _stype, _link in self.samples:
            buf = io.BytesIO()
            with sf.SoundFile(buf, "w", samplerate=rate, channels=1,
                              format="OGG", subtype="VORBIS",
                              compression_level=compression) as f:
                f.write(pcm.astype("float64") / 32768.0)
            b = buf.getvalue()
            blobs.append(b)
            spans.append((pos, pos + len(b)))
            pos += len(b)
        return b"".join(blobs), spans

    # -- chunk helpers
    @staticmethod
    def _chunk(cid: bytes, payload: bytes) -> bytes:
        pad = b"\0" if len(payload) & 1 else b""
        return cid + struct.pack("<I", len(payload)) + payload + pad

    @staticmethod
    def _zstr(s: str, n: int) -> bytes:
        b = s.encode("latin1", "replace")[: n - 1]
        return b + b"\0" * (n - len(b))

    def build(self, zones: list[dict], preset_name: str, sf3: float | None = None) -> bytes:
        # ---- INFO
        info = b"INFO"
        # A version of 3.x is how a reader knows the samples are Ogg streams.
        info += self._chunk(b"ifil", struct.pack("<HH", 3, 0) if sf3
                            else struct.pack("<HH", 2, 4))
        info += self._chunk(b"isng", self._zstr("EMU8000", 8))
        info += self._chunk(b"INAM", self._zstr(self.name, 32))
        info += self._chunk(b"IENG", self._zstr("Versilian Studios & Karoryfer", 48))
        info += self._chunk(b"ICOP", self._zstr("CC0 1.0 Universal (public domain)", 48))
        info += self._chunk(b"ICMT", self._zstr(
            "Osiris Piano: a Yamaha C2 at half-stick, soft pedal down, low dynamics. "
            "Converted for Keys by tools/make_osiris.py.", 160))
        info += self._chunk(b"ISFT", self._zstr("Keys make_osiris.py", 32))
        chunks = self._chunk(b"LIST", info)

        # ---- sdta
        spans: list[tuple[int, int]] | None = None
        if sf3 is not None:
            blob, spans = self.build_sf3_smpl(sf3)
            sdta = b"sdta" + self._chunk(b"smpl", blob)
        else:
            pcm = np.concatenate(self.pcm) if self.pcm else np.zeros(0, dtype="<i2")
            sdta = b"sdta" + self._chunk(b"smpl", pcm.tobytes())
        chunks += self._chunk(b"LIST", sdta)

        # ---- pdta
        # one preset -> one instrument
        phdr = self._zstr(preset_name, 20) + struct.pack("<HHHIII", 0, 0, 0, 0, 0, 0)
        phdr += self._zstr("EOP", 20) + struct.pack("<HHHIII", 0, 0, 1, 0, 0, 0)
        pbag = struct.pack("<HH", 0, 0) + struct.pack("<HH", 1, 0)
        pmod = struct.pack("<HHhHH", 0, 0, 0, 0, 0)
        pgen = struct.pack("<HH", 41, 0) + struct.pack("<HH", 0, 0)   # instrument 0

        inst = self._zstr(self.name[:19], 20) + struct.pack("<H", 0)
        inst += self._zstr("EOI", 20) + struct.pack("<H", len(zones))

        ibag = b"".join(struct.pack("<HH", i * 0, 0) for i in range(0))  # filled below
        ibag = b""
        igen = b""
        gen_index = 0
        for z in zones:
            ibag += struct.pack("<HH", gen_index, 0)
            gens = [
                (GEN_KEYRANGE, z["lokey"] | (z["hikey"] << 8)),
                (GEN_VELRANGE, z["lovel"] | (z["hivel"] << 8)),
            ]
            if z.get("pan"):
                gens.append((GEN_PAN, z["pan"] & 0xFFFF))
            if z.get("attenuation"):
                gens.append((GEN_ATTENUATION, z["attenuation"]))
            gens += [
                (GEN_RELEASEVOLENV, timecents(z["release"]) & 0xFFFF),
                (GEN_SAMPLEMODES, 0),                       # no loop
                (GEN_ROOTKEY, z["root"]),
                (GEN_SAMPLEID, z["sample"]),                # must be last
            ]
            for op, amt in gens:
                igen += struct.pack("<HH", op, amt & 0xFFFF)
            gen_index += len(gens)
        ibag += struct.pack("<HH", gen_index, 0)            # terminal
        igen += struct.pack("<HH", 0, 0)                    # terminal
        imod = struct.pack("<HHhHH", 0, 0, 0, 0, 0)

        shdr = b""
        pos = 0
        for i, (name, data, rate, root, stype, link) in enumerate(self.samples):
            if spans is not None:
                # Byte offsets into the Ogg blob; loop points stay in FRAMES.
                start, end = spans[i]
                loop_start, loop_end = 0, len(data) - 1
                stype |= self.SF3_COMPRESSED
            else:
                start = pos
                end = start + len(data)
                loop_start, loop_end = end - 1, end
                pos = end + 46
            shdr += (self._zstr(name, 20)
                     + struct.pack("<IIIIIBbHH", start, end, loop_start, loop_end,
                                   rate, root, 0, link, stype))
        shdr += self._zstr("EOS", 20) + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)

        pdta = b"pdta"
        for cid, payload in ((b"phdr", phdr), (b"pbag", pbag), (b"pmod", pmod),
                             (b"pgen", pgen), (b"inst", inst), (b"ibag", ibag),
                             (b"imod", imod), (b"igen", igen), (b"shdr", shdr)):
            pdta += self._chunk(cid, payload)
        chunks += self._chunk(b"LIST", pdta)

        return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"sfbk" + chunks


# --- the build -----------------------------------------------------------------
def main() -> int:
    # The verification child re-enters here. It needs the regions to compare against,
    # so it takes the font and the mic and rebuilds just the mapping (cheap -- text).
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-only":
        font, mic = Path(sys.argv[2]), sys.argv[3]
        src = Path(os.environ["_KEYS_OSIRIS_SRC"])
        regions: list[Region] = []
        for vel, tag in ((1, "vl1"), (2, "vl2")):
            regions += parse_map(src / "Programs" / "modules" / "mappings"
                                 / f"uc_mic{mic.lower()}_{tag}_map.sfz", vel, src / "Programs")
        return verify(font, regions, argparse.Namespace(mic=mic))

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, type=Path,
                    help="a checkout of github.com/sfzinstruments/Osiris_Piano")
    ap.add_argument("--mic", default="A", choices=list("ABC"),
                    help="A is inside the piano, B at the edge, C outside")
    ap.add_argument("--out", type=Path, default=ROOT / "soundfonts" / "OsirisUnaCorda.sf2")
    ap.add_argument("--stereo", action="store_true",
                    help="keep both channels (doubles the size)")
    ap.add_argument("--rate", type=int, default=32000,
                    help="output sample rate; 0 keeps the native 48000")
    ap.add_argument("--tail", type=float, default=6.0,
                    help="seconds of decay to keep; 0 keeps everything")
    ap.add_argument("--level-match", type=float, default=6.0, metavar="DB",
                    help="pull any sample that sits more than DB away from the smooth "
                         "trend of its neighbours back toward it, capped at DB. "
                         "Osiris was recorded at very low dynamics and a few notes "
                         "landed several dB out. 0 disables it.")
    ap.add_argument("--sf3", nargs="?", type=float, const=0.3, default=None,
                    metavar="COMPRESSION",
                    help="write SF3 (Ogg Vorbis samples) instead of SF2 -- about 10x "
                         "smaller on disk, at roughly 170 ms per MB to decode when the "
                         "font is loaded. Optional 0..1 COMPRESSION, where 0.0 is "
                         "largest/best and 1.0 smallest/worst; default 0.3")
    args = ap.parse_args()

    maps = args.src / "Programs" / "modules" / "mappings"
    regions: list[Region] = []
    for vel, tag in ((1, "vl1"), (2, "vl2")):
        p = maps / f"uc_mic{args.mic.lower()}_{tag}_map.sfz"
        if not p.exists():
            print(f"  [FAIL] no mapping at {p}")
            return 1
        regions += parse_map(p, vel, args.src / "Programs")
    print(f"  {len(regions)} regions from {maps.name} (mic {args.mic})")

    missing = [r for r in regions if not r.sample.exists()]
    if missing:
        print(f"  [FAIL] {len(missing)} samples not on disk, first: {missing[0].sample}")
        return 1

    writer = SF2Writer("Osiris Una Corda")
    zones: list[dict] = []
    src_bytes = 0

    # Decode everything first: levelling needs to see the whole layer before it can
    # tell an outlier from the instrument's own slope.
    decoded: dict[int, tuple[np.ndarray, int]] = {}
    for r in regions:
        decoded[id(r)] = load_sample(r.sample, not args.stereo, args.rate, args.tail)
        src_bytes += r.sample.stat().st_size

    gains: dict[int, float] = {}          # region id -> dB of correction
    if args.level_match:
        for vel in (1, 2):
            layer = sorted((r for r in regions if r.velocity == vel), key=lambda r: r.root)
            per_key = [(r.root, decoded[id(r)][0]) for r in layer]
            got = match_levels(per_key, args.level_match, normalised=args.sf3 is not None)
            for r in layer:
                gains[id(r)] = got.get(r.root, 0.0)
        moved = [(r, gains[id(r)]) for r in regions if abs(gains[id(r)]) > 1.5]
        if moved:
            print(f"  levelled {len(moved)} of {len(regions)} samples "
                  f"(limit +/-{args.level_match:g} dB"
                  f"{', also undoing SF3 peak normalisation' if args.sf3 is not None else ''}):")
            for r, db in sorted(moved, key=lambda t: t[0].root)[:10]:
                print(f"      {note_name(r.root):5} v{r.velocity}  {db:+5.1f} dB")
            if len(moved) > 10:
                print(f"      ... and {len(moved) - 10} more")

    for r in sorted(regions, key=lambda r: (r.velocity, r.lokey)):
        pcm, rate = decoded[id(r)]
        # As an attenuation generator, never by scaling the PCM -- see match_levels.
        atten = attenuation_units(gains.get(id(r), 0.0))
        lovel, hivel = (0, 63) if r.velocity == 1 else (64, 127)
        if args.stereo:
            left = np.ascontiguousarray(pcm[:, 0])
            right = np.ascontiguousarray(pcm[:, 1])
            li, _ = writer.add_sample(r.name + "L", left, rate, r.root, LEFT)
            ri, _ = writer.add_sample(r.name + "R", right, rate, r.root, RIGHT)
            writer.samples[li] = writer.samples[li][:5] + (ri,)
            writer.samples[ri] = writer.samples[ri][:5] + (li,)
            for idx, pan in ((li, -500), (ri, 500)):
                zones.append(dict(lokey=r.lokey, hikey=r.hikey, lovel=lovel, hivel=hivel,
                                  root=r.root, sample=idx, pan=pan, attenuation=atten,
                                  release=20.0 if r.one_shot else r.release))
        else:
            i, _ = writer.add_sample(r.name, np.ascontiguousarray(pcm[:, 0]),
                                     rate, r.root)
            zones.append(dict(lokey=r.lokey, hikey=r.hikey, lovel=lovel, hivel=hivel,
                              root=r.root, sample=i, attenuation=atten,
                              # SF2 has no one_shot. The nearest honest equivalent is
                              # a release long enough that the sample always finishes
                              # first, which is what one_shot means here.
                              release=20.0 if r.one_shot else r.release))

    blob = writer.build(zones, "Osiris Una Corda", sf3=args.sf3)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)

    print(f"  {len(writer.samples)} samples, {len(zones)} zones")
    print(f"  source flac {src_bytes/1e6:7.1f} MB")
    print(f"  wrote       {len(blob)/1e6:7.1f} MB  -> {args.out}")
    print(f"  settings: {'stereo' if args.stereo else 'mono'}, "
          f"{args.rate or 48000} Hz, tail {args.tail or 'full'}s, "
          f"{'SF3 compression=' + str(args.sf3) if args.sf3 is not None else 'SF2'}")

    os.environ["_KEYS_OSIRIS_SRC"] = str(args.src)
    return verify(args.out, regions, args)


def _centroid(x: np.ndarray, sr: int) -> float:
    """Spectral centre of mass of half a second after the onset."""
    n = min(len(x), sr // 2)
    if n < 1024:
        return 0.0
    seg = x[:n] * np.hanning(n)
    mag = np.abs(np.fft.rfft(seg)) ** 2
    freq = np.fft.rfftfreq(n, 1.0 / sr)
    tot = mag.sum()
    return float((freq * mag).sum() / tot) if tot else 0.0


def verify(path: Path, regions: list[Region], args) -> int:
    """Play it and check it against the samples it was built from.

    Writing SF2 by hand fails in ways that do not raise: an off-by-one in a terminal
    record silently loses the last zone, a wrong rootkey transposes one note, a
    missing sample link plays silence across half the keyboard. None of that shows in
    a file listing and all of it shows in a render.

    The test is FAITHFULNESS TO THE SOURCE, not absolute pitch. An earlier version
    asserted that the strongest partial near each key was one of its own harmonics,
    and it failed the top six keys on every build -- correctly measuring, wrongly
    judging. Osiris's top octave, recorded at very low dynamics with a microphone
    inside the lid, genuinely has more energy in body rumble around 120 Hz than in its
    own 4 kHz fundamental; the source FLAC measures the same way. A converter is not
    entitled to an opinion about that. What it must guarantee is that key N plays the
    sample the mapping assigned to key N, which comparing spectra against the source
    checks directly and without any assumption about the instrument.
    """
    # IN A SUBPROCESS, ON PURPOSE. A malformed SF3 does not make FluidSynth return an
    # error -- it makes it read off the end of a buffer and take the process down with
    # an access violation, which no `except` here can catch. Found the hard way:
    # `--sf3 0.0` produces per-sample Ogg streams around 141 KB and faults on load,
    # while 0.05 (129 KB) is fine. The exact threshold is inside FluidSynth and is not
    # worth reverse-engineering; what matters is that a build which crashes the synth
    # must FAIL here rather than write a file and exit 0.
    if os.environ.get("_KEYS_OSIRIS_CHILD") != "1":
        proc = subprocess.run(
            [sys.executable, __file__, "--verify-only", str(path), str(args.mic)],
            env={**os.environ, "_KEYS_OSIRIS_CHILD": "1",
                 "_KEYS_OSIRIS_SRC": os.environ.get("_KEYS_OSIRIS_SRC", "")},
            capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            # A fault arrives either as a raw NTSTATUS exit code or, because ctypes
            # turns it into an OSError first, as a Python traceback and exit 1. Both
            # mean the same thing and neither is self-explanatory, so say it.
            crashed = ("access violation" in proc.stderr
                       or "Traceback" in proc.stderr
                       or proc.returncode not in (0, 1))
            if crashed:
                last = proc.stderr.strip().splitlines()[-1:] or [""]
                print(f"  [FAIL] the render check CRASHED -- this font would take the "
                      f"app down, not merely sound wrong.")
                print(f"         {last[0][:110]}")
                print(f"         Known cause: --sf3 compression near 0.0 makes "
                      f"per-sample Ogg streams FluidSynth mishandles. 0.05 and above "
                      f"are fine here; the default 0.3 is well clear.")
            return 1
        return 0

    try:
        import fluidsynth
    except ImportError:
        print("  (skipping the render check -- no fluidsynth)")
        return 0

    fs = fluidsynth.Synth(samplerate=48000.0, gain=0.6)
    sfid = fs.sfload(str(path))
    if sfid == -1:
        print("  [FAIL] FluidSynth would not load it"); fs.delete(); return 1
    if fs.program_select(0, sfid, 0, 0) == -1:
        print("  [FAIL] no preset 0:0"); fs.delete(); return 1

    def play(note: int, vel: int) -> np.ndarray:
        fs.noteon(0, note, vel)
        buf = np.array(fs.get_samples(24000), dtype=np.float64).reshape(-1, 2).mean(axis=1)
        fs.noteoff(0, note)
        fs.get_samples(4800)
        return buf / 32768.0

    silent = [n for n in range(21, 109) if np.abs(play(n, 100)).max() < 1e-4]

    # One root key per region, loud layer only, against that region's own file.
    mismatched = []
    checked = 0
    for r in regions:
        if r.velocity != 2:
            continue
        src, sr = sf.read(str(r.sample), dtype="float64", always_2d=True)
        want = _centroid(src.mean(axis=1), sr)
        got = _centroid(play(r.root, 100), 48000)
        checked += 1
        if want > 0 and abs(got - want) / want > 0.25:
            mismatched.append((r.root, r.sample.name, round(want), round(got)))
    fs.delete()

    ok = True
    if silent:
        print(f"  [FAIL] {len(silent)} silent keys: {silent[:12]}"); ok = False
    else:
        print("  [PASS] all 88 keys sound")
    if mismatched:
        print(f"  [FAIL] {len(mismatched)}/{checked} keys do not match their source "
              f"sample: {mismatched[:4]}")
        ok = False
    else:
        print(f"  [PASS] all {checked} mapped keys match the spectrum of their own source file")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
