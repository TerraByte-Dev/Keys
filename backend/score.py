"""MusicXML in, a note timeline out.

MusicXML is the interchange format for sheet music -- Finale, Sibelius, MuseScore,
Dorico and Notion all read and write it. Two flavours: ``.musicxml`` is the XML, and
``.mxl`` is a zip whose ``META-INF/container.xml`` names the score inside it. Both are
handled here, with the standard library and no new dependency.

**MIDI is not sheet music and cannot be.** A MIDI file has no enharmonic spelling, no
voices, no beaming and no layout -- it cannot tell E flat from D sharp, which is the
one distinction this app is careful about everywhere else. MIDI is fine for playing
along to; it can never be the score.

The four things that make a naive MusicXML reader wrong, all of them handled below:

* **``<backup>`` and ``<forward>``.** A piano score is one part with two staves, and
  the writer emits the right hand, backs the cursor up to the start of the measure,
  then emits the left. A reader that only ever moves forward stacks the left hand
  after the right and produces nonsense.
* **``<chord/>``.** Marks a note as starting *with the previous one* rather than after
  it. Advance the cursor on a chord tone and every chord becomes an arpeggio.
* **Ties.** Two notated notes, one sounding note. Held across a barline they must
  merge, or the grader asks you to strike a key you are already holding.
* **``<divisions>``.** Duration is in divisions per quarter note, it is declared per
  part, and it can change mid-part. Everything here is normalised to quarter notes on
  the way in so nothing downstream has to know.

Grace notes carry no duration and are dropped -- they are ornament, and a grader that
demands them is grading a performance decision. Repeats and voltas are reported but
not expanded: playing the written order is a correct reading of the score, and
silently duplicating bars would make the bar numbers lie.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Semitones above C for each letter, and the sharp/flat spelling tables. Deliberately
# not imported from music.py: that module is on the 60 Hz hot path and promises pure
# theory, and a file parser has no business adding an import edge to it.
_STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ALTER_STR = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

MAX_NOTES = 200_000        # a Mahler symphony is ~60k; past this it is not sheet music


class ScoreError(ValueError):
    """The file is not a score we can read. The message is shown to the user."""


@dataclass
class Note:
    onset: float            # quarter notes from the start of the piece
    duration: float         # quarter notes, ties already merged
    midi: int
    name: str               # spelled as written: "Eb4", never "D#4"
    voice: int
    staff: int              # 1 is the upper staff, usually the right hand
    measure: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "onset": round(self.onset, 6), "duration": round(self.duration, 6),
            "midi": self.midi, "name": self.name, "voice": self.voice,
            "staff": self.staff, "measure": self.measure,
        }


@dataclass
class Measure:
    number: int
    onset: float
    beats: int
    beat_type: int
    fifths: int             # -7..7, negative is flats

    def to_dict(self) -> dict[str, Any]:
        return {"number": self.number, "onset": round(self.onset, 6),
                "beats": self.beats, "beat_type": self.beat_type, "fifths": self.fifths}


@dataclass
class Score:
    title: str = ""
    composer: str = ""
    parts: list[str] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    tempo: float = 0.0                       # quarter notes per minute, 0 if unmarked
    warnings: list[str] = field(default_factory=list)

    @property
    def quarters(self) -> float:
        return max((n.onset + n.duration for n in self.notes), default=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "composer": self.composer, "parts": self.parts,
            "tempo": self.tempo, "quarters": round(self.quarters, 6),
            "measures": [m.to_dict() for m in self.measures],
            "notes": [n.to_dict() for n in self.notes],
            "warnings": self.warnings,
        }


# --- reading the container ----------------------------------------------------
def _root_from_bytes(data: bytes, name: str = "") -> ET.Element:
    """The <score-partwise> element, whether the bytes are XML or an .mxl zip."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
                # The container names the score. Guessing "score.xml" works often enough
                # to be dangerous and fails on every file MuseScore writes.
                path = ""
                if "META-INF/container.xml" in z.namelist():
                    container = ET.fromstring(z.read("META-INF/container.xml"))
                    node = container.find(".//rootfile")
                    path = (node.get("full-path") or "") if node is not None else ""
                if not path:
                    candidates = [n for n in z.namelist()
                                  if n.lower().endswith((".xml", ".musicxml"))
                                  and not n.startswith("META-INF")]
                    if not candidates:
                        raise ScoreError(f"{name or 'that .mxl'} has no score inside it")
                    path = candidates[0]
                data = z.read(path)
        except zipfile.BadZipFile as exc:
            raise ScoreError(f"{name or 'that file'} is not a readable .mxl: {exc}") from None

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ScoreError(f"{name or 'that file'} is not valid XML: {exc}") from None

    if root.tag == "score-timewise":
        raise ScoreError(
            "that is a score-timewise file. Almost nothing writes them; open it in "
            "MuseScore and export again to get the partwise form.")
    if root.tag != "score-partwise":
        raise ScoreError(f"expected a MusicXML score, found <{root.tag}>")
    return root


def _text(node: ET.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    found = node.findtext(path)
    return default if found is None else found.strip()


def _num(node: ET.Element | None, path: str, default: float = 0.0) -> float:
    raw = _text(node, path)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# --- the parse ----------------------------------------------------------------
def parse(data: bytes, name: str = "") -> Score:
    root = _root_from_bytes(data, name)
    score = Score()

    score.title = (_text(root, "work/work-title")
                   or _text(root, "movement-title")
                   or (Path(name).stem if name else ""))
    for creator in root.findall("identification/creator"):
        if (creator.get("type") or "").lower() in ("composer", "") and creator.text:
            score.composer = creator.text.strip()
            break

    names = {p.get("id"): _text(p, "part-name") for p in root.findall("part-list/score-part")}
    seen_measures: dict[int, Measure] = {}
    notes: list[Note] = []

    for part in root.findall("part"):
        pid = part.get("id") or ""
        score.parts.append(names.get(pid) or pid or "Part")
        notes.extend(_parse_part(part, seen_measures, score))
        if len(notes) > MAX_NOTES:
            raise ScoreError(f"that score has over {MAX_NOTES:,} notes, which is not sheet music")

    notes.sort(key=lambda n: (n.onset, n.staff, n.midi))
    score.notes = notes
    score.measures = [seen_measures[k] for k in sorted(seen_measures)]
    if not notes:
        score.warnings.append("no notes found -- is this an empty score?")
    return score


def _parse_part(part: ET.Element, measures: dict[int, Measure], score: Score) -> list[Note]:
    out: list[Note] = []
    divisions = 1.0
    beats, beat_type, fifths = 4, 4, 0
    part_onset = 0.0                       # quarter notes elapsed before this measure
    # Open ties, keyed the way MusicXML actually identifies a continuation: same pitch,
    # same voice, same staff. Keyed by pitch alone, a tie in one hand would swallow the
    # same note in the other.
    open_ties: dict[tuple[int, int, int], Note] = {}

    for m_index, measure in enumerate(part.findall("measure"), start=1):
        try:
            number = int(measure.get("number") or m_index)
        except ValueError:
            number = m_index

        cursor = part_onset
        measure_end = part_onset

        for node in measure:
            tag = node.tag
            if tag == "attributes":
                divisions = _num(node, "divisions", divisions) or divisions
                if node.find("time") is not None:
                    beats = int(_num(node, "time/beats", beats))
                    beat_type = int(_num(node, "time/beat-type", beat_type))
                if node.find("key") is not None:
                    fifths = int(_num(node, "key/fifths", fifths))
                continue

            if tag == "direction":
                sound = node.find("sound")
                if sound is not None and sound.get("tempo"):
                    try:
                        score.tempo = score.tempo or float(sound.get("tempo"))
                    except ValueError:
                        pass
                continue

            if tag == "sound" and node.get("tempo") and not score.tempo:
                try:
                    score.tempo = float(node.get("tempo"))
                except ValueError:
                    pass
                continue

            if tag == "backup":
                cursor -= _num(node, "duration") / divisions
                continue
            if tag == "forward":
                cursor += _num(node, "duration") / divisions
                measure_end = max(measure_end, cursor)
                continue
            if tag == "barline":
                # findtext cannot select attributes; the element's presence is the test.
                if node.find("repeat") is not None:
                    if "repeats" not in " ".join(score.warnings):
                        score.warnings.append(
                            "this score has repeats; they are shown where they are written "
                            "and not played out")
                continue
            if tag != "note":
                continue

            # --- a note ---------------------------------------------------------
            if node.find("grace") is not None:
                continue                    # ornament, no duration, not graded
            dur = _num(node, "duration") / divisions
            is_chord = node.find("chord") is not None
            start = cursor if not is_chord else _chord_start(out, cursor)

            if node.find("rest") is not None:
                if not is_chord:
                    cursor += dur
                    measure_end = max(measure_end, cursor)
                continue

            pitch = node.find("pitch")
            if pitch is None:
                if not is_chord:
                    cursor += dur
                    measure_end = max(measure_end, cursor)
                continue

            step = (pitch.findtext("step") or "C").strip().upper()
            octave = int(_num(pitch, "octave", 4))
            alter = int(_num(pitch, "alter", 0))
            midi = (octave + 1) * 12 + _STEP_PC.get(step, 0) + alter
            voice = int(_num(node, "voice", 1))
            staff = int(_num(node, "staff", 1))
            key = (midi, voice, staff)

            ties = {t.get("type") for t in node.findall("tie")}
            ties |= {t.get("type") for t in node.findall("notations/tied")}

            if "stop" in ties and key in open_ties:
                # One sounding note, however many notated ones. Extending rather than
                # appending is what keeps the grader from asking you to re-strike a key
                # you are still holding.
                open_ties[key].duration += dur
                if "start" not in ties:
                    open_ties.pop(key, None)
            else:
                note = Note(
                    onset=start, duration=dur, midi=max(0, min(127, midi)),
                    name=f"{step}{_ALTER_STR.get(alter, '')}{octave}",
                    voice=voice, staff=staff, measure=number,
                )
                out.append(note)
                if "start" in ties:
                    open_ties[key] = note

            if not is_chord:
                cursor += dur
                measure_end = max(measure_end, cursor)

        if number not in measures:
            measures[number] = Measure(number=number, onset=part_onset, beats=beats,
                                       beat_type=beat_type, fifths=fifths)
        # A measure's length is what was actually written in it, not what the time
        # signature claims -- a pickup bar is short and a cadenza is long, and both are
        # correct. Only a measure with nothing in it falls back to the nominal length.
        if measure_end > part_onset:
            part_onset = measure_end
        else:
            part_onset += beats * 4.0 / beat_type

    return out


def _chord_start(out: list[Note], cursor: float) -> float:
    """Chord tones share the previous note's onset, not the cursor."""
    return out[-1].onset if out else cursor


def load(path: Path) -> Score:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScoreError(f"could not read {path.name}: {exc}") from None
    return parse(data, path.name)


def summarise(score: Score) -> dict[str, Any]:
    """The bits worth showing in a library list, without shipping every note."""
    staves = sorted({n.staff for n in score.notes})
    return {
        "title": score.title, "composer": score.composer,
        "parts": score.parts, "staves": staves,
        "measures": len(score.measures), "notes": len(score.notes),
        "quarters": round(score.quarters, 3), "tempo": score.tempo,
        "warnings": score.warnings,
    }
