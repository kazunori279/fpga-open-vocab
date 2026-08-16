# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Watch the hub port the board is on, and write down every change.

    uv run host/usb_watch.py --out /tmp/usb_watch.log
    uv run host/usb_watch.py --hub 2-1:1 --interval 0.5

WHY THIS EXISTS. Issue #9's outage ends with the board off the bus, and the only
thing that brings it back is cutting VBUS. But cutting VBUS is a power-on reset,
and a power-on reset clears both POWMAN's CHIP_RESET and the watchdog scratch
that m9 keeps its stage, its frame number and its copy of the reset reason in.
Everything the board knew about why it left dies with the 5 V that is the only
known way to get it back, so an outage that ends in `uhubctl` is unattributable
by construction. The record has to be kept somewhere the power cycle cannot
reach. That is here.

WHAT THE BITS MEAN, because the whole argument turns on them:

  power enable connect   normal.
  power                  VBUS is out there and nothing is pulling D+ up.
                         `connect` is the hub reporting a pull-up, which is a
                         purely electrical property of the far end of the cable
                         - no amount of traffic, from this port or a neighbour,
                         can clear it. So this line means the board stopped
                         being a USB device: either it took its own pull-up
                         down, or it no longer has the volts to hold one up.
                         This is the 2026-08-16 outage, and telling those two
                         apart is what issue #9 is now about.
  (no power)             us, cycling the port. If this appears and nobody ran
                         bootsel.py, the hub dropped VBUS on its own.

Only transitions are logged, plus a heartbeat, so a gap in the file means the
watcher stopped rather than the board being fine. Timestamps are local wall
clock, to line up against the run log's frame numbers and `log show` on the Mac.

Run it beside a soak, not instead of one:

    uv run host/usb_watch.py --out /tmp/usb_watch.log &
    uv run host/demo.py --frames 3000 ... | tee /tmp/soak.log
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PORT_RE = re.compile(r"^\s*Port (\d+):\s+([0-9a-fA-F]{4})\s*(.*?)\s*$")
HUB_RE = re.compile(r"^\s*Current status for hub (\S+)")

# uhubctl is quick, but a wedged USB stack can make it sit there. Time it out
# well inside the poll interval's patience so a stuck call shows up as its own
# logged event rather than as silence.
UHUBCTL_TIMEOUT_S = 10.0


def read_ports() -> dict[str, str] | str:
    """Every port uhubctl can see, as {"2-1:1": "0103 power enable connect ..."}.

    Returns a string instead of a dict if uhubctl could not be asked, so that
    losing the instrument is itself an event worth a line in the log.
    """
    try:
        r = subprocess.run(["uhubctl"], capture_output=True, text=True,
                           timeout=UHUBCTL_TIMEOUT_S)
    except FileNotFoundError:
        return "uhubctl not installed"
    except subprocess.TimeoutExpired:
        return f"uhubctl did not answer in {UHUBCTL_TIMEOUT_S:g}s"
    except subprocess.SubprocessError as e:
        return f"uhubctl failed: {e}"
    if r.returncode != 0:
        return f"uhubctl exit {r.returncode}: {r.stderr.strip() or 'no message'}"

    ports: dict[str, str] = {}
    hub = None
    for line in r.stdout.splitlines():
        m = HUB_RE.match(line)
        if m:
            hub = m.group(1)
            continue
        m = PORT_RE.match(line)
        if m and hub:
            flags, rest = m.group(2), m.group(3)
            ports[f"{hub}:{m.group(1)}"] = f"{flags} {rest}".strip()
    return ports


def wanted(key: str, sel: str | None) -> bool:
    """Does port `key` ("2-1:1") match a --hub of "2-1" or "2-1:1" or None?"""
    if sel is None:
        return True
    if ":" in sel:
        return key == sel
    return key.startswith(sel + ":")


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hub", metavar="HUB[:PORT]",
                    help="narrow to one hub or one port; default is everything "
                         "uhubctl can see, which also catches a neighbour "
                         "blinking at the same moment")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between polls (default 1.0)")
    ap.add_argument("--heartbeat", type=float, default=60.0,
                    help="seconds between 'still here' lines, so a gap in the "
                         "log is unambiguous (default 60; 0 to disable)")
    ap.add_argument("--out", type=Path,
                    help="also append to this file (stdout always gets it)")
    args = ap.parse_args()

    out = args.out.open("a", buffering=1) if args.out else None

    def say(text: str) -> None:
        line = f"{stamp()}  {text}"
        print(line, flush=True)
        if out:
            out.write(line + "\n")

    prev: dict[str, str] | str | None = None
    # Start the clock now, not at zero: the opening state dump is already a
    # full picture, and a heartbeat one line under it says nothing.
    last_beat = time.monotonic()
    say(f"watching {args.hub or 'every port uhubctl can see'} "
        f"every {args.interval:g}s")

    try:
        while True:
            now = read_ports()

            if isinstance(now, str):
                if now != prev:
                    say(f"INSTRUMENT  {now}")
            else:
                now = {k: v for k, v in now.items() if wanted(k, args.hub)}
                if prev is None:
                    for k in sorted(now):
                        say(f"{k}  {now[k]}")
                elif isinstance(prev, str):
                    say("INSTRUMENT  uhubctl is answering again")
                    for k in sorted(now):
                        say(f"{k}  {now[k]}")
                else:
                    for k in sorted(set(prev) | set(now)):
                        was, is_ = prev.get(k, "(not in the tree)"), \
                                   now.get(k, "(not in the tree)")
                        if was != is_:
                            say(f"{k}  {was}   ->   {is_}")

            prev = now

            if args.heartbeat and time.monotonic() - last_beat >= args.heartbeat:
                last_beat = time.monotonic()
                if isinstance(now, dict):
                    say("still here: " + " | ".join(
                        f"{k} {now[k]}" for k in sorted(now)))
                else:
                    say(f"still here, but {now}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        say("stopped")
        return 0
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    sys.exit(main())
