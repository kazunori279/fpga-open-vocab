// PARK THE PSRAM'S CHIP SELECT. THIS IS ISSUE #9, AND IT IS WHY THIS FILE EXISTS.
//
// GPIO0 is U1's chip select - the APS1604M PSRAM sharing the RP2354A's QSPI bus
// with the in-package flash, on QMI CS1 (docs/pinmap.md). CS is active low,
// PADS_BANK0_GPIO0_RESET is 0x116 so the pad comes out of reset with PDE set and
// PUE clear, and a target that does not link hardware_psram has no .psram_load
// or .psram_noload to place - so the QMI never takes the pin and nothing else in
// the tree touches it either: the firmware's pins start at GPIO1. The pull-down
// therefore holds U1 SELECTED for the entire run, which is orders of magnitude
// past the part's ~8 us tCEM, watching every read the flash answers and free to
// decide one of them was addressed to it and drive SD0..3 back.
//
// That is #9, and 2026-08-16 caught it in the act. At the outage the flash stops
// answering: `picotool info` says "Program Information: none", three reads of the
// same 4 KB come back as three different high-entropy strings, and
// `picotool verify` fails - then a VBUS cycle, and the SAME flash verifies OK
// against the SAME image. Nothing was ever corrupted; the bus was jammed, and
// only removing the 5 V clears it, because only that power cycles U1. It also
// explains both shapes the outage takes, since XIP dies instantly (D1 goes dark
// mid-frame) and the watchdog's reboot 8 s later hands a bootrom that cannot read
// the image either: 280 MHz frame 1554 fell through to USB boot, 150 MHz frame
// 1478 did the same, and the run before that never got its pull-up back up at all.
//
// Two clocks, 76 frames apart, is also the answer to whether this was the clock:
// it is not. Nor is it the rail - the meter read 5.09 V and 0.16 A through the
// failure, with no sag and no spike. The earliest logs of it are in
// bench/soak/, at 150 MHz, from 2026-08-15.
//
// WHY THIS IS A SHARED FILE AND A PREINIT HOOK, NOT THREE LINES IN main() (#17).
//
// It was three lines in m9's main() from 8daa66b until #17. That fixed the one
// target anybody was running and left every other one exposed - m2, m5b, m6, m7,
// m8, cam_probe, diag - which is a hole that costs an afternoon the first time
// somebody runs a long clock ladder out of m7 and gets a "corrupt" flash that
// verifies fine after a power cycle. The failure took ~1,500 frames (~10 minutes)
// to bite, so a bring-up image that runs for two minutes hides it rather than
// avoiding it.
//
// So: one translation unit, listed in every target, registering the park through
// the SDK's preinit array. A new target gets it by being built the same way as
// the others, and forgetting to call something is no longer possible - there is
// nothing to call.
//
// Driving the pin high rather than linking hardware_psram stays deliberate.
// Nothing but m5 and psram_probe has any use for the 2 MB, and
// psram_detect_size() returns 0 on this board for reasons docs/pinmap.md still
// calls unexplained, so initialising a part we do not need would buy a new way to
// fail. Targets that DO want it are unaffected: hardware_psram's own
// runtime_init_setup_psram() sits at priority "11080", long after this one, and
// takes the pin back with gpio_set_function(..., GPIO_FUNC_XIP_CS1).
//
// PICO_RUNTIME_INIT_POST_CLOCK_RESETS is "00600" and is the last slot that
// releases peripherals from reset, so "00601" is the earliest point at which
// writing PADS_BANK0 and IO_BANK0 does anything at all. Earlier is not better,
// it is a no-op. This still cannot be first in absolute terms - the code is
// itself running from XIP, so the window between the pad leaving isolation and
// this hook is unavoidable - but it takes the exposure from a whole run down to
// the first few hundred microseconds of boot, and it is now earlier than main().
//
// The three lines below are byte-for-byte the sequence that produced 5 x 3000
// clean frames on 2026-08-16 after 4 of 5 runs had died at frames 687-1987.
// Value before direction, so the pin never drives low on its way up. The pad's
// pull-down is deliberately left alone: the output driver wins against it, and
// the point of copying a proven sequence is not to improve it.

#include "pico/runtime_init.h"
#include "hardware/gpio.h"

#include "qspi_park.h"

#ifndef PICO_PSRAM_CS_PIN
#error "PICO_PSRAM_CS_PIN is not defined - check that PICO_BOARD=forgix and boards/forgix.h is on the header path"
#endif

void fgx_qspi_park(void)
{
    gpio_init(PICO_PSRAM_CS_PIN);
    gpio_put(PICO_PSRAM_CS_PIN, 1);
    gpio_set_dir(PICO_PSRAM_CS_PIN, GPIO_OUT);
}

// THE HOOK IS A BUILD OPTION BECAUSE THE HOOK HAS NEVER BOOTED (#17, re-opened).
//
// Nothing built with the registration below has ever run on this board. It went
// in on 2026-08-17 16:44 and the only image ever linked against it, build-320,
// was never flashed; the appliance has been running the 2026-08-16 image, which
// still had the three lines at the top of m9's main(), for every bench since.
// On 2026-08-20 a 280 MHz image carrying the hook was flashed for the first
// time and the board did not enumerate at all - port `power` with no `connect`,
// through two VBUS cycles and a twelve-second one - i.e. it wedged before USB
// existed, which is the one failure mode this firmware is arranged to make
// impossible (see main()). That cost a PRG-GND strap.
//
// The three GPIO lines are not the suspect: they are byte-for-byte what ran at
// the top of main() for 15,008 clean frames, which is also pre-USB. What is new
// is the slot. So the slot is what this flag turns off, and m9 calls the
// function explicitly - the placement that is known to work - so that a
// -DFGX_QSPI_PARK_PREINIT=0 image differs from the proven one in exactly the
// registration and nothing else. Default 1 preserves what is committed until
// the experiment says which way to jump; do not raise it to a shipping default
// again without a boot on hardware behind it.
#ifndef FGX_QSPI_PARK_PREINIT
#define FGX_QSPI_PARK_PREINIT 1
#endif

#if FGX_QSPI_PARK_PREINIT
PICO_RUNTIME_INIT_FUNC_HW(fgx_qspi_park, "00601");
#endif
