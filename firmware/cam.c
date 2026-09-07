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

// Sticky, cleared at the top of cam_capture(). A stalled transfer leaves the
// state machine mid-byte and the ArduChip mid-transaction, so everything after
// it on this bus is meaningless - and, worse, expensive: cam_wait_idle() would
// otherwise spend 20,000 register reads discovering the same stall twenty
// thousand times, which is how a 2 ms fault becomes a watchdog reboot. Once set,
// cam_xfer() returns without touching the wire and the bounded loops give up.
static bool     bus_fault;

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

// Put the state machine back where cam_bus_pio() found it. Bailing out of a
// transfer leaves the OSR half-consumed and the ISR half-filled, and a shift
// register that is one bit out stays one bit out forever - so the recovery is
// not optional decoration. pio_sm_init() inside cam_spi_init() disables, clears
// both FIFOs, restarts the clock divider and sets the PC back to the top.
static void cam_bus_resync(void)
{
    if (!bus_pio) return;
    pio_sm_set_enabled(cam_pio, cam_sm, false);
    cam_spi_init(cam_pio, cam_sm, cam_off,
                 CAM_PIN_MOSI, CAM_PIN_MISO, CAM_PIN_SCK, bus_div);
}

// Full duplex, MSB first. `rx` may be NULL. Neither implementation touches CS -
// see the note in cam_spi.pio about why a burst read cannot afford it.
//
// BOUNDED, for the same reason cam_wait_idle() and the CAP_DONE poll are, and
// this is the one that was not. `while (ri < n)` has no exit but n bytes coming
// back, so one byte that never arrives spun the core until the 8 s watchdog took
// the board - and it did, twice, at 280/140 (issue #8). A dropped byte should
// cost one frame. cam_capture() returns 0, ft_capture() returns NULL, and m9
// prints "no usable frame off the camera" and takes the next one: that path
// already existed and was unreachable because the driver died before it.
static bool inject_stall;

void cam_bus_fault_inject(void) { inject_stall = true; }

// #12's high-water mark. See cam.h; the arithmetic sits inside the deadline
// check below, where the clock has already been read.
static uint32_t gap_max_us;

uint32_t cam_bus_gap_max_us(bool clear)
{
    const uint32_t g = gap_max_us;
    if (clear) gap_max_us = 0;
    return g;
}

// Where in the whole burst this transfer starts, so a stall can be reported as a
// byte offset into the frame rather than into a 256-byte chunk nobody can place.
// Set by cam_collect(); a register access leaves it at CAM_OFF_REG, which prints
// as "a register access" instead of a meaningless zero.
#define CAM_OFF_REG 0xffffffffu
static uint32_t burst_off = CAM_OFF_REG, burst_len;

// FDEBUG IS STICKY AND NOBODY WAS CLEARING IT, which is why the fdebug in #12's
// only captured stall says nothing: the bits latch on the first stall after boot
// and stay latched, so by frame 200 every one of them is set by the ordinary
// starvation of a software-fed FIFO. Cleared per transfer, the same field
// answers the question it was printed for - TXSTALL means *this* transfer ran
// the state machine dry (our end stopped feeding), RXSTALL means it filled the
// RX FIFO and stalled (our end stopped draining), and neither means the state
// machine was still clocking and the camera stopped answering.
static inline void fdebug_clear(void)
{
    cam_pio->fdebug = 0x01010101u << cam_sm;
}

static bool cam_xfer(const uint8_t *tx, uint8_t *rx, size_t n)
{
    if (bus_fault) return false;

    if (bus_pio) {
        fdebug_clear();
        // Stopping the state machine starves the RX FIFO while the TX side
        // keeps accepting four more words, which is exactly the shape of a byte
        // that never comes back. Armed by 'C' on m9's console; see cam.h.
        if (inject_stall) {
            inject_stall = false;
            pio_sm_set_enabled(cam_pio, cam_sm, false);
        }
        // Kept pipelined rather than put-then-get per byte. The FIFOs are four
        // deep each way, and at 8 MHz a byte is 1 us against maybe 30 ns of loop,
        // so serializing would cost only a few percent - but the burst is 32,768
        // bytes and a few percent of the one transfer that scales with the frame
        // is worth eight lines.
        size_t ti = 0, ri = 0;
        uint64_t idle_since = 0;
        // gap_max_us is "the worst gap that was NOT a fault", and this is what
        // makes that true: a transfer on its way to the deadline walks the
        // counter up through 1,999 us first, so a single stall - injected or
        // real - would otherwise overwrite the margin with the deadline and the
        // figure would say nothing. The fault path below puts this back.
        const uint32_t gap_entry = gap_max_us;
        while (ri < n) {
            bool moved = false;
            if (ti < n && !pio_sm_is_tx_fifo_full(cam_pio, cam_sm)) {
                // Byte at bits 31:24: shift-left OSR, threshold 8.
                pio_sm_put(cam_pio, cam_sm, (uint32_t)tx[ti] << 24);
                ti++;
                moved = true;
            }
            if (!pio_sm_is_rx_fifo_empty(cam_pio, cam_sm)) {
                uint8_t b = (uint8_t)pio_sm_get(cam_pio, cam_sm);
                if (rx) rx[ri] = b;
                ri++;
                moved = true;
            }
            // The clock is only read when nothing moved, so the deadline costs
            // the healthy burst nothing at all rather than a timer read per
            // byte. It also bounds the *stall* rather than the transfer, which
            // is the thing that actually fails and the thing that needs no
            // arithmetic about how long 32,768 bytes ought to take.
            if (moved) { idle_since = 0; continue; }
            uint64_t now = time_us_64();
            if (!idle_since) { idle_since = now; continue; }
            const uint32_t gap = (uint32_t)(now - idle_since);
            // #12. Free here and nowhere else: `now` has already been paid for.
            if (gap > gap_max_us) gap_max_us = gap;
            if (gap < CAM_XFER_STALL_US) continue;

            // Everything a diagnosis needs, because the trigger is still open
            // (#12): which direction stopped - ti == n with ri short means bytes
            // went out and did not come back - and whether the state machine is
            // even running. Print before the resync; it clears all of it.
            //
            // The three stall bits are the discriminator #12 has been asking
            // for, and they are only worth reading because fdebug_clear() runs
            // at the top of every transfer. See the note there.
            const uint32_t fd = cam_pio->fdebug;
            const bool txstall = (fd >> (24 + cam_sm)) & 1u;
            const bool rxstall = (fd >> cam_sm) & 1u;
            printf("  !! camera bus stalled %u us at byte %u of %u, %u sent",
                   (unsigned)gap, (unsigned)ri, (unsigned)n, (unsigned)ti);
            if (burst_off == CAM_OFF_REG) printf("  (a register access)\n");
            else printf("  (byte %u of the %u-byte burst)\n",
                        (unsigned)(burst_off + ri), (unsigned)burst_len);
            printf("     pio pc=%u fstat=%08x fdebug=%08x sck=%.1f MHz\n"
                   "     %s\n",
                   (unsigned)pio_sm_get_pc(cam_pio, cam_sm),
                   (unsigned)cam_pio->fstat, (unsigned)fd,
                   (double)cam_bus_mhz(),
                   txstall ? "TXSTALL: this end stopped feeding the state machine"
                   : rxstall ? "RXSTALL: this end stopped draining it"
                   : "neither TXSTALL nor RXSTALL: the state machine was still "
                     "clocking and the byte did not come back");
            gap_max_us = gap_entry;
            bus_fault = true;
            cam_bus_resync();
            return false;
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
    return true;
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

// #33. How long the last wait actually waited, so a caller can ask whether its
// own writes were gated at all. The reset gate in cam_begin() measures 425 polls
// on every boot, dead or live, which is a real wait and rules the reset out;
// the question that leaves is whether the SAME predicate gates a plain register
// write, or whether the sensor only reports busy for the big commands.
uint16_t cam_last_idle_polls;

bool cam_wait_idle(const char *what)
{
    for (int i = 0; i < 20000; i++) {
        cam_last_idle_polls = (uint16_t)i;
        if ((cam_read_reg(CAM_REG_SENSOR_STATE) & 0x03) == CAM_REG_SENSOR_STATE_IDLE)
            return true;
        // A stalled bus cannot answer this question, and asking it 20,000 more
        // times is 2 s of not answering it.
        if (bus_fault) return false;
        sleep_us(100);
    }
    printf("  !! sensor never went idle after %s\n", what);
    return false;
}

// ---- reading the die ---------------------------------------------------------
//
// The passthrough, adopted 2026-09-07 after bench/probe/20260907-i2crec/ showed
// 0x48 returning the exact sixteen bits this firmware had written into the die's
// exposure registers on two consecutive boots. cam.h carries the provenance and
// the safety measurement; this is the driver's copy of the sequence, which until
// now only existed inside the probes.
uint8_t cam_sensor_read(uint16_t sensor_reg)
{
    cam_write_reg(CAM_REG_DEBUG_DEVICE_ADDRESS, CAM_SENSOR_I2C_ADDR);
    cam_wait_idle("i2c device address");
    cam_write_reg(CAM_REG_I2C_ADDR_H, (uint8_t)(sensor_reg >> 8));
    cam_wait_idle("i2c register address high");
    cam_write_reg(CAM_REG_I2C_ADDR_L, (uint8_t)(sensor_reg & 0xff));
    cam_wait_idle("i2c register address low");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_INITIATE_READ);
    cam_wait_idle("i2c direct read");
    sleep_ms(2);
    return cam_read_reg(CAM_REG_I2C_DATA);
}

// Sixteen bits is two transactions and they tear - bench/probe/20260907-awb/
// wrote 0x0400 and read 040b, high byte refreshed and low byte still holding the
// previous value's low half. Read the pair until two consecutive pairs agree.
// `ok` is false when they never do, which is a different answer from any value.
uint16_t cam_sensor_read16(uint16_t reg_h, uint16_t reg_l, bool *ok)
{
    uint16_t prev = 0;
    for (int i = 0; i < 8; i++) {
        uint16_t v = (uint16_t)((cam_sensor_read(reg_h) << 8) |
                                 cam_sensor_read(reg_l));
        if (i > 0 && v == prev) { if (ok) *ok = true; return v; }
        prev = v;
    }
    if (ok) *ok = false;
    return prev;
}

// ---- #33: did the lock take? -------------------------------------------------
//
// The manual exposure block. cam.h declines to declare 0x31-0x35 on purpose -
// its register map is transcribed from ArduCAM's driver, which touches none of
// them, and that is the provenance line it keeps. They are needed here and
// nowhere else in the driver, so they live here.
#define MANUAL_EXPOSURE_H  0x33
#define MANUAL_EXPOSURE_M  0x34
#define MANUAL_EXPOSURE_L  0x35

cam_lock_state_t cam_exposure_lock_state = CAM_LOCK_UNKNOWN;
uint16_t cam_exposure_lock_wrote;
uint16_t cam_exposure_lock_read;

static void write_manual_exposure(uint32_t v)
{
    cam_write_reg(MANUAL_EXPOSURE_H, (uint8_t)((v >> 16) & 0x0f));
    cam_wait_idle("manual exposure high");
    cam_write_reg(MANUAL_EXPOSURE_M, (uint8_t)((v >> 8) & 0xff));
    cam_wait_idle("manual exposure mid");
    cam_write_reg(MANUAL_EXPOSURE_L, (uint8_t)(v & 0xff));
    cam_wait_idle("manual exposure low");
}

// WHAT THIS IS FOR, AND WHY IT IS NOT A PICTURE.
//
// Issue #33 is that the auto loops come up disabled on some acquires and
// cam_image_defaults() does not notice. Until 2026-09-07 there was no way for it
// to notice: CAM_REG_AUTO_CONTROL is write-only, so nothing could be read back
// to say whether a mask had landed. bench/probe/20260907-awb/ found the place it
// can be read - not on the ArduChip but one level down, at the die's own
// exposure registers.
//
// Write an exposure with every loop locked and read 0x3002/0x3003 back. If the
// die keeps it, the lock reached the sensor. If the die puts its own value back,
// the AE loop is still running with the mask at zero - which is also what pins
// the frame near mid-scale whatever exposure is written, and is what every "flat
// boot" in that directory turned out to be.
//
// TWO EXPOSURES, NOT ONE. A single value the loop happens to agree with looks
// held either way. These two are a factor of eight apart, so the loop cannot
// agree with both.
//
// AND IT REFUSES TO ANSWER BEFORE THE FIRST FRAME. This was written on
// 2026-09-07 as the first statement of cam_image_defaults() and it said `held`
// on 33 boots out of 33 while the same call, run again a few frames later on
// those same boots, said `dragged` on 32 of them - bench/probe/20260907-lockrate/.
// A check that cannot fail is not a check. The AE loop only revises the
// exposure when the sensor is clocking frames out, so at the top of
// cam_image_defaults() there is nothing running that could drag the write back,
// and the held is an artefact of asking early. Zero frames is the one case that
// is knowably meaningless, so it is the one this refuses; one frame in it
// already dragged on 7 of 12 boots.
//
// WHICH MAKES THE TWO ANSWERS UNEQUAL IN WEIGHT, and callers should read them
// that way. `dragged` is proof: the die overwrote a value it was handed, so the
// loop is live. `held` is only the absence of that proof within this call's
// two writes and 60 ms - the 33 boots include 9 that answered differently to
// the same question three times over about ten seconds.
//
// This leaves the loops FREE, because that is the state cam_image_defaults()
// wants and this runs on the way there. It does not restore any manual exposure
// value, and it does not need to: cam.h:362 records that switching the loops
// back on does not undo a manual write, so the caller gets the loops re-enabled
// and the sensor re-converging, which is exactly the boot state.
uint32_t cam_frames_triggered;

cam_lock_state_t cam_exposure_lock_check(void)
{
    static const uint32_t TRY[2] = { 0x00080u, 0x00400u };
    bool held = true, readable = true;

    if (cam_frames_triggered == 0) {
        cam_exposure_lock_state = CAM_LOCK_UNTESTED;
        return cam_exposure_lock_state;
    }

    cam_image_auto_mask(0u);
    for (int i = 0; i < 2; i++) {
        bool ok = false;
        write_manual_exposure(TRY[i]);
        sleep_ms(60);
        uint16_t got = cam_sensor_read16(CAM_OV3640_AEC_H, CAM_OV3640_AEC_L, &ok);
        cam_exposure_lock_wrote = (uint16_t)TRY[i];
        cam_exposure_lock_read  = got;
        if (!ok) readable = false;
        if (got != (uint16_t)TRY[i]) held = false;
    }

    cam_exposure_lock_state = !readable ? CAM_LOCK_UNREADABLE
                            : held      ? CAM_LOCK_HELD
                                        : CAM_LOCK_DRAGGED;
    return cam_exposure_lock_state;
}

const char *cam_lock_state_name(cam_lock_state_t s)
{
    switch (s) {
    case CAM_LOCK_HELD:       return "held";
    case CAM_LOCK_DRAGGED:    return "dragged";
    case CAM_LOCK_UNREADABLE: return "unreadable";
    case CAM_LOCK_UNTESTED:   return "untested";
    default:                  return "unknown";
    }
}

// ---- #33's read-only witness -------------------------------------------------
//
// cam.h has what this answers and, more to the point, what it does not. The one
// thing to keep in mind reading the code: nothing here writes to the sensor, and
// that is the whole reason it exists rather than being a second entry point into
// the check above.
void cam_die_sample(cam_die_sample_t *s)
{
    bool ok = false;
    s->exposure = cam_sensor_read16(CAM_OV3640_AEC_H, CAM_OV3640_AEC_L, &ok);
    // Single bytes, so there is no pair to disagree and no way to catch a torn
    // read - the tearing cam_sensor_read16() defends against is between two
    // transactions and these are one each. `ok` is the exposure's alone and the
    // struct says so; do not read it as a verdict on the other two.
    s->gain = cam_sensor_read(CAM_OV3640_GAIN);
    s->awb  = cam_sensor_read(CAM_OV3640_AWB_WITNESS);
    s->ok   = ok;
}

uint8_t cam_die_live(const cam_die_sample_t *a, const cam_die_sample_t *b)
{
    if (!a->ok || !b->ok) return 0u;
    uint8_t live = 0u;
    if (a->exposure != b->exposure) live |= CAM_AUTO_EXPOSURE;
    if (a->gain     != b->gain)     live |= CAM_AUTO_GAIN;
    if (a->awb      != b->awb)      live |= CAM_AUTO_WB;
    return live;
}

// What the last capture actually wrote, so `rewrite = false` can skip. -1 means
// "unknown", which is the honest state after a reset or a cam_begin().
static int last_fmt = -1, last_mode = -1;

// #33, and this build only MEASURES it - the wait below is bit for bit the
// bound cam_wait_idle() uses, so nothing about the boot path changes.
//
// The suspicion is that `cam_wait_idle("reset")` is not a gate at all. It reads
// CAM_REG_SENSOR_STATE on the SPI transaction immediately after the reset write,
// and if the ArduChip has not yet begun executing the reset that read returns
// the state from BEFORE it - IDLE - so the wait returns having never seen the
// sensor go busy. Everything after it, including cam_image_defaults()'s three
// CAM_REG_AUTO_CONTROL writes, would then land in the middle of a reset.
//
// That would explain all of #33 at once: intermittent because it is a race,
// invisible in the register dump because 0x30 is a write-selector, and cured by
// re-issuing CAM_AUTO_ALL later because by then the reset is long over.
//
// It is also only a story until someone reads the poll count. If the loop
// exits on iteration 0 with a state that was already IDLE, the gate is doing
// nothing and the story stands. If it observes busy and waits, the fault is
// somewhere else and this comment is wrong.
uint16_t cam_reset_polls;
uint8_t  cam_reset_first_state;
bool     cam_reset_saw_busy;

void cam_begin(uint8_t id, bool verbose)
{
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_SENSOR_RESET_ENABLE);
    cam_reset_polls = 0;
    cam_reset_saw_busy = false;
    cam_reset_first_state = 0xff;
    for (int i = 0; i < 20000; i++) {
        uint8_t st = cam_read_reg(CAM_REG_SENSOR_STATE);
        if (i == 0) cam_reset_first_state = st;
        if ((st & 0x03) == CAM_REG_SENSOR_STATE_IDLE) break;
        cam_reset_saw_busy = true;
        if (bus_fault) break;
        cam_reset_polls++;
        sleep_us(100);
    }

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

    // THE FRAME SOURCE IS PUT BACK TO THE SENSOR HERE, AND IT IS NOT PARANOIA.
    // 0x06 lives on the ArduChip, which a reflash and an RP2354 reboot do not
    // power down - bench/probe/20260907-simsrc/ established that a hub power
    // cycle is the only thing that does. So a run that ends on the synthetic
    // source hands the next boot a camera that is not a camera, and the reset
    // above does not clear it: caught on the first bench-shaped run of m9's 'M'
    // key, which booted with the exposure ramp reading 44 44 44 for 174 frames,
    // a background spread of exactly +-0.0000, and every score in the log taken
    // against a fixed pattern nobody had asked for. Nothing in that log said so
    // - it looked like a scene the camera could not expose for.
    //
    // ONE WRITE, UNCONDITIONALLY, and not a read-then-fix. This is the point in
    // the program where "what the camera is" gets decided, and a bring-up that
    // only corrects the state it happens to find leaves the boot path depending
    // on what the last run did.
    cam_frame_source_synth(false);

    // The reset above put the sensor back to its default VGA, so the cache
    // above now describes a machine that no longer exists. Saying so is the
    // whole of issue #29: without it a `rewrite = false` capture skips the
    // CAPTURE_RESOLUTION write because "the mode has not changed", when only
    // the record of it survived, and the FIFO comes back 640x480x2 into a
    // 128x128 buffer. This is NOT the repeat-write fault cam.h guards against
    // - that one is a *second identical* write, and after a reset the write is
    // the first one the sensor has seen.
    last_fmt = last_mode = -1;
}

// All three selectors on every call, so the mask is a target and not a toggle.
// CAM_AUTO_ALL reproduces the write sequence cam_begin() has always issued,
// register for register, which is what keeps the boot path unmeasured-but-
// unchanged.
uint16_t cam_auto_polls[4];

void cam_image_auto_mask(uint8_t tracking)
{
    const uint8_t e = (tracking & CAM_AUTO_EXPOSURE) ? AUTO_ON : 0u;
    const uint8_t g = (tracking & CAM_AUTO_GAIN)     ? AUTO_ON : 0u;
    const uint8_t w = (tracking & CAM_AUTO_WB)       ? AUTO_ON : 0u;
    cam_write_reg(CAM_REG_AUTO_CONTROL, e | AUTO_SEL_EXPOSURE);
    cam_wait_idle(e ? "auto exposure on" : "auto exposure off");
    cam_auto_polls[0] = cam_last_idle_polls;
    cam_write_reg(CAM_REG_AUTO_CONTROL, g | AUTO_SEL_GAIN);
    cam_wait_idle(g ? "auto gain on" : "auto gain off");
    cam_auto_polls[1] = cam_last_idle_polls;
    cam_write_reg(CAM_REG_AUTO_CONTROL, w | AUTO_SEL_WHITEBALANCE);
    cam_wait_idle(w ? "auto white balance on" : "auto white balance off");
    cam_auto_polls[2] = cam_last_idle_polls;
}

void cam_image_auto(bool on)
{
    cam_image_auto_mask(on ? CAM_AUTO_ALL : 0u);
}

// One register write and nothing else. In particular it does NOT touch the auto
// loops: switching the synthetic source on while they run leaves them running,
// which is deliberate, because the arm this exists for is "the camera is not in
// the experiment" and not "the camera is frozen" - those are different controls
// and 'L' is the other one. Off restores the camera without re-running
// cam_image_defaults(), so whatever the loops had converged to is what comes
// back; the probe saw the camera return on all three boots.
// READ-MODIFY-WRITE, NOT A CONSTANT. 0x06 is RW and only bit 7 is documented;
// the other seven are not, and this module powers on with 0x01 in there. The
// probe preserved them and got its six bit-identical frames with 0x81, so
// writing a bare 0x80 or a bare 0x00 would be a different experiment from the
// one that produced the evidence, and writing 0x81 as a literal would be
// hardcoding one boot's power-on content as if it were the specification.
void cam_frame_source_synth(bool on)
{
    const uint8_t was = cam_read_reg(CAM_REG_FRAME_SOURCE);
    const uint8_t now = on ? (uint8_t)(was |  CAM_FRAME_SOURCE_SYNTH)
                           : (uint8_t)(was & ~CAM_FRAME_SOURCE_SYNTH);
    cam_write_reg(CAM_REG_FRAME_SOURCE, now);
    cam_wait_idle(on ? "synthetic frame source on"
                     : "frame source back to the sensor");
}

void cam_image_defaults(void)
{
    // #33 IS NOT FIXABLE FROM INSIDE THIS FUNCTION, and that is a measurement
    // rather than a judgement. cam_exposure_lock_check() was called here, as the
    // first statement, for exactly one afternoon. Across 33 boots it said `held`
    // 33 times, while the identical call a few frames later on those same boots
    // said `dragged` on 32 of them: bench/probe/20260907-lockrate/. Nothing has
    // clocked a frame by the time this runs, so the AE loop has nothing to
    // revise and the lock trivially appears to take. The check now refuses that
    // case outright and the call is gone, which leaves the boot path bit for bit
    // where it has always been.
    //
    // So the noticing belongs to the caller, after its warm-up captures. cam.h
    // says where; m9's bench arms are the place that wants it.
    cam_image_auto(true);
    // NOT part of cam_image_auto(). This one is not a loop being switched on -
    // cam.h:220 has the measurement: writing it AT ALL is what takes blue from
    // 42 to 133, whatever value goes in. Undoing it has never been wanted and
    // is not what locking the sensor means.
    cam_write_reg(CAM_REG_WB_MODE_CONTROL, 0);
    cam_wait_idle("white balance mode");
    cam_auto_polls[3] = cam_last_idle_polls;
}

const cam_recipe_t CAM_RECIPE_VENDOR = { "vendor", false, false, 0 };

// When the outstanding trigger was issued, so cam_collect() can still report
// expose_us across the gap. Zero means nothing is in flight; collecting without
// a trigger is a caller bug rather than a camera fault, so it says so.
static uint64_t trig_us;

// The same instant, kept after cam_collect() has consumed it - so a caller that
// wants to know how old the frame in its hands is can still ask. Never cleared,
// because "the last trigger issued" is always a meaningful answer and "nothing
// in flight" is trig_us's job to say, not this one's.
static uint64_t last_trig_us;

bool cam_trigger(const cam_recipe_t *r, uint8_t mode, uint8_t fmt, cam_time_t *t)
{
    cam_time_t discard;
    if (!t) t = &discard;

    // One capture is the unit of recovery: the bus was resynced when the fault
    // was raised, so this one starts clean and gets to fail on its own merits.
    bus_fault = false;

    // Only so cam_exposure_lock_check() can refuse to answer before the sensor
    // has clocked a frame. Nothing reads it for any other purpose and nothing
    // resets it: "has this boot ever taken a picture" is the whole question.
    cam_frames_triggered++;

    uint64_t t0 = time_us_64();
    // The guard cam.h's header comment is about. Not tidiness.
    if (r->rewrite || fmt != last_fmt) {
        cam_write_reg(CAM_REG_FORMAT, fmt);
        if (!cam_wait_idle("format")) return false;
        last_fmt = fmt;
    }
    if (r->rewrite || mode != last_mode) {
        cam_write_reg(CAM_REG_CAPTURE_RESOLUTION, CAM_SET_CAPTURE_MODE | mode);
        if (!cam_wait_idle("resolution")) return false;
        last_mode = mode;
    }
    if (r->settle_ms) sleep_ms(r->settle_ms);
    uint64_t t1 = time_us_64();

    if (r->flush) cam_write_reg(ARDUCHIP_FIFO_2, FIFO_CLEAR_MASK);
    cam_write_reg(ARDUCHIP_FIFO, FIFO_CLEAR_ID_MASK);
    cam_write_reg(ARDUCHIP_FIFO, FIFO_START_MASK);
    // The three writes above are the whole trigger, and a bus that stalled
    // during them did not arm anything. Saying so here is what keeps the next
    // cam_collect() from waiting 3 s for a capture nobody started.
    if (bus_fault) return false;

    t->setup_us = (uint32_t)(t1 - t0);
    trig_us = last_trig_us = t1;
    return true;
}

uint64_t cam_last_trig_us(void) { return last_trig_us; }

uint32_t cam_collect(uint8_t *dst, uint32_t cap, cam_time_t *t)
{
    cam_time_t discard;
    if (!t) t = &discard;

    if (!trig_us) { printf("  !! collect with no capture in flight\n"); return 0; }
    const uint64_t t1 = trig_us;
    trig_us = 0;

    // Bounded for the same reason cam_wait_idle() is. A camera that never
    // asserts CAP_DONE is a result, not a reason to stop printing.
    const uint64_t tw = time_us_64();
    bool done = false;
    for (int i = 0; i < 30000 && !done; i++) {
        done = (cam_read_reg(ARDUCHIP_TRIG) & CAP_DONE_MASK) != 0;
        if (bus_fault) return 0;   // 3 s of polling a bus that has stopped
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
    burst_len = len;
    for (uint32_t off = 0; off < len; off += sizeof zeros) {
        uint32_t n = len - off;
        if (n > sizeof zeros) n = sizeof zeros;
        burst_off = off;
        cam_xfer(zeros, dst + off, n);
    }
    burst_off = CAM_OFF_REG;
    cs_high();
    uint64_t t4 = time_us_64();
    // A short burst is a torn frame, not a frame. The first stalled chunk
    // short-circuits the rest, so this costs one deadline and not 128 of them.
    if (bus_fault) return 0;

    // Two different questions, and cam.h says why the answers diverge: t2 - t1
    // is how long the sensor and its frame boundary took, t2 - tw is how much of
    // that this caller stood still for.
    t->expose_us = (uint32_t)(t2 - t1);
    t->wait_us   = (uint32_t)(t2 - tw);
    t->read_us   = (uint32_t)(t4 - t3);
    return len;
}

uint32_t cam_capture(const cam_recipe_t *r, uint8_t mode, uint8_t fmt,
                     uint8_t *dst, uint32_t cap, cam_time_t *t)
{
    cam_time_t discard;
    if (!t) t = &discard;
    t->setup_us = t->expose_us = t->wait_us = t->read_us = 0;
    if (!cam_trigger(r, mode, fmt, t)) return 0;
    return cam_collect(dst, cap, t);
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
