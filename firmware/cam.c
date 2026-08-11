// The Arducam Mega driver. See cam.h for what was measured and why.

#include <stdio.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"

#include "cam.h"
#include "cam_pixel.h"   // cam_rgb565_expand, for cam_frame_means()
#include "cam_spi.pio.h"

static PIO      cam_pio;
static uint     cam_sm;
static uint     cam_off;
static bool     bus_pio;      // false = bit-bang
static float    bus_div;

static void cs_high(void) { gpio_put(CAM_PIN_CS, 1); }
static void cs_low(void)  { gpio_put(CAM_PIN_CS, 0); }

void cam_bus_init(PIO pio)
{
    gpio_init(CAM_PIN_CS);
    gpio_set_dir(CAM_PIN_CS, GPIO_OUT);
    cs_high();
    cam_pio = pio;
    cam_off = pio_add_program(cam_pio, &cam_spi_program);
    cam_sm  = (uint)pio_claim_unused_sm(cam_pio, true);
}

void cam_bus_pio(uint32_t hz)
{
    if (bus_pio) pio_sm_set_enabled(cam_pio, cam_sm, false);
    bus_div = cam_spi_clkdiv(hz);
    cam_spi_init(cam_pio, cam_sm, cam_off,
                 CAM_PIN_MOSI, CAM_PIN_MISO, CAM_PIN_SCK, bus_div);
    bus_pio = true;
}

// SPI mode 0 by hand at ~500 kHz. Deliberately slow and deliberately dumb.
void cam_bus_bitbang(void)
{
    if (bus_pio) pio_sm_set_enabled(cam_pio, cam_sm, false);
    gpio_set_function(CAM_PIN_MOSI, GPIO_FUNC_SIO);
    gpio_set_function(CAM_PIN_SCK,  GPIO_FUNC_SIO);
    gpio_set_function(CAM_PIN_MISO, GPIO_FUNC_SIO);
    gpio_set_dir(CAM_PIN_MOSI, GPIO_OUT);
    gpio_set_dir(CAM_PIN_SCK,  GPIO_OUT);
    gpio_set_dir(CAM_PIN_MISO, GPIO_IN);
    gpio_put(CAM_PIN_SCK, 0);
    bus_pio = false;
    bus_div = 0;
}

float cam_bus_mhz(void)
{
    if (!bus_pio) return 0.5f;
    return (float)clock_get_hz(clk_sys) / (4.0f * bus_div) / 1e6f;
}

// Full duplex, MSB first. `rx` may be NULL. Neither implementation touches CS -
// see the note in cam_spi.pio about why a burst read cannot afford it.
static void cam_xfer(const uint8_t *tx, uint8_t *rx, size_t n)
{
    if (bus_pio) {
        // Kept pipelined rather than put-then-get per byte. The FIFOs are four
        // deep each way, and at 8 MHz a byte is 1 us against maybe 30 ns of loop,
        // so serializing would cost only a few percent - but the burst is 32,768
        // bytes and a few percent of the one transfer that scales with the frame
        // is worth eight lines.
        size_t ti = 0, ri = 0;
        while (ri < n) {
            if (ti < n && !pio_sm_is_tx_fifo_full(cam_pio, cam_sm)) {
                // Byte at bits 31:24: shift-left OSR, threshold 8.
                pio_sm_put(cam_pio, cam_sm, (uint32_t)tx[ti] << 24);
                ti++;
            }
            if (!pio_sm_is_rx_fifo_empty(cam_pio, cam_sm)) {
                uint8_t b = (uint8_t)pio_sm_get(cam_pio, cam_sm);
                if (rx) rx[ri] = b;
                ri++;
            }
        }
    } else {
        for (size_t i = 0; i < n; i++) {
            uint8_t o = tx[i], v = 0;
            for (int b = 7; b >= 0; b--) {
                gpio_put(CAM_PIN_MOSI, (o >> b) & 1);
                busy_wait_us_32(1);
                gpio_put(CAM_PIN_SCK, 1);
                v = (uint8_t)((v << 1) | (uint8_t)gpio_get(CAM_PIN_MISO));
                busy_wait_us_32(1);
                gpio_put(CAM_PIN_SCK, 0);
            }
            if (rx) rx[i] = v;
        }
    }
}

void cam_write_reg(uint8_t addr, uint8_t val)
{
    const uint8_t tx[2] = { (uint8_t)(addr | 0x80), val };
    cs_low();
    cam_xfer(tx, NULL, 2);
    cs_high();
}

// Two dummy bytes after the address, and the value is the *second* one. The
// first is the ArduChip's turnaround; reading the first is the classic way to
// get a register file that looks shifted by one.
uint8_t cam_read_reg(uint8_t addr)
{
    const uint8_t tx[3] = { (uint8_t)(addr & 0x7f), 0x00, 0x00 };
    uint8_t rx[3] = { 0, 0, 0 };
    cs_low();
    cam_xfer(tx, rx, 3);
    cs_high();
    return rx[2];
}

bool cam_wait_idle(const char *what)
{
    for (int i = 0; i < 20000; i++) {
        if ((cam_read_reg(CAM_REG_SENSOR_STATE) & 0x03) == CAM_REG_SENSOR_STATE_IDLE)
            return true;
        sleep_us(100);
    }
    printf("  !! sensor never went idle after %s\n", what);
    return false;
}

void cam_begin(uint8_t id, bool verbose)
{
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_SENSOR_RESET_ENABLE);
    cam_wait_idle("reset");

    uint8_t y = cam_read_reg(CAM_REG_YEAR_ID)  & 0x3f; cam_wait_idle("year");
    uint8_t m = cam_read_reg(CAM_REG_MONTH_ID) & 0x0f; cam_wait_idle("month");
    uint8_t d = cam_read_reg(CAM_REG_DAY_ID)   & 0x1f; cam_wait_idle("day");
    uint8_t f = cam_read_reg(CAM_REG_FPGA_VERSION_NUMBER); cam_wait_idle("fpga");

    if (verbose) {
        printf("  sensor id : 0x%02x  (%s table)\n", id,
               id >= SENSOR_5MP ? "current" : "legacy");
        printf("  firmware  : 20%02u-%02u-%02u, fpga rev %u\n", y, m, d, f);
    }

    // 0x78 is the 5MP/3MP device address in ArduCAM's CameraInfo tables.
    cam_write_reg(CAM_REG_DEBUG_DEVICE_ADDRESS, 0x78);
    cam_wait_idle("device address");
}

void cam_image_defaults(void)
{
    cam_write_reg(CAM_REG_AUTO_CONTROL, AUTO_ON | AUTO_SEL_EXPOSURE);
    cam_wait_idle("auto exposure");
    cam_write_reg(CAM_REG_AUTO_CONTROL, AUTO_ON | AUTO_SEL_GAIN);
    cam_wait_idle("auto gain");
    cam_write_reg(CAM_REG_AUTO_CONTROL, AUTO_ON | AUTO_SEL_WHITEBALANCE);
    cam_wait_idle("auto white balance");
    cam_write_reg(CAM_REG_WB_MODE_CONTROL, 0);
    cam_wait_idle("white balance mode");
}

const cam_recipe_t CAM_RECIPE_VENDOR = { "vendor", false, false, 0 };

// What the last capture actually wrote, so `rewrite = false` can skip. -1 means
// "unknown", which is the honest state after a reset or a cam_begin().
static int last_fmt = -1, last_mode = -1;

uint32_t cam_capture(const cam_recipe_t *r, uint8_t mode, uint8_t fmt,
                     uint8_t *dst, uint32_t cap, cam_time_t *t)
{
    cam_time_t discard;
    if (!t) t = &discard;

    uint64_t t0 = time_us_64();
    // The guard cam.h's header comment is about. Not tidiness.
    if (r->rewrite || fmt != last_fmt) {
        cam_write_reg(CAM_REG_FORMAT, fmt);
        if (!cam_wait_idle("format")) return 0;
        last_fmt = fmt;
    }
    if (r->rewrite || mode != last_mode) {
        cam_write_reg(CAM_REG_CAPTURE_RESOLUTION, CAM_SET_CAPTURE_MODE | mode);
        if (!cam_wait_idle("resolution")) return 0;
        last_mode = mode;
    }
    if (r->settle_ms) sleep_ms(r->settle_ms);
    uint64_t t1 = time_us_64();

    if (r->flush) cam_write_reg(ARDUCHIP_FIFO_2, FIFO_CLEAR_MASK);
    cam_write_reg(ARDUCHIP_FIFO, FIFO_CLEAR_ID_MASK);
    cam_write_reg(ARDUCHIP_FIFO, FIFO_START_MASK);

    // Bounded for the same reason cam_wait_idle() is. A camera that never
    // asserts CAP_DONE is a result, not a reason to stop printing.
    bool done = false;
    for (int i = 0; i < 30000 && !done; i++) {
        done = (cam_read_reg(ARDUCHIP_TRIG) & CAP_DONE_MASK) != 0;
        if (!done) sleep_us(100);
    }
    uint64_t t2 = time_us_64();
    if (!done) { printf("  !! CAP_DONE never asserted\n"); return 0; }

    uint32_t l1 = cam_read_reg(FIFO_SIZE1);
    uint32_t l2 = cam_read_reg(FIFO_SIZE2);
    uint32_t l3 = cam_read_reg(FIFO_SIZE3);
    uint32_t len = ((l3 << 16) | (l2 << 8) | l1) & 0xffffffu;
    if (len == 0 || len > cap) {
        printf("  !! FIFO length %u, buffer is %u\n", (unsigned)len, (unsigned)cap);
        return len;
    }

    // Burst read. One command byte, then one extra dummy - ArduCAM's driver
    // sends that dummy only on the first burst after a capture, and this does
    // exactly one burst per capture, so it is unconditional here.
    uint64_t t3 = time_us_64();
    cs_low();
    const uint8_t cmd[2] = { BURST_FIFO_READ, 0x00 };
    cam_xfer(cmd, NULL, 2);
    static uint8_t zeros[256];
    for (uint32_t off = 0; off < len; off += sizeof zeros) {
        uint32_t n = len - off;
        if (n > sizeof zeros) n = sizeof zeros;
        cam_xfer(zeros, dst + off, n);
    }
    cs_high();
    uint64_t t4 = time_us_64();

    t->setup_us  = (uint32_t)(t1 - t0);
    t->expose_us = (uint32_t)(t2 - t1);
    t->read_us   = (uint32_t)(t4 - t3);
    return len;
}

bool cam_frame_is_constant(const uint8_t *p, uint32_t len)
{
    for (uint32_t i = 2; i + 1 < len; i += 2)
        if (p[i] != p[0] || p[i + 1] != p[1]) return false;
    return true;
}

void cam_frame_means(const uint8_t *p, uint32_t len, int m[3])
{
    uint32_t sum[3] = { 0, 0, 0 };
    uint32_t n = len / 2;
    for (uint32_t i = 0; i + 1 < len; i += 2) {
        uint8_t v[3];
        cam_rgb565_expand((uint16_t)((p[i] << 8) | p[i + 1]), v);
        sum[0] += v[0]; sum[1] += v[1]; sum[2] += v[2];
    }
    for (int c = 0; c < 3; c++) m[c] = n ? (int)(sum[c] / n) : 0;
}
