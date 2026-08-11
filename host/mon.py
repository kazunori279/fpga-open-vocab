# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Read a firmware report off the USB CDC port and tee it to a file.

    uv run host/mon.py --out /tmp/m5.log

`screen` works too, but it loses the transcript unless you remember -L, and
these reports cannot be repeated without a power cycle: the firmware prints
once and then sits in a tight loop. So this always writes a file.

Asserting DTR matters. diag.c and m5.c both block on stdio_usb_connected(),
which is DTR, so a reader that does not raise it sees nothing and looks like a
dead board. `cat /dev/cu.*` is exactly that trap.

Exits when the port goes quiet for --idle seconds after the first byte, so it
does not hang forever on a firmware that ends in an infinite loop.
"""

import argparse
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports


def pick_port() -> str:
    ports = [p.device for p in list_ports.comports() if "usbmodem" in p.device]
    if not ports:
        raise SystemExit("no /dev/cu.usbmodem* found - is the board plugged in?")
    if len(ports) > 1:
        raise SystemExit(f"several ports, pass --port: {', '.join(ports)}")
    return ports[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--out", type=Path, default=Path("/tmp/serial.log"))
    ap.add_argument("--idle", type=float, default=20.0,
                    help="stop after this many quiet seconds")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="give up if nothing arrives at all within this long")
    args = ap.parse_args()

    port = args.port or pick_port()
    print(f"port      : {port}", file=sys.stderr)

    # dsrdtr=False plus an explicit dtr=True is the combination that actually
    # raises DTR on macOS; setting it in the constructor alone is not reliable.
    with serial.Serial(port, 115200, timeout=0.5) as s, args.out.open("w") as log:
        s.dtr = True
        started = time.monotonic()
        last = None
        while True:
            chunk = s.read(4096)
            if chunk:
                text = chunk.decode("utf-8", "replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                log.write(text)
                log.flush()
                last = time.monotonic()
            elif last is None:
                if time.monotonic() - started > args.wait:
                    print("\n(nothing received - try unplugging and replugging USB)",
                          file=sys.stderr)
                    return 1
            elif time.monotonic() - last > args.idle:
                break

    print(f"\nsaved     : {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
