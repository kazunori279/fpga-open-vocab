# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Photograph the USB current meter on a timer, so the moment of the drop is kept.

    uv run host/meter_cam.py --aim                  # find the crop, once
    uv run host/meter_cam.py --out /tmp/meter_cam   # then leave it running

WHY THIS EXISTS. `host/usb_watch.py` says WHEN the board left the bus; it cannot
say what the 5 V rail was doing at the time, and that is now the open question in
issue #9. The 2026-08-16 outage went `power enable connect` -> (7.75 s) ->
`power`, with D1 dark and no watchdog reboot, which reads as the chip losing its
supply rather than letting go of its pull-up. A meter in line with VBUS settles
it, and the reading only means something if it was taken at the right second:

  current falls to ~0     the board stopped drawing. Its rail collapsed or the
                          chip is off; VBUS from the hub was still there.
  current spikes, then 0  the board pulled too much and something upstream
                          current-limited. That is a different bug with a
                          different fix.
  current unchanged       the board is still drawing and still computing, and
                          only the pull-up went away - back to firmware.
  volts sag below ~4.7    the cable and the hub, not the board.

So the frames have to already be on disk before the failure, because the failure
is the thing that ends the session. Each JPEG is named for the second it was
taken, which is the same wall clock usb_watch.py stamps its lines with: read the
outage time out of the log and `ls` for it.

    ls /tmp/meter_cam | grep 0617        # around 06:17

THE CROP GOES STALE. The default box below is where the display sat on
2026-08-16. Nudge the camera, the desk, or the meter and it is pointing at
woodgrain - and a directory full of woodgrain looks exactly like a directory full
of evidence until you open one. Run --aim after anything moves; it writes the
whole frame next to the crop so you can see both.

Run it beside the other two, not instead of them:

    uv run host/usb_watch.py --out /tmp/usb_watch.log &
    uv run host/meter_cam.py --out /tmp/meter_cam &
    uv run host/demo.py --frames 3000 ... | tee /tmp/soak.log
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Where the meter's display was on 2026-08-16, as WIDTH:HEIGHT:X:Y in a 1920x1080
# frame from the StreamCam. See THE CROP GOES STALE above.
DEFAULT_CROP = "460:200:470:460"
DEFAULT_SIZE = "1920x1080"

# avfoundation hands out the first frames before exposure and focus have settled,
# so the opening JPEGs are dark and soft. Not worth special-casing in the long
# run - it is a few seconds out of hours - but --aim would otherwise show you a
# blur and have you re-aim a camera that was pointed correctly all along.
AIM_WARMUP_FRAMES = 120


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg not found. brew install ffmpeg")
    return exe


def capture_args(dev: str, size: str) -> list[str]:
    return [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "avfoundation", "-framerate", "30", "-video_size", size,
            "-i", dev]


def aim(dev: str, size: str, crop: str, zoom: int) -> int:
    """One full frame and one cropped frame, so you can check what it is seeing."""
    w, h = crop.split(":", maxsplit=1)[0], crop.split(":")[1]
    full, close = Path("/tmp/meter_aim_full.jpg"), Path("/tmp/meter_aim_crop.jpg")
    for path, vf in ((full, "null"),
                     (close, (f"crop={crop},"
                             f"scale={int(w) * zoom}:{int(h) * zoom}:flags=lanczos"))):
        r = subprocess.run(capture_args(dev, size)
                           + ["-vf", vf, "-frames:v", str(AIM_WARMUP_FRAMES),
                              "-update", "1", str(path)],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            sys.exit(f"ffmpeg failed on {path.name}:\n{r.stderr.strip()}")
    print(f"whole frame  {full}\ncrop         {close}   (crop={crop})")
    print("If the crop is not the display, open the whole frame, read the pixel "
          "coordinates of the display off it, and pass --crop W:H:X:Y.")
    return 0


def prune(out: Path, keep: int) -> None:
    shots = sorted(out.glob("*.jpg"))
    for old in shots[:-keep] if keep else []:
        old.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", default="0",
                    help="avfoundation index; `ffmpeg -f avfoundation "
                         "-list_devices true -i \"\"` lists them (default 0)")
    ap.add_argument("--crop", default=DEFAULT_CROP, metavar="W:H:X:Y",
                    help=f"the meter's display in the frame (default {DEFAULT_CROP})")
    ap.add_argument("--size", default=DEFAULT_SIZE,
                    help=f"capture resolution (default {DEFAULT_SIZE})")
    ap.add_argument("--zoom", type=int, default=3,
                    help="enlarge the crop by this much before writing; the "
                         "digits are small and JPEG is unkind to them (default 3, "
                         "which reads cleanly on the StreamCam at 1080p)")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="seconds between frames (default 2.0)")
    ap.add_argument("--keep", type=int, default=5400,
                    help="frames to keep, oldest deleted first; 5400 at the "
                         "default interval is three hours, about 180 MB at the "
                         "default zoom (0 to keep them all)")
    ap.add_argument("--out", type=Path, default=Path("/tmp/meter_cam"),
                    help="directory for the JPEGs (default /tmp/meter_cam)")
    ap.add_argument("--aim", action="store_true",
                    help="grab one frame and one crop, print where they are, "
                         "and exit. Do this before every soak.")
    args = ap.parse_args()

    if args.aim:
        return aim(args.device, args.size, args.crop, args.zoom)

    w, h = args.crop.split(":")[0], args.crop.split(":")[1]
    args.out.mkdir(parents=True, exist_ok=True)

    cmd = capture_args(args.device, args.size) + [
        "-vf", (f"crop={args.crop},"
               f"scale={int(w) * args.zoom}:{int(h) * args.zoom}:flags=lanczos"),
        "-r", f"1/{args.interval:g}",
        "-qscale:v", "7",
        "-f", "image2", "-strftime", "1",
        str(args.out / "%Y%m%d-%H%M%S.jpg"),
    ]
    print(f"[meter] {args.out}/  every {args.interval:g}s, keeping {args.keep or 'all'}",
          flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True)
    try:
        while proc.poll() is None:
            time.sleep(30)
            if args.keep:
                prune(args.out, args.keep)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # A camera that quietly stopped an hour in is the same failure as a crop
    # pointed at the desk: you find out when you go looking for the evidence.
    err = (proc.stderr.read() if proc.stderr else "").strip()
    if proc.returncode not in (0, -15, 255) or err:
        print(f"[meter] ffmpeg exit {proc.returncode}: {err or 'no message'}",
              file=sys.stderr)
    print(f"[meter] {len(list(args.out.glob('*.jpg')))} frames in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
