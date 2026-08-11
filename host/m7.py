# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Ship a bitstream to forgix_m7 and tee its report.

    uv run host/m7.py --bitstream rtl/build/gemm_top_m16.hex --out /tmp/m7c.log

M7f added a second, optional bitstream:

    uv run host/m7.py --wide rtl/build/gemm_top_wide_m16.hex --out /tmp/m7f.log

which makes one boot run the whole six-rung ladder over one forward data line and
then over three, through the PIN2 <-> PIN17 jumper. The board asks
for it by printing SEND-WIDE-BITSTREAM on a line of its own and then hunting for
the magic for thirty seconds; if --wide was not given it times out and reports
configuration A alone, which is why this stays an ordinary optional flag rather
than a mode. Nothing here knows what the second image *is* - only when to send
it - so a bitstream mismatch presents on the board, as a CRC failure on the
first transaction, rather than as a silent wrong answer.

M15 runs each of those ladders twice, at int32 DRAIN and again at rq, and that is
four ladders in a boot rather than two. Nothing changes here: rq is a CFG bit, so
both wire formats live in the one bitstream and the board decides. A bitstream
built without RQ=1 presents the same way the wrong wide image does - loudly, on
the first rq transaction - which is the reason this file was kept ignorant.

Same shape as host/m6.py, and thin for the same reason: everything that could be
got wrong twice - the blocking, the strip layout, the weight order, the golden
tensors - lives on the MCU, which already has weights.bin in flash. If any of it
were computed here, a passing run would prove the FPGA agrees with this file
rather than with encoder.c, which is not the claim M7c is making.

    "FGXB" | len u32 LE | crc32 u32 LE | len bytes

Two differences from m6.py, both about how much longer this run is. M7c does a
whole reference frame on the MCU (~3.4 s) and then a whole frame on the tile,
which is the number the milestone exists to report and which nobody has measured
yet - the projection is ~1.9 s but the point of running it is that the
projection might be wrong. So --wait allows for the reference pass, and --idle
allows for the longest gap between two printed lines - which the configuration C
sweep made much longer than the first version of this script assumed.

Asserting DTR matters: m7.c blocks on stdio_usb_connected(), which is DTR, so a
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

# What m7.c prints, on a line of its own, when it is ready for the second
# bitstream. Kept here rather than inline so the length is the search window.
WIDE_CUE = "SEND-WIDE-BITSTREAM"


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
    # See the note in host/m6.py: gemm_top.hex is the last *untagged* build and
    # froze at M14. The default is the shipped netlist instead, because a stale
    # bitstream presents as a dead board rather than as a mismatch.
    ap.add_argument("--bitstream", type=Path,
                    default=Path("rtl/build/gemm_top_m16.hex"))
    ap.add_argument("--wide", type=Path, default=None,
                    help="second bitstream for configuration C, sent when the "
                         "board asks; needs the PIN2 <-> PIN17 jumper fitted")
    ap.add_argument("--port", default=None)
    ap.add_argument("--out", type=Path, default=Path("/tmp/m7c.log"))
    # 20 s was too short and cost a run. The board is silent for as long as one
    # mode's slowest layer takes, and in the configuration C accumulator sweep
    # that is over half a minute - a quiet gap is the normal state of this
    # harness, not a symptom. The stall deadline in gemm_host.c is what turns a
    # genuinely wedged link into output, so the host no longer has to guess.
    ap.add_argument("--idle", type=float, default=90.0,
                    help="stop after this many quiet seconds")
    ap.add_argument("--wait", type=float, default=120.0,
                    help="give up if the board never says anything")
    # M7g. A finished run used to sit in a tight loop, which is the one state
    # the 1200-baud BOOTSEL touch cannot reach - so every reflash after a
    # completed run needed a physical unplug. m7.c now parks in a getchar loop
    # instead and takes 'B'. This flag is the whole reflash cycle:
    #     uv run host/m7.py --bootsel && cp -X firmware/build/forgix_m7.uf2 \
    #         /Volumes/RP2350/
    #
    # M7h reordered what it tries, because 'B' turned out to be the weaker of
    # the two. A parked M7g board went deaf on stdin while staying enumerated,
    # and 'B' has no answer to that; the 1200-baud touch is handled inside the
    # USB stack's line-coding callback rather than by the application loop, so
    # it survives more. m7.c's park() now reboots itself to the bitstream
    # prompt after eight seconds, which is the state the touch reaches most
    # reliably - so this waits that long before giving up on a quiet board.
    ap.add_argument("--bootsel", action="store_true",
                    help="reboot the board into BOOTSEL and wait for the drive")
    args = ap.parse_args()

    if args.bootsel:
        port = args.port or pick_port()
        deadline = time.monotonic() + 40.0
        while time.monotonic() < deadline:
            try:
                # 'B' first: instant when the board is parked and listening.
                with serial.Serial(port, 115200, timeout=0.5) as s:
                    s.dtr = True
                    time.sleep(0.2)   # the board only reads stdin once CDC is up
                    s.write(b"B")
                    s.flush()
                    time.sleep(0.5)
                # Then the touch, which needs no cooperation from the run loop.
                with serial.Serial(port, 1200) as s:
                    s.dtr = False
                    time.sleep(0.3)
            except OSError:
                pass                  # the port vanishing is the success case
            for _ in range(20):
                if Path("/Volumes/RP2350").is_dir():
                    print("BOOTSEL - /Volumes/RP2350 is up", file=sys.stderr)
                    return 0
                time.sleep(0.25)
        print("the board never reached BOOTSEL - unplug and replug USB",
              file=sys.stderr)
        return 1

    if not args.bitstream.exists():
        raise SystemExit(f"{args.bitstream}: not found - run ./rtl/build.sh gemm_top")
    image, kind = load_image(args.bitstream)
    print(f"bitstream : {args.bitstream} ({kind}), {len(image)} bytes, "
          f"crc32=0x{zlib.crc32(image):08X}", file=sys.stderr)

    wide = None
    if args.wide is not None:
        if not args.wide.exists():
            raise SystemExit(
                f"{args.wide}: not found - run ./rtl/build.sh gemm_top_wide")
        wide, wkind = load_image(args.wide)
        print(f"wide      : {args.wide} ({wkind}), {len(wide)} bytes, "
              f"crc32=0x{zlib.crc32(wide):08X}", file=sys.stderr)

    port = args.port or pick_port()
    print(f"port      : {port}", file=sys.stderr)

    with serial.Serial(port, 115200, timeout=0.5) as s, args.out.open("w") as log:
        s.dtr = True

        def pump() -> str:
            """Drain whatever the board has said, to the terminal and the log.

            A vanished port is a normal end of run now, not a failure. M7h gave
            park() an eight-second watchdog so a finished board always returns
            to the bitstream prompt; that reboot drops the CDC device, and
            pyserial raises rather than returning EOF. Treating it as quiet
            lets the idle timeout end the loop in the ordinary way, so the run
            still gets its "saved" line and its exit code from the RESULT line
            it already printed. Before this, a completed PASS exited 1 with a
            traceback.
            """
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
            """Header then the payload, pumping so a stall is visible."""
            s.write(MAGIC + len(img).to_bytes(4, "little")
                    + zlib.crc32(img).to_bytes(4, "little"))
            for i in range(0, len(img), CHUNK):
                s.write(img[i:i + CHUNK])
                pump()
            s.flush()

        # m7.c prints its banner, then dots once a second while it hunts for the
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

        send(image)

        # The reference pass runs silently for ~3.4 s and the layer table then
        # arrives a line at a time, so the quiet gaps here are seconds long. The
        # idle timeout is what ends the run; there is no end-of-run marker to
        # wait for, because a run that dies before its RESULT line still has to
        # come back with whatever it did print.
        #
        # The wide bitstream goes out from inside this loop, on the board's cue.
        #
        # Search the carry *plus this read*, then carry only the marker's length
        # minus one - the exact overlap a marker split across two reads needs.
        # The first version searched a fixed 64-character window instead, and
        # the marker is followed immediately by a ~170-character prompt that the
        # board flushes with it: the whole thing arrived in one read, the window
        # kept only its last 64 characters, and the marker was truncated away.
        # A run that had already produced six good rows then sat out its thirty
        # seconds and reported configuration A alone.
        last = time.monotonic()
        carry, sent_wide = "", False
        while time.monotonic() - last < args.idle:
            text = pump()
            if not text:
                continue
            last = time.monotonic()
            if wide is None or sent_wide:
                continue
            hay = carry + text
            carry = hay[-(len(WIDE_CUE) - 1):]   # off `hay`: a read can be short
            if WIDE_CUE in hay:
                sent_wide = True
                # The board is hunting for the magic and printing a dot a second
                # while it waits, so there is nothing to synchronise against
                # beyond the marker itself.
                print(f"\n(sending {args.wide})", file=sys.stderr)
                send(wide)

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
