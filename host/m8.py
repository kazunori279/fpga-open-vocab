# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Ship a bitstream to forgix_m8 and watch it embed frames until Ctrl-C.

    uv run host/m8.py --bitstream rtl/bitstreams/m16/gemm_top_wide.hex --out /tmp/m8c.log

host/m7.py with the ladder taken out. Two real differences, both from what M8c
is rather than from how it is driven:

**One bitstream, and this script does not know which.** m7.py sends a narrow
image and then, on the board's cue, an optional wide one, because m7 measures
both configurations in one boot. m8 runs the wide bitstream if that is what
arrived and the narrow one otherwise, and it works out which by *running the
whole test vector over the wire and comparing 512 floats* - see m8.c's probe.
So there is no --wide, no cue to watch for, and no flag that can disagree with
the hex file: pass whichever image is built and the board reports what it found.

**There is no end of run.** m7 parks after its RESULT line, so m7.py's idle
timeout is how it knows the run finished. m8 loops forever by design, which
makes a quiet port a symptom again rather than the normal end - the loop prints
a line a frame at roughly one frame a second. So --idle is short here, and
hitting it is reported as a stall rather than as completion. Ctrl-C is the
ordinary way to stop, and it sends 'B' on the way out so the board is left in
BOOTSEL ready for the next flash rather than looping at a lens cap.

Same framing as m6.py and m7.py, and thin for the same reason: everything that
could be got wrong twice lives on the MCU, which already has weights.bin in
flash.

    "FGXB" | len u32 LE | crc32 u32 LE | len bytes

Asserting DTR matters: m8.c blocks on stdio_usb_connected(), which is DTR, so a
reader that does not raise it sees a board that looks dead.
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

# Chunked rather than one write, for m7.py's reason: a single 173 KB write makes
# any stall look like a hang with no way to tell how far it got.
CHUNK = 4096


# The RP2350's USB vendor ID. Matching on it rather than on the device name is
# not defensive programming, it is the fix for a specific hour lost on this
# bench: macOS builds the /dev/cu.usbmodemNNN suffix out of the USB *location*
# id, so the numbers shuffle whenever anything on the bus is replugged, and this
# desk has a Tiliqua and a Digilent cable on the same hub. A stale --port number
# does not error - it opens the neighbour, which is silent, and the board then
# looks wedged. Identify by what the device is.
RP2350_VID = "2E8A"


def pick_port() -> str:
    hits = [p for p in list_ports.comports()
            if f"VID:PID={RP2350_VID}" in (p.hwid or "").upper()]
    if not hits:
        others = ", ".join(p.device for p in list_ports.comports()
                           if "usbmodem" in p.device) or "none"
        raise SystemExit(
            f"no RP2350 CDC port (USB VID {RP2350_VID}) found. Other modems: "
            f"{others}. Is the board plugged in, or still in BOOTSEL?")
    if len(hits) > 1:
        raise SystemExit("several RP2350s, pass --port: "
                         + ", ".join(p.device for p in hits))
    return hits[0].device


def load_image(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()
    if path.suffix.lower() != ".hex":
        return raw, "raw binary"
    compact = re.sub(r"\s+", "", raw.decode("ascii"))
    if len(compact) % 2:
        raise SystemExit(f"{path}: odd number of hex digits")
    return bytes.fromhex(compact), "Efinity ASCII hex"


def bootsel(port: str) -> int:
    """Reboot into BOOTSEL. m7.py's routine, and it works the same way here.

    'B' first, because it is instant when the board is listening - and m8's
    loop checks stdin once a frame, so it is listening almost always, unlike a
    parked m7. Then the 1200-baud touch, which is handled inside the USB stack
    rather than by the application loop and so survives more.
    """
    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        try:
            with serial.Serial(port, 115200, timeout=0.5) as s:
                s.dtr = True
                time.sleep(0.2)
                s.write(b"B")
                s.flush()
                time.sleep(0.5)
            with serial.Serial(port, 1200) as s:
                s.dtr = False
                time.sleep(0.3)
        except OSError:
            pass                      # the port vanishing is the success case
        for _ in range(20):
            if Path("/Volumes/RP2350").is_dir():
                print("BOOTSEL - /Volumes/RP2350 is up", file=sys.stderr)
                return 0
            time.sleep(0.25)
    print("the board never reached BOOTSEL - unplug and replug USB",
          file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    # See the note in host/m6.py: the shipped netlist, not rtl/build/, which is
    # gitignored and overwritten by every synthesis run.
    ap.add_argument("--bitstream", type=Path,
                    default=Path("rtl/bitstreams/m16/gemm_top_wide.hex"),
                    help="the wide image by default: m8 probes the wire and "
                         "reports which configuration it got, so either works")
    ap.add_argument("--port", default=None)
    ap.add_argument("--out", type=Path, default=Path("/tmp/m8c.log"))
    # ~1 s a frame once the loop starts, but the start-up is quiet for much
    # longer than that: the MCU reference is ~3.3 s and the probe is up to two
    # whole frames, one of which may be failing its way through a stall
    # deadline. 45 s clears all of it with room to spare and still catches a
    # wedge inside a minute.
    ap.add_argument("--idle", type=float, default=45.0,
                    help="stop, and report a stall, after this many quiet "
                         "seconds")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="give up if the board never says anything")
    ap.add_argument("--frames", type=int, default=0,
                    help="stop after this many frame lines (0 = until Ctrl-C)")
    ap.add_argument("--bootsel", action="store_true",
                    help="reboot the board into BOOTSEL and wait for the drive")
    args = ap.parse_args()

    if args.bootsel:
        return bootsel(args.port or pick_port())

    if not args.bitstream.exists():
        raise SystemExit(f"{args.bitstream}: not found - run ./rtl/build.sh")
    image, kind = load_image(args.bitstream)
    print(f"bitstream : {args.bitstream} ({kind}), {len(image)} bytes, "
          f"crc32=0x{zlib.crc32(image):08X}", file=sys.stderr)

    port = args.port or pick_port()
    print(f"port      : {port}", file=sys.stderr)

    frames, stalled, interrupted = 0, False, False

    with serial.Serial(port, 115200, timeout=0.5) as s, args.out.open("w") as log:
        s.dtr = True

        def pump() -> str:
            try:
                chunk = s.read(4096)
            except (OSError, serial.SerialException):
                return ""
            if not chunk:
                return ""
            text = chunk.decode("utf-8", "replace")
            sys.stdout.write(text)
            sys.stdout.flush()
            log.write(text)
            log.flush()
            return text

        def send(img: bytes) -> None:
            s.write(MAGIC + len(img).to_bytes(4, "little")
                    + zlib.crc32(img).to_bytes(4, "little"))
            for i in range(0, len(img), CHUNK):
                s.write(img[i:i + CHUNK])
                pump()
            s.flush()

        # Wait for the banner rather than sending immediately, so the header
        # cannot land in the CDC buffer before stdio is up.
        started = time.monotonic()
        seen = ""
        while "waiting for a bitstream" not in seen:
            seen += pump()
            if time.monotonic() - started > args.wait:
                print("\n(the board never announced itself - unplug and replug "
                      "USB)", file=sys.stderr)
                return 1

        send(image)

        # Then read until Ctrl-C, --frames, or silence. Counting "frame " lines
        # rather than newlines because the start-up prints plenty of lines and
        # --frames is meant to bound the *demo*, which is what a scripted run
        # wants: flash, prove the loop turns over N times, leave.
        last = time.monotonic()
        carry = ""
        try:
            while time.monotonic() - last < args.idle:
                text = pump()
                if not text:
                    continue
                last = time.monotonic()
                carry += text
                lines = carry.split("\n")
                carry = lines[-1]
                frames += sum(1 for ln in lines[:-1] if ln.startswith("frame "))
                if args.frames and frames >= args.frames:
                    break
            else:
                stalled = True
        except KeyboardInterrupt:
            interrupted = True

        # Leave the board in BOOTSEL either way. m8 never stops on its own, so
        # "the script finished" and "the board is still looping" are the same
        # state, and the next thing anybody does is flash it.
        try:
            s.write(b"B")
            s.flush()
            time.sleep(0.6)
            pump()
        except (OSError, serial.SerialException):
            pass

    print(f"\nsaved     : {args.out}  ({frames} frame lines)", file=sys.stderr)

    text = args.out.read_text()
    if "RESULT : FAIL" in text:
        print("(the board failed one of its three start-up checks)",
              file=sys.stderr)
        return 1
    if stalled:
        print(f"(nothing printed for {args.idle:.0f} s - the loop stalled)",
              file=sys.stderr)
        return 2
    if not frames:
        print("(the loop never printed a frame)", file=sys.stderr)
        return 2
    if interrupted:
        print("(stopped by Ctrl-C)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
