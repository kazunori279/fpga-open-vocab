// Forgix pin assignments. Authority: docs/pinmap.md.
#ifndef FORGIX_H
#define FORGIX_H

// --- RP2354A <-> Trion T8F49, on board -------------------------------------
#define PIN_FPGA_CS       1   // G3  SS_N     SPI0 CSn during config
#define PIN_FPGA_CLK      2   // F3  CCK      SPI0 SCK   during config
#define PIN_FPGA_MOSI     3   // F2  CDI0     SPI0 TX    during config
#define PIN_FPGA_NRESET   4   // G4  CRESET_N dedicated, active low
#define PIN_FPGA_DONE     5   // F4  CDONE    dedicated, input
#define PIN_FPGA_NSTATUS  6   // A4  NSTATUS  input during config
#define PIN_FPGA_OSC_EN  19   // Y2 output enable, active high, 1M pull-down

// --- header pads -----------------------------------------------------------
#define PIN_HDR_PIN2     22   // pad 4  - jumper to pad 24 (PIN17 = FPGA B3)
#define PIN_HDR_PIN3     23   // pad 5

// CRESET_N and SS_N both carry 10k pull-DOWNS. SS_N low when CRESET_N is
// released is what selects passive SPI configuration, so this board is strapped
// passive in hardware and cannot self-boot: there is no config flash footprint
// and CSO is consumed by SW1.
#define FPGA_SPI          spi0
#define FPGA_SPI_HZ       (8 * 1000 * 1000)

#endif
