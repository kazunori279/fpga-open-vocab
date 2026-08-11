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
RECOVER = "uhubctl -l 2-1 -p 1 -a cycle   # then wait ~9 s and retry"


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
        raise SystemExit(
            f"no RP2350 CDC port (USB VID {RP2350_VID}) found. Other modems: "
            f"{neighbours()}. Is the board plugged in, or still in BOOTSEL?")
    if len(hits) > 1:
        raise SystemExit("several RP2350s, pass --port: "
                         + ", ".join(p.device for p in hits))
    return hits[0].device


if __name__ == "__main__":
    port = find_port()
    if port is None:
        print(f"no RP2350 (VID {RP2350_VID}) - the board is not enumerating.",
              file=sys.stderr)
        print(f"  other modems: {neighbours()}", file=sys.stderr)
        print(f"  {RECOVER}", file=sys.stderr)
        sys.exit(1)
    print(port)
