// Park U1's chip select before anything can share the QSPI bus with it.
//
// EVERY target that links qspi_park.c must call fgx_qspi_park() as the first
// statement of its main(). There is no hook and there cannot be one: the SDK
// compiles these three GPIO calls into coprocessor instructions that fault if
// they run before the per-core initializers, which is every numeric preinit
// priority. That was tried, and it cost a strap - qspi_park.c has the map
// addresses. m5 and psram_probe are the exceptions: they want the part, link
// hardware_psram, and do not link this file.
//
// See qspi_park.c for why the pin needs parking at all - that is issues #9, #16
// and #17, and it is the most expensive bug this project has had.

#ifndef FGX_QSPI_PARK_H
#define FGX_QSPI_PARK_H

// Drive PICO_PSRAM_CS_PIN high, deselecting the PSRAM. Idempotent, and the
// first statement of main(). NOT safe from a target that links hardware_psram:
// runtime_init_setup_psram() has already claimed the pin by then, and this would
// take it back off the QMI.
void fgx_qspi_park(void);

#endif
