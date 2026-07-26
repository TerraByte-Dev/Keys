"""The grader. One state machine for every exercise type.

It is fed from the drain loop, never the MIDI callback, and it holds no lock: the
server rebinds `App.run` in a single atomic assignment, so the loop sees either the old
run or the new one and never a half-built one -- the same argument the zone routing
table already relies on.

Two behaviours differ between exercise types, and both are plan fields rather than
branches in here:

* ``lookahead``  0 blocks on a wrong note (reaction time means something); >0 lets one
  dropped note resolve forward instead of marking every remaining step wrong.
* ``bpm``        None means untimed, so there is no grid to be early or late against.

The first-attempt-only rule carries over from sight reading unchanged, and matters more
for chords, not less: fumbling three fingers onto a Dm7 is one miss, not three.
"""

from __future__ import annotations

import time
from typing import Any

from . import CHORD_WINDOW_MS, Plan, Step


class Run:
    def __init__(self, plan: Plan, session_id: int | None = None) -> None:
        self.plan = plan
        self.session_id = session_id
        self.index = 0
        self.records: list[dict[str, Any]] = []
        self.started_at = time.perf_counter()
        self.finished = False

        self._pending: set[int] = set()
        self._step_onsets: list[float] = []
        self._step_vels: list[int] = []
        self._attempted = False          # has this step been answered once?
        self._wrong = False              # was that first answer wrong?
        self._target_since = self.started_at
        self._arm(0)

    # ------------------------------------------------------------------ state
    @property
    def done(self) -> bool:
        return self.finished or self.index >= len(self.plan.steps)

    def _arm(self, i: int) -> None:
        if i < len(self.plan.steps):
            self._pending = set(self.plan.steps[i].notes)
        else:
            self._pending = set()
        self._step_onsets = []
        self._step_vels = []
        self._attempted = False
        self._wrong = False

    # ------------------------------------------------------------------- feed
    def on_note(self, midi: int, velocity: int, t: float,
                click_offset: float | None = None) -> dict[str, Any] | None:
        """One note-on. Returns feedback for the UI, or None if nothing changed."""
        if self.done:
            return None

        if midi in self._pending:
            self._pending.discard(midi)
            self._step_onsets.append(t)
            self._step_vels.append(velocity)
            if self._pending:
                return None                    # chord half-played, keep waiting
            return self._complete(t, click_offset, played=midi)

        # Not part of this step. On a timed plan a note may belong a little further on,
        # which means the step(s) in between were skipped rather than played wrong.
        if self.plan.lookahead:
            jump = self._resolve_ahead(midi)
            if jump is not None:
                self._skip_to(jump, t)
                self._pending.discard(midi)
                self._step_onsets.append(t)
                self._step_vels.append(velocity)
                if not self._pending:
                    return self._complete(t, click_offset, played=midi)
                return None

        return self._miss(midi, t)

    def _resolve_ahead(self, midi: int) -> int | None:
        steps = self.plan.steps
        for k in range(self.index + 1, min(self.index + 1 + self.plan.lookahead, len(steps))):
            if midi in steps[k].notes:
                return k
        return None

    def _skip_to(self, k: int, t: float) -> None:
        for i in range(self.index, k):
            self.records.append(self._record(i, played=-1, correct=False, onset=None,
                                             offset=None, skipped=True))
        self.index = k
        self._arm(k)
        self._target_since = t

    def _miss(self, midi: int, t: float) -> dict[str, Any]:
        # Only the FIRST answer is scored. Hunting for the right key afterwards is
        # already recorded as the miss; counting each wrong key again would bury it.
        first = not self._attempted
        self._attempted = True
        if first:
            self._wrong = True
        return {
            "correct": False, "index": self.index, "played": midi,
            "expected": sorted(self.plan.steps[self.index].notes),
            "complete": False, "scored": first,
        }

    def _complete(self, t: float, click_offset: float | None, played: int) -> dict[str, Any]:
        i = self.index
        onset = self._step_onsets[0] if self._step_onsets else t
        # Spread is how far apart the notes of one step landed. For a two-hand step that
        # IS hands-together synchrony; for a chord it is how much you rolled it.
        spread = (max(self._step_onsets) - min(self._step_onsets)) * 1000.0 \
            if len(self._step_onsets) > 1 else 0.0
        rec = self._record(
            i, played=played, correct=not self._wrong, onset=onset,
            offset=click_offset, spread=spread,
            reaction=(onset - self._target_since) * 1000.0,
            velocities=list(self._step_vels),
        )
        self.records.append(rec)

        self.index += 1
        self._arm(self.index)
        self._target_since = t
        complete = self.index >= len(self.plan.steps)
        return {
            "correct": rec["correct"], "index": i, "played": played,
            "expected": sorted(self.plan.steps[i].notes),
            "reaction_ms": round(rec["reaction_ms"] or 0.0, 1),
            "spread_ms": round(spread, 1),
            "complete": complete, "scored": True,
            "next": self.index if not complete else None,
        }

    def _record(self, i: int, *, played: int, correct: bool, onset: float | None,
                offset: float | None, spread: float = 0.0, reaction: float | None = None,
                skipped: bool = False, velocities: list[int] | None = None) -> dict[str, Any]:
        step: Step = self.plan.steps[i]
        return {
            "idx": i,
            "note": min(step.notes) if step.notes else -1,
            "played": played,
            "correct": bool(correct),
            "skipped": skipped,
            "onset": onset,
            "reaction_ms": reaction,
            "offset_ms": offset,
            "spread_ms": spread,
            "hand": step.hand,
            "crossing": step.crossing,
            "finger": step.fingers[0] if step.fingers else 0,
            "velocities": velocities or [],
        }

    # ----------------------------------------------------------------- output
    def stop(self) -> None:
        """Mark the remaining steps unplayed. Stopping early is not failing -- the
        records simply end, and the metrics say how many steps they cover."""
        self.finished = True

    def state(self, namer) -> dict[str, Any]:
        p = self.plan
        return {
            "running": not self.done,
            "exercise": p.exercise,
            "variant": p.variant,
            "title": p.title,
            "key": p.key,
            "bpm": p.bpm,
            "timed": p.timed,
            "beats_per_bar": p.beats_per_bar,
            "count_in_bars": p.count_in_bars,
            "staff": p.staff,
            "show_keyboard": p.show_keyboard,
            "show_fingers": p.show_fingers,
            "params": p.params,
            "steps": [s.to_dict(namer) for s in p.steps],
            "index": self.index,
            "target": sorted(p.steps[self.index].notes) if not self.done else None,
            "records": [
                {k: r[k] for k in ("idx", "correct", "played", "skipped", "reaction_ms")}
                for r in self.records
            ],
        }
