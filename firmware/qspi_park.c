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
// WHY THIS IS A SHARED FILE THAT EVERY TARGET CALLS, AND NOT A PREINIT HOOK.
//
// It was three lines in m9's main() from 8daa66b until #17. That fixed the one
// target anybody was running and left every other one exposed - m2, m5b, m6, m7,
// m8, cam_probe, diag - which is a hole that costs an afternoon the first time
// somebody runs a long clock ladder out of m7 and gets a "corrupt" flash that
// verifies fine after a power cycle. The failure took ~1,500 frames (~10 minutes)
// to bite, so a bring-up image that runs for two minutes hides it rather than
// avoiding it.
//
// So: one translation unit, and one call at the top of every main() that does
// not want the PSRAM. That is eight identical lines, which is worse than zero,
// and #17 was right to want zero. It got zero by registering the park in the
// SDK's preinit array at "00601", and that image bricked the board. Read the
// next block before trying it again.
//
// THE PREINIT ARRAY CANNOT CALL hardware_gpio ON THIS PLATFORM. NOT AT "00601",
// NOT AT ANY NUMERIC PRIORITY.
//
// On rp2350-arm-s the SDK compiles gpio_put(), gpio_set_dir() and gpio_init()
// into GPIO COPROCESSOR instructions - this file's three lines disassemble to
// `mcrr 0, 4, r3, r2, cr0` and `mcrr 0, 4, r3, r2, cr4`. Access to that
// coprocessor is off out of reset and is turned on by the SDK's own
// runtime_init_per_core_enable_coprocessors(), which is a PER-CORE initializer:
// it lands in `.preinit_array.ZZZZZ.00200`. The array is emitted with
// SORT_BY_NAME, "Z" sorts after every digit, and so EVERY per-core initializer
// runs after EVERY numeric one. In the map for the image that bricked the board:
//
//     0x1000f774  __pre_init_fgx_qspi_park                          (00601)
//     0x1000f798  __pre_init_runtime_init_per_core_enable_coprocessors (ZZZZZ.00200)
//
// Nine entries too early. An mcrr to a disabled coprocessor is a NOCP
// UsageFault, taken before stdio_init_all() has run, so the board never
// enumerates: `power` with no `connect`, two VBUS cycles and a twelve-second
// power-off all fail, and it comes back on a PRG-GND strap. 2026-08-20 spent one
// finding that out. The bring-up log has the session.
//
// This is not a thing to work around by moving the priority. A per-core slot
// after ZZZZZ.00200 would be legal but would also run after
// runtime_init_setup_psram() ("11080", numeric, therefore earlier), so on m5 and
// psram_probe it would take the pin straight back off the QMI - and it would run
// again on core 1. Hand-writing the SIO and PADS_BANK0 stores would dodge the
// coprocessor, but it would also stop this file being the proven sequence, which
// is the only property it has. main() runs after all of it, once, on core 0,
// with everything enabled. That is where the call belongs.
//
// Driving the pin high rather than linking hardware_psram stays deliberate.
// Nothing but m5 and psram_probe has any use for the 2 MB, and
// psram_detect_size() returns 0 on this board for reasons docs/pinmap.md still
// calls unexplained, so initialising a part we do not need would buy a new way to
// fail. Those two targets do not link this file and must not call it: their own
// runtime_init_setup_psram() has already claimed the pin with
// gpio_set_function(..., GPIO_FUNC_XIP_CS1) by the time main() starts.
//
// The cost of calling it from main() rather than before it is the window between
// the pad leaving isolation and the first line of main - a few milliseconds of
// XIP, against a whole run before #9 was found. The 08-16 image paid exactly that
// window for 15,008 clean frames.
//
// The three lines below are byte-for-byte the sequence that produced 5 x 3000
// clean frames on 2026-08-16 after 4 of 5 runs had died at frames 687-1987.
// Value before direction, so the pin never drives low on its way up. The pad's
// pull-down is deliberately left alone: the output driver wins against it, and
// the point of copying a proven sequence is not to improve it.

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
