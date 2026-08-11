# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Probe a Forgix board over USB CDC and report the loader's state.

    uv run host/probe.py [/dev/cu.usbmodemXXXX]

Note: HELLO/STATUS report the *loader* state machine, not the FPGA pins. Only
an END reply carries CDONE/STATUS. A loader state of DONE means the last
programming attempt finished with CDONE asserted.
"""

import sys
import time

import serial

import forge

DEFAULT_DEV = "/dev/cu.usbmodem21201"


def attempt(dev: str, abort_first: bool) -> bool:
    with forge.open_port(dev, timeout=3.0) as port:
        if abort_first:
            # Resets a loader stuck mid-transfer; flush whatever it replies.
            port.write(forge.build(forge.ABORT, 0))
            port.flush()
            time.sleep(0.3)
            port.reset_input_buffer()

        ok = False
        for seq, cmd in enumerate((forge.HELLO, forge.STATUS)):
            name = forge.CMD_NAME[cmd]
            try:
                reply = forge.exchange(port, cmd, seq, raise_on_nack=False)
            except forge.ForgeError as e:
                print(f"  {name:7} -> {e}")
                continue
            print(f"  {name:7} -> {reply.describe()}")
            ok = True
        return ok


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DEV
    print(f"opening {dev}")
    for abort_first in (False, True):
        print(f"-- attempt (abort-first={abort_first})")
        try:
            if attempt(dev, abort_first):
                return 0
        except (serial.SerialException, OSError) as e:
            print(f"  port error: {e}")
        time.sleep(0.5)
    print("\nno response. The board has no reset button - unplug and replug USB.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
