"""Regression test for the MusicXML reader.

    .venv\\Scripts\\python.exe tools\\score_check.py

No audio, no browser, no network. Every fixture is written here rather than downloaded,
which is deliberate on two counts: the music this app is likely to be pointed at is in
copyright and cannot live in a repository, and a hand-built fixture is the only way to
know what the right answer is before the parser gives you one.

The traps are all silent. A reader that ignores ``<backup>`` produces a piano score
with the left hand playing after the right instead of underneath it -- and it looks
like a valid score, just an unplayable one. A reader that advances the cursor on
``<chord/>`` turns every chord into an arpeggio. Neither raises anything.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.score import ScoreError, parse, summarise  # noqa: E402

ok = True


def step(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))


def xml(body: str, divisions: int = 2, fifths: int = 0, beats: int = 4) -> bytes:
    return (f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Fixture</work-title></work>
  <identification><creator type="composer">Nobody</creator></identification>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">{body}</part>
</score-partwise>""").encode()


def attrs(divisions: int = 2, fifths: int = 0, beats: int = 4, staves: int = 1) -> str:
    return (f"<attributes><divisions>{divisions}</divisions>"
            f"<key><fifths>{fifths}</fifths></key>"
            f"<time><beats>{beats}</beats><beat-type>4</beat-type></time>"
            f"<staves>{staves}</staves></attributes>")


def note(stepname: str, octave: int, dur: int, alter: int = 0, chord: bool = False,
         voice: int = 1, staff: int = 1, tie: str = "") -> str:
    a = f"<alter>{alter}</alter>" if alter else ""
    c = "<chord/>" if chord else ""
    t = f'<tie type="{tie}"/><notations><tied type="{tie}"/></notations>' if tie else ""
    return (f"<note>{c}<pitch><step>{stepname}</step>{a}<octave>{octave}</octave></pitch>"
            f"<duration>{dur}</duration><voice>{voice}</voice><staff>{staff}</staff>{t}</note>")


def rest(dur: int, voice: int = 1, staff: int = 1) -> str:
    return (f"<note><rest/><duration>{dur}</duration>"
            f"<voice>{voice}</voice><staff>{staff}</staff></note>")


print("1. a note is a pitch, a spelling and a place in time")
s = parse(xml(f'<measure number="1">{attrs(fifths=-3)}'
              + note("E", 4, 2, alter=-1) + rest(2) + note("G", 4, 4) + "</measure>"))
step("title and composer read", s.title == "Fixture" and s.composer == "Nobody")
step("two notes, the rest is not one", len(s.notes) == 2, str(len(s.notes)))
n0, n1 = s.notes
step("spelled as written, not as MIDI", n0.name == "Eb4" and n0.midi == 63,
     f"{n0.name} = {n0.midi} -- a MIDI file could not tell this from D#4")
step("durations are quarter notes", [n0.duration, n1.duration] == [1.0, 2.0],
     "divisions=2 means a duration of 2 is one quarter")
step("the rest moves time on", n1.onset == 2.0, f"onset {n1.onset}")
step("key signature captured", s.measures[0].fifths == -3)
step("time signature captured",
     (s.measures[0].beats, s.measures[0].beat_type) == (4, 4))

print("2. <chord/> means together, not after")
s = parse(xml(f'<measure number="1">{attrs()}'
              + note("C", 4, 2) + note("E", 4, 2, chord=True) + note("G", 4, 2, chord=True)
              + note("D", 4, 2) + "</measure>"))
onsets = [n.onset for n in s.notes]
step("three notes share an onset", onsets.count(0.0) == 3, str(onsets))
step("the note after the chord is one quarter in, not three",
     max(onsets) == 1.0, "advancing on a chord tone turns every chord into an arpeggio")
step("all four notes kept", len(s.notes) == 4)

print("3. <backup> -- the left hand plays UNDER the right, not after it")
body = (f'<measure number="1">{attrs(staves=2)}'
        + note("C", 5, 2, staff=1) + note("D", 5, 2, staff=1)
        + note("E", 5, 2, staff=1) + note("F", 5, 2, staff=1)
        + "<backup><duration>8</duration></backup>"
        + note("C", 3, 8, voice=2, staff=2)
        + "</measure>")
s = parse(xml(body))
left = [n for n in s.notes if n.staff == 2]
right = [n for n in s.notes if n.staff == 1]
step("both hands present", len(right) == 4 and len(left) == 1)
step("the left hand starts at zero, not after the right",
     left[0].onset == 0.0,
     "ignoring <backup> stacks the hands in sequence and looks like a valid score")
step("and lasts the whole bar", left[0].duration == 4.0)
step("the piece is one bar long, not two", abs(s.quarters - 4.0) < 1e-9, str(s.quarters))

print("4. ties are one sounding note")
body = (f'<measure number="1">{attrs()}' + note("G", 4, 6) + note("A", 4, 2, tie="start")
        + '</measure><measure number="2">' + note("A", 4, 4, tie="stop")
        + note("B", 4, 4) + "</measure>")
s = parse(xml(body))
a4 = [n for n in s.notes if n.name == "A4"]
step("the tied pair is ONE note", len(a4) == 1,
     "two notes here asks you to re-strike a key you are still holding")
step("its length is the sum", a4[0].duration == 3.0, f"{a4[0].duration} quarters")
step("it starts where the first one did", a4[0].onset == 3.0)
step("the note after it is not swallowed", any(n.name == "B4" for n in s.notes))

print("5. divisions are per part and may change mid-part")
body = (f'<measure number="1">{attrs(divisions=1)}' + note("C", 4, 1) + note("D", 4, 1)
        + '</measure><measure number="2"><attributes><divisions>8</divisions></attributes>'
        + note("E", 4, 8) + note("F", 4, 8) + "</measure>")
s = parse(xml(body))
step("both measures are quarter notes", [n.duration for n in s.notes] == [1.0] * 4,
     str([n.duration for n in s.notes]))
step("onsets keep running across the change",
     [n.onset for n in s.notes] == [0.0, 1.0, 2.0, 3.0],
     "a fixed divisions assumption puts measure 2 in the wrong place")

print("6. grace notes are ornament, not grading material")
body = (f'<measure number="1">{attrs()}'
        + '<note><grace/><pitch><step>B</step><octave>3</octave></pitch>'
        + "<voice>1</voice></note>" + note("C", 4, 4) + "</measure>")
s = parse(xml(body))
step("the grace note is dropped", len(s.notes) == 1 and s.notes[0].name == "C4")
step("it did not steal any time", s.notes[0].onset == 0.0)

print("7. a pickup bar is short, and that is correct")
body = (f'<measure number="0" implicit="yes">{attrs()}' + note("G", 4, 2)
        + '</measure><measure number="1">' + note("C", 5, 8) + "</measure>")
s = parse(xml(body))
step("the downbeat lands one quarter in, not four",
     s.notes[1].onset == 1.0,
     "padding a pickup to a full bar puts the whole piece on the wrong beat")

print("8. containers and rubbish")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("META-INF/container.xml",
               '<container><rootfiles><rootfile full-path="Score.musicxml"/>'
               "</rootfiles></container>")
    z.writestr("Score.musicxml", xml(f'<measure number="1">{attrs()}'
                                     + note("C", 4, 4) + "</measure>").decode())
s = parse(buf.getvalue(), "test.mxl")
step(".mxl is read through its container", len(s.notes) == 1,
     "the score is not always called score.xml -- MuseScore never calls it that")

buf2 = io.BytesIO()
with zipfile.ZipFile(buf2, "w") as z:
    z.writestr("whatever.xml", xml(f'<measure number="1">{attrs()}'
                                   + note("D", 4, 4) + "</measure>").decode())
step("a container-less zip still works", len(parse(buf2.getvalue()).notes) == 1)

for data, why in [
    (b"not xml at all", "prose"),
    (b"<html><body>hi</body></html>", "a web page"),
    (b"PK\x03\x04garbage", "a broken zip"),
    (b'<?xml version="1.0"?><score-timewise/>', "a timewise score"),
]:
    try:
        parse(data, "x")
        step(f"rejects {why}", False, "it parsed something it should not have")
    except ScoreError as exc:
        step(f"rejects {why}", True, str(exc)[:56])

print("9. an empty score is empty, not an error")
s = parse(xml(f'<measure number="1">{attrs()}' + rest(8) + "</measure>"))
step("no notes", s.notes == [])
step("and it says so", any("no notes" in w for w in s.warnings), str(s.warnings))

print("10. repeats are reported, not silently played out")
body = (f'<measure number="1">{attrs()}' + note("C", 4, 8)
        + '<barline location="right"><repeat direction="backward"/></barline></measure>')
s = parse(xml(body))
step("one note, not two", len(s.notes) == 1,
     "expanding repeats would make the bar numbers lie")
step("the reader says it saw one", any("repeat" in w for w in s.warnings), str(s.warnings))

print("11. the summary a library list would show")
body = (f'<measure number="1">{attrs(staves=2)}' + note("C", 5, 4, staff=1)
        + "<backup><duration>4</duration></backup>" + note("C", 3, 4, voice=2, staff=2)
        + '<direction><sound tempo="96"/></direction></measure>')
s = parse(xml(body))
info = summarise(s)
step("both staves listed", info["staves"] == [1, 2], str(info["staves"]))
step("tempo read from <sound>", info["tempo"] == 96.0, str(info["tempo"]))
step("counts are counts", info["notes"] == 2 and info["measures"] == 1)

print("12. the library keeps your file, not its idea of your file")
import tempfile  # noqa: E402
from backend import config  # noqa: E402
config.DATA_DIR = Path(tempfile.mkdtemp(prefix="keys-scores-"))
from backend.scores import Library  # noqa: E402

lib = Library()
step("starts empty", lib.all() == [])
raw = xml(f'<measure number="1">{attrs()}' + note("C", 4, 4) + note("E", 4, 4) + "</measure>")
meta = lib.add("My Piece.musicxml", raw)
step("imports", meta is not None and meta["notes"] == 2, str(lib.last_error))
step("title comes from the file, not the filename", meta["title"] == "Fixture")
step("listed", len(lib.all()) == 1)

stored = lib.data(meta["id"])
step("THE BYTES ARE UNTOUCHED", stored == raw,
     "Verovio renders the original; re-serialising it loses whatever made your copy yours")

# A .mid used to be refused outright. It is converted now, so what has to hold is
# that a BROKEN one is still refused, with a sentence rather than a stack trace.
step("rejects a .mid that is not a MIDI file", lib.add("song.mid", b"MThd") is None
     and "MIDI" in lib.last_error, lib.last_error[:60])
step("rejects a .wav outright", lib.add("song.wav", b"RIFF") is None
     and "not a score Keys can read" in lib.last_error, lib.last_error[:60])
step("rejects an empty file", lib.add("x.musicxml", b"") is None)
step("rejects rubbish before storing it, not after",
     lib.add("bad.musicxml", b"<html/>") is None and len(lib.all()) == 1,
     "a file that cannot be read must never enter the library")

lib.rename(meta["id"], "Something Else")
step("renamed", lib.get(meta["id"])["title"] == "Something Else")
step("timeline still parses after a rename",
     len(lib.parsed(meta["id"]).notes) == 2)

step("removed", lib.remove(meta["id"]) and lib.all() == [])
step("removing twice is harmless", lib.remove(meta["id"]) is False)
step("the files went with it",
     not list((config.DATA_DIR / "scores").glob("*")),
     "a sidecar left behind would haunt the list forever")

print()
print("ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
