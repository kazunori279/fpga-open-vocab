# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Ship a bitstream to forgix_m6 and tee its report.

    uv run host/m6.py --bitstream rtl/build/gemm_top_m16.hex --out /tmp/m6.log

This is the whole host side of M6c, and it is deliberately thin. Everything that
could be got wrong twice - the strip layout, the weight order, the golden
accumulators - lives on the MCU, which already has weights.bin in flash. If the
strip were built here, a passing run would prove the FPGA agrees with this file
rather than with encoder.c, which is not the claim M6 is making.

So the only thing that crosses USB is 173 KB of bitstream, wrapped in four
fields:

    "FGXB" | len u32 LE | crc32 u32 LE | len bytes

and after that this script is a `tail -f` that knows when to stop. The MCU
reconfigures the FPGA from SRAM, so a new RTL revision costs one run of this and
no PRG-GND strap.

Asserting DTR matters: m6.c blocks on stdio_usb_connected(), which is DTR, so a
reader that does not raise it sees a board that looks dead. `cat /dev/cu.*` is
exactly that trap.
"""

import argparse
import re
import sys
import time
import zlib
from pathlib import Path

import serial
from serial.tools import list_ports

MAGIC = b"FGXB"

# Chunked rather than one write. The MCU reads the stream a byte at a time
# through getchar_timeout_us(), and while USB CDC's own flow control means it
# cannot actually be overrun, a single 173 KB write makes any stall look like a
# hang with no way to tell how far it got.
CHUNK = 4096


def pick_port() -> str:
    ports = [p.device for p in list_ports.comports() if "usbmodem" in p.device]
    if not ports:
        raise SystemExit("no /dev/cu.usbmodem* found - is the board plugged in?")
    if len(ports) > 1:
        raise SystemExit(f"several ports, pass --port: {', '.join(ports)}")
    return ports[0]


def load_image(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    if path.suffix.lower() != ".hex":
        return raw, "raw binary"
    compact = re.sub(r"\s+", "", raw.decode("ascii"))
    if len(compact) % 2:
        raise SystemExit(f"{path}: odd number of hex digits")
    return bytes.fromhex(compact), "Efinity ASCII hex"


def main() -> int:
    ap = argparse.ArgumentParser()
    # The shipped netlist, not the last untagged build. `build.sh` writes
    # gemm_top.hex only when TAG is empty, so that name froze at M14 while every
    # milestone since has built under a tag - and a stale bitstream fails as
    # *every rate erroring*, which reads exactly like a dead board. Cost a flash
    # and a full sweep to diagnose once; the crc32 line at the top of the log is
    # what tells the two apart.
    ap.add_argument("--bitstream", type=Path,
                    default=Path("rtl/build/gemm_top_m16.hex"))
    ap.add_argument("--port", default=None)
    ap.add_argument("--out", type=Path, default=Path("/tmp/m6.log"))
    ap.add_argument("--idle", type=float, default=30.0,
                    help="stop after this many quiet seconds")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="give up if the board never says anything")
    args = ap.parse_args()

    if not args.bitstream.exists():
        raise SystemExit(f"{args.bitstream}: not found - run ./rtl/build.sh gemm_top")
    image, kind = load_image(args.bitstream)
    crc = zlib.crc32(image)
    print(f"bitstream : {args.bitstream} ({kind}), {len(image)} bytes, "
          f"crc32=0x{crc:08X}", file=sys.stderr)

    port = args.port or pick_port()
    print(f"port      : {port}", file=sys.stderr)

    header = MAGIC + len(image).to_bytes(4, "little") + crc.to_bytes(4, "little")

    with serial.Serial(port, 115200, timeout=0.5) as s, args.out.open("w") as log:
        s.dtr = True

        def pump() -> str:
            """Drain whatever the board has said, to the terminal and the log."""
            chunk = s.read(4096)
            if not chunk:
                return ""
            text = chunk.decode("utf-8", "replace")
            sys.stdout.write(text)
            sys.stdout.flush()
            log.write(text)
            log.flush()
            return text

        # m6.c prints its banner, then dots once a second while it hunts for the
        # magic. Waiting for the banner rather than sending immediately means the
        # header cannot land in the CDC buffer before stdio is up - and the MCU
        # hunts for MAGIC anyway, so a stray byte from a previous session is
        # discarded rather than parsed as a length.
        started = time.monotonic()
        seen = ""
        while "waiting for a bitstream" not in seen:
            seen += pump()
            if time.monotonic() - started > args.wait:
                print("\n(the board never announced itself - unplug and replug USB)",
                      file=sys.stderr)
                return 1

        s.write(header)
        for i in range(0, len(image), CHUNK):
            s.write(image[i:i + CHUNK])
            pump()
        s.flush()

        last = time.monotonic()
        while time.monotonic() - last < args.idle:
            if pump():
                last = time.monotonic()

    print(f"\nsaved     : {args.out}", file=sys.stderr)

    text = args.out.read_text()
    if "RESULT : PASS" in text:
        return 0
    if "RESULT : FAIL" in text:
        return 1
    print("(no RESULT line - the run did not finish)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
