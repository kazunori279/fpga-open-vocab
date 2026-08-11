# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Render forgix_cam_probe's frame dumps, and check the device's quantizer.

    uv run host/cam.py /tmp/m8a.log

Two jobs, and the second is the one that matters.

**Render the frame.** RGB565 has two byte orders and the ArduChip's datasheet
does not say which one comes out of the FIFO, so both PNGs get written and you
look at them. This is not laziness - it is the only test that catches the whole
class of faults that produce a well-formed tensor of the wrong picture. A frame
that is byte-swapped, row-reversed, or one pixel out of phase still quantizes
cleanly, still has a stable CRC, and is still not what the camera saw.

**Check the quantizer.** The device computed the int8 CHW tensor it would hand
fgx_run(), and printed a CRC32 of it. This recomputes the same tensor from the
same bytes with numpy and compares. M5's contract is that the encoder is
bit-exact from the codes inward; this is the half of the chain that contract
does not cover, and until these two CRCs match, "bit-exact" describes a pipeline
whose input is unverified.

The arithmetic being checked, from model/distill.py:44 and model/export.py:310:

    ToTensor        v / 255
    Normalize       (x - 0.5) / 0.5
    quantize        clip(rint(x / in_scale), -127, 127)

composed to `clip(rint((v / 127.5 - 1) / in_scale), -127, 127)`, in float32,
CHW. in_scale is read out of weights.bin rather than typed in here, for the
reason cam_probe.c links encoder.c to get it: a constant copied into two files
is a constant that will disagree in one of them.
"""

import argparse
import base64
import binascii
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

BEGIN = re.compile(r"^BEGIN (\w+) (\d+) (\d+) (\d+) ([0-9a-f]{8})\s*$")
CHWCRC = re.compile(r"chw crc32\s*:\s*([0-9a-f]{8})\s*\((high|low) byte first\)")
# demo.py --snap-every prints this just before it asks for a dump, so a PNG can
# be tied to the frame line that scored it. Optional by design: a log without
# these still parses, it just cannot name the frame.
SNAPAT = re.compile(r"^snap\s*:.*\bframe (\d+)")


def write_png(path: Path, rgb: np.ndarray) -> None:
    """Minimal 8-bit truecolour PNG. Hand-rolled to keep the dependency at numpy.

    A viewer is the instrument here, so the file format is not worth a wheel.
    """
    h, w, _ = rgb.shape
    stride = np.hstack([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)])

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(stride.tobytes(), 9))
        + chunk(b"IEND", b""))


def unpack565(buf: np.ndarray, w: int, h: int, hi_first: bool) -> np.ndarray:
    """FIFO bytes -> (h, w, 3) uint8, expanding 5/6/5 by bit replication.

    Replication and not `round(v * 255 / 31)`: it is exact at both endpoints,
    needs no divide, and - the only reason that matters - it is what
    cam_probe.c's unpack_chw() does. The two have to agree by construction or
    the CRC comparison below is comparing two different definitions of correct.
    """
    b = buf[: w * h * 2].reshape(-1, 2).astype(np.uint16)
    px = (b[:, 0] << 8) | b[:, 1] if hi_first else (b[:, 1] << 8) | b[:, 0]
    r5 = (px >> 11) & 0x1F
    g6 = (px >> 5) & 0x3F
    b5 = px & 0x1F
    rgb = np.stack([(r5 << 3) | (r5 >> 2),
                    (g6 << 2) | (g6 >> 4),
                    (b5 << 3) | (b5 >> 2)], axis=1).astype(np.uint8)
    return rgb.reshape(h, w, 3)


def to_codes(rgb: np.ndarray, in_scale: float) -> np.ndarray:
    """(h, w, 3) uint8 -> int8 CHW, in float32 all the way.

    float32 explicitly at every step. numpy promotes to float64 at the first
    opportunity and the device has no float64, so a check done in double would
    pass on a device that is wrong by an LSB - which is exactly the size of
    error this is looking for.
    """
    x = rgb.astype(np.float32) / np.float32(127.5) - np.float32(1.0)
    x = x / np.float32(in_scale)
    q = np.clip(np.rint(x), -127, 127).astype(np.int8)
    return np.ascontiguousarray(q.transpose(2, 0, 1))


def selftest(table: Path) -> bool:
    """Check this file's numpy against firmware/test_cam_pixel.c's output.

    Without this, the CRC comparison further down is theatre. It claims two
    independent implementations agree, and they are not independent - the same
    person wrote both from the same three lines of export.py, which is the
    standard way two implementations come to share a mistake. The C is the one
    that runs on the board, so the C emits a table and the numpy is checked
    against it. Any disagreement is found here, with no camera involved.
    """
    rows = [ln.split(",") for ln in table.read_text().splitlines()
            if ln and not ln.startswith("#")]
    kinds = {r[0] for r in rows}
    if not {"expand5", "expand6", "code", "layout"} <= kinds:
        sys.exit(f"{table}: not a test_cam_pixel table (saw {sorted(kinds)})")

    bad = 0
    for r in rows:
        if r[0] == "expand5":
            v = int(r[1])
            got = (v << 3) | (v >> 2)
        elif r[0] == "expand6":
            v = int(r[1])
            got = (v << 2) | (v >> 4)
        elif r[0] == "code":
            scale, v = np.float32(r[1]), int(r[2])
            got = int(to_codes(np.full((1, 1, 3), v, np.uint8), scale)[0, 0, 0])
        else:
            continue
        want = int(r[-1])
        if got != want:
            bad += 1
            if bad <= 8:
                print(f"  !! {','.join(r[:-1])}: C says {want}, numpy says {got}")

    # The layout rows, replayed as one frame each way through the same code
    # path a real capture takes.
    src = np.array([0x00, 0x00, 0xFF, 0xFF, 0xF8, 0x00, 0x07, 0xE0,
                    0x00, 0x1F, 0x12, 0x34, 0xAB, 0xCD, 0x80, 0x01], np.uint8)
    for hi in (0, 1):
        want = [int(r[3]) for r in rows if r[0] == "layout" and int(r[1]) == hi]
        got = to_codes(unpack565(src, 4, 2, hi == 1),
                       np.float32("0.0078125")).reshape(-1).tolist()
        if got != want:
            bad += 1
            print(f"  !! layout hi_first={hi}: C {want}\n"
                  f"                    numpy {got}")

    n = sum(1 for r in rows if r[0] in ("expand5", "expand6", "code")) + 2
    print(f"selftest  : {n} device-side values, {bad} disagreements")
    return bad == 0


def read_in_scale(path: Path) -> tuple[float, int]:
    """in_scale and in_size out of weights.bin's header (model/export.py:237)."""
    fmt = "<4sIIIIII ff"
    magic, _ver, _n, in_size, _cin, _d, _ds, in_scale, _hs = struct.unpack(
        fmt, path.read_bytes()[:struct.calcsize(fmt)])
    if magic != b"FGX5":
        sys.exit(f"{path}: not an FGX5 blob")
    return in_scale, in_size


def parse(log: Path) -> list[dict]:
    """Pull every BEGIN/END block out of a session log, with the CRCs after it.

    Tolerant of interleaved chatter on purpose: the dump shares a CDC endpoint
    with the probe's own report, and a checker that only works on a
    perfectly-clean capture is a checker that gets bypassed on the first messy
    one.
    """
    frames, cur, at = [], None, None
    for line in log.read_text(errors="replace").splitlines():
        m = BEGIN.match(line)
        if m:
            cur = {"tag": m[1], "w": int(m[2]), "h": int(m[3]),
                   "n": int(m[4]), "crc": int(m[5], 16), "b64": [],
                   "device_chw": {}, "at": at}
            at = None
            continue
        if cur is None:
            m = SNAPAT.match(line)
            if m:
                at = int(m[1])
                continue
        if cur is not None and line.startswith("END "):
            frames.append(cur)
            cur = None
            continue
        if cur is not None:
            cur["b64"].append(line.strip())
            continue
        m = CHWCRC.search(line)
        if m and frames:
            frames[-1]["device_chw"][m[2]] = int(m[1], 16)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path, nargs="?",
                    help="a forgix_cam_probe session log")
    ap.add_argument("--selftest", type=Path, metavar="TABLE",
                    help="check this file's numpy against test_cam_pixel.c's "
                         "output, and exit")
    ap.add_argument("--weights", type=Path,
                    default=Path("model/runs/so400m-full-a05/export/weights.bin"))
    ap.add_argument("--in-scale", type=float,
                    help="override the blob's in_scale; for synthetic frames "
                         "from test_cam_pixel.c --frame, which have no blob")
    ap.add_argument("--out", type=Path, default=Path("/tmp"),
                    help="where the PNGs go")
    ap.add_argument("--rot", type=int, default=0, choices=(0, 90, 180, 270),
                    help="turn the PNG this many degrees counter-clockwise, to "
                         "match firmware FT_MOUNT_ROT. Display only: the chw "
                         "CRC below is still compared against the raw frame, "
                         "because that is what the device hashed")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest(args.selftest)
        print("\nRESULT : " + ("PASS - numpy agrees with the firmware"
                               if ok else "FAIL - see above"))
        return 0 if ok else 1
    if not args.log:
        ap.error("a log file is required unless --selftest is given")

    args.out.mkdir(parents=True, exist_ok=True)

    if args.in_scale is not None:
        in_scale = np.float32(args.in_scale)
        print(f"in_scale  : {float(in_scale):.9g}  (--in-scale, no blob read)")
    else:
        raw_scale, in_size = read_in_scale(args.weights)
        in_scale = np.float32(raw_scale)
        print(f"in_scale  : {float(in_scale):.9g}  (from {args.weights})")
        print(f"model in  : {in_size}x{in_size}")

    # The composition claimed in the docstring, checked rather than asserted in
    # prose. Every byte value, not a sample: it is 256 of them.
    v = np.arange(256, dtype=np.float32)
    torch_path = (v / np.float32(255.0) - np.float32(0.5)) / np.float32(0.5)
    if not np.array_equal(torch_path, v / np.float32(127.5) - np.float32(1.0)):
        print("!! ToTensor+Normalize does NOT equal v/127.5-1 in float32")
        return 1
    print("prescale  : v/127.5-1 == (v/255-0.5)/0.5 for all 256 byte values")

    frames = parse(args.log)
    if not frames:
        print(f"\nRESULT : FAIL - no BEGIN/END blocks in {args.log}")
        return 1

    # M13's 'V' dumps ride the same BEGIN/END envelope - deliberately, so there
    # is one emitter and one parser - but they are 512 floats, not RGB565.
    # Rendered as pixels they produce a plausible-looking violet PNG that means
    # nothing, which is worse than an error. host/caption.py is what reads them.
    frames = [f for f in frames if f["tag"] != "m9emb"]
    if not frames:
        print("\nRESULT : no pixel dumps here - this log has only 'V' vectors. "
              "Read them with host/caption.py.")
        return 0

    ok = True
    for i, f in enumerate(frames):
        # One dump per tag was the old assumption and it overwrote silently:
        # m9 tags every block "m9", so a log with several of them used to leave
        # only the last one on disk, with nothing said about the rest. Numbered
        # in log order when there is more than one; a single-dump log keeps the
        # bare tag it always had. Multi-tag logs from the M8a bring-up (f128,
        # f128fast, qvga in one capture) do get renamed by this, which is
        # harmless - nothing reads these paths, they are looked at by eye.
        stem = f["tag"] if len(frames) == 1 else f"{f['tag']}-{i:02d}"
        if f.get("at") is not None:
            stem = f"{stem}-f{f['at']:04d}"
        raw = base64.b64decode("".join(f["b64"]))
        crc = binascii.crc32(raw) & 0xFFFFFFFF
        print(f"\n-- {stem}: {f['w']}x{f['h']}, {len(raw)} bytes --")
        if len(raw) != f["n"]:
            print(f"  !! {len(raw)} bytes decoded, header says {f['n']}")
            ok = False
        print(f"  crc32     : {crc:08x} vs {f['crc']:08x} announced  "
              f"{'ok' if crc == f['crc'] else 'CORRUPT IN TRANSIT'}")
        if crc != f["crc"]:
            ok = False
            continue

        buf = np.frombuffer(raw, np.uint8)
        want = f["w"] * f["h"] * 2
        if len(buf) < want:
            print(f"  !! need {want} bytes for {f['w']}x{f['h']}, have {len(buf)}")
            ok = False
            continue

        for name, hi in (("hi", True), ("lo", False)):
            rgb = unpack565(buf, f["w"], f["h"], hi)
            png = args.out / f"{stem}-{name}.png"
            # np.rot90 turns counter-clockwise, which is the same direction
            # cam_pixel.h's CAM_ROT_90 means, so the two constants read alike.
            write_png(png, np.rot90(rgb, args.rot // 90) if args.rot else rgb)
            mean = rgb.reshape(-1, 3).mean(axis=0)
            print(f"  {name} byte first : mean rgb "
                  f"({mean[0]:5.1f},{mean[1]:5.1f},{mean[2]:5.1f})  "
                  f"-> {png}")

            # Only the mode the encoder eats gets a tensor; the device only
            # printed CRCs for that one.
            key = "high" if hi else "low"
            if key not in f["device_chw"]:
                continue
            codes = to_codes(rgb, in_scale)
            hcrc = binascii.crc32(codes.tobytes()) & 0xFFFFFFFF
            dcrc = f["device_chw"][key]
            same = hcrc == dcrc
            ok &= same
            print(f"     chw crc32 : host {hcrc:08x} vs device {dcrc:08x}  "
                  f"{'MATCH' if same else 'MISMATCH'}")

        # A frame that is one constant colour renders as a plausible PNG and
        # passes every CRC. Saying so is cheaper than squinting at a flat image.
        if buf[:want].min() == buf[:want].max():
            print(f"  !! every byte is 0x{buf[0]:02x} - the sensor produced nothing")
            ok = False

    print("\nRESULT : " + (
        "PASS - frames decoded and the device's quantizer matches numpy.\n"
        "         Now open the PNGs. The CRCs cannot tell you the picture is "
        "the right way up."
        if ok else "FAIL - see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
