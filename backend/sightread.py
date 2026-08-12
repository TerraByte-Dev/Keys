"""Sight reading drill -- the one feature here with a clear learning payoff.

Deliberately narrow. It shows a measure of notes, waits for the right key, and records
how long that took. It does not teach rhythm, it does not sequence a curriculum, and it
does not try to be flowkey. What it does that a $29 app does not is weight the next
measure toward *your* worst notes, using your own history.

Two decisions worth stating:

* A wrong note does **not** advance the cursor. You have to find the right key. That is
  what makes the reaction time mean something, and it is how a teacher would sit there.
* Only the *first* attempt at a target is logged. Hunting for the note after a miss is
  already recorded as the miss; counting each wrong key separately would bury the signal.

Octave matters. F4 is not F5 and answering the wrong one is wrong.
"""

from __future__ import annotations

import random
import time
from typing import Any

from . import config, music
from .store import Store

MAX_LEAP = 12          # keep successive notes within an octave -- reading, not gymnastics
WEAK_BOOST = 3.0       # how hard the adaptive weighting pulls toward your worst notes
MIN_ATTEMPTS = 3       # a note needs this many attempts before it counts as "weak"


class SightReader:
    def __init__(self, store: Store, settings: config.Settings | None = None) -> None:
        self.store = store
        self.settings = settings or config.settings
        self.rng = random.Random()

        self.active = False
        self.exercise_id = 0
        self.notes: list[dict[str, Any]] = []
        self.index = 0
        self.results: list[dict[str, Any]] = []
        self._target_since: float | None = None
        self._attempted = False          # has the current target been answered once?
        self.session_id: int | None = None

        self.total = 0
        self.correct = 0

    # ---------------------------------------------------------------- config
    def cfg(self) -> dict[str, Any]:
        base = dict(config.DEFAULTS["sightread"])
        base.update(self.settings.get("sightread", default={}) or {})
        return base

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.settings.update({"sightread": patch})
        return self.cfg()

    # ------------------------------------------------------------ generation
    def _range_for_clef(self, clef: str, lo: int, hi: int) -> tuple[int, int]:
        """See music.reading_window -- this used to be a second copy of it that had
        already drifted from the one in exercises/reading.py, and raised IndexError on
        an empty pool where the other silently generated unplayable notes."""
        low_key, high_key = config.instrument_range(self.settings)
        return music.reading_window(clef, lo, hi, low_key, high_key)

    def _weights(self, candidates: list[int]) -> list[float]:
        cfg = self.cfg()
        if not cfg.get("adaptive", True):
            return [1.0] * len(candidates)
        weak = {
            row["note"]: row
            for row in self.store.weak_notes(limit=40)
            if row.get("attempts", 0) >= MIN_ATTEMPTS
        }
        out = []
        for note in candidates:
            row = weak.get(note)
            if row is None:
                out.append(1.0)
            else:
                # accuracy 1.0 -> weight 1.0, accuracy 0.0 -> weight 1 + WEAK_BOOST
                out.append(1.0 + WEAK_BOOST * (1.0 - float(row.get("accuracy", 1.0))))
        return out

    def new_exercise(self) -> dict[str, Any]:
        cfg = self.cfg()
        key = cfg.get("key", "C")
        clef = cfg.get("clef", "both")
        lo, hi = self._range_for_clef(clef, int(cfg.get("low", 55)), int(cfg.get("high", 79)))
        count = max(1, min(16, int(cfg.get("notes_per_measure", 4))))

        scale = set(music.scale_pitch_classes(key, "major"))
        candidates = [n for n in range(lo, hi + 1) if n % 12 in scale]
        if not candidates:
            candidates = list(range(lo, hi + 1))
        weights = self._weights(candidates)

        picked: list[int] = []
        for i in range(count):
            if i == 0:
                pool, pool_w = candidates, weights
            else:
                near = [
                    (n, w) for n, w in zip(candidates, weights)
                    if abs(n - picked[-1]) <= MAX_LEAP and n != picked[-1]
                ]
                pool, pool_w = ([p[0] for p in near], [p[1] for p in near]) if near else (candidates, weights)
            picked.append(self.rng.choices(pool, weights=pool_w, k=1)[0])

        self.exercise_id += 1
        self.notes = [
            {
                "midi": n,
                "name": music.note_name(n, key),
                # Middle C is the boundary a two-staff grand staff actually uses.
                "staff": "treble" if (clef == "treble" or (clef == "both" and n >= 60)) else "bass",
            }
            for n in picked
        ]
        self.index = 0
        self.results = []
        self._attempted = False
        self._target_since = time.perf_counter()
        self.active = True
        return self.state()

    # -------------------------------------------------------------- grading
    def on_note(self, midi: int, velocity: int, t: float) -> dict[str, Any] | None:
        """One note-on from the drain loop. Returns a feedback dict, or None if idle."""
        if not self.active or self.index >= len(self.notes):
            return None
        target = self.notes[self.index]["midi"]
        hit = midi == target
        reaction_ms = int(max(0.0, (t - (self._target_since or t)) * 1000))

        if not self._attempted:
            self._attempted = True
            self.total += 1
            if hit:
                self.correct += 1
            cfg = self.cfg()
            self.store.log_sightread(
                self.session_id or 0, target, hit, reaction_ms,
                str(cfg.get("key", "C")), self.notes[self.index]["staff"],
            )
            self.results.append({
                "midi": target, "played": midi, "correct": hit, "reaction_ms": reaction_ms,
            })

        feedback = {
            "correct": hit,
            "expected": target,
            "played": midi,
            "index": self.index,
            "reaction_ms": reaction_ms,
            "complete": False,
        }
        if hit:
            self.index += 1
            self._attempted = False
            self._target_since = t
            if self.index >= len(self.notes):
                feedback["complete"] = True
                self.active = False
        return feedback

    def stop(self) -> None:
        self.active = False

    # ---------------------------------------------------------------- output
    def state(self) -> dict[str, Any]:
        cfg = self.cfg()
        return {
            "active": self.active,
            "exercise_id": self.exercise_id,
            "config": cfg,
            "key_signature": music.key_signature(str(cfg.get("key", "C"))),
            "notes": self.notes,
            "index": self.index,
            "target": self.notes[self.index]["midi"] if self.index < len(self.notes) else None,
            "results": self.results,
            "run_total": self.total,
            "run_correct": self.correct,
            "run_accuracy": round(self.correct / self.total, 3) if self.total else None,
            "history": self.store.sightread_summary(days=30),
            "weak_notes": [
                {**row, "name": music.note_name(row["note"], str(cfg.get("key", "C")))}
                for row in self.store.weak_notes(limit=8)
            ],
        }
