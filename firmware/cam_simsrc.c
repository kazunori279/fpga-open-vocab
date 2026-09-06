// Does the ArduChip have a frame source that is not the sensor? (#30, #33)
//
// WHY THIS IS A SEPARATE BINARY AND NOT A HOTKEY IN m9. Every drift measurement
// this repo has run asks whether scores move while the scene is still, and none
// of them can separate a camera that is re-deciding from anything else in the
// chain that might be - the board warming up, the room, the encoder, the link.
// If the ArduChip will feed the pipeline frames the sensor never saw, that whole
// question collapses into one bench: run the scoring path on a source that
// CANNOT drift, and if `common` still walks, the drift is not the camera.
//
// The register that would do it is `0x05` bit[7], data source: 0 camera, 1
// simulated. `0x06` bit[7] is documented as a 32-bit counter pattern.
//
// AND THE PROVENANCE OF THOSE TWO LINES IS THE PROBLEM. They are from the Mega
// SPI Camera Application Note, September 2023, section 4 - which is the document
// cam.h's register block explicitly says it did NOT transcribe from, "and not
// from the application note, which disagrees with it". Checked on 2026-09-07
// against ArduCAM's own driver, github.com/ArduCAM/Arducam_Mega,
// src/Arducam/ArducamCamera.c: **the driver never reads or writes 0x05 or 0x06
// at all.** There is no vendor code exercising this. It may not exist on this
// FPGA revision, it may exist and not reach the RGB565 path, or the app note may
// simply be wrong the way cam.h has already caught it being.
//
// So this is a probe, not a feature. Nothing in the scoring path is allowed to
// depend on a simulated source until a boot has printed the verdict below.
//
// THE DECISIVE TEST IS NOT "DOES THE PICTURE LOOK SYNTHETIC". It is whether
// consecutive frames are BIT-IDENTICAL, because that is the exact property #30
// needs and the only one that makes the bench worth running. A source that
// changes the picture but still varies frame to frame is not a fixed source and
// buys nothing. So every frame here is reduced to a crc32 and the verdict counts
// distinct values.
//
// THE FOUR OUTCOMES, NAMED BEFORE THE RUN so the verdict is a lookup and not a
// judgement made while reading the output:
//
//   1. frames change from live AND go bit-identical -> the mode exists and is
//      what #30 needs. docs/camera.md item 2 is live; wire it into m9 next.
//   2. frames do not change at all -> the register is a no-op on this revision.
//      The app note is wrong here too. Item 2 is dead, go to item 1, the I2C
//      passthrough readback.
//   3. frames still differ frame to frame -> whatever is feeding the FIFO is
//      not a fixed source, so it is not usable for #30 either way.
//
//      AND OUTCOME 3 DOES NOT SEPARATE "IT DID NOTHING" FROM "IT DID SOMETHING
//      THAT ALSO VARIES", which is worth saying plainly because a crc cannot.
//      A still scene on a live sensor produces six different frames, so does a
//      hypothetical noisy synthetic one, and neither shares a crc with the
//      other. The mean RGB rows are printed for both sets so a reader can see
//      whether the picture moved at all; no threshold is applied to them here,
//      because picking one that calls 130 -> 118 "a different source" is fitting
//      a constant to the first run that ever produced the number. Either way
//      outcome 3 sends you to the same place outcome 2 does.
//   4. capture breaks - zero length, or constant-frame - > 0x05 and 0x06 sit
//      between ARDUCHIP_FIFO (0x04) and ARDUCHIP_FIFO_2 (0x07), so a write here
//      disturbing capture is a live possibility and not a surprise. Recoverable:
//      USB out for ten seconds. Reported, not worked around.
//
// A READBACK COMES FIRST AND MAY END IT IN ONE LINE. The app note types
// 0x00-0x0C as RW, unlike the whole 0x20-0x35 control surface, which is why #33
// exists at all. So the write can be checked: if `0x05` does not read back what
// was just written to it, the register is not implemented and outcome 2 is
// settled without capturing anything.
//
// The original value of each register is read before the write and restored
// after, and a live capture at the end has to come back or the run says so.

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"

#include "cam.h"
#include "cam_dump.h"    // cam_crc32
#include "qspi_park.h"

// Documented by the application note only. Deliberately not in cam.h: that file
// is transcribed from the vendor driver, and putting an unexercised register in
// it would blur the one distinction this probe exists to keep.
#define ARDUCHIP_DATA_SOURCE  0x05   // bit[7]: 0 camera, 1 simulated
#define ARDUCHIP_COUNTER_SRC  0x06   // bit[7]: 32-bit counter pattern
#define SOURCE_SIM_MASK       0x80u

// Six is enough to tell "identical" from "varying" and short enough that a
// broken capture path does not cost a minute per mode.
#define NFRAME 6

static uint8_t raw[128 * 128 * 2];

typedef struct {
    int      n;                // frames that returned the right length
    int      uniq;             // distinct crc32 among them
    int      flat;             // frames that were a single repeated value
    uint32_t crc[NFRAME];
    int      mean[3];          // of the last good frame
} shot_t;

// `mode` is the already-legacy-resolved resolution code.
static shot_t capture_set(uint8_t mode)
{
    shot_t s = { 0 };
    for (int i = 0; i < NFRAME; i++) {
        cam_time_t t;
        uint32_t len = cam_capture(&CAM_RECIPE_VENDOR, mode,
                                   CAM_IMAGE_PIX_FMT_RGB565,
                                   raw, sizeof raw, &t);
        if (len != sizeof raw)
            continue;
        if (cam_frame_is_constant(raw, len))
            s.flat++;
        s.crc[s.n++] = cam_crc32(raw, len);
        cam_frame_means(raw, len, s.mean);
    }
    for (int i = 0; i < s.n; i++) {
        bool seen = false;
        for (int k = 0; k < i; k++)
            if (s.crc[k] == s.crc[i]) seen = true;
        if (!seen) s.uniq++;
    }
    return s;
}

static void report(const char *what, const shot_t *s)
{
    printf("  %-18s %d/%d good   %d distinct   %d flat   mean RGB %d %d %d\n",
           what, s->n, NFRAME, s->uniq, s->flat,
           s->mean[0], s->mean[1], s->mean[2]);
    printf("  %-18s", "");
    for (int i = 0; i < s->n; i++)
        printf(" %08x", (unsigned)s->crc[i]);
    printf("\n");
}

// Do any two sets share a frame? If the "simulated" set contains a crc the live
// set also produced, the write did not change the source.
static bool overlaps(const shot_t *a, const shot_t *b)
{
    for (int i = 0; i < a->n; i++)
        for (int k = 0; k < b->n; k++)
            if (a->crc[i] == b->crc[k]) return true;
    return false;
}

// One register, the whole sequence: read, write bit 7, read back, capture,
// restore. Returns the outcome number from the header comment.
static int try_source(const char *name, uint8_t reg, uint8_t mode,
                      const shot_t *live)
{
    printf("\n-- %s (0x%02x bit[7]) --\n", name, reg);

    uint8_t was = cam_read_reg(reg);
    cam_wait_idle(name);
    cam_write_reg(reg, (uint8_t)(was | SOURCE_SIM_MASK));
    cam_wait_idle(name);
    uint8_t back = cam_read_reg(reg);
    printf("  was 0x%02x, wrote 0x%02x, reads back 0x%02x\n",
           was, (uint8_t)(was | SOURCE_SIM_MASK), back);

    int outcome;
    if (!(back & SOURCE_SIM_MASK)) {
        // The app note types this range RW. It did not take the bit, so there
        // is nothing behind it to capture from.
        printf("  bit 7 did not stick. The register is not implemented on this "
               "FPGA revision\n"
               "  (%02x), so there is no simulated source to capture from and "
               "no point capturing.\n",
               cam_read_reg(CAM_REG_FPGA_VERSION_NUMBER));
        outcome = 2;
    } else {
        shot_t sim = capture_set(mode);
        report("simulated", &sim);

        if (sim.n == 0)
            outcome = 4;
        else if (overlaps(&sim, live))
            // A crc this run has already seen from the live sensor. Two
            // different sources producing the same 32,768 bytes is not a
            // coincidence worth entertaining, so the write did nothing.
            outcome = 2;
        else if (sim.flat == sim.n)
            // Every frame a single repeated value is the blanking fault's
            // signature, not a test pattern. cam.h has it as a capture failure
            // and it is one here too.
            outcome = 4;
        else if (sim.uniq == 1)
            outcome = 1;
        else
            outcome = 3;
    }

    cam_wait_idle(name);
    cam_write_reg(reg, was);
    cam_wait_idle(name);
    printf("  restored to 0x%02x\n", was);
    return outcome;
}

static const char *verdict_text(int o)
{
    switch (o) {
    case 1: return "the source exists and is FIXED - this is what #30 needs. "
                   "Wire it into m9.";
    case 2: return "no effect. The application note does not describe this "
                   "revision; go to the I2C passthrough (#33 item 1).";
    case 3: return "frames still vary, so there is no fixed source here "
                   "whether or not the write did\n                   anything. "
                   "Compare the mean RGB rows above, then go to #33 item 1.";
    default: return "the capture path broke. Power-cycle the board - USB out "
                    "for ten seconds - before running anything else.";
    }
}

int main(void)
{
    // #9, and the same first statement for the same reason as cam_probe.c.
    fgx_qspi_park();

    stdio_init_all();
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    cam_bus_init(pio0);

    printf("\n=== Is there a frame source that is not the sensor? (#30) ===\n\n");
    printf("clock     : %u MHz sys\n",
           (unsigned)(clock_get_hz(clk_sys) / 1000000));

    cam_bus_bitbang();
    uint8_t id = cam_read_reg(CAM_REG_SENSOR_ID);
    if (!cam_id_plausible(id)) {
        printf("\nRESULT : FAIL - no camera answering on the bus (id 0x%02x). "
               "Nothing below can run.\n", id);
        while (true) tight_loop_contents();
    }
    // 8 MHz for register writes, and this probe writes nothing but registers
    // until it captures. See cam.h: a 16 MHz write lands and returns a black
    // frame, which is exactly the outcome this probe would misread as a result.
    cam_bus_pio(8000000);
    printf("sensor id : 0x%02x\n", id);

    printf("\n-- bring-up --\n");
    cam_begin(id, true);
    const uint8_t m128 = cam_mode_128(id);

    // THE BASELINE HAS TO EARN ITS PLACE. If the live camera also returns six
    // identical frames, then "bit-identical under the write" means nothing -
    // it was already true. A still scene on a working sensor does not do this,
    // but a lens cap does, and so does the blanking fault.
    printf("\n-- baseline: the live sensor --\n");
    shot_t live = capture_set(m128);
    report("live", &live);
    if (live.n == 0) {
        printf("\nRESULT : FAIL - the camera captures nothing before this probe "
               "has written anything.\n         Fix that first; there is no "
               "baseline to compare a simulated source against.\n");
        while (true) tight_loop_contents();
    }
    if (live.uniq == 1) {
        printf("\nRESULT : VOID - six live frames were bit-identical, so this "
               "probe cannot tell a fixed\n         source from a fixed scene. "
               "Check the lens cap and the room, then run it again.\n");
        while (true) tight_loop_contents();
    }

    int o5 = try_source("data source", ARDUCHIP_DATA_SOURCE, m128, &live);
    int o6 = try_source("counter pattern", ARDUCHIP_COUNTER_SRC, m128, &live);

    // Did the camera come back? A probe that leaves the board in a state the
    // next run inherits without saying so is the thing run.sh's flags exist for.
    printf("\n-- after restoring both registers --\n");
    shot_t back = capture_set(m128);
    report("live again", &back);

    printf("\nRESULT : 0x05 -> outcome %d, %s\n", o5, verdict_text(o5));
    printf("         0x06 -> outcome %d, %s\n", o6, verdict_text(o6));
    if (back.n == 0 || back.uniq == 1)
        printf("         AND THE CAMERA DID NOT COME BACK. Power-cycle before "
               "the next run; do not\n         trust a bench taken on this "
               "boot.\n");
    else
        printf("         The camera came back: %d/%d good, %d distinct.\n",
               back.n, NFRAME, back.uniq);

    while (true) tight_loop_contents();
}
