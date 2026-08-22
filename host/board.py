# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Which serial port is the Forgix board - one answer, for everything that asks.

Run it to print the port, or exit 1 with advice if the board is not there:

    uv run --script host/board.py

Imported, it is `pick_port()` (the strict one, raises) and `find_port()` (the
cheap one, returns None). Nothing here imports torch, so a caller can ask the
question before paying a minute for the teacher.

**This file exists because two callers asked the question a worse way.**
`host/cue.py` and `ab.sh` both globbed `/dev/cu.usbmodem*` and treated any hit
as the board. This desk always has a Tiliqua R5 on the same hub, which enumerates
as exactly that, so the check could not fail: it passed with the board absent,
skipped the power cycle it existed to trigger, and handed the failure to demo.py
a minute later. On 2026-08-11 that cost two runs of a scene an operator was
holding still.

The general form of the bug is worth naming, because the glob looks correct.
A check written as "is something like the board present" answers a question
nobody has. The question is "is the board present", and on USB the only thing
that answers it is the vendor ID.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

from serial.tools import list_ports

# The RP2350's USB vendor ID. Matching on it rather than on the device name is
# not defensive programming, it is the fix for a specific hour lost on this
# bench: macOS builds the /dev/cu.usbmodemNNN suffix out of the USB *location*
# id, so the numbers shuffle whenever anything on the bus is replugged, and this
# desk has a Tiliqua and a Digilent cable on the same hub. A stale --port number
# does not error - it opens the neighbour, which is silent, and the board then
# looks wedged. Identify by what the device is.
RP2350_VID = "2E8A"

# How it comes back. A reboot is not enough once the firmware has stopped
# answering, and picotool cannot reach a device that is not enumerating at all.
#
# THE PORT CANNOT BE A CONSTANT, which this file learned the hard way. It used
# to read `-l 2-1 -p 1`, and on 2026-08-16 the board moved to port 2 because a
# neighbour was unplugged - so the printed advice would have cycled an EMPTY
# port and then reported that even the hammer did not work. Recovery advice
# naming the wrong port is worse than advice naming none.
#
# And it has to be looked up EARLY, while the board is still there: the moment
# this string is needed - issue #9's outage - the board is gone from uhubctl's
# tree and cannot be found by VID any more.
_WHERE: tuple[str, str] | None = None


def on_bus() -> tuple[str, str, str] | None:
    """(hub, port, what uhubctl calls it) for the RP2350, or None if it is gone.

    This is a different question from `ports()` and the difference is the whole
    reason it exists. `ports()` asks whether the board is presenting a *serial*
    node; this asks whether it is on the bus at all. In BOOTSEL the answer is
    yes-and-no: enumerated, visible, mass storage, no CDC. Reporting that as
    "not enumerating" sends the reader after issue #9's outage instead of at a
    board that is sitting there waiting for a .uf2 - and the advice for the
    outage is a power cycle, which would drop it out of BOOTSEL.
    """
    try:
        out = subprocess.run(["uhubctl"], capture_output=True, text=True,
                             timeout=20, check=False).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    hub = None
    for line in out.splitlines():
        m = re.match(r"\s*Current status for hub (\S+)", line)
        if m:
            hub = m.group(1)
            continue
        m = re.match(r"\s*Port (\d+):", line)
        if m and hub and f"{RP2350_VID.lower()}:" in line.lower():
            desc = re.search(r"\[([^\]]*)\]", line)
            return (hub, m.group(1), desc.group(1) if desc else line.strip())
    return None


def note_where() -> tuple[str, str] | None:
    """Remember which hub port the board is on. Call it while it is still on."""
    global _WHERE  # noqa: PLW0603  - a process-wide cache is the point: the
    # whole reason this exists is that the answer must outlive the board leaving
    # the bus, so it cannot live on anything the caller holds.
    seen = on_bus()
    if seen:
        _WHERE = (seen[0], seen[1])
    return _WHERE


def recover() -> str:
    """The command that brings the board back, naming the port if we know it."""
    if _WHERE:
        return (f"uhubctl -l {_WHERE[0]} -p {_WHERE[1]} -a cycle"
                f"   # then wait ~9 s and retry")
    return ("uhubctl -l HUB -p PORT -a cycle   # then wait ~9 s and retry. "
            "The board is not in uhubctl's tree any more, so it cannot name "
            "the port for you: take the one showing `power` with no `connect`.")


def ports() -> list:
    """Every RP2350 CDC node currently enumerated."""
    return [p for p in list_ports.comports()
            if f"VID:PID={RP2350_VID}" in (p.hwid or "").upper()]


def find_port() -> str | None:
    """The board's port, or None. For callers that want to decide themselves."""
    hits = ports()
    return hits[0].device if len(hits) == 1 else None


def neighbours() -> str:
    """The modems that are not the board - printed so the glob's ghost is named."""
    return ", ".join(p.device for p in list_ports.comports()
                     if "usbmodem" in p.device or "ttyACM" in p.device) or "none"


def pick_port() -> str:
    hits = ports()
    if not hits:
        seen = on_bus()
        where = (f" It IS on the bus at {seen[0]}:{seen[1]} as `{seen[2]}`, so "
                 f"it is in BOOTSEL and needs flashing, not recovering."
                 if seen else " It is not on the bus at all.")
        raise SystemExit(
            f"no RP2350 CDC port (USB VID {RP2350_VID}) found. Other modems: "
            f"{neighbours()}.{where}")
    if len(hits) > 1:
        raise SystemExit("several RP2350s, pass --port: "
                         + ", ".join(p.device for p in hits))
    note_where()   # while it is still there to be found
    return hits[0].device


def state() -> tuple[str, str]:
    """One word for what the board is doing, plus the detail behind it.

        port     a CDC node is there; the detail is the device path
        bootsel  on the bus, no CDC node; the detail is hub:port and the name
        absent   not on the bus at all; the detail is what else is enumerated

    The three want different things done and two of them wear the same symptom
    from a caller's side, which is the whole reason this returns a token rather
    than a bool. `absent` is worth retrying - it is issue #9's outage and a
    power cycle or a replug fixes it. `bootsel` is not: nothing on the bus is
    going to grow a serial port on its own, so a supervisor that keeps
    relaunching is only paying for teacher loads. host/watch.py reads this
    before it relaunches, for exactly that reason.
    """
    port = find_port()
    if port is not None:
        return "port", port
    seen = on_bus()
    if seen:
        return "bootsel", f"{seen[0]}:{seen[1]} as `{seen[2]}`"
    return "absent", f"other modems: {neighbours()}"


if __name__ == "__main__":
    if "--state" in sys.argv:
        what, detail = state()
        print(f"{what} {detail}")
        sys.exit(0)

    port = find_port()
    if port is not None:
        print(port)
        sys.exit(0)

    # Two failures wear the same symptom here - no CDC node - and they want
    # opposite things done. Tell them apart before printing advice.
    seen = on_bus()
    if seen:
        hub, prt, desc = seen
        print(f"RP2350 is on the bus at {hub}:{prt} as `{desc}`, with no CDC "
              f"port. That is BOOTSEL: it is enumerating fine and running no "
              f"firmware.", file=sys.stderr)
        print("  uv run host/bootsel.py --flash firmware/build/forgix_m9.uf2",
              file=sys.stderr)
        print("  Do NOT power-cycle the hub. That is the other failure's fix, "
              "and here it only drops the board out of BOOTSEL.",
              file=sys.stderr)
        if os.path.isdir("/Volumes/RP2350"):
            print("  /Volumes/RP2350 is mounted. Never copy the .uf2 to it - "
                  "see docs/building.md; bootsel.py unmounts it for you.",
                  file=sys.stderr)
        sys.exit(1)

    print(f"no RP2350 (VID {RP2350_VID}) - the board is not on the bus.",
          file=sys.stderr)
    print(f"  other modems: {neighbours()}", file=sys.stderr)
    print(f"  {recover()}", file=sys.stderr)
    sys.exit(1)
