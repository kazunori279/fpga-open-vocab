// Park U1's chip select before anything can share the QSPI bus with it.
//
// This header exists so a target can call fgx_qspi_park() explicitly. Almost
// nothing should: linking qspi_park.c registers the same function as a
// runtime-init hook, so it has already run by the time main() is entered. See
// qspi_park.c for why the pin needs parking at all - that is issues #9, #16
// and #17, and it is the most expensive bug this project has had.

#ifndef FGX_QSPI_PARK_H
#define FGX_QSPI_PARK_H

// Drive PICO_PSRAM_CS_PIN high, deselecting the PSRAM. Idempotent, and safe to
// call from a target that links hardware_psram: the PSRAM's own runtime init
// runs later and takes the pin back with gpio_set_function().
void fgx_qspi_park(void);

#endif
