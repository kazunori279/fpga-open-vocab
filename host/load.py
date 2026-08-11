# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Stream an Efinity bitstream to the Trion T8 via the Forge Loader firmware.

Accepts Efinity ASCII .hex (one hex byte per line) or raw .bin.

    uv run host/load.py rtl/bitstreams/m11/gemm_top_wide.hex [--port /dev/cu.usbmodemXXXX]

The END reply carries the real verdict: CDONE and STATUS both high means the
FPGA accepted the bitstream and entered user mode.
"""

import argparse
import re
import struct
import sys
import time
import zlib
from pathlib import Path

import forge


def load_image(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    if path.suffix.lower() == ".hex":
        compact = re.sub(r"\s+", "", raw.decode("ascii"))
        if len(compact) % 2:
            raise ValueError("hex file has an odd number of hex digits")
        return bytes.fromhex(compact), "Efinity ASCII hex"
    return raw, "raw binary"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--port", default="/dev/cu.usbmodem21201")
    ap.add_argument("--spi-hz", type=int, default=0, help="0 = firmware default (8 MHz)")
    ap.add_argument("--chunk", type=int, default=forge.MAX_PAYLOAD)
    ap.add_argument(
        "--corrupt",
        type=int,
        default=0,
        metavar="N",
        help="control test: invert N bytes mid-image; a real loader should refuse it",
    )
    ap.add_argument(
        "--garbage",
        choices=("zeros", "ones", "invert"),
        help="control test: replace the whole image. Nothing here is a valid "
        "bitstream, so CDONE must stay low - if it goes high the pin read is "
        "meaningless.",
    )
    args = ap.parse_args()

    data, fmt = load_image(args.image)
    if args.corrupt:
        mid = len(data) // 2
        buf = bytearray(data)
        for i in range(mid, min(mid + args.corrupt, len(buf))):
            buf[i] ^= 0xFF
        data = bytes(buf)
        fmt += f", CORRUPTED {args.corrupt} bytes @ {mid}"
    if args.garbage:
        n = len(data)
        if args.garbage == "zeros":
            data = b"\x00" * n
        elif args.garbage == "ones":
            data = b"\xff" * n
        else:
            data = bytes(b ^ 0xFF for b in data)
        fmt = f"GARBAGE ({args.garbage}), {n} bytes"

    crc = zlib.crc32(data)
    print(f"image  : {args.image}  ({fmt})")
    print(f"         {len(data)} bytes, crc32=0x{crc:08X}")

    with forge.open_port(args.port) as port:
        seq = 0
        r = forge.exchange(port, forge.HELLO, seq); seq += 1
        print(f"hello  : {r.describe()}")

        payload = struct.pack("<QIIII", len(data), crc, args.chunk, args.spi_hz, 0)
        r = forge.exchange(port, forge.START, seq, payload); seq += 1
        print(f"start  : {r.msg}")

        t0 = time.time()
        for off in range(0, len(data), args.chunk):
            chunk = data[off : off + args.chunk]
            forge.exchange(port, forge.DATA, seq, chunk); seq += 1
            pct = 100 * (off + len(chunk)) // len(data)
            print(f"\rsend   : {off + len(chunk)}/{len(data)} ({pct}%)", end="", flush=True)
        dt = time.time() - t0
        print(f"\rsend   : {len(data)} bytes in {dt:.2f}s ({len(data)/dt/1024:.1f} KB/s)      ")

        try:
            r = forge.exchange(port, forge.END, seq); seq += 1
        except forge.Nack as e:
            print(f"end    : {e}")
            print("\nRESULT : FAIL - FPGA rejected the bitstream")
            return 1

        cdone, status = r.pins
        print(f"end    : {r.describe()}")
        print()
        print("RESULT : " + ("PASS - FPGA configured and in user mode" if cdone else "FAIL"))
        return 0 if cdone else 1


if __name__ == "__main__":
    sys.exit(main())
