// Passive x1 SPI configuration of the Trion T8F49.
//
// This replaces the vendor Forge Loader for M2, not because the loader is
// inadequate but because M2 has to repurpose GPIO1/2/3 as the data link the
// moment CDONE goes high. Owning the configuration path is a precondition for
// owning the pins afterwards, so M1b folds into M2 here.
//
// Mode 3 (CPOL=1, CPHA=1) at 8 MHz, matching the vendor firmware's
// board_config.h. MISO is not connected on this board.

#include <stdio.h>

#include "hardware/gpio.h"
#include "hardware/spi.h"
#include "pico/stdlib.h"

#include "fpga_config.h"
#include "forgix.h"

// Trion needs idle clocks after the image so the config engine can finish and
// release the user design. AN 006 asks for at least 100; the vendor loader
// sends considerably more, and clocks are free.
#define TRAILING_CLOCKS 2048

// And it needs them *before* the image too. AN 006 Figure 15 draws the CDI0
// waveform as "Header, D, D, D, ..." - the header Efinity prepends to its .hex
// is not decoration the config engine skips, it is the lead-in the part is
// clocked through before it starts matching the synchronization pattern.
//
// This cost two bench sessions to find, because the failure is silent and
// perfectly reproducible: the vendor's plasm_led.hex configured on the first
// try, the byte-identical payload with its 256-byte header removed never did,
// and neither did any bitstream from rtl/build.sh, which passes
// generate_header=off. The old code waited 100 us here - but an SPI master
// emits no clock while idle, so the part got time and no clocks.
//
// Supplying the lead-in here rather than relying on the header keeps
// configuration working whichever way the bitstream was generated. 256 bytes
// matches what Efinity's header happens to provide.
#define LEADIN_BYTES 256

void fpga_config_pins_init(void)
{
    gpio_init(PIN_FPGA_NRESET);
    gpio_set_dir(PIN_FPGA_NRESET, GPIO_OUT);
    gpio_put(PIN_FPGA_NRESET, 0);          // hold in reset

    gpio_init(PIN_FPGA_DONE);
    gpio_set_dir(PIN_FPGA_DONE, GPIO_IN);

    gpio_init(PIN_FPGA_NSTATUS);
    gpio_set_dir(PIN_FPGA_NSTATUS, GPIO_IN);

    gpio_init(PIN_FPGA_OSC_EN);
    gpio_set_dir(PIN_FPGA_OSC_EN, GPIO_OUT);
    gpio_put(PIN_FPGA_OSC_EN, 0);
}

bool fpga_done(void)    { return gpio_get(PIN_FPGA_DONE); }
bool fpga_nstatus(void) { return gpio_get(PIN_FPGA_NSTATUS); }

// Hands GPIO1/2/3 back from PIO (or from a previous run) to SPI0.
static void claim_spi_pins(void)
{
    spi_init(FPGA_SPI, FPGA_SPI_HZ);
    spi_set_format(FPGA_SPI, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
    gpio_set_function(PIN_FPGA_CLK,  GPIO_FUNC_SPI);
    gpio_set_function(PIN_FPGA_MOSI, GPIO_FUNC_SPI);

    // CS is driven by hand: SS_N has to be low *before* CRESET_N is released,
    // because that is what selects passive mode, and a hardware CS would only
    // assert once the first transfer started.
    gpio_init(PIN_FPGA_CS);
    gpio_set_dir(PIN_FPGA_CS, GPIO_OUT);
    gpio_put(PIN_FPGA_CS, 1);
}

// Clocks `n` bytes of zeros out with CS asserted. Nothing reads the data; the
// point is the CCK edges.
static void clock_zeros(size_t n)
{
    static const uint8_t zeros[64] = {0};
    while (n) {
        size_t chunk = n < sizeof zeros ? n : sizeof zeros;
        spi_write_blocking(FPGA_SPI, zeros, chunk);
        n -= chunk;
    }
}

int fpga_configure(const uint8_t *image, size_t len)
{
    return fpga_configure_leadin(image, len, LEADIN_BYTES);
}

int fpga_configure_leadin(const uint8_t *image, size_t len, size_t leadin)
{
    claim_spi_pins();

    // The oscillator is off out of reset (1 M pull-down on OSC_EN), and a T8
    // design with no clock is indistinguishable from a T8 that failed to
    // configure. Turn it on first and give it time to start.
    gpio_put(PIN_FPGA_OSC_EN, 1);
    sleep_ms(2);

    gpio_put(PIN_FPGA_NRESET, 0);
    gpio_put(PIN_FPGA_CS, 0);              // passive mode strap
    sleep_us(500);
    gpio_put(PIN_FPGA_NRESET, 1);          // sampled here: SS_N low, CBUS = 111

    // nSTATUS is supposed to release once the device has accepted the mode and
    // is ready for data, and this used to be a timeout loop that returned
    // FPGA_ERR_NSTATUS. Pin-probing the board showed nSTATUS is driven high
    // externally and never dips, even with the part held in reset, so the loop
    // exited on its first iteration every time and proved nothing. Keep the
    // read, drop the pretence: it is a diagnostic, not a gate.
    bool nstatus = gpio_get(PIN_FPGA_NSTATUS);
    (void)nstatus;

    // tDMIN: the part needs to be clocked for a while after CRESET_N before it
    // starts matching the synchronization pattern. See LEADIN_BYTES above.
    sleep_us(100);
    clock_zeros(leadin);

    spi_write_blocking(FPGA_SPI, image, len);

    // Trailing clocks. CDONE rises within a microsecond or so of the last real
    // byte on a good image (73 us measured on the vendor bitstream, which is
    // well inside this), but keep clocking while polling rather than reading
    // once: a single immediate read cannot tell "not configured" from "needed
    // one more microsecond", and that ambiguity cost a bench session.
    int rc = FPGA_ERR_NO_DONE;
    for (int i = 0; i < TRAILING_CLOCKS / 8 / 64; i++) {
        clock_zeros(64);
        if (gpio_get(PIN_FPGA_DONE)) {
            rc = FPGA_OK;
            break;
        }
    }

    if (rc == FPGA_OK) {
        // CDONE is high, so the user design owns its pins from this instant -
        // and in configuration A that includes SS_N, which link_narrow drives.
        // Stop driving it here rather than in fpga_release_link_pins(), which
        // the caller reaches some microseconds later: raising CS would be worse
        // than leaving it low, and both are two push-pull drivers on one net.
        gpio_set_dir(PIN_FPGA_CS, GPIO_IN);
    } else {
        gpio_put(PIN_FPGA_CS, 1);
    }

    return rc;
}

void fpga_release_link_pins(void)
{
    // Drop SPI0 off GPIO1/2/3 so PIO can claim them. SS_N (GPIO1) becomes an
    // input in configuration A - the FPGA drives it now - so it must not be
    // left as a push-pull output or the two chips fight.
    gpio_set_function(PIN_FPGA_CLK,  GPIO_FUNC_SIO);
    gpio_set_function(PIN_FPGA_MOSI, GPIO_FUNC_SIO);
    gpio_set_function(PIN_FPGA_CS,   GPIO_FUNC_SIO);
    gpio_set_dir(PIN_FPGA_CS, GPIO_IN);
}

const char *fpga_strerror(int err)
{
    switch (err) {
    case FPGA_OK:           return "configured, CDONE high";
    case FPGA_ERR_NSTATUS:  return "nSTATUS never released - check CRESET_N/SS_N strapping";
    case FPGA_ERR_NO_DONE:  return "image sent but CDONE stayed low - bad bitstream or wrong device";
    default:                return "unknown";
    }
}
