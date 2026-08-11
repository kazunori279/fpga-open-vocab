// M8a: bring the Arducam Mega up on the RP header, and prove the pixels.
//
// One boot has to answer four questions, and they are ordered so that a failure
// at step N tells you the answer to step N+1 does not exist yet:
//
//   1. Is the bus wired right?      Read a register with a known value.
//   2. How fast can it go?          Read the same register at 0.5 - 16 MHz.
//   3. Does 128x128 RGB565 exist?   M7b ordered this part on the strength of a
//                                   resolution table that lists 128x128 and a
//                                   format table that lists RGB565, and never
//                                   found a sentence saying the two combine.
//   4. Are the pixels the pixels?   Dump the frame and look at it.
//
// Step 4 is the whole point and the reason this is a separate binary. M5's
// contract is that the encoder is bit-exact against numpy; that guarantee says
// nothing at all if the int8 codes going in are wrong, and every way the input
// can be wrong - byte order, 5/6/5 expansion, channel order, row order, a stale
// FIFO - produces a tensor that is perfectly well-formed and silently not the
// image. None of those show up as an error. They show up as a picture that is
// blue, or upside down, or diagonally sheared, so the test is to render it.
//
// WHAT THE DEVICE DECIDES AND WHAT THE HOST DECIDES. The device dumps the FIFO
// bytes verbatim, base64, with a CRC32 - no unpacking, no normalization, no
// interpretation. Byte order and channel order are the host's to guess, because
// the host can try both and show you the two PNGs side by side, and this board
// cannot. What the device *does* compute is the int8 CHW tensor it would hand
// fgx_run(), and a CRC32 of that; host/cam.py recomputes the same tensor from
// the same bytes with numpy and compares the two CRCs. That check is the actual
// deliverable. It is the one that makes "the encoder is bit-exact" mean
// something end to end rather than only from the codes inward.
//
// TWO BUS BACKENDS, ONE BINARY, for the reason M5b gives: a probe that needs a
// second flash to answer its own follow-up question is a probe that will get
// one answer. If the chip ID comes back wrong on PIO, the immediate question is
// whether the fault is in cam_spi.pio or in the wiring, and a 500 kHz bit-bang
// of the same read separates those two without touching the board. The bit-bang
// is not a fallback for production; it is a control.
//
// Pins, from docs/milestones.md#m8--camera - SCK GPIO8, MISO GPIO9, MOSI
// GPIO12, CS GPIO13, plus 3V3 and GND. The link's pins (1/2/3/6, or 22 in
// configuration C) are untouched, so this can eventually run beside it; today
// it runs alone.
//
// Register names, values and sequences are transcribed from ArduCAM's own
// driver - github.com/ArduCAM/Arducam_Mega, src/Arducam/ArducamCamera.c - and
// not from the application note, which disagrees with it. Transcribed rather
// than vendored: the library is ~1,200 lines of camera-model dispatch, Arduino
// HAL and JPEG plumbing, of which this needs the forty lines below.

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pio.h"

#include "cam.h"         // the driver: pins, registers, bus, capture. M8b moved
                         // it out of this file so m7.c gets the same one, and
                         // in particular the same resolution-write guard
#include "cam_dump.h"    // the BEGIN/END frame format, shared with the host test
#include "cam_pixel.h"   // pulls in encoder.h; the arithmetic lives there so
                         // test_cam_pixel.c can check it without a board
#include "encoder.h"

// Linked by blobs.S, same as m5.c/m7.c. Only the header is used here, for
// in_scale - the quantizer step the image has to be divided by. Hard-coding it
// would be a second copy of a number that model/export.py owns.
extern const uint8_t fgx_weights[], fgx_weights_end[];

// --- frame buffers ---------------------------------------------------------
// 320x240 RGB565 is the largest thing captured here, at 150 KB; 128x128 needs
// 32 KB of it. The int8 tensor is a separate 48 KB because both are wanted at
// once - the CRC pair is only evidence if the two were computed from the same
// bytes in the same boot.
#define RAW_MAX   (320u * 240u * 2u)
#define CHW_MAX   (3u * 128u * 128u)
static uint8_t raw[RAW_MAX];
static int8_t  chw[CHW_MAX];

// THE REPEAT-CAPTURE FAULT lives in cam.h now, next to the guard that fixes it,
// because m7.c depends on that guard and a comment only this file can see is a
// comment the next reader of the driver will not get. What stays here is the
// matrix that re-measures it every boot; see main().

// --- steps -----------------------------------------------------------------

// Question 1 and 2 in one table. The sensor ID is the register to sweep on
// because it is the only one with a value that is both known-in-advance and
// not 0x00 or 0xff - a bus that is floating, shorted or unpowered reads those,
// and reading them from a *known* register is how you tell "wrong" from
// "nothing there".
static uint8_t probe_id(void)
{
    static const uint32_t rates[] = { 500000, 1000000, 2000000, 4000000,
                                      8000000, 12000000, 16000000 };
    uint8_t agreed = 0;

    printf("\n-- question 1 and 2: is it wired, and how fast --\n");
    printf("  %-10s %-9s %6s   %s\n", "bus", "rate", "id", "verdict");

    cam_bus_bitbang();
    uint8_t bb = cam_read_reg(CAM_REG_SENSOR_ID);
    printf("  %-10s %6.2f MHz   0x%02x   %s\n", "bit-bang", 0.5, bb,
           cam_id_plausible(bb) ? "plausible"
                                      : "NOT a known Mega sensor id");
    agreed = bb;

    for (size_t i = 0; i < count_of(rates); i++) {
        cam_bus_pio(rates[i]);
        // Four reads, not one. A marginal sample point fails intermittently,
        // and one good read at 16 MHz would set the operating rate wrongly.
        uint8_t v = cam_read_reg(CAM_REG_SENSOR_ID);
        bool stable = true;
        for (int k = 0; k < 3; k++)
            if (cam_read_reg(CAM_REG_SENSOR_ID) != v) stable = false;
        float real = cam_bus_mhz();
        printf("  %-10s %6.2f MHz   0x%02x   %s\n", "pio", real, v,
               !stable        ? "UNSTABLE"
               : (v == agreed) ? "agrees with bit-bang"
                               : "DISAGREES with bit-bang");
    }
    return agreed;
}

// One capture, timed, checked against the size it should be, and dumped.
//
// There was a warm-up capture here - capture once, throw it away, keep the
// second - on the theory that the first frame after a resolution change is
// invalid. The next run disproved it outright: with the warm-up in place *every*
// kept frame became a constant fill, including the 128x128 one that had been
// fine before, because the warm-up had moved every kept frame out of position 1.
// The fault is not "the first frame after a mode change is bad", it is "only the
// first capture after cam_begin() is good". A fix that makes the symptom
// spread is a fix aimed at the wrong thing, so it is gone; see cam_capture()'s
// cam_recipe_t and the matrix in main() for what replaced it.
static void try_mode(const cam_recipe_t *r, const char *tag, uint8_t mode,
                     uint32_t w, uint32_t h,
                     float in_scale, bool make_tensor, uint32_t rate_hz)
{
    cam_time_t tm = { 0, 0, 0 };
    cam_bus_pio(rate_hz);
    printf("\n-- %s: %ux%u RGB565, resolution register 0x%02x, bus %.1f MHz, "
           "recipe %s --\n",
           tag, (unsigned)w, (unsigned)h, mode,
           (double)cam_bus_mhz(), r->name);

    uint32_t len = cam_capture(r, mode, CAM_IMAGE_PIX_FMT_RGB565,
                               raw, RAW_MAX, &tm);
    if (len == 0 || len > RAW_MAX) return;
    if (cam_frame_is_constant(raw, len))
        printf("  !! CONSTANT FILL - every pixel is %02x %02x, this is not a "
               "picture\n", raw[0], raw[1]);

    const uint32_t want = w * h * 2u;
    printf("  fifo      : %u bytes, expected %u  %s\n",
           (unsigned)len, (unsigned)want,
           len == want ? "" : "<-- MISMATCH, the mode is not what was asked for");
    printf("  timing    : setup %.1f ms, expose %.1f ms, read %.1f ms"
           "  (%.2f MB/s over the bus)\n",
           tm.setup_us / 1000.0, tm.expose_us / 1000.0, tm.read_us / 1000.0,
           len / 1048576.0 / (tm.read_us / 1e6));
    printf("  first 16  : ");
    for (int i = 0; i < 16 && (uint32_t)i < len; i++) printf("%02x ", raw[i]);
    printf("\n");

    cam_dump_frame(tag, raw, len, w, h);

    // Only for the mode the encoder actually eats. A 320x240 tensor would need
    // a resize, and where that resize happens is an M8b question - the point of
    // capturing QVGA here is to have an answer if 128x128 turns out not to
    // exist, not to preprocess it.
    if (!make_tensor || len != want) return;
    const uint32_t plane = w * h;
    for (int hi = 0; hi < 2; hi++) {
        cam_frame_to_chw(raw, w, h, hi != 0, in_scale, chw);
        printf("  chw crc32 : %08x   (%s byte first)\n",
               (unsigned)cam_crc32((const uint8_t *)chw, 3u * plane),
               hi ? "high" : "low");
    }
}

int main(void)
{
    stdio_init_all();
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    // pio0: this binary runs alone, so there is nothing to share with. m7.c
    // puts the camera on pio1 because the link already owns pio0's
    // instruction memory there.
    cam_bus_init(pio0);

    printf("\n=== M8a: Arducam Mega on the RP header ===\n\n");
    printf("clock     : %u MHz sys\n", (unsigned)(clock_get_hz(clk_sys) / 1000000));
    printf("pins      : mosi GPIO%d, miso GPIO%d, sck GPIO%d, cs GPIO%d\n",
           CAM_PIN_MOSI, CAM_PIN_MISO, CAM_PIN_SCK, CAM_PIN_CS);

    fgx_model_t m;
    const size_t w_len = (size_t)(fgx_weights_end - fgx_weights);
    if (!fgx_open(&m, fgx_weights, w_len)) {
        printf("\nRESULT : FAIL - flash blob is malformed, no in_scale to quantize with\n");
        while (true) tight_loop_contents();
    }
    const float in_scale = m.hdr->in_scale;
    printf("in_scale  : %.9g  (from the blob, not hard-coded)\n", in_scale);
    printf("model in  : %ux%ux%u\n", (unsigned)m.hdr->in_size,
           (unsigned)m.hdr->in_size, (unsigned)m.hdr->in_ch);

    uint8_t id = probe_id();
    if (!cam_id_plausible(id)) {
        printf("\nRESULT : FAIL - no camera answering on the bus.\n"
               "         Check 3V3 (silk `3V3`, short edge) and GND (silk "
               "`GND`, long row), then that\n"
               "         MISO is on silk `1` and MOSI on silk `7` and not the "
               "other way round.\n");
        while (true) tight_loop_contents();
    }

    cam_bus_pio(8000000);
    printf("\n-- bring-up --\n");
    cam_begin(id, true);

    const uint8_t m128 = cam_mode_128(id);

    // --- the repeat-capture matrix ------------------------------------------
    // Seven captures of the same mode at the same rate, differing only in the
    // recipe, so that the one variable is the one under test. Rows 0 and 1 are
    // deliberately identical and deliberately first: row 0 reproduces the
    // known-good capture and row 1 reproduces the known-bad one, which is what
    // makes the remaining five rows mean anything. A matrix whose control does
    // not reproduce the fault is measuring a different bench than the one the
    // fault was found on.
    //
    // The whole matrix costs about a second and 7 x 33 ms of bus. Bisecting the
    // same three hypotheses one flash at a time costs an afternoon, and each
    // flash re-rolls whatever the sensor's state was.
    //
    // What it measured on 2026-08-03, one boot, one scene, 128x128 at 8 MHz:
    //
    //   #  recipe      crc32    verdict
    //   0  as-was      7f04a4ea a picture     <- first write of the value: fine
    //   1  as-was      c80a8564 CONSTANT      <- redundant write: blank
    //   2  no-rewrite  4f90cd14 a picture
    //   3  flush       c80a8564 CONSTANT      <- flushFifo does not rescue it
    //   4  norw+flush  859365a2 a picture
    //   5  settle300   2fb19c18 a picture
    //   6  everything  18628f40 a picture
    //
    // Row 3 is the one worth keeping in mind. flushFifo() -
    // writeReg(ARDUCHIP_FIFO_2, FIFO_CLEAR_MASK), which the vendor has commented
    // out at the head of cameraSetCapture() (ArducamCamera.c:339) - was the
    // leading hypothesis, and it is byte-for-byte irrelevant: row 3 returns the
    // identical constant to row 1. The vendor was right to comment it out, and
    // the thing that actually mattered was four lines up in a different function.
    static const cam_recipe_t MATRIX[] = {
        { "as-was",      true,  false,   0 },   // control: expect a picture
        { "as-was",      true,  false,   0 },   // control: expect a constant
        { "no-rewrite",  false, false,   0 },
        { "flush",       true,  true,    0 },
        { "norw+flush",  false, true,    0 },
        { "settle300",   false, false, 300 },
        { "everything",  false, true,  300 },
    };
    const int NMATRIX = (int)(sizeof MATRIX / sizeof MATRIX[0]);
    bool as_expected = true;

    printf("\n-- repeat-capture matrix: %ux%u, 8 MHz, same scene throughout --\n",
           128u, 128u);
    printf("  %-2s %-11s %-8s %-9s %s\n", "#", "recipe", "crc32", "first px", "verdict");
    for (int i = 0; i < NMATRIX; i++) {
        uint32_t len = cam_capture(&MATRIX[i], m128, CAM_IMAGE_PIX_FMT_RGB565,
                                   raw, RAW_MAX, NULL);
        if (len != 128u * 128u * 2u) {
            printf("  %-2d %-11s  -- capture failed, len %u\n",
                   i, MATRIX[i].name, (unsigned)len);
            as_expected = false;
            continue;
        }
        bool flat = cam_frame_is_constant(raw, len);
        // Rows 1 and 3 are the two that reproduce the fault; everything else
        // should be a picture. Stated as an expectation rather than used to
        // pick a recipe, because the recipe is not in question any more - what
        // this checks is whether a different module or a newer ArduChip
        // firmware behaves the same way, which is worth a second a boot.
        bool want_flat = (i == 1 || i == 3);
        if (flat != want_flat) as_expected = false;
        printf("  %-2d %-11s %08x %02x %02x     %-9s %s\n", i, MATRIX[i].name,
               (unsigned)cam_crc32(raw, len), raw[0], raw[1],
               flat ? "CONSTANT" : "a picture",
               flat == want_flat ? "" : "<-- NOT what 2026-08-03 measured");
    }

    const cam_recipe_t *use = &CAM_RECIPE_VENDOR;
    printf("\n  -> %s. Using recipe `%s` below.\n",
           as_expected ? "Matches the recorded matrix exactly"
                       : "DOES NOT match the recorded matrix - re-read the "
                         "comment on cam_capture()",
           use->name);

    // --- image controls -----------------------------------------------------
    // The first frames off this camera came out warm - mean RGB (118, 111, 54),
    // so blue at half of red. That is either the scene (a beige wall under
    // indoor light is genuinely that colour) or a camera default, and the
    // difference matters: the student was distilled on COCO, which is daylight-
    // ish, and a fixed colour cast is a distribution shift the model has no way
    // to know about.
    //
    // Two numbers settle it and neither is a PNG: the mean of the three channels
    // is exposure, the spread between them is white balance. Sweep the controls,
    // print both, and the decision is arithmetic.
    //
    // TWO captures per setting, and that is the second finding here rather than
    // caution. The repeat-capture fault above is caused by an I2C write to the
    // sensor, and every one of these controls is an I2C write to the sensor - so
    // the question of whether the *first* frame after a brightness change is
    // also blank is the same question, and worth one extra 100 ms capture to
    // answer rather than assume.
    static const struct { const char *name; uint8_t reg, val; } CTLS[] = {
        { "(as begin left it)", CAM_REG_EV_CONTROL,         0 },   // no-op probe
        { "auto-exposure on",   CAM_REG_AUTO_CONTROL,       AUTO_ON | AUTO_SEL_EXPOSURE },
        { "auto-gain on",       CAM_REG_AUTO_CONTROL,       AUTO_ON | AUTO_SEL_GAIN },
        { "auto-WB on",         CAM_REG_AUTO_CONTROL,       AUTO_ON | AUTO_SEL_WHITEBALANCE },
        { "WB mode office",     CAM_REG_WB_MODE_CONTROL,    2 },
        { "WB mode home",       CAM_REG_WB_MODE_CONTROL,    4 },
        { "WB mode auto",       CAM_REG_WB_MODE_CONTROL,    0 },
        { "EV +1",              CAM_REG_EV_CONTROL,         1 },
        { "EV +2",              CAM_REG_EV_CONTROL,         3 },
        { "EV back to 0",       CAM_REG_EV_CONTROL,         0 },
        { "brightness +1",      CAM_REG_BRIGHTNESS_CONTROL, 1 },
        { "brightness +2",      CAM_REG_BRIGHTNESS_CONTROL, 3 },
        { "brightness back",    CAM_REG_BRIGHTNESS_CONTROL, 0 },
    };
    printf("\n-- image controls: mean RGB after each, 128x128 @ 8 MHz --\n");
    printf("  %-20s %-6s %-18s %-18s\n", "setting", "reg", "1st capture", "2nd capture");
    for (size_t i = 0; i < sizeof CTLS / sizeof CTLS[0]; i++) {
        cam_write_reg(CTLS[i].reg, CTLS[i].val);
        if (!cam_wait_idle(CTLS[i].name)) continue;

        char cell[2][20];
        for (int pass = 0; pass < 2; pass++) {
            uint32_t len = cam_capture(use, m128, CAM_IMAGE_PIX_FMT_RGB565,
                                       raw, RAW_MAX, NULL);
            if (len != 128u * 128u * 2u) {
                snprintf(cell[pass], sizeof cell[pass], "failed len %u", (unsigned)len);
            } else if (cam_frame_is_constant(raw, len)) {
                snprintf(cell[pass], sizeof cell[pass], "CONSTANT");
            } else {
                int mean[3];
                cam_frame_means(raw, len, mean);
                snprintf(cell[pass], sizeof cell[pass], "%3d %3d %3d",
                         mean[0], mean[1], mean[2]);
            }
        }
        printf("  %-20s 0x%02x   %-18s %-18s\n",
               CTLS[i].name, CTLS[i].reg, cell[0], cell[1]);
    }
    // The negative result the two-captures-per-setting column was built to get:
    // no row above blanks its first capture. So the fault really is specific to
    // CAM_REG_CAPTURE_RESOLUTION and not to sensor I2C writes in general, which
    // is why cam_image_defaults() can write four registers and capture straight
    // afterwards without a throwaway frame.
    printf("  (no setting blanks its first capture - the fault is specific to "
           "the resolution register)\n");

    cam_image_defaults();

    // 8 MHz is ArduCAM's documented ceiling and the reference point.
    try_mode(use, "f128", m128, 128, 128, in_scale, true, 8000000);

    // 16 MHz is the interesting one. The register sweep above already agrees
    // with the bit-bang control at 16 MHz, but a register read is three bytes
    // and a frame is a 32,768-byte burst held under one CS, so sustained
    // signalling is a different claim from the one the sweep supports. What
    // settles it is the picture: a burst that drops or doubles a bit tears the
    // rest of the frame, which is obvious by eye and invisible in a CRC the
    // device computed over its own corrupted buffer.
    //
    // Worth 17 ms of a frame if it holds - read is 32.9 ms at 8 MHz against a
    // whole-frame inference of 851 ms, so this is not rounding error.
    try_mode(use, "f128fast", m128, 128, 128, in_scale, true, 16000000);

    // Question 3's fallback, taken unconditionally rather than only on failure.
    // It costs 150 KB of terminal and one second, and if 128x128 RGB565 does
    // not exist then this boot is the one that has to prove QVGA does - there
    // is no reason to spend a second flash finding out.
    try_mode(use, "qvga", cam_mode_qvga(id),
             320, 240, in_scale, false, 8000000);

    printf("\nRESULT : bus up, frames dumped. Render them:\n"
           "         uv run host/cam.py <logfile>\n");
    while (true) tight_loop_contents();
}
