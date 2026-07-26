"""Keys backend package.

This file exists for exactly one reason: **the two lines below have to run before
anything else in this process, and putting them here makes that unavoidable.**
Python guarantees a package's ``__init__`` executes before any of its submodules,
so there is no import order a caller can pick that skips them.

Both are load-bearing and both were measured on this machine, not guessed.
See ``docs/FEASIBILITY.md`` and the project ``CLAUDE.md``.
"""

from __future__ import annotations

import os
import sys

# --- 1. GIL tuning. Must happen before any thread starts. --------------------
# The default 0.005 switch interval gives a ~14.5 ms median delay before the MIDI
# callback thread gets to run -- worse than every buffer setting combined. 0.001
# changes nothing (14.4 ms); the threshold is strictly BELOW 1000 microseconds.
# At 0.0008 the median is 0.53 ms, which is the idle baseline.
sys.setswitchinterval(0.0008)

# --- 2. DLL discovery. Must happen before `import fluidsynth`. ---------------
# pyfluidsynth resolves libfluidsynth-3.dll via ctypes.util.find_library(), whose
# Windows implementation walks os.environ['PATH'] and nothing else -- it is blind
# to os.add_dll_directory(). The lookup runs at fluidsynth's module import time,
# so this cannot be deferred.
FLUIDSYNTH_BIN = os.environ.get("KEYS_FLUIDSYNTH_BIN", r"C:\tools\fluidsynth\bin")
if FLUIDSYNTH_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FLUIDSYNTH_BIN + os.pathsep + os.environ.get("PATH", "")

__all__ = ["FLUIDSYNTH_BIN"]
