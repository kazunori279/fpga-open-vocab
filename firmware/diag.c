// M2 configuration diagnostic - a ladder that names what stops the T8
// configuring, in one flash.
//
// Round 1 established that fpga_config.c is correct: the vendor's plasm_led
// image configures through it unchanged, CDONE rising 73 us after the last
// image byte. Our link_narrow.hex, same length and same device, does not. So
// the variable is the image, and the one structural difference is which balls
// the design claims: plasm_led uses only CDI1/CDI5/CDI7, which are idle in x1
// passive mode, while link_narrow reuses all four pins the configuration
// interface is actively driving - F3/CCK, F2/CDI0, G3/SS_N and A4/NSTATUS.
//
// Each rung below adds one more of those pins. The first rung that fails names
// the pin. If `vendor` passes and `probe_a` fails, no pin is to blame and the
// containerised Efinity 2026.1 is emitting images this part will not take; if
// `vendor` itself fails the run is void and something on the bench changed.
//
// The 1200-baud CDC reset path is linked but does not fire on this board, so
// for the first ten milestones reflashing cost a physical PRG-GND strap and the
// ladder was walked in full rather than stopping at the first success. M7i
// found `picotool reboot -f -u` works over the USB vendor interface, which
// makes iteration cheap again - but walking the whole ladder still costs
// nothing and still reports more, so it stays.

#include <stdio.h>

#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/spi.h"
#include "pico/stdlib.h"

#include "fpga_config.h"
#include "forgix.h"

#define DECLARE_IMAGE(sym)               \
    extern const uint8_t sym[];          \
    extern const size_t  sym##_len;      \
    extern const char   *sym##_name;

DECLARE_IMAGE(vendor)
DECLARE_IMAGE(probe_a)
DECLARE_IMAGE(probe_b)
DECLARE_IMAGE(probe_c)

// Efinity prepends a 256-byte ASCII header ("Version:", "Generated:", ...) that
// rtl/build.sh suppresses with generate_header=off, so only the vendor image
// carries one. Putting the stripped variant on the ladder rather than assuming
// it equivalent is what cracked this: it failed where the byte-identical
// payload with its header passed. The header is not pre-sync filler the config
// engine skips, it is the lead-in clocking the part needs - which is now
// fpga_config.c's job. Rungs below this one were all chasing the wrong thing.
#define VENDOR_HEADER_BYTES 256

#define CDONE_TIMEOUT_MS   300
#define NSTATUS_TIMEOUT_MS 100

struct attempt {
    const char *label;
    const uint8_t *image;
    size_t len;
    const char *claims;      // which active config pins the design reuses
};

struct result {
    bool nstatus_low_in_reset;
    int  nstatus_rise_us;    // -1 if it never rose after CRESET_N release
    int  cdone_rise_us;      // -1 if CDONE never rose
    bool cdone_final;
    bool nstatus_final;
};

// Reads a pin with an internal pull applied, to tell "driven" from "floating".
// A pin that follows whichever pull is enabled has nothing external on it, and
// any logic that waits on its level is waiting on nothing.
static bool read_with_pull(uint pin, bool up)
{
    gpio_set_dir(pin, GPIO_IN);
    if (up)
        gpio_pull_up(pin);
    else
        gpio_pull_down(pin);
    sleep_ms(2);
    bool v = gpio_get(pin);
    gpio_disable_pulls(pin);
    sleep_ms(1);
    return v;
}

static void probe_pin(const char *name, uint pin)
{
    bool up = read_with_pull(pin, true);
    bool down = read_with_pull(pin, false);
    const char *verdict = up == down ? (up ? "driven HIGH" : "driven LOW")
                                     : "FLOATING (follows the internal pull)";
    printf("  %-8s pull-up=%d pull-down=%d  -> %s\n", name, up, down, verdict);
}

// Hands GPIO1/2/3 back to the FPGA. Mandatory the moment CDONE rises on any
// design that drives G3: the MCU has been holding SS_N low push-pull for the
// whole of configuration, and link_narrow makes G3 an output. Two push-pull
// drivers on one net is a short, so release before printing anything.
static void release_link_pins(void)
{
    gpio_set_function(PIN_FPGA_CLK,  GPIO_FUNC_SIO);
    gpio_set_function(PIN_FPGA_MOSI, GPIO_FUNC_SIO);
    gpio_set_function(PIN_FPGA_CS,   GPIO_FUNC_SIO);
    gpio_set_dir(PIN_FPGA_CLK,  GPIO_IN);
    gpio_set_dir(PIN_FPGA_MOSI, GPIO_IN);
    gpio_set_dir(PIN_FPGA_CS,   GPIO_IN);
}

static void run(const struct attempt *a, struct result *r)
{
    spi_init(FPGA_SPI, FPGA_SPI_HZ);
    spi_set_format(FPGA_SPI, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
    gpio_set_function(PIN_FPGA_CLK,  GPIO_FUNC_SPI);
    gpio_set_function(PIN_FPGA_MOSI, GPIO_FUNC_SPI);
    gpio_set_function(PIN_FPGA_CS, GPIO_FUNC_SIO);
    gpio_set_dir(PIN_FPGA_CS, GPIO_OUT);
    gpio_put(PIN_FPGA_CS, 1);

    gpio_set_dir(PIN_FPGA_NRESET, GPIO_OUT);
    gpio_put(PIN_FPGA_OSC_EN, 1);
    sleep_ms(2);

    gpio_put(PIN_FPGA_NRESET, 0);
    gpio_put(PIN_FPGA_CS, 0);              // passive-mode strap
    // Sample across the whole reset window rather than once at the end: the
    // part may pull nSTATUS low only briefly while it clears configuration.
    r->nstatus_low_in_reset = false;
    for (int i = 0; i < 100; i++) {
        if (!gpio_get(PIN_FPGA_NSTATUS))
            r->nstatus_low_in_reset = true;
        sleep_us(5);
    }
    gpio_put(PIN_FPGA_NRESET, 1);

    absolute_time_t t0 = get_absolute_time();
    r->nstatus_rise_us = -1;
    while (absolute_time_diff_us(t0, get_absolute_time()) < NSTATUS_TIMEOUT_MS * 1000) {
        if (gpio_get(PIN_FPGA_NSTATUS)) {
            r->nstatus_rise_us = (int)absolute_time_diff_us(t0, get_absolute_time());
            break;
        }
    }
    sleep_us(100);

    spi_write_blocking(FPGA_SPI, a->image, a->len);

    // Keep clocking while waiting. The M2 firmware sent a fixed 2048 trailing
    // clocks and read CDONE exactly once, immediately - which cannot tell "not
    // configured" from "needed one more microsecond".
    static const uint8_t zeros[64] = {0};
    t0 = get_absolute_time();
    r->cdone_rise_us = -1;
    while (absolute_time_diff_us(t0, get_absolute_time()) < CDONE_TIMEOUT_MS * 1000) {
        if (gpio_get(PIN_FPGA_DONE)) {
            r->cdone_rise_us = (int)absolute_time_diff_us(t0, get_absolute_time());
            break;
        }
        spi_write_blocking(FPGA_SPI, zeros, sizeof zeros);
    }

    if (r->cdone_rise_us >= 0) {
        release_link_pins();
    } else {
        gpio_put(PIN_FPGA_CS, 1);
    }
    sleep_ms(1);
    r->cdone_final = gpio_get(PIN_FPGA_DONE);
    r->nstatus_final = gpio_get(PIN_FPGA_NSTATUS);
}

int main(void)
{
    set_sys_clock_khz(150000, true);
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    fpga_config_pins_init();

    printf("\n=== Forgix config ladder ===\n");
    printf("\npin probe (FPGA held in reset, before any bitstream):\n");
    probe_pin("CDONE", PIN_FPGA_DONE);
    probe_pin("nSTATUS", PIN_FPGA_NSTATUS);
    gpio_set_dir(PIN_FPGA_DONE, GPIO_IN);
    gpio_set_dir(PIN_FPGA_NSTATUS, GPIO_IN);

    const struct attempt ladder[] = {
        {"vendor plasm_led",       vendor,  vendor_len,  "none (control)"},
        {"vendor, header stripped",
         vendor + VENDOR_HEADER_BYTES, vendor_len - VENDOR_HEADER_BYTES, "none"},
        {"probe_a  LEDs only",     probe_a, probe_a_len, "none"},
        {"probe_b  + A4",          probe_b, probe_b_len, "NSTATUS"},
        {"probe_c  + F3,F2",       probe_c, probe_c_len, "NSTATUS CCK CDI0"},
        {"link_narrow  + G3",      fpga_bitstream, fpga_bitstream_len,
                                   "NSTATUS CCK CDI0 SS_N"},
    };
    const int n = sizeof ladder / sizeof ladder[0];

    printf("\n%-24s %-22s %8s %8s %6s\n",
           "image", "reuses config pins", "nS_rise", "CDONE", "final");
    int last_pass = -1;
    for (int i = 0; i < n; i++) {
        if (ladder[i].len == 0) {
            printf("%-24s   skipped (image missing from the build)\n", ladder[i].label);
            continue;
        }
        struct result r;
        run(&ladder[i], &r);

        char rise[12], done[12];
        if (r.nstatus_rise_us < 0) snprintf(rise, sizeof rise, "never");
        else                       snprintf(rise, sizeof rise, "%dus", r.nstatus_rise_us);
        if (r.cdone_rise_us < 0)   snprintf(done, sizeof done, "never");
        else                       snprintf(done, sizeof done, "%dus", r.cdone_rise_us);

        printf("%-24s %-22s %8s %8s   D=%d S=%d%s\n",
               ladder[i].label, ladder[i].claims, rise, done,
               r.cdone_final, r.nstatus_final,
               r.nstatus_low_in_reset ? "  (nS dipped in reset)" : "");
        if (r.cdone_final)
            last_pass = i;
    }

    if (last_pass < 0) {
        printf("\nNothing configured, not even the vendor image. The run is void:\n"
               "something on the bench changed since round 1.\n");
    } else {
        // Leave the board in a state worth looking at: re-run the highest rung
        // that passed, so the LED reflects a design we know loaded.
        struct result r;
        run(&ladder[last_pass], &r);
        printf("\nhighest passing rung: %s - left configured, LED should be lit.\n",
               ladder[last_pass].label);
        if (last_pass < n - 1)
            printf("first failing rung  : %s - it reuses %s.\n",
                   ladder[last_pass + 1].label, ladder[last_pass + 1].claims);
    }

    printf("\ndone. reflash: picotool reboot -f -u, then copy the .uf2 to\n"
           "      /Volumes/RP2350. PRG-GND strap is the fallback.\n");
    while (true) sleep_ms(1000);
}
