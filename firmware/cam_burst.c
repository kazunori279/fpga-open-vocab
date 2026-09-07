// Does 0x01 burst frames into the 8 MB cache, and does the driver survive it?
//
// docs/camera.md has carried this register as an unprobed line for weeks: "0-254
// means frames = value + 1; 255 means the memory is full (8 MB)". That is the
// application note talking, and the application note is the source that has
// already been wrong twice on this module - 0x05 versus 0x06 for the frame
// source, and 0x07 bit 6's name. Nothing in ArduCAM's own driver writes 0x01, so
// there is no second opinion to read. The only way to know is to ask the board.
//
// WHAT MAKES THIS ANSWERABLE WITHOUT LOOKING AT A PICTURE. cam_collect() already
// reads the FIFO length out of 0x45/0x46/0x47 before it reads a byte of pixels.
// If a burst of N+1 frames is a real thing, the length after one trigger is
// (N+1) times the length after a trigger with 0x01 at zero - and that is a
// relation between two numbers this boot measured, with no constant in it. A
// frame size is not written down anywhere in this file.
//
// THE RULER IS MEASURED FIRST AND HAS TO HOLD STILL. Stage L takes four ordinary
// captures and requires all four lengths to agree before anything else runs. A
// board whose single-frame length wanders has no ruler, and every multiple below
// it would be arithmetic on noise. That is a VOID, not a result.
//
// AND A LENGTH IS NOT A FRAME. A FIFO four times as long could be four frames or
// one frame written four times, and the difference matters to the only use
// anybody has proposed for this register - keeping the exposure ramp as images.
// So stage P slices the burst at the ruler and crc32s each slice. All slices
// identical means the register pads; slices that differ means it captures.
//
// THE CONTROL IS THE PUT-BACK, and it is here because of what the last two
// weeks taught. bench/probe/20260907-hold/ found cam_image_auto_mask(CAM_AUTO_ALL)
// failing to switch a loop back ON 31 times in 56 - the un-set failing where
// the set worked. 0x01 is a register the driver never touches, so if writing it
// back to zero does not take, every capture for the rest of that boot is a
// multi-frame blob arriving in a single-frame buffer, and the caller sees a
// length mismatch it has no reason to attribute to a probe that finished. Stage
// Z re-runs stage L after the sweep and requires the same ruler back.
//
// 255 IS RUN LAST AND ON ITS OWN CLOCK. 8 MB is 256 frames at this resolution,
// and cam_collect()'s CAP_DONE poll gives up after 3 s - correctly, for the
// single frames it was written for. So this file polls CAP_DONE itself, with the
// bound printed next to the answer, and a stage that hits the bound reports the
// bound rather than a verdict.

#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/vreg.h"
#include "cam.h"
#include "cam_dump.h"    // cam_crc32
#include "qspi_park.h"

#ifndef FGX_SYS_KHZ
#define FGX_SYS_KHZ 320000
#endif

// Not in cam.h, and deliberately not added to it by this file. cam.h's register
// block is either transcribed from ArduCAM's driver or, in CAM_REG_FRAME_SOURCE's
// one labelled case, measured. This is neither yet. It gets a name there if the
// board agrees with the note.
#define ARDUCHIP_CAP_CTRL   0x01

// Eight frames, because that is the largest burst this file reads back whole and
// the buffer is what bounds it. 8 * 128 * 128 * 2 = 256 KB.
#define NFRAME_MAX  8
static uint8_t raw[NFRAME_MAX * 128 * 128 * 2];

static uint8_t m128;
static uint8_t cam_id;

// The counts asked for, in the order they are asked. Zero first, so the register
// is written on the baseline arm too and stage N's first row is the control on
// "writing 0x01 at all does nothing".
static const uint8_t NCOUNT[] = { 0, 1, 3, 7 };
#define NCOUNTS ((int)(sizeof NCOUNT / sizeof NCOUNT[0]))

// ---------------------------------------------------------------------------

// CAP_DONE, on a bound this file chooses per stage. cam_collect()'s own bound is
// 3 s and is right for one frame; 256 of them is not one frame.
static bool wait_done(uint32_t ms)
{
    const uint64_t t0 = time_us_64();
    while ((time_us_64() - t0) < (uint64_t)ms * 1000u) {
        if (cam_read_reg(ARDUCHIP_TRIG) & CAP_DONE_MASK) return true;
        sleep_us(200);
    }
    return false;
}

static uint32_t fifo_len(void)
{
    uint32_t l1 = cam_read_reg(FIFO_SIZE1);
    uint32_t l2 = cam_read_reg(FIFO_SIZE2);
    uint32_t l3 = cam_read_reg(FIFO_SIZE3);
    return ((l3 << 16) | (l2 << 8) | l1) & 0xffffffu;
}

static void set_count(uint8_t n)
{
    cam_write_reg(ARDUCHIP_CAP_CTRL, n);
    cam_wait_idle("capture count");
}

// One capture with the count register already set, reported as a length only.
// Returns 0 if CAP_DONE never came, and `us` gets trigger-to-CAP_DONE either way.
static uint32_t burst_len(uint32_t ms, uint32_t *us)
{
    cam_time_t t;
    const uint64_t t0 = time_us_64();
    if (!cam_trigger(&CAM_RECIPE_VENDOR, m128, CAM_IMAGE_PIX_FMT_RGB565, &t)) {
        *us = 0;
        return 0;
    }
    const bool done = wait_done(ms);
    *us = (uint32_t)(time_us_64() - t0);
    return done ? fifo_len() : 0;
}

// An ordinary capture through the shipped path, for the ruler and for stage T's
// denominator. Returns the FIFO length cam_collect() reported.
static uint32_t one_frame(uint32_t *us)
{
    cam_time_t t;
    const uint64_t t0 = time_us_64();
    uint32_t len = cam_capture(&CAM_RECIPE_VENDOR, m128,
                               CAM_IMAGE_PIX_FMT_RGB565,
                               raw, sizeof raw, &t);
    if (us) *us = (uint32_t)(time_us_64() - t0);
    return len;
}

// =========================================================================
// Stage L: the ruler
// =========================================================================
//
// A LENGTH OF ZERO IS COUNTED SEPARATELY AND NOT AVERAGED IN, which is a change
// made after boot 00 and before boot 01. That boot's closing ruler read
//
//   capture 0: 0 bytes / capture 1: 32768 / capture 2: 32768 / capture 3: 32768
//
// and the stage called the whole thing void, because "all four agree" was the
// only rule it had. Those are two different faults with two different owners: a
// ruler that wanders is a camera nobody can measure, and one empty capture
// followed by three good ones is a cost the stage before it charged. The stage
// now says which, and this stage is run between the stages so the charge lands
// on the right one.
#define NRULER 4
static uint32_t stage_l(const char *when, int *empties)
{
    printf("\n== stage L (%s): one frame, four times ==\n", when);
    uint32_t len[NRULER];
    int nz = 0;
    for (int i = 0; i < NRULER; i++) {
        len[i] = one_frame(NULL);
        if (len[i] == 0) nz++;
        printf("  capture %d: %u bytes%s\n", i, (unsigned)len[i],
               len[i] == 0 ? "   <- EMPTY" : "");
    }
    if (empties) *empties = nz;

    uint32_t r = 0;
    bool agree = true;
    for (int i = 0; i < NRULER; i++) {
        if (len[i] == 0) continue;
        if (r == 0) r = len[i];
        else if (len[i] != r) agree = false;
    }
    if (!agree || r == 0) {
        printf("  RULER VOID - the non-empty captures do not agree, so no "
               "multiple of them means anything\n");
        return 0;
    }
    if (nz)
        printf("  ruler: %u bytes a frame, after %d empty capture%s. The empty "
               "one is charged to the stage above, not to the camera.\n",
               (unsigned)r, nz, nz == 1 ? "" : "s");
    else
        printf("  ruler: %u bytes a frame\n", (unsigned)r);
    return r;
}

// =========================================================================
// Stage R: can 0x01 be read back?
// =========================================================================
//
// Worth one stage of its own because CAM_REG_AUTO_CONTROL cannot be, and that
// single fact is what made #33 a three-week problem: a write-only switch has to
// be inferred from its effect. If 0x01 reads back, a caller can check its own
// state directly and stage Z below stops being the only way to know.
static void stage_r(void)
{
    printf("\n== stage R: does 0x01 read back what was written? ==\n");
    const uint8_t before = cam_read_reg(ARDUCHIP_CAP_CTRL);
    int echoed = 0;
    printf("  at entry: %02x\n", before);
    for (int i = 0; i < NCOUNTS; i++) {
        set_count(NCOUNT[i]);
        const uint8_t got = cam_read_reg(ARDUCHIP_CAP_CTRL);
        const bool ok = (got == NCOUNT[i]);
        if (ok) echoed++;
        printf("  wrote %02x, read %02x   %s\n", NCOUNT[i], got,
               ok ? "echoes" : "does NOT echo");
    }
    set_count(0);
    printf("  %d of %d writes echoed - 0x01 is %s\n", echoed, NCOUNTS,
           echoed == NCOUNTS ? "readable"
           : echoed == 0     ? "write-only, like 0x30"
                             : "PARTLY echoing, which is neither");
}

// =========================================================================
// Stage N: is the length (N+1) rulers?
// =========================================================================
static void stage_n(uint32_t ruler, uint32_t *got_len)
{
    printf("\n== stage N: the FIFO length against the count register ==\n");
    printf("\n  0x01  frames wanted   FIFO bytes   / ruler   verdict\n");
    int agree = 0;
    for (int i = 0; i < NCOUNTS; i++) {
        const uint8_t n = NCOUNT[i];
        const uint32_t want = (uint32_t)(n + 1) * ruler;
        set_count(n);
        uint32_t us = 0;
        const uint32_t len = burst_len(5000u, &us);
        got_len[i] = len;

        char q[24];
        if (len == 0)                     snprintf(q, sizeof q, "-");
        else if (len % ruler == 0)        snprintf(q, sizeof q, "%u exactly",
                                                   (unsigned)(len / ruler));
        else                              snprintf(q, sizeof q, "%u.%u",
                                                   (unsigned)(len / ruler),
                                                   (unsigned)((len % ruler) * 10u
                                                              / ruler));
        const bool ok = (len == want);
        if (ok) agree++;
        printf("  %02x    %-13u  %-11u  %-8s %s\n",
               n, (unsigned)(n + 1), (unsigned)len, q,
               len == 0 ? "NO CAP_DONE in 5 s"
               : ok     ? "as the note says"
               : len == ruler ? "ONE FRAME - the count register did nothing"
                              : "neither one frame nor the note's count");
        printf("        %u us to CAP_DONE\n", (unsigned)us);
    }
    printf("\n  %d of %d counts gave (N+1) rulers\n", agree, NCOUNTS);
    set_count(0);
}

// =========================================================================
// Stage P: are the slices different frames, or one frame repeated?
// =========================================================================
static void stage_p(uint32_t ruler)
{
    const uint8_t n = NFRAME_MAX - 1;
    printf("\n== stage P: slicing a burst of %u at the ruler ==\n",
           (unsigned)(n + 1));

    set_count(n);
    uint32_t us = 0;
    const uint32_t len = burst_len(5000u, &us);
    if (len == 0) { printf("  no CAP_DONE; nothing to slice\n"); set_count(0); return; }
    if (len > sizeof raw) {
        printf("  FIFO is %u bytes and the buffer is %u; not read\n",
               (unsigned)len, (unsigned)sizeof raw);
        set_count(0);
        return;
    }

    // The frame is already captured and sitting in the ArduChip, so this is the
    // read half of cam_collect() and nothing else. Doing it by hand rather than
    // calling cam_collect() only because that function wants to have issued the
    // trigger itself.
    cam_time_t t;
    const uint32_t got = cam_collect(raw, sizeof raw, &t);
    if (got != len) {
        printf("  collect returned %u against a length of %u; not sliced\n",
               (unsigned)got, (unsigned)len);
        set_count(0);
        return;
    }

    const uint32_t nsl = len / ruler;
    printf("\n  slice   crc32     R   G   B\n");
    uint32_t crc[NFRAME_MAX];
    int distinct = 0;
    for (uint32_t s = 0; s < nsl && s < NFRAME_MAX; s++) {
        const uint8_t *p = raw + s * ruler;
        crc[s] = cam_crc32(p, ruler);
        int m[3];
        cam_frame_means(p, ruler, m);
        bool seen = false;
        for (uint32_t k = 0; k < s; k++) if (crc[k] == crc[s]) seen = true;
        if (!seen) distinct++;
        printf("  %-6u  %08x  %3d %3d %3d%s\n",
               (unsigned)s, (unsigned)crc[s], m[0], m[1], m[2],
               seen ? "   = an earlier slice" : "");
    }
    printf("\n  %d distinct slice%s out of %u\n",
           distinct, distinct == 1 ? "" : "s", (unsigned)nsl);
    printf("  %s\n",
           distinct == 1 ? "ONE FRAME, REPEATED - the register lengthens the "
                           "FIFO and does not fill it with captures"
           : (int)nsl == distinct ? "every slice differs - these are captures"
                                  : "some slices repeat; see the crcs");
    set_count(0);
}

// =========================================================================
// Stage T: is a burst faster than the same frames one at a time?
// =========================================================================
//
// The whole point of the register, if it works, is that the trigger and the
// frame boundary are paid once instead of N times. Both arms here include the
// SPI read of every byte, because a caller has to move the pixels either way.
static void stage_t(uint32_t ruler)
{
    const uint8_t n = NFRAME_MAX - 1;
    printf("\n== stage T: %u frames, singly and in one burst ==\n",
           (unsigned)(n + 1));

    const uint64_t s0 = time_us_64();
    for (int i = 0; i <= n; i++) (void)one_frame(NULL);
    const uint32_t single_us = (uint32_t)(time_us_64() - s0);

    set_count(n);
    const uint64_t b0 = time_us_64();
    uint32_t us = 0;
    const uint32_t len = burst_len(5000u, &us);
    cam_time_t t;
    if (len && len <= sizeof raw) (void)cam_collect(raw, sizeof raw, &t);
    const uint32_t burst_us = (uint32_t)(time_us_64() - b0);
    set_count(0);

    printf("  %u singles : %u us  (%u us a frame)\n",
           (unsigned)(n + 1), (unsigned)single_us,
           (unsigned)(single_us / (n + 1)));
    if (len == 0) {
        printf("  one burst  : no CAP_DONE, so no comparison\n");
        return;
    }
    printf("  one burst  : %u us for %u bytes (%u rulers)\n",
           (unsigned)burst_us, (unsigned)len, (unsigned)(len / ruler));
    if (len == (uint32_t)(n + 1) * ruler)
        printf("  the burst is %u%% of the singles, for the same pixels\n",
               (unsigned)((uint64_t)burst_us * 100u / single_us));
    else
        printf("  NOT the same pixels - the burst returned %u rulers, not %u, "
               "so the two times are not comparable\n",
               (unsigned)(len / ruler), (unsigned)(n + 1));
}

// =========================================================================
// Stage M: 255, the one value the note describes differently
// =========================================================================
static void stage_m(uint32_t ruler)
{
    printf("\n== stage M: 0x01 = 255, which the note calls 'memory full' ==\n");
    printf("  the cache is 8 MB, so the note's claim is %u rulers, and at this\n"
           "  boot's frame rate that is tens of seconds. Bound: 40 s.\n",
           (unsigned)(8u * 1024u * 1024u / ruler));

    set_count(255);
    uint32_t us = 0;
    const uint32_t len = burst_len(40000u, &us);
    if (len == 0) {
        printf("  no CAP_DONE inside 40 s. That is the bound and not a verdict; "
               "a longer one may or may not finish.\n");
        set_count(0);
        return;
    }

    const uint32_t eight_mb = 8u * 1024u * 1024u;
    printf("  %u bytes after %u us\n", (unsigned)len, (unsigned)us);
    printf("  against 8 MB (%u): %s%u bytes\n", (unsigned)eight_mb,
           len >= eight_mb ? "+" : "-",
           (unsigned)(len >= eight_mb ? len - eight_mb : eight_mb - len));
    printf("  against the ruler: %u whole frames and %u bytes over\n",
           (unsigned)(len / ruler), (unsigned)(len % ruler));
    // Two ways to be wrong and they are not the same. A length that is a whole
    // number of frames but not 8 MB means the cache is not the size the note
    // says. A length that is neither means the fill stops mid-frame, and the
    // last frame in that FIFO is torn - which a caller slicing at the ruler
    // would read as a frame.
    printf("  %s\n",
           len == eight_mb        ? "exactly 8 MB, and a whole number of frames"
           : len % ruler == 0     ? "a whole number of frames, but not 8 MB"
                                  : "NEITHER 8 MB NOR A WHOLE NUMBER OF FRAMES - "
                                    "the last frame in this FIFO is torn");
    set_count(0);
}

int main(void)
{
    fgx_qspi_park();

    if (FGX_SYS_KHZ > 220000)
        vreg_set_voltage(VREG_VOLTAGE_1_25);
    else if (FGX_SYS_KHZ > 150000)
        vreg_set_voltage(VREG_VOLTAGE_1_20);
    sleep_ms(10);
    bool at_rate = set_sys_clock_khz(FGX_SYS_KHZ, false);
    if (!at_rate)
        set_sys_clock_khz(150000, true);
    sleep_ms(50);

    stdio_init_all();
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    cam_bus_init(pio0);

    printf("\n=== 0x01: frames burst into the cache, or not ===\n\n");
    printf("clock     : %u MHz sys%s\n",
           (unsigned)(clock_get_hz(clk_sys) / 1000000),
           at_rate ? "" : "   <- ASKED FOR ANOTHER RATE AND DID NOT GET IT");

    cam_bus_bitbang();
    cam_id = cam_read_reg(CAM_REG_SENSOR_ID);
    if (!cam_id_plausible(cam_id)) {
        printf("\nVERDICT : nocamera   (id 0x%02x)\n", cam_id);
        while (true) tight_loop_contents();
    }
    cam_bus_pio(8000000);

    cam_begin(cam_id, true);
    m128 = cam_mode_128(cam_id);
    cam_image_defaults();
    for (int i = 0; i < 20; i++) (void)one_frame(NULL);

    int e0 = 0, en = 0, et = 0, em = 0;
    const uint32_t ruler = stage_l("before", &e0);
    if (!ruler) {
        printf("\nVERDICT : void   (no ruler)\n");
        while (true) tight_loop_contents();
    }

    stage_r();

    // THE CONTROL, RUN FOUR TIMES. Everything below writes a register the driver
    // does not know exists, and each stage puts it back to zero before it
    // returns. A ruler taken between the stages is what attributes an empty
    // capture to the stage that caused it - one taken only at the end can say a
    // capture was lost and cannot say to what.
    uint32_t got[NCOUNTS];
    stage_n(ruler, got);
    const uint32_t rn = stage_l("after N", &en);

    stage_p(ruler);
    stage_t(ruler);
    const uint32_t rt = stage_l("after P and T", &et);

    stage_m(ruler);
    const uint32_t rm = stage_l("after M", &em);

    int nmatch = 0;
    for (int i = 0; i < NCOUNTS; i++)
        if (got[i] == (uint32_t)(NCOUNT[i] + 1) * ruler) nmatch++;

    const bool same = (rn == ruler) && (rt == ruler) && (rm == ruler);
    printf("\nVERDICT : count=%s  ruler=%s  empties=%d/%d/%d/%d\n",
           nmatch == NCOUNTS ? "burst"
           : nmatch == 0     ? "noeffect"
                             : "partial",
           same ? "unchanged" : "MOVED",
           e0, en, et, em);
    if (!same)
        printf("          %u before, then %u, %u, %u. Writing 0 back to 0x01 "
               "did not put the frame size back, which is the shape of "
               "20260907-hold's unlock fault on a different register.\n",
               (unsigned)ruler, (unsigned)rn, (unsigned)rt, (unsigned)rm);
    if (em > en && em > et)
        printf("          and the empty captures are charged to stage M: %d "
               "after the 8 MB burst against %d and %d after the others. A "
               "255 costs the capture that follows it.\n", em, en, et);

    while (true) tight_loop_contents();
}
