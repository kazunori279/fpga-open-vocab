#!/usr/bin/env python3
"""Get the board back, from any state, without a human at the desk.

    uv run host/bootsel.py
    uv run host/bootsel.py --flash firmware/build/forgix_m9.uf2
    uv run host/bootsel.py --power-cycle          # skip straight to the hammer

THERE ARE TWO WEDGES AND THEY NEED DIFFERENT TOOLS. Both were hit in one
session, which is the only reason this file knows the difference.

  soft - the board is at the bitstream prompt printing a dot a second. It looks
         dead because it is waiting for a host that has not spoken, and
         ft_recv_bitstream() swallows stdin while it waits, so the 'B' hotkey
         that every other prompt honours is eaten here and `picotool reboot -u`
         reports success while nothing happens. The 1200-baud DTR touch works:
         the pico-sdk handles it in the USB line-coding callback, in interrupt
         context, so it does not need the application loop to be sane.
         m7.py --bootsel has done this since M7h; this is the general version.

  hard - the board is SILENT. Still enumerated, still a /dev/cu.usbmodem*, but
         not one byte comes out and neither 'B' nor the touch lands, because the
         USB stack itself has stopped being serviced. Nothing over the wire can
         fix this. It needs the power removed.

WHICH IS POSSIBLE HERE, WHICH WAS THE SURPRISE. The Apple internal hub on this
Mac reports `ppps` - per-port power switching - so uhubctl can drop VBUS on the
board's port alone and leave the Digilent cable on the neighbouring port up:

    hub 2-1 [05ac:800b Apple USB2 Hub, 2 ports, ppps]
      Port 1: [2e8a:0009]                      <- Forgix
      Port 2: [0403:6010 Digilent USB Device]

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


def find_port() -> str | None:
    hits = [p for p in list_ports.comports()
            if f"VID:PID={RP2350_VID}" in (p.hwid or "").upper()]
    if len(hits) > 1:
        raise SystemExit("several RP2350s, pass --port: "
                         + ", ".join(p.device for p in hits))
    return hits[0].device if hits else None


def find_hub_port() -> tuple[str, str] | None:
    """Locate the board in uhubctl's tree as (hub, port), or None."""
    try:
        out = subprocess.run(["uhubctl"], capture_output=True, text=True,
                             timeout=20).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    hub = None
    for line in out.splitlines():
        m = re.match(r"\s*Current status for hub (\S+)", line)
        if m:
            hub = m.group(1)
            continue
        m = re.match(r"\s*Port (\d+):", line)
        if m and hub and f"{RP2350_VID.lower()}:{RP2350_PID}" in line.lower():
            return hub, m.group(1)
    return None


def power_cycle() -> bool:
    loc = find_hub_port()
    if loc is None:
        print("  no power-switchable port found for the board (is uhubctl "
              "installed, and does the hub report ppps?)", file=sys.stderr)
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
    args = ap.parse_args()

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
        # filesystem is involved and none of the above can happen. -x starts the
        # application afterwards, which the copy path only does as a side effect
        # of the volume ejecting.
        #
        # The copy is kept as a fallback for a host with no picotool. Not a bare
        # `cp`: plain `cp` to a FAT volume with no xattr support writes the file
        # correctly and *then* exits 1 with "could not copy extended attributes
        # ... Attribute not found", so its status is unusable; -X skips the
        # xattrs and makes it meaningful again. shutil.copyfile is the obvious
        # choice and fails with EPERM under a sandboxed interpreter that cp(1)
        # is not subject to.
        print(f"flashing {args.flash.name} ({args.flash.stat().st_size} B) ...")
        t0 = time.monotonic()
        try:
            r = subprocess.run(["picotool", "load", "-x", str(args.flash)],
                               capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            print("  no picotool; falling back to the mass-storage copy")
            r = subprocess.run(["cp", "-X", str(args.flash),
                                str(BOOTSEL_VOL / args.flash.name)],
                               capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"FAIL - flash failed: {(r.stderr or r.stdout).strip()}",
                  file=sys.stderr)
            return 1
        print(f"  wrote in {time.monotonic() - t0:.1f} s")
    elif args.run:
        subprocess.run(["picotool", "reboot"], capture_output=True, timeout=30)

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
