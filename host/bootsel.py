#!/usr/bin/env python3
"""Get the board back, from any state, without a human at the desk.

    uv run host/bootsel.py
    uv run host/bootsel.py --flash firmware/build/forgix_m9.uf2
    uv run host/bootsel.py --power-cycle          # skip straight to the hammer

THERE ARE TWO WEDGES AND THEY NEED DIFFERENT TOOLS. Both were hit in one
session, which is the only reason this file knows the difference.

  soft - the board is at the bitstream prompt printing a dot a second. It looks
         dead because it is waiting for a host that has not spoken.
         `picotool reboot -u` reports success while nothing happens; the
         1200-baud DTR touch works, because the pico-sdk handles it in the USB
         line-coding callback, in interrupt context, so it does not need the
         application loop to be sane. m7.py --bootsel has done this since M7h;
         this is the general version.

         'B' ALSO WORKS HERE NOW (issue #3, 2026-08-15). It did not until then:
         ft_recv_bitstream() swallowed stdin while it waited, so the one prompt
         a board lands at after any host-side abort was the one prompt whose
         hotkey was eaten. Fixed in firmware/frame.c, behind two guards - 'B' is
         the last byte of the "FGXB" magic, and a byte arriving inside a stream
         is data rather than a keypress. The order below does not change: 'B'
         then the touch, and the touch stays because it is the path that works
         on a board flashed before the fix.

  hard - the board is SILENT. Still enumerated, still a /dev/cu.usbmodem*, but
         not one byte comes out and neither 'B' nor the touch lands, because the
         USB stack itself has stopped being serviced. Nothing over the wire can
         fix this. It needs the power removed.

WHICH IS POSSIBLE HERE, WHICH WAS THE SURPRISE. The Apple internal hub on this
Mac reports `ppps` - per-port power switching - so uhubctl can drop VBUS on the
board's port alone and leave whatever is on the neighbouring port up:

    hub 2-1 [05ac:800b Apple USB2 Hub, 2 ports, ppps]
      Port 1: [2e8a:0009]                      <- Forgix
      Port 2: whatever else is on the desk

AND THIS IS THE ONLY PLACE ON THIS MAC WHERE THAT IS TRUE. `hub 2-1` is the
Mac mini's internal 2-port USB2 hub, in front of both front-panel connectors;
every other port belongs to a root port that uhubctl cannot switch. Moving the
board to a rear port to get it away from a noisy neighbour would therefore
trade the neighbour for the recovery, which is a bad trade - move the neighbour
instead. `host/usb_watch.py` logs this port's status for the same reason.

That is a real power cycle, so the T8 comes back UNCONFIGURED - every host
script re-sends the bitstream anyway, so this costs nothing but the download.

Order matters: try the cheap cooperative paths first and only then cut power,
because a power cycle also drops whatever the FPGA was holding.

THERE IS A THIRD WEDGE AND IT IS IN THE FLASH PATH, not the board: the BOOTSEL
mass-storage volume can go stale while staying in the mount table, and then
every access to it blocks in the kernel - `cp` hangs uninterruptibly, `ls
/Volumes/RP2350` hangs, `diskutil unmount force` hangs. Asking what state the
board is in is itself what hangs, which is what makes it confusing. --flash
therefore goes through picotool (PICOBOOT, no filesystem) and only falls back to
the copy if picotool is missing. If you have already wedged the volume, uhubctl
is the way out; nothing in userspace will release it.
"""

import argparse
import contextlib
import re
import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

# /dev/cu.usbmodem* suffixes come from USB location ids and shuffle on replug,
# and this desk has a Tiliqua and a Digilent cable on the same bus. A stale port
# number does not error - it opens the neighbour, which is silent, and the board
# then looks wedged. Identify by what the device is. (demo.py:110 says the same.)
RP2350_VID = "2E8A"
RP2350_PID = "0009"
BOOTSEL_VOL = Path("/Volumes/RP2350")

# Set once from --hub. A global rather than a parameter because every path that
# can end in a power cycle - the fallback in to_bootsel(), the retry in flash() -
# would otherwise have to carry it, and none of them has any other opinion.
HUB_OVERRIDE: str | None = None


def find_port() -> str | None:
    hits = [p for p in list_ports.comports()
            if f"VID:PID={RP2350_VID}" in (p.hwid or "").upper()]
    if len(hits) > 1:
        raise SystemExit("several RP2350s, pass --port: "
                         + ", ".join(p.device for p in hits))
    return hits[0].device if hits else None


def find_hub_port() -> tuple[str, str] | None:
    """Locate the board in uhubctl's tree as (hub, port), or None.

    Matched on the VID alone. It used to want `2e8a:0009`, the application's
    PID, which meant the one tool for a board in BOOTSEL could not find a board
    in BOOTSEL - it enumerates as `2e8a:000f` - so `--power-cycle` answered "no
    power-switchable port found" for a board sitting right there on port 1.
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
            return hub, m.group(1)
    return None


def power_cycle(where: str | None = None) -> bool:
    where = where or HUB_OVERRIDE
    # AND THE CASE THAT NEEDS THIS MOST IS THE ONE WHERE THE BOARD IS NOT IN THE
    # TREE AT ALL. Issue #9's outage ends with nothing on the port - uhubctl
    # shows `power` and no `connect` - so there is nothing to search for, and
    # the only tool that can bring it back cannot find where it went. --hub
    # HUB:PORT is that answer. Do not write the number on the wall, though:
    # this desk read `2-1:1` until 2026-08-16 and reads `2-1:2` since, because
    # a neighbour was unplugged. Take the port showing `power` with no
    # `connect`, or ask board.note_where() while the board is still there.
    if where:
        hub, _, port = where.partition(":")
        loc: tuple[str, str] | None = (hub, port) if port else None
        if loc is None:
            print(f"  --hub wants HUB:PORT, not {where!r}", file=sys.stderr)
            return False
    else:
        loc = find_hub_port()
    if loc is None:
        print("  no power-switchable port found for the board (is uhubctl "
              "installed, and does the hub report ppps?). If the board has "
              "left the bus entirely there is nothing to find: pass --hub "
              "HUB:PORT.", file=sys.stderr)
        return False
    hub, port = loc
    print(f"  power-cycling hub {hub} port {port}")
    try:
        subprocess.run(["uhubctl", "-l", hub, "-p", port, "-a", "cycle"],
                       capture_output=True, text=True, timeout=60, check=True)
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"  uhubctl failed: {e}", file=sys.stderr)
        return False
    for _ in range(40):
        time.sleep(0.5)
        if find_port() or BOOTSEL_VOL.is_dir():
            return True
    return False


def eject_bootsel() -> None:
    """Unmount /Volumes/RP2350 before the board takes it away.

    Every reboot out of BOOTSEL yanks a mounted FAT volume out from under
    macOS. The visible cost is a "Disk Not Ejected Properly" notification every
    single time, which after a day of flashing is most of what the notification
    centre contains. The invisible cost is the one worth fixing: the log says

        diskarbitrationd: added volume id = ... /Volumes/RP2350/ to
                          danglingVolumeList
        com.apple.fskit.msdos: Failed to clean dirty bit, error ... Code=5

    and a dangling volume with an unclean dirty bit is the same stale-mount
    state the third wedge above is about. So this is hygiene, not cosmetics.

    Best effort on purpose. If the volume is not there, or is already the wedged
    kind where every access blocks in the kernel, rebooting is still the right
    next move - the timeout is there so this cannot become a fourth way to hang.
    """
    if not BOOTSEL_VOL.is_dir():
        return
    with contextlib.suppress(subprocess.SubprocessError, FileNotFoundError):
        subprocess.run(["diskutil", "unmount", str(BOOTSEL_VOL)],
                       capture_output=True, text=True, timeout=15, check=False)


def reboot_to_app() -> None:
    """picotool reboot, with the volume put away first. Always use this."""
    eject_bootsel()
    subprocess.run(["picotool", "reboot"], capture_output=True, timeout=30,
                   check=False)


def nudge(port: str) -> None:
    """'B' then the 1200-baud touch. m7.py:129 found this order the right way
    round: 'B' is instant when the board is parked and listening, and the touch
    is the one that survives a run loop that has gone deaf."""
    try:
        with serial.Serial(port, 115200, timeout=0.5) as s:
            s.dtr = True
            time.sleep(0.2)          # the board only reads stdin once CDC is up
            s.write(b"B")
            s.flush()
            time.sleep(0.5)
    except (serial.SerialException, OSError):
        pass                         # the port vanishing is the success case
    try:
        with serial.Serial(port, 1200) as s:
            s.dtr = False
            time.sleep(0.3)
    except (serial.SerialException, OSError):
        pass


def to_bootsel(port: str | None, budget: float = 40.0) -> bool:
    if BOOTSEL_VOL.is_dir():
        print(f"already in BOOTSEL ({BOOTSEL_VOL})")
        return True
    if port:
        print(f"nudging {port} ('B', then the 1200-baud touch)")
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            nudge(port)
            for _ in range(20):
                if BOOTSEL_VOL.is_dir():
                    print(f"BOOTSEL up at {BOOTSEL_VOL}")
                    return True
                time.sleep(0.25)
        print("  no answer over the wire - the board is in the silent wedge")
    else:
        print("no CDC port for the board at all")

    print("falling back to a power cycle")
    if not power_cycle():
        return False
    # Power-on lands in the application, not the bootloader, so the cheap path
    # has to run again - but now against a board that is definitely listening.
    port = find_port()
    if BOOTSEL_VOL.is_dir():
        return True
    if not port:
        return False
    print(f"  back at {port}, nudging again")
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        nudge(port)
        for _ in range(20):
            if BOOTSEL_VOL.is_dir():
                print(f"BOOTSEL up at {BOOTSEL_VOL}")
                return True
            time.sleep(0.25)
    return False


def flash(image: Path) -> bool:
    """Write the image and prove it landed, power-cycling once if it did not.

    THE WRITE CAN DO NOTHING AND SAY NOTHING. Twice in one session `picotool
    load` returned 0 after 2.5 s - a 2.1 MB image takes ~15 s - and left the
    board in BOOTSEL with `Program Information: none`. Both times the BOOTSEL
    had been entered from an odd state (a `reset_usb_boot()` with a download in
    flight), and both times a VBUS cycle followed by the same command wrote in
    14.8 s and verified. A silent no-op flash is the worst outcome available
    here: the board comes back running the OLD image, so the next run measures
    the previous build and nothing says so.

    So: verify always, and treat a failed verify as a retry rather than an
    error, because the retry is the thing that works. This is also why the
    command is `load` and not `load -x` - issue #3 records `-x` leaving the
    board in BOOTSEL with no program - and why the reboot is explicit.
    """
    for attempt in (1, 2):
        t0 = time.monotonic()
        try:
            r = subprocess.run(["picotool", "load", "-f", str(image)],
                               capture_output=True, text=True, timeout=300,
                               check=False)
        except FileNotFoundError:
            print("  no picotool; falling back to the mass-storage copy")
            r = subprocess.run(["cp", "-X", str(image),
                                str(BOOTSEL_VOL / image.name)],
                               capture_output=True, text=True, timeout=180,
                               check=False)
            print(f"  wrote in {time.monotonic() - t0:.1f} s (unverified - the "
                  f"copy path cannot check itself)")
            return r.returncode == 0
        if r.returncode != 0:
            print(f"FAIL - flash failed: {(r.stderr or r.stdout).strip()}",
                  file=sys.stderr)
            return False
        secs = time.monotonic() - t0
        v = subprocess.run(["picotool", "verify", str(image)],
                           capture_output=True, text=True, timeout=300,
                           check=False)
        if v.returncode == 0:
            print(f"  wrote in {secs:.1f} s, verified")
            reboot_to_app()
            return True
        print(f"  wrote in {secs:.1f} s and the readback DID NOT MATCH - the "
              f"board is still running the old image")
        if attempt == 2:
            print("FAIL - two writes, neither verified. This one needs hands.",
                  file=sys.stderr)
            return False
        print("  power-cycling and writing again")
        if not power_cycle() or not to_bootsel(find_port()):
            return False
    return False


def wait_cdc(timeout: float = 25.0) -> str | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        dev = find_port()
        if dev:
            return dev
        time.sleep(0.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", help="override the VID-based port pick")
    ap.add_argument("--flash", type=Path,
                    help="flash this .uf2 once BOOTSEL comes up, then run it")
    ap.add_argument("--power-cycle", action="store_true",
                    help="cut VBUS first instead of trying the wire")
    ap.add_argument("--run", action="store_true",
                    help="leave the board in the application, not BOOTSEL")
    ap.add_argument("--hub", metavar="HUB:PORT",
                    help="where the board is plugged in, for when it has left "
                         "the bus and cannot be found by VID. `uhubctl` with "
                         "no arguments lists them; take the port showing "
                         "`power` with no `connect`")
    args = ap.parse_args()

    global HUB_OVERRIDE  # noqa: PLW0603  - --hub has to reach the recovery
    # paths, which are called from library entry points that never see argv.
    HUB_OVERRIDE = args.hub

    if args.power_cycle:
        print("power cycle requested")
        if not power_cycle():
            return 1
        if args.run and not args.flash:
            dev = wait_cdc()
            print(f"running at {dev}" if dev else "FAIL - no CDC port back")
            return 0 if dev else 1

    if not to_bootsel(args.port or find_port()):
        print("FAIL - could not reach BOOTSEL, even with the power cycle. "
              "This one really does need hands.", file=sys.stderr)
        return 1

    if args.flash:
        if not args.flash.is_file():
            print(f"FAIL - no such image: {args.flash}", file=sys.stderr)
            return 1
        # picotool, NOT a copy to /Volumes/RP2350. The drag-and-drop volume is
        # the documented way and it is the one that strands you: when the
        # mass-storage side wedges, the mount goes stale but stays in the mount
        # table, and then every access blocks in the kernel - `cp` hangs
        # uninterruptibly, and so does a bare `ls` of the mount point, so even
        # *asking* what state the board is in hangs. `diskutil unmount force`
        # hangs too. That is how one bad flash costs a power cycle plus a
        # confused ten minutes.
        #
        # picotool speaks PICOBOOT to the USB interface directly, so no
        # filesystem is involved and none of the above can happen. flash()
        # below reboots into the application explicitly, which the copy path
        # only does as a side effect of the volume ejecting.
        #
        # The copy is kept as a fallback for a host with no picotool. Not a bare
        # `cp`: plain `cp` to a FAT volume with no xattr support writes the file
        # correctly and *then* exits 1 with "could not copy extended attributes
        # ... Attribute not found", so its status is unusable; -X skips the
        # xattrs and makes it meaningful again. shutil.copyfile is the obvious
        # choice and fails with EPERM under a sandboxed interpreter that cp(1)
        # is not subject to.
        print(f"flashing {args.flash.name} ({args.flash.stat().st_size} B) ...")
        if not flash(args.flash):
            return 1
    elif args.run:
        reboot_to_app()

    if args.flash or args.run:
        dev = wait_cdc()
        if dev is None:
            print("FAIL - the board did not come back as a CDC port",
                  file=sys.stderr)
            return 1
        print(f"back up at {dev}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
