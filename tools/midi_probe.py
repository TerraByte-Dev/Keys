"""M0 — Hello, MIDI. Zero dependencies: stock Python + ctypes + WinMM.

Run it, press a key on the piano, see the note in the terminal.

    python midi_probe.py          # list ports, open the first one, print events
    python midi_probe.py --list   # list ports and exit
    python midi_probe.py 1        # open port index 1

If it reports 0 MIDI input ports, the problem is the Windows driver, not this code.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

winmm = ctypes.WinDLL("winmm")

# --- WinMM constants ---------------------------------------------------------
CALLBACK_FUNCTION = 0x00030000
MIM_OPEN, MIM_CLOSE, MIM_DATA, MIM_LONGDATA, MIM_ERROR = 0x3C1, 0x3C2, 0x3C3, 0x3C4, 0x3C5
MMSYSERR_NOERROR = 0

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class MIDIINCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wt.WORD),
        ("wPid", wt.WORD),
        ("vDriverVersion", wt.UINT),
        ("szPname", wt.WCHAR * 32),
        ("dwSupport", wt.DWORD),
    ]


# DWORD_PTR is pointer-sized; on 64-bit Python that is 64 bits.
DWORD_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
HMIDIIN = wt.HANDLE

MidiInProc = ctypes.WINFUNCTYPE(None, HMIDIIN, wt.UINT, DWORD_PTR, DWORD_PTR, DWORD_PTR)

winmm.midiInGetNumDevs.restype = wt.UINT
winmm.midiInGetDevCapsW.argtypes = [DWORD_PTR, ctypes.POINTER(MIDIINCAPS), wt.UINT]
winmm.midiInGetDevCapsW.restype = wt.UINT
winmm.midiInOpen.argtypes = [
    ctypes.POINTER(HMIDIIN), wt.UINT, DWORD_PTR, DWORD_PTR, wt.DWORD,
]
winmm.midiInOpen.restype = wt.UINT
winmm.midiInStart.argtypes = [HMIDIIN]
winmm.midiInStop.argtypes = [HMIDIIN]
winmm.midiInClose.argtypes = [HMIDIIN]


def note_name(note: int) -> str:
    """60 -> 'C4' (middle C), matching the convention the whole project uses."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def list_ports() -> list[str]:
    names = []
    for i in range(winmm.midiInGetNumDevs()):
        caps = MIDIINCAPS()
        if winmm.midiInGetDevCapsW(DWORD_PTR(i), ctypes.byref(caps), ctypes.sizeof(caps)) == MMSYSERR_NOERROR:
            names.append(caps.szPname)
        else:
            names.append("<unreadable>")
    return names


def describe(status: int, d1: int, d2: int) -> str:
    kind, chan = status & 0xF0, (status & 0x0F) + 1
    if kind == 0x90 and d2 > 0:
        return f"note_on   ch{chan:<2} {note_name(d1):<4} ({d1:>3})  vel={d2:>3}"
    if kind == 0x80 or (kind == 0x90 and d2 == 0):
        return f"note_off  ch{chan:<2} {note_name(d1):<4} ({d1:>3})"
    if kind == 0xB0:
        label = {64: "sustain", 1: "mod wheel", 7: "volume", 11: "expression"}.get(d1, f"cc{d1}")
        return f"control   ch{chan:<2} {label:<10} = {d2}"
    if kind == 0xE0:
        return f"pitchbend ch{chan:<2} {((d2 << 7) | d1) - 8192:+d}"
    if kind == 0xC0:
        return f"program   ch{chan:<2} {d1}"
    if kind == 0xD0:
        return f"aftertouch ch{chan:<2} {d1}"
    return f"raw       {status:02X} {d1:02X} {d2:02X}"


def main() -> int:
    args = [a for a in sys.argv[1:]]
    ports = list_ports()

    print(f"MIDI input ports: {len(ports)}")
    for i, name in enumerate(ports):
        print(f"  [{i}] {name}")

    if not ports:
        print(
            "\nNo MIDI inputs. The piano is not reaching Windows as a MIDI device.\n"
            "  -> Device Manager > Sound, video and game controllers\n"
            "  -> uninstall the vendor MIDI driver WITH 'remove the driver' ticked, then replug."
        )
        return 1

    if "--list" in args:
        return 0

    index = next((int(a) for a in args if a.isdigit()), 0)
    if index >= len(ports):
        print(f"\nNo port {index}.")
        return 1

    show_realtime = "--raw" in args
    total = 0
    realtime = 0
    velocities: list[int] = []
    seen_ccs: dict[int, int] = {}

    @MidiInProc
    def callback(_h, msg, _inst, p1, _p2):
        # Real project rule: this callback is the hot path. Here it prints, which is
        # exactly what production code must NOT do -- fine for a probe, never in the synth.
        nonlocal total, realtime
        if msg != MIM_DATA:
            return
        total += 1
        status, d1, d2 = p1 & 0xFF, (p1 >> 8) & 0x7F, (p1 >> 16) & 0x7F

        # The P-71 spams clock (F8) + active sensing (FE) nonstop -- ~98% of all traffic.
        # The synth must drop these first thing; here we just hide them unless asked.
        if status >= 0xF8:
            realtime += 1
            if not show_realtime:
                return

        if status & 0xF0 == 0x90 and d2 > 0:
            velocities.append(d2)
        elif status & 0xF0 == 0xB0:
            seen_ccs[d1] = d2

        print(f"  {describe(status, d1, d2)}")

    handle = HMIDIIN()
    proc = ctypes.cast(callback, ctypes.c_void_p).value
    rc = winmm.midiInOpen(ctypes.byref(handle), index, proc, 0, CALLBACK_FUNCTION)
    if rc != MMSYSERR_NOERROR:
        print(f"\nmidiInOpen failed (code {rc}) on port [{index}]: {ports[index]}")
        return 1

    winmm.midiInStart(handle)
    print(f"\nListening on [{index}] {ports[index]} -- play something. Ctrl+C to stop.")
    if not show_realtime:
        print("(clock + active-sensing hidden; --raw shows everything)\n")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        winmm.midiInStop(handle)
        winmm.midiInClose(handle)

    print(f"\n{'-' * 52}")
    print(f"{total} messages; {realtime} were clock/active-sensing ({realtime * 100 // max(total, 1)}%)")

    if velocities:
        lo, hi = min(velocities), max(velocities)
        distinct = len(set(velocities))
        print(f"{len(velocities)} notes, velocity {lo}-{hi}, {distinct} distinct value(s)")
        # 20 buckets across the 1-127 range, so a flat keyboard is visually obvious.
        buckets = [0] * 20
        for v in velocities:
            buckets[min((v - 1) * 20 // 127, 19)] += 1
        peak = max(buckets)
        for i, n in enumerate(buckets):
            if n:
                print(f"  {i * 127 // 20 + 1:>3}-{(i + 1) * 127 // 20:>3} {'#' * (n * 30 // peak):<30} {n}")
        if distinct == 1:
            print("\n  ^ ONE velocity value = touch response is OFF (set to Fixed).")
            print("    Hold [GRAND PIANO/FUNCTION] + press B2 (white key left of middle C) for Medium.")
        elif hi - lo < 40:
            print(f"\n  ^ narrow range ({hi - lo}) -- try playing much softer and much harder.")
        else:
            print("\n  ^ good dynamic range. Touch response is working.")
    else:
        print("no notes played")

    if seen_ccs:
        print("controllers seen: " + ", ".join(
            f"CC{cc}{' (sustain)' if cc == 64 else ''}={val}" for cc, val in sorted(seen_ccs.items())
        ))
    else:
        print("no controllers seen (sustain pedal sends CC64 -- press it to confirm it works)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
