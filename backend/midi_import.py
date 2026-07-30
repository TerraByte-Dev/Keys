"""MIDI in, MusicXML out, so a `.mid` can be read and played like any other score.

Keys engraves MusicXML and nothing else, which is the right contract -- MIDI has no
spelling, no voices and no layout, and a reader that pretends otherwise produces
notation nobody can follow. But almost every free score on the internet ships a MIDI
beside its PDF, and telling someone to install a notation program to convert it is
telling them no.

So this converts, and it is honest about the one thing that makes it possible: a MIDI
that came out of an ENGRAVER is not a recording. It is already quantised, its tracks
are already staves, and it usually carries the key and time signature the engraver
typed. That is a completely different problem from transcribing a performance, and it
is the problem this solves. A MIDI captured from someone playing will come through as
a wall of unreadable durations, and that is not a bug here so much as the reason the
app asks for MusicXML in the first place.

Four decisions worth knowing:

**Divisions are the file's own PPQ.** MusicXML counts durations in divisions per
quarter note, MIDI counts ticks per quarter note, so adopting the source's number
makes every duration an exact integer and removes rounding from the whole pipeline.

**Spelling comes from music.py.** The key-signature meta event gives the fifths, and
the existing line-of-fifths speller turns a pitch into E flat rather than D sharp.
Reimplementing that here would be a second answer to a question already answered.

**Tracks are staves, and voices are found by overlap.** A note joins the first voice
whose previous note has finished; anything sounding at the same time as its voice's
last note starts a new one. Notes that share an onset AND a duration are a chord
rather than a voice.

**Anything not representable is snapped, and counted.** A duration that is not a note
type, a dot or a clean tuplet gets the nearest one, and the count comes back in the
report so the caller can say so instead of quietly engraving a lie.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from . import music

# Note type names by how many quarter notes they last.
_TYPES: tuple[tuple[float, str], ...] = (
    (4.0, "whole"), (2.0, "half"), (1.0, "quarter"),
    (0.5, "eighth"), (0.25, "16th"), (0.125, "32nd"), (0.0625, "64th"),
)
# Every length a single notehead can express, longest first: each type, dotted once
# and twice. A duration that is not on this list is written as a tie of ones that are.
_WRITABLE: tuple[tuple[float, str, int], ...] = tuple(sorted(
    ((base * (2 - 0.5 ** dots), name, dots) for base, name in _TYPES for dots in (0, 1, 2)),
    key=lambda x: -x[0]))
# Where a second staff's notes go when a file has only one track.
_SPLIT = 60          # middle C: at or above is the right hand
MAX_VOICES = 4       # per staff; beyond this the notation stops being readable
MAX_NOTES = 20000


class MidiError(ValueError):
    pass


@dataclass
class _Ev:
    tick: int
    dur: int
    midi: int
    staff: int
    voice: int = 1
    chord: bool = False
    spell: tuple[str, int, int] | None = None   # set for notes outside the key


@dataclass
class _Seg:
    """One notehead. A long note becomes several of these, tied."""
    tick: int
    dur: int
    src: _Ev
    tie_stop: bool = False
    tie_start: bool = False


@dataclass
class _Doc:
    ppq: int = 384
    fifths: int = 0
    beats: int = 4
    beat_type: int = 4
    tempo: float = 100.0
    name: str = ""
    events: list[_Ev] = field(default_factory=list)
    snapped: int = 0


# --- reading the file --------------------------------------------------------
def _varlen(buf: bytes, i: int) -> tuple[int, int]:
    n = 0
    while True:
        b = buf[i]
        i += 1
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            return n, i


def _tracks(data: bytes) -> tuple[int, list[bytes]]:
    # Length first. A file that is only the four magic bytes used to reach the unpack
    # below and come out as struct.error, which is not something a caller can show
    # anyone -- every failure in here has to be a MidiError with a sentence in it.
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("not a MIDI file -- it does not start with a MIDI header")
    (hlen,) = struct.unpack(">I", data[4:8])
    fmt, _ntrk, div = struct.unpack(">HHH", data[8:14])
    if fmt not in (0, 1):
        raise MidiError(f"MIDI format {fmt} is not supported (only 0 and 1)")
    if div & 0x8000:
        raise MidiError("SMPTE timecode MIDI is not supported; this needs ticks "
                        "per quarter note")
    out, i = [], 8 + hlen
    while i + 8 <= len(data):
        if data[i:i + 4] != b"MTrk":
            break
        (ln,) = struct.unpack(">I", data[i + 4:i + 8])
        out.append(data[i + 8:i + 8 + ln])
        i += 8 + ln
    if not out:
        raise MidiError("the file has no tracks")
    return div or 384, out


def _read(data: bytes) -> _Doc:
    ppq, tracks = _tracks(data)
    doc = _Doc(ppq=ppq)
    # Track -> staff, assigned over the tracks that actually carry notes, so a
    # leading control track does not consume staff 1.
    sounding: list[list[tuple[int, int, int]]] = []      # per track: (tick, midi, ...)

    for trk in tracks:
        i, now, running = 0, 0, 0
        open_notes: dict[int, int] = {}
        found: list[tuple[int, int, int]] = []
        while i < len(trk):
            delta, i = _varlen(trk, i)
            now += delta
            if i >= len(trk):
                break
            b0 = trk[i]
            if b0 == 0xFF:
                typ = trk[i + 1]
                ln, i = _varlen(trk, i + 2)
                payload = trk[i:i + ln]
                i += ln
                if typ == 0x59 and len(payload) >= 1:
                    doc.fifths = struct.unpack("b", payload[:1])[0]
                elif typ == 0x58 and len(payload) >= 2:
                    doc.beats = payload[0] or 4
                    doc.beat_type = 1 << payload[1] if payload[1] < 8 else 4
                elif typ == 0x51 and len(payload) >= 3:
                    usec = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                    if usec:
                        doc.tempo = 60_000_000.0 / usec
                elif typ == 0x03 and not doc.name:
                    text = payload.decode("utf-8", "replace").strip()
                    if text and "control" not in text.lower():
                        doc.name = text
                continue
            if b0 in (0xF0, 0xF7):
                ln, i = _varlen(trk, i + 1)
                i += ln
                continue
            if b0 & 0x80:
                running = b0
                i += 1
            status, chan = running & 0xF0, running & 0x0F
            if status in (0xC0, 0xD0):
                i += 1
                continue
            if i + 1 >= len(trk):
                break
            d1, d2 = trk[i], trk[i + 1]
            i += 2
            if status == 0x90 and d2 > 0:
                open_notes[d1] = now
            elif status in (0x80, 0x90):
                start = open_notes.pop(d1, None)
                if start is not None and now > start:
                    found.append((start, now - start, d1))
            _ = chan
        # Anything still held at the end of the track gets a nominal quarter.
        for note, start in open_notes.items():
            found.append((start, ppq, note))
        sounding.append(sorted(found))

    with_notes = [t for t in sounding if t]
    if not with_notes:
        raise MidiError("the file contains no notes")
    if sum(len(t) for t in with_notes) > MAX_NOTES:
        raise MidiError("that file is enormous; Keys reads page-turner scores, "
                        "not whole operas")

    if len(with_notes) == 1:
        # One track, so the staves have to be guessed. Middle C is the split every
        # engraver uses as a default and the only defensible one without more
        # information.
        for tick, dur, note in with_notes[0]:
            doc.events.append(_Ev(tick, dur, note, 1 if note >= _SPLIT else 2))
    else:
        # Two tracks are hands. More than two, and the extras join the nearest of
        # the two staves by their average pitch -- a grand staff has two lines on it
        # however many streams the file was written in.
        order = sorted(range(len(with_notes)),
                       key=lambda k: -sum(n[2] for n in with_notes[k]) / len(with_notes[k]))
        for rank, k in enumerate(order):
            staff = 1 if rank == 0 else 2
            for tick, dur, note in with_notes[k]:
                doc.events.append(_Ev(tick, dur, note, staff))
    # Ascending pitch, matching the order _segments will emit in. Only that one is
    # load-bearing (see the note there); this keeps the two consistent so reading the
    # event list tells you what the output will look like.
    doc.events.sort(key=lambda e: (e.tick, e.staff, e.midi))
    return doc


# --- durations --------------------------------------------------------------
def _shape(quarters: float) -> tuple[str, int, tuple[int, int] | None, bool]:
    """(type, dots, time-modification, snapped) for a length in quarter notes."""
    best: tuple[float, str, int, tuple[int, int] | None] | None = None
    for base, name in _TYPES:
        for dots in (0, 1, 2):
            plain = base * (2 - 0.5 ** dots)
            for ratio in (None, (3, 2), (5, 4), (7, 4), (6, 4)):
                length = plain if ratio is None else plain * ratio[1] / ratio[0]
                err = abs(length - quarters)
                if best is None or err < best[0]:
                    best = (err, name, dots, ratio)
    assert best is not None
    err, name, dots, ratio = best
    return name, dots, ratio, err > 1e-6


def _decompose(ticks: int, ppq: int) -> list[int]:
    """A duration as a chain of lengths a single notehead can express.

    Greedy, largest first. Five sixteenths is not a note, it is a quarter tied to a
    sixteenth, and writing it as one notehead of "about that long" is how a bar stops
    adding up.
    """
    # If the whole length is already one notehead -- including a clean tuplet -- keep
    # it whole. Without this check the greedy pass below shreds a triplet eighth into
    # a 16th, a 64th and a fragment: three tied noteheads where the engraver wrote one,
    # and three chances to be unwritable instead of none.
    _t, _d, _r, imperfect = _shape(ticks / ppq)
    if not imperfect:
        return [ticks]

    out: list[int] = []
    left = ticks
    guard = 0
    while left > 0 and guard < 32:
        guard += 1
        for quarters, _name, _dots in _WRITABLE:
            span = int(round(quarters * ppq))
            if span and span <= left:
                out.append(span)
                left -= span
                break
        else:
            # Shorter than a 64th: give it the shortest thing there is and stop.
            out.append(left)
            left = 0
    return out or [ticks]


def _segments(doc: _Doc, bar: int) -> list[_Seg]:
    """Split every note at bar lines and into writable lengths, tying the pieces.

    This is the whole reason a naive converter produces bars that do not add up: a
    note that starts on the last beat and lasts three is not one notehead, and putting
    it in the measure it started in leaves that measure a bar and a half long. 52 of
    73 scores in the test corpus were wrong this way before this existed.
    """
    segs: list[_Seg] = []
    for e in doc.events:
        pieces: list[tuple[int, int]] = []
        t, left = e.tick, e.dur
        while left > 0:
            room = bar - (t % bar)
            take = min(left, room)
            for chunk in _decompose(take, doc.ppq):
                pieces.append((t, chunk))
                t += chunk
            left -= take
        for i, (tick, dur) in enumerate(pieces):
            segs.append(_Seg(tick, dur, e,
                             tie_stop=i > 0,
                             tie_start=i < len(pieces) - 1))
    # ASCENDING by pitch, and this is the load-bearing sort in the whole module.
    #
    # <chord/> means "add this note to the previous one", so the FIRST note of a chord
    # must not carry it -- and _assign_voices assigns that flag after sorting the group
    # ascending. Emit descending and the flagged note comes out first: a <chord/> with
    # nothing in front of it to join. Verovio does not reject that. Its WebAssembly
    # reads out of bounds and the engraver dies, so a one-word ordering bug presents
    # as "the renderer crashed" on 59 of 73 files. tools/midi_check.py flips this line
    # to prove it still would.
    segs.sort(key=lambda s2: (s2.tick, s2.src.staff, s2.src.midi))
    return segs


# --- writing MusicXML -------------------------------------------------------
def _pitch(e: _Ev, key: str) -> tuple[str, int, int]:
    if e.spell is not None:
        return e.spell
    p = music.note_parts(e.midi, key)
    alter = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}.get(p["accidental"], 0)
    return p["letter"], alter, p["octave"]


# The five black-key pitch classes, spelled both ways.
_AS_SHARP = {1: ("C", 1), 3: ("D", 1), 6: ("F", 1), 8: ("G", 1), 10: ("A", 1)}
_AS_FLAT = {1: ("D", -1), 3: ("E", -1), 6: ("G", -1), 8: ("A", -1), 10: ("B", -1)}


def _spell_chromatics(doc: _Doc, key: str) -> None:
    """Spell notes outside the key by where the line is going.

    music.note_parts spells against one key signature, which is right for every note
    IN the key and arbitrary for the ones outside it -- so Fur Elise opened on E, Eb,
    E when the D sharp is a lower neighbour resolving upward and every edition ever
    printed spells it D sharp.

    The rule engravers use is direction: a chromatic note that rises is a sharp, one
    that falls is a flat. Applied per voice, because the note that resolves it is the
    next note in the same line and not whatever else the other hand is doing.
    """
    in_key = set(music.scale_pitch_classes(key))
    lines: dict[tuple[int, int], list[_Ev]] = {}
    for e in doc.events:
        if not e.chord:
            lines.setdefault((e.staff, e.voice), []).append(e)

    for row in lines.values():
        for i, e in enumerate(row):
            pc = e.midi % 12
            if pc in in_key or pc not in _AS_SHARP:
                continue
            nxt = row[i + 1] if i + 1 < len(row) else None
            prev = row[i - 1] if i else None
            if nxt is not None and nxt.midi != e.midi:
                rising = nxt.midi > e.midi
            elif prev is not None and prev.midi != e.midi:
                # No note after it, so lean on where it came FROM: a note approached
                # from below is continuing upward.
                rising = prev.midi < e.midi
            else:
                continue
            step, alter = (_AS_SHARP if rising else _AS_FLAT)[pc]
            octave = e.midi // 12 - 1
            e.spell = (step, alter, octave)


def _assign_voices(events: list[_Ev]) -> None:
    """Group simultaneous same-length notes into chords, everything else into
    voices by overlap. Done per staff, because a voice does not cross the brace."""
    for staff in (1, 2):
        rows = [e for e in events if e.staff == staff]
        ends: list[int] = []                       # end tick of each voice
        i = 0
        while i < len(rows):
            tick = rows[i].tick
            same = [e for e in rows[i:] if e.tick == tick]
            i += len(same)
            # A chord is one voice: same onset, same length.
            by_len: dict[int, list[_Ev]] = {}
            for e in same:
                by_len.setdefault(e.dur, []).append(e)
            for dur, group in sorted(by_len.items(), key=lambda kv: -kv[0]):
                slot = next((v for v, end in enumerate(ends) if end <= tick), None)
                if slot is None:
                    if len(ends) >= MAX_VOICES:
                        slot = min(range(len(ends)), key=lambda v: ends[v])
                    else:
                        ends.append(tick)
                        slot = len(ends) - 1
                ends[slot] = tick + dur
                for n, e in enumerate(sorted(group, key=lambda x: x.midi)):
                    e.voice = slot + 1 + (0 if staff == 1 else MAX_VOICES)
                    e.chord = n > 0


def _note_xml(seg: _Seg, key: str, divisions: int, ppq: int) -> tuple[str, bool]:
    e = seg.src
    quarters = seg.dur / ppq
    typ, dots, ratio, snapped = _shape(quarters)
    step, alter, octave = _pitch(e, key)
    dur = max(1, int(round(quarters * divisions)))

    bits = ["      <note>"]
    if e.chord:
        bits.append("        <chord/>")
    bits.append("        <pitch>")
    bits.append(f"          <step>{step}</step>")
    if alter:
        bits.append(f"          <alter>{alter}</alter>")
    bits.append(f"          <octave>{octave}</octave>")
    bits.append("        </pitch>")
    bits.append(f"        <duration>{dur}</duration>")
    # <tie> is the sounding instruction and <tied> is the drawn slur; a reader that
    # only sees one of them either plays two notes or draws nothing.
    if seg.tie_stop:
        bits.append('        <tie type="stop"/>')
    if seg.tie_start:
        bits.append('        <tie type="start"/>')
    bits.append(f"        <voice>{e.voice}</voice>")
    bits.append(f"        <type>{typ}</type>")
    bits.extend("        <dot/>" for _ in range(dots))
    if ratio:
        bits.append("        <time-modification>")
        bits.append(f"          <actual-notes>{ratio[0]}</actual-notes>")
        bits.append(f"          <normal-notes>{ratio[1]}</normal-notes>")
        bits.append("        </time-modification>")
    bits.append(f"        <staff>{e.staff}</staff>")
    if seg.tie_stop or seg.tie_start:
        bits.append("        <notations>")
        if seg.tie_stop:
            bits.append('          <tied type="stop"/>')
        if seg.tie_start:
            bits.append('          <tied type="start"/>')
        bits.append("        </notations>")
    bits.append("      </note>")
    return "\n".join(bits), snapped


def _rest_xml(ticks: int, voice: int, staff: int, divisions: int, ppq: int) -> str:
    """A gap, written as however many rests it actually takes."""
    out = []
    for chunk in _decompose(ticks, ppq):
        quarters = chunk / ppq
        typ, dots, _ratio, _s = _shape(quarters)
        dur = max(1, int(round(quarters * divisions)))
        bits = ["      <note>", "        <rest/>",
                f"        <duration>{dur}</duration>",
                f"        <voice>{voice}</voice>",
                f"        <type>{typ}</type>"]
        bits.extend("        <dot/>" for _ in range(dots))
        bits.append(f"        <staff>{staff}</staff>")
        bits.append("      </note>")
        out.append("\n".join(bits))
    return "\n".join(out)


def convert(data: bytes, title: str = "") -> tuple[bytes, dict[str, Any]]:
    """MIDI bytes to MusicXML bytes, plus a report on what had to be approximated."""
    doc = _read(data)
    _assign_voices(doc.events)

    divisions = doc.ppq
    key = music.KEYS[max(0, min(14, doc.fifths + 7))]
    _spell_chromatics(doc, key)
    bar = int(round(doc.beats * (4 / doc.beat_type) * doc.ppq))
    if bar <= 0:
        bar = 4 * doc.ppq

    last = max(e.tick + e.dur for e in doc.events)
    n_bars = max(1, -(-last // bar))
    segs = _segments(doc, bar)
    snapped = 0

    body: list[str] = []
    for m in range(n_bars):
        start, end = m * bar, (m + 1) * bar
        here = [g for g in segs if start <= g.tick < end]
        body.append(f'    <measure number="{m + 1}">')
        if m == 0:
            body.append("\n".join((
                "      <attributes>",
                f"        <divisions>{divisions}</divisions>",
                f"        <key><fifths>{doc.fifths}</fifths></key>",
                f"        <time><beats>{doc.beats}</beats>"
                f"<beat-type>{doc.beat_type}</beat-type></time>",
                "        <staves>2</staves>",
                '        <clef number="1"><sign>G</sign><line>2</line></clef>',
                '        <clef number="2"><sign>F</sign><line>4</line></clef>',
                "      </attributes>")))

        wrote_any = False
        for staff in (1, 2):
            voices = sorted({g.src.voice for g in here if g.src.staff == staff})
            for voice in voices:
                rows = [g for g in here
                        if g.src.staff == staff and g.src.voice == voice]
                if not rows:
                    continue
                if wrote_any:
                    body.append(f"      <backup><duration>{bar}</duration></backup>")
                cursor = start
                for g in rows:
                    if g.src.chord:
                        xml, sn = _note_xml(g, key, divisions, doc.ppq)
                        snapped += sn
                        body.append(xml)
                        continue
                    if g.tick > cursor:
                        body.append(_rest_xml(g.tick - cursor, voice, staff,
                                              divisions, doc.ppq))
                        cursor = g.tick
                    xml, sn = _note_xml(g, key, divisions, doc.ppq)
                    snapped += sn
                    body.append(xml)
                    cursor = g.tick + g.dur
                if cursor < end:
                    body.append(_rest_xml(end - cursor, voice, staff, divisions, doc.ppq))
                wrote_any = True
        if not wrote_any:
            body.append(_rest_xml(bar, 1, 1, divisions, doc.ppq))
        body.append("    </measure>")

    shown = escape(title or doc.name or "Untitled")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
        ' "http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="4.0">\n'
        "  <work>\n"
        f"    <work-title>{shown}</work-title>\n"
        "  </work>\n"
        "  <identification>\n"
        "    <encoding>\n"
        "      <software>Keys midi_import</software>\n"
        "    </encoding>\n"
        "  </identification>\n"
        "  <part-list>\n"
        '    <score-part id="P1">\n'
        "      <part-name>Piano</part-name>\n"
        "    </score-part>\n"
        "  </part-list>\n"
        '  <part id="P1">\n'
        + "\n".join(body) + "\n"
        "  </part>\n"
        "</score-partwise>\n"
    )
    report = {
        "notes": len(doc.events),
        "measures": n_bars,
        "fifths": doc.fifths,
        "time": f"{doc.beats}/{doc.beat_type}",
        "tempo": round(doc.tempo, 1),
        "ppq": doc.ppq,
        "snapped": snapped,
        "title": title or doc.name or "",
    }
    return xml.encode("utf-8"), report
