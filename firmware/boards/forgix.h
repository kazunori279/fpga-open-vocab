// Board definition for Adiuvo Forgix (RP2354A + Efinix Trion T8F49).
//
// Not a Raspberry Pi board, so nothing here comes from the SDK's board list.
// Values are from docs/pinmap.md, which was extracted from the vendor KiCad
// source rather than the datasheet-shaped PDF.

#ifndef _BOARDS_FORGIX_H
#define _BOARDS_FORGIX_H

#define PICO_RP2350A 1                              // 60-pin QFN, 30 GPIO

// U2 is an RP2354A: 2 MB of flash stacked in the package, not a separate part.
#define PICO_FLASH_SIZE_BYTES (2 * 1024 * 1024)

// U1 is an APS1604M, 16 Mbit = 2 MB, on the QMI second chip select. CS1 is
// GPIO0 on this board, which is also the SDK's default UART TX - harmless here
// because stdio goes over USB, but it is why the auto-detect skip list exists.
//
// The size is declared *and* auto-detected: declaring it lets the linker place
// __uninitialized_psram objects, detecting it means a missing or dead chip
// reports itself instead of turning into bus faults halfway through a run.
// Note the size has to be told to CMake as well, via pico_override_psram_size()
// - the board header reaches the compiler but not the linker script that
// defines the PSRAM region, so a header-only declaration links to a zero-length
// region and any __uninitialized_psram object "overflows PSRAM by its own size".
#define PICO_PSRAM_CS_PIN 0
#ifndef PICO_PSRAM_SIZE_BYTES
#define PICO_PSRAM_SIZE_BYTES (2 * 1024 * 1024)
#endif

// There is no user LED on the RP side - D1 belongs to the FPGA.
#define PICO_DEFAULT_LED_PIN_INVERTED 0

// The board has no crystal footprint for the RP; it uses the internal ROSC-
// trimmed XOSC path shared with the RP2354A package. Standard 12 MHz applies.
#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER 64

#endif
