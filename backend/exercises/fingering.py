"""Which finger plays which note. Tables, not theory.

This lives here rather than in ``backend/music.py`` on purpose, and the split is not
filing tidiness:

* ``music.py`` runs inside the 60 Hz drain loop and promises **pure theory**. Every
  answer it gives is *derived* -- a pitch class's spelling falls out of its distance
  along the line of fifths, a chord's name falls out of a set-cover search. Nothing
  in it is a matter of opinion, and nothing in it needs a citation.
* Fingering is a **convention**. No rule of harmony puts the right thumb on F in
  C major and on C in F major; a few centuries of hands did that, editions still
  disagree in the corners, and the only honest way to hold it is a table with its
  sources named. It is not derivable from the line of fifths and never will be.

Putting a table nobody can derive inside the module whose whole claim is that
everything in it *is* derived would quietly break that claim. So: no imports, no
pitch arithmetic, no theory in here at all. ``tools/exercise_check.py`` is what ties
the two together, and it is where the tables are validated against real pitches.

How a row is shaped
-------------------
A row is ``(right_hand, left_hand)``, each 8 digits covering one octave ascending
from the tonic, finger 1 = thumb. Digit 0 is the tonic, digit 7 is the note an
octave above it -- and those two are *not* interchangeable, which is the whole
reason the row is 8 long rather than 7:

* **RH**: digits 0..6 are the repeating cycle. Digit 7 is the terminal finger, used
  only on the very last note of a run. C major recurs 1-2-3-1-2-3-4 and *finishes*
  on 5; two octaves is ``1 2 3 1 2 3 4 | 1 2 3 1 2 3 4 | 5``.
* **LH**: mirrored. Digit 0 is the terminal finger at the *bottom* of the run and
  digits 1..7 are the repeating cycle. Two octaves of C is
  ``5 | 4 3 2 1 3 2 1 | 4 3 2 1 3 2 1``.

In both hands the terminal finger simply extends the last group by one: RH digit 7
is always digit 6 plus one, LH digit 0 is always digit 1 plus one. That invariant is
checked, and it is what catches a fat-fingered row.

Some published charts print a *smaller* first digit in the right hand for a
single-octave rendition -- pianoscales.org gives B flat minor as 2-1-2-3-1-2-3-4 and
G sharp harmonic minor as 2-3-1-2-3-1-2-3, where the recurring finger on the tonic
is 4 and 3 respectively. Those starts are unplayable as a repeating pattern (the
octave note would need two different fingers at once), and Keys generates
multi-octave runs, so this table stores the recurring finger. The same charts print
B flat *major* as 4-1-2-3-1-2-3-4, which is the convention being followed here.
"""

from __future__ import annotations

from typing import Sequence

# --- what the minors actually needed -----------------------------------------
# The widely repeated claim that a minor scale borrows its relative or parallel
# major's fingering is false, and cheaply disproved: B minor and D major are the
# same seven notes, but D major's left hand (5-4-3-2-1-3-2-1) puts the thumb on
# F sharp, which is black. B minor's left hand is 4-3-2-1-4-3-2-1. Three more rows
# differ from their relative major for the same reason (B flat, E flat, A flat
# minor), and one form of one key differs from the *other forms of itself*:
#
#   A flat natural minor  LH 3-2-1-3-2-1-4-3   thumbs on Cb and Fb
#   A flat harmonic minor LH 3-2-1-4-3-2-1-3   thumbs on Cb and G natural
#
# because raising the 7th turns Gb (black) into G (white) and moves where the thumb
# can go -- and because the augmented 2nd wants to sit under 1-2 or 2-3, which the
# natural minor's fingering would spread across 1-4.
#
# Only the 6th and 7th degrees change between the three minor forms, so a form only
# needs its own row when a thumb sits on degree 6 or 7. That is exactly four places:
# A flat (LH, natural vs harmonic, above) and the right hands of C sharp and F sharp
# melodic minor, whose raised 6th (A sharp / D sharp) is black and cannot take a
# thumb. Every other row is shared by all three forms because nothing under a thumb
# moved.
#
# Melodic minor descends as the natural minor, and nobody re-fingers at the top of
# the scale, so a melodic row's thumbs are on white keys in *both* forms. That is
# what decides C sharp and F sharp melodic minor: moving the upper thumb from
# degree 6 to degree 7 (B sharp / E sharp, both white; B / E on the way down, also
# white) keeps the lower thumb where the natural minor already put it, so only one
# thumb moves between going up and coming down.
#
# SOURCES (fetched 2026-07-26, and cross-checked against each other and against the
# two structural rules below):
#
#   [1] pianoscales.org -- natural minor <https://www.pianoscales.org/minor.html>
#       and harmonic minor <https://www.pianoscales.org/minor-harmonic.html>.
#       Complete 12-key tables for both hands. The primary source for the minors:
#       every one of its rows puts every thumb on a white key. Its right-hand
#       *first* digits are single-octave starts (see the module docstring) and are
#       normalised here; nothing else was changed.
#   [2] piano.org -- <https://piano.org/theory/piano-fingering/>. Used for the
#       majors, where it agrees with [1] and with the verified list in this file.
#       Its natural-minor table is NOT trustworthy and is a good illustration of
#       the mistake this comment block exists to avoid: it hands C sharp minor,
#       E flat minor, G sharp minor and B flat minor their parallel major's left
#       hand, which lands the thumb on a black key in all four.
#   [3] pianolessons.com -- F sharp melodic minor
#       <https://pianolessons.com/piano-lessons/f-sharp-minor-melodic-scale.php>
#       and C sharp melodic minor
#       <https://pianolessons.com/piano-lessons/c-sharp-minor-melodic-scale.php>.
#       Prose rather than a table, and internally inconsistent about the final
#       digit, but it independently places the right thumb on A and E sharp in
#       F sharp melodic minor -- the choice adopted here.
#   [4] The two structural rules, stated in the commentary on [1] and in the
#       Do Re Mi Studios harmonic-minor notes
#       <https://doremistudios.com.au/piano-harmonic-minor-scales/>:
#       the thumb never plays a black key, and the augmented 2nd of a harmonic
#       minor should fall under 1-2 or 2-3 (the noted exception being the left
#       hand of E flat / D sharp minor, which this table reproduces: Cb takes 1
#       and D takes 3).
#
# WHERE THE SOURCES GENUINELY DISAGREE:
#
#   F sharp natural/harmonic minor, RH. [1] puts the thumbs on A and D; [2] puts
#   them on B and E (Gb major's fingering). Both obey the two rules. Taken: A and
#   D, i.e. 3-4-1-2-3-1-2-3, because that is the same shape C sharp minor and
#   A flat minor use, and A flat minor has no choice about it -- Cb and Fb are the
#   only white keys in the scale. One shape for all three beats three shapes.
#
#   F sharp melodic minor, RH. [3]'s prose says thumbs on A and E sharp; the same
#   page's summary contradicts itself and lists a thumb on D sharp, which is black.
#   Taken: A and E sharp, 2-3-1-2-3-4-1-2, which is the only reading that obeys
#   the thumb rule and the only one that keeps the lower thumb where the natural
#   minor put it.

# Canonical rows, keyed by the flat spelling of a black-key tonic. Enharmonic
# spellings are added by _expand() below -- they are the same physical keys and
# therefore the same fingering, so they must never drift apart in the source.
_TABLE: dict[str, dict[str, tuple[tuple[int, ...], tuple[int, ...]]]] = {
    "C": {
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "Db": {  # C# minor is the practical spelling of the minor; same keys, same row.
        "major":          ((2, 3, 1, 2, 3, 4, 1, 2), (3, 2, 1, 4, 3, 2, 1, 3)),
        "natural_minor":  ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 4, 3, 2, 1, 3)),
        "harmonic_minor": ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 4, 3, 2, 1, 3)),
        # Raised 6th is A#, black: the RH thumb moves from degree 6 to degree 7.
        "melodic_minor":  ((2, 3, 1, 2, 3, 4, 1, 2), (3, 2, 1, 4, 3, 2, 1, 3)),
    },
    "D": {
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "Eb": {
        "major":          ((3, 1, 2, 3, 4, 1, 2, 3), (3, 2, 1, 4, 3, 2, 1, 3)),
        # LH differs from Eb major: C and G are the major's thumbs, and in the minor
        # they become Cb (white, fine) and Gb (black, impossible).
        "natural_minor":  ((3, 1, 2, 3, 4, 1, 2, 3), (2, 1, 4, 3, 2, 1, 3, 2)),
        "harmonic_minor": ((3, 1, 2, 3, 4, 1, 2, 3), (2, 1, 4, 3, 2, 1, 3, 2)),
        "melodic_minor":  ((3, 1, 2, 3, 4, 1, 2, 3), (2, 1, 4, 3, 2, 1, 3, 2)),
    },
    "E": {
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "F": {
        "major":          ((1, 2, 3, 4, 1, 2, 3, 4), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 4, 1, 2, 3, 4), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 4, 1, 2, 3, 4), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 4, 1, 2, 3, 4), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "Gb": {  # F# minor is the practical spelling of the minor; same keys, same row.
        "major":          ((2, 3, 4, 1, 2, 3, 1, 2), (4, 3, 2, 1, 3, 2, 1, 4)),
        "natural_minor":  ((3, 4, 1, 2, 3, 1, 2, 3), (4, 3, 2, 1, 3, 2, 1, 4)),
        "harmonic_minor": ((3, 4, 1, 2, 3, 1, 2, 3), (4, 3, 2, 1, 3, 2, 1, 4)),
        # Raised 6th is D#, black: the RH thumb moves from degree 6 to degree 7.
        "melodic_minor":  ((2, 3, 1, 2, 3, 4, 1, 2), (4, 3, 2, 1, 3, 2, 1, 4)),
    },
    "G": {
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "Ab": {  # G# minor is the practical spelling of the minor; same keys, same row.
        "major":          ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 4, 3, 2, 1, 3)),
        "natural_minor":  ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 3, 2, 1, 4, 3)),
        # The one LH that changes with the form: the raised 7th (G natural) is white
        # and takes the thumb, which puts the augmented 2nd Fb-G under 2-1.
        "harmonic_minor": ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 4, 3, 2, 1, 3)),
        # Melodic descends as the natural minor, where degree 7 is Gb and black, so
        # the harmonic LH cannot be used for it: the natural LH serves both ways.
        "melodic_minor":  ((3, 4, 1, 2, 3, 1, 2, 3), (3, 2, 1, 3, 2, 1, 4, 3)),
    },
    "A": {
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 3, 2, 1)),
    },
    "Bb": {
        "major":          ((4, 1, 2, 3, 1, 2, 3, 4), (3, 2, 1, 4, 3, 2, 1, 3)),
        # LH differs from Bb major: D and A are the major's thumbs and become Db
        # and Ab, both black. C and F are the only white keys left in the scale.
        "natural_minor":  ((4, 1, 2, 3, 1, 2, 3, 4), (2, 1, 3, 2, 1, 4, 3, 2)),
        "harmonic_minor": ((4, 1, 2, 3, 1, 2, 3, 4), (2, 1, 3, 2, 1, 4, 3, 2)),
        "melodic_minor":  ((4, 1, 2, 3, 1, 2, 3, 4), (2, 1, 3, 2, 1, 4, 3, 2)),
    },
    "B": {
        # The LH that disproves the relative-major claim: D major is 5-4-3-2-1-3-2-1
        # and would put this thumb on F#.
        "major":          ((1, 2, 3, 1, 2, 3, 4, 5), (4, 3, 2, 1, 4, 3, 2, 1)),
        "natural_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (4, 3, 2, 1, 4, 3, 2, 1)),
        "harmonic_minor": ((1, 2, 3, 1, 2, 3, 4, 5), (4, 3, 2, 1, 4, 3, 2, 1)),
        "melodic_minor":  ((1, 2, 3, 1, 2, 3, 4, 5), (4, 3, 2, 1, 4, 3, 2, 1)),
    },
}

# Both spellings of the two tonics that are routinely written either way. D#, G# and
# A# are absent for the same reason backend.music.KEYS omits them: they are not
# practical key names, and a caller asking for one is asking for a typo back.
_ENHARMONIC: dict[str, str] = {"Db": "C#", "Gb": "F#"}


def _expand() -> dict[tuple[str, str], tuple[tuple[int, ...], tuple[int, ...]]]:
    out: dict[tuple[str, str], tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for tonic, forms in _TABLE.items():
        for form, row in forms.items():
            out[(tonic, form)] = row
            alias = _ENHARMONIC.get(tonic)
            if alias is not None:
                out[(alias, form)] = row
    return out


SCALE_FINGERING: dict[tuple[str, str], tuple[tuple[int, ...], tuple[int, ...]]] = _expand()

ARPEGGIO_FINGERING: dict[tuple[str, str], tuple[tuple[int, ...], tuple[int, ...]]] = {}
"""Deferred, deliberately empty.

Arpeggio fingering is a far weaker convention than scale fingering: the thumb-on-a-
white-key rule that pins down nearly every scale row above says almost nothing about
a triad, editions differ on the black-key tonics, and the answer changes again for
every inversion. No cross-check to the standard the scale table was held to was
done, so nothing is shipped. ``fingers_for`` returns an empty tuple for any form
that is not in ``SCALE_FINGERING``, and callers already have to cope with that. A
wrong fingering drilled daily is worse than no fingering.
"""


def _canonical(tonic: str) -> str:
    """Normalise capitalisation the same way backend.music._key_name does."""
    t = (tonic or "").strip()
    return t[:1].upper() + t[1:].lower()


def fingers_for(tonic: str, form: str, hand: str, degrees: int) -> tuple[int, ...]:
    """Fingering for ``degrees`` notes ascending from the tonic.

    Returns ``()`` for an unknown (tonic, form), an unknown hand, or a run of no
    notes -- every caller has to cope with a missing fingering anyway, because
    arpeggios do not have one yet.

    The one-octave pattern wraps the way a hand does: it repeats every seven notes,
    so each octave's tonic is the start of the next group, and the terminal digit is
    spent only on the last note of the whole run. Ascending, that is the top for the
    right hand (5 in C major) and the bottom for the left (5 there too).

    A run that stops part-way through an octave gets the cycle's finger on its last
    note rather than the terminal one. A real hand would re-finger the tail of a
    six-note run; guessing how is not something a table can do honestly.
    """
    row = SCALE_FINGERING.get((_canonical(tonic), form))
    if row is None or degrees <= 0:
        return ()
    h = (hand or "").strip().upper()[:1]
    if h == "R":
        pattern = row[0]
        out = [pattern[i % 7] for i in range(degrees)]
        # The top note takes the terminal finger only when the run really ends on a
        # tonic; anywhere else the cycle already gave the right answer.
        if degrees > 1 and (degrees - 1) % 7 == 0:
            out[-1] = pattern[7]
        return tuple(out)
    if h == "L":
        pattern = row[1]
        # Digit 0 is the bottom terminal, digits 1..7 are the cycle.
        return tuple([pattern[0]]
                     + [pattern[((i - 1) % 7) + 1] for i in range(1, degrees)])
    # "B" (both) has no single answer -- a hands-together generator asks twice.
    return ()


def crossings(fingers: Sequence[int]) -> tuple[bool, ...]:
    """Flag the steps where the hand crosses itself.

    True at index i > 0 when the finger there is the thumb, or when the previous
    finger was the thumb and this one is not.

    Both halves are needed because one function serves both hands in both
    directions, and the difficult moment is not the same one:

    * right hand ascending, the thumb tucks *under* -- the hard step is the note the
      thumb plays, caught by the first clause;
    * left hand ascending, the hand passes *over* a planted thumb -- the hard step is
      the note after it, caught by the second.

    So a crossing is treated as the two-note gesture it actually is, and both the
    approach to the thumb and the departure from it get flagged. That is what
    ``metrics.crossing_cost_ms`` wants: it measures the inter-onset gap *into* every
    flagged step against the median gap everywhere else, so covering both notes
    catches the hesitation whichever side of the thumb it lands on, and a single
    octave already yields enough flagged steps for the metric to report at all.

    The known cost: a scale whose first note is the thumb has index 1 flagged, which
    is a departure from the starting thumb rather than a crossing. It adds one
    ordinary gap to a set compared by median, so it moves the number by very little
    -- and the alternative, special-casing the first note, would need this function
    to know which end of the run it was looking at.
    """
    return tuple(
        i > 0 and (f == 1 or (fingers[i - 1] == 1 and f != 1))
        for i, f in enumerate(fingers)
    )
