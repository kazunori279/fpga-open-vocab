// M2 bring-up firmware: configure the Trion T8, then measure the link.
//
// Two things have to be true before the number this prints means anything, and
// they are checked in order:
//   1. CDONE goes high  - the MCU owns the configuration path
//   2. errors == 0      - the link is actually carrying data
// A high throughput figure with a nonzero error count is not a result.

#include <stdio.h>

#include "hardware/clocks.h"
#include "pico/stdlib.h"

#include "fpga_config.h"
#include "forgix.h"
#include "link_test.h"
#include "qspi_park.h"   // #9, and see the call at the top of main()

int main(void)
{
    // #9: park U1's chip select before anything else can share the QSPI
    // bus with it. FIRST STATEMENT, and deliberately not a preinit hook -
    // qspi_park.c has the map addresses and the strap that bought them.
    fgx_qspi_park();

    // 150 MHz is the RP2350 default and the reference point for every rate in
    // the sweep: link_clk = sys_clk / (cycles_per_bit * clkdiv).
    set_sys_clock_khz(150000, true);
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== Forgix M2 link bring-up ===\n");
    printf("config     : %s\n",
           LINK_CFG == LINK_CFG_WIDE ? "WIDE (needs PIN2 <-> PIN17 jumper)"
                                     : "NARROW (no board modification)");
    printf("bitstream  : %s, %u bytes\n",
           fpga_bitstream_name, (unsigned)fpga_bitstream_len);

    fpga_config_pins_init();

    // Ascending sweep rather than one call to fpga_configure(). The lead-in
    // clock requirement is what stopped M2 for two bench sessions, and it was
    // found by inference - the vendor image carried a 256-byte Efinity header
    // and ours did not - not by measurement. Sweeping from zero costs about a
    // second and turns LEADIN_BYTES from a plausible constant into one the
    // board has agreed to, in the same flash that runs the sweep - written when
    // reflashing needed a physical PRG-GND strap and each flash had to earn its
    // keep, and kept because the sweep is still better evidence than a constant.
    static const size_t leadins[] = {0, 32, 64, 128, 256, 1024, 4096};
    int err = FPGA_ERR_NO_DONE;
    size_t used = 0;
    for (unsigned i = 0; i < sizeof leadins / sizeof leadins[0]; i++) {
        err = fpga_configure_leadin(fpga_bitstream, fpga_bitstream_len, leadins[i]);
        printf("configure  : lead-in %5u bytes (%6u clocks) -> %s\n",
               (unsigned)leadins[i], (unsigned)leadins[i] * 8, fpga_strerror(err));
        if (err == FPGA_OK) {
            used = leadins[i];
            break;
        }
    }
    printf("pins       : CDONE=%d nSTATUS=%d\n", fpga_done(), fpga_nstatus());

    if (err != FPGA_OK) {
        printf("\nstopping: the link test would measure nothing.\n");
        while (true) sleep_ms(1000);
    }
    printf("configured : minimum lead-in that worked is %u bytes; "
           "fpga_config.c defaults to 256\n", (unsigned)used);

    fpga_release_link_pins();
    link_test_init();

    while (true) {
        link_test_sweep();
        printf("\npress enter to repeat\n");
        while (getchar_timeout_us(1000000) == PICO_ERROR_TIMEOUT)
            ;
    }
}
