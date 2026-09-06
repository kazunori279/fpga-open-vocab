// Can this module be told an exposure, or only told to stop deciding one? (#30)
//
// THE REPO CONTRADICTS ITSELF ABOUT THIS AND THE CODE HOLDS THE STRONGER CLAIM.
// firmware/cam.h:246 states it as fact:
//
//     There is no manual value to write on this module - the register takes a
//     switch, not a number - so a lock can only ever mean "stop deciding",
//     never "use this number".
//
// That is true OF 0x30, which is a selector with an on-bit. But the Mega SPI
// Camera Application Note, September 2023, section 4, lists five more registers
// that are not 0x30 at all:
//
//     0x31 / 0x32          manual gain     [9:8] / [7:0]
//     0x33 / 0x34 / 0x35   manual exposure [19:16] / [15:8] / [7:0]
//
// docs/camera.md has said since 2026-09-06 that writing these "turns that hope
// into a lock". So one file says the module cannot be given a number and
// another says here are the five registers that take one, and nothing in the
// repo has run the experiment. This is the same shape as the 1200-baud CDC
// touch, where four files disagreed and the fix was to make them all say it was
// unmeasured rather than to pick a winner. This binary measures it instead.
//
// IT MATTERS TODAY AND NOT IN THE ABSTRACT. bench/soak/20260907-camlock-cold/
// ran #30's lock arm cold, sixteen runs, and locking exposure and gain did not
// reduce the common-mode walk - the locked mean was higher and three of eight
// locked runs moved their level by 14 to 18 counts after the freeze. cam.h's
// own note is that switching a loop off "drifts rather than latching". If these
// five registers work, that whole arm was a hope and can be rebuilt as a lock.
//
// THE READBACK CANNOT SETTLE IT, WHICH IS WHY THIS IS A SWEEP. The app note
// types the entire 0x20-0x35 control surface WO - that is the structural half
// of #33 and the reason cam_read_reg(0x30) returns 00. So there is no asking
// the register what it holds; the only witness is the pixels.
//
// A SINGLE BEFORE-AND-AFTER IS NOT ENOUGH EITHER. A room changes, a cloud
// moves, and this repo has nineteen runs of one desk spanning a ceiling of
// 1.000 to 0.579. So the test is a sweep with its own null and its own retrace,
// and the verdict rule is fixed here, before the run:
//
//   NULL     - auto exposure and gain switched off, N captures, nothing
//              written. This is what the scene and the un-latched loops do on
//              their own over the length of a sweep. It is the noise floor and
//              it is measured, not assumed.
//   UP       - the same N captures with an ascending exposure value written
//              before each.
//   DOWN     - the same values descending.
//
//   RESPONDS requires BOTH:
//     (a) the UP luma range is larger than the NULL luma range, and
//     (b) DOWN retraces UP - the two agree at matched values, within the NULL
//         range, so the pixels are following the number and not the clock.
//
//   Either one alone is not enough. (a) without (b) is a scene that drifted
//   while the sweep happened to be ascending. (b) without (a) is two flat lines
//   agreeing that nothing happened.
//
// No threshold is picked beyond "larger than the null", because a threshold
// chosen after seeing the first sweep is a constant fitted to it.
//
// WRITE ORDER IS AN ASSUMPTION AND IS STATED. The three exposure bytes go
// high-to-low, [19:16] then [15:8] then [7:0], which is the app note's order.
// If the module latches on a different byte the sweep would show as no
// response, so a null result here is a null result about this order and not
// about the registers absolutely.
//
// ---------------------------------------------------------------------------
// SETTLE CAPTURES, ADDED AFTER THE FIRST RUN. See
// bench/probe/20260907-manexp/no-settle.log for the run that forced this.
//
// The first version measured immediately after each write and the rule above
// returned "does not respond" for exposure - on a sweep whose UP range was 228
// against a NULL range of 1. A null that tight with a spread that wide is not a
// register doing nothing, so the rule was wrong rather than the registers, and
// the reason is mechanical: **the sensor applies an exposure at a frame
// boundary, so the capture taken straight after a write shows the PREVIOUS
// value.** Every point in that run was reporting its predecessor, which is
// exactly what breaks (b) while leaving (a) intact - UP and DOWN visit the same
// values in opposite orders, so a one-step shift misaligns them maximally.
//
// The fix is a settle, not a threshold. THE VERDICT RULE ABOVE IS UNCHANGED,
// deliberately: if the registers work, discarding captures between the write
// and the measurement makes UP and DOWN retrace under the same rule that just
// rejected them, and if they do not work, nothing here can rescue them. A rule
// relaxed until the data passes is not a rule.
//
// SETTLE is 3 because the first run's shift implies one frame and three is
// margin. It is not tuned - no value of it was tried and rejected.
//
// ---------------------------------------------------------------------------
// A THIRD PASS AND A SECOND RULE, ADDED AFTER THE SECOND RUN. See
// bench/probe/20260907-manexp/settle.log.
//
// The settle worked and rule (b) still said no, and this time the rule is
// provably at fault rather than arguably. Rule (b) is
//
//     worst disagreement between UP and DOWN  <=  range of the NULL
//
// which compares a BETWEEN-PASS quantity against a WITHIN-PASS one. Those are
// not the same kind of number, and the settle made that fatal: with the null
// now perfectly flat - range 0, six identical captures of a still scene - the
// right-hand side is zero and NO amount of response can pass, because any real
// register has some hysteresis. Rule (b) became strictly unsatisfiable exactly
// because the measurement got better. That is a defect in how the rule was
// built, visible from the rule alone, and it is not the same thing as a rule
// that merely returned an unwelcome answer.
//
// I am not rescoring the runs that have happened. Rule (a)/(b) keeps its
// verdict on them and the logs keep it in writing. What follows is a different
// rule, fixed here, before the run it will judge:
//
//   MIX      - a third pass over the same values in a third order: the
//              even-indexed values ascending, then the odd-indexed ones. Not
//              ascending and not descending, so nothing that drifts
//              monotonically with time can line up with all three passes.
//
//   Each value is now visited three times, once per pass. For every value take
//   the spread of its three readings, and take the spread of the per-value
//   means across values.
//
//   RESPONDS-B iff  spread ACROSS values  >  worst spread WITHIN a value.
//
//   In words: revisiting the same number lands you closer together than
//   visiting different numbers does. That is the whole question - does the
//   written value pin the pixels - and it is the standard between-group versus
//   within-group comparison rather than a homemade one.
//
// THERE IS NO CONSTANT IN RULE (c), WHICH IS THE POINT. It has no threshold, no
// tolerance and no null to compare against, so there is nothing in it that
// could have been chosen to make the data pass. Both quantities come from the
// same three passes. A rule with a free parameter written after seeing the data
// would be fitting; this one has no free parameter to fit.

#include <stdio.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "hardware/vreg.h"

#include "cam.h"
#include "qspi_park.h"

// Application note only. Deliberately not in cam.h for the same reason
// cam_simsrc.c keeps 0x05 and 0x06 out of it: that file is transcribed from the
// vendor driver, which touches none of these.
#define CAM_REG_MANUAL_GAIN_H      0x31   // [9:8]
#define CAM_REG_MANUAL_GAIN_L      0x32   // [7:0]
#define CAM_REG_MANUAL_EXPOSURE_H  0x33   // [19:16]
#define CAM_REG_MANUAL_EXPOSURE_M  0x34   // [15:8]
#define CAM_REG_MANUAL_EXPOSURE_L  0x35   // [7:0]

// Geometric rather than linear, four decades of it, because the units are not
// documented and a linear sweep across a 20-bit field would spend seven of its
// eight points in whichever end turns out to be saturated.
static const uint32_t EXPOSURE[] = {
    0x00010, 0x00040, 0x00100, 0x00400, 0x01000, 0x04000, 0x10000, 0x40000,
};
#define NEXP ((int)(sizeof EXPOSURE / sizeof EXPOSURE[0]))

// 10 bits, so the whole field fits in six geometric points.
static const uint32_t GAIN[] = { 0x001, 0x004, 0x010, 0x040, 0x100, 0x3ff };
#define NGAIN ((int)(sizeof GAIN / sizeof GAIN[0]))

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;   // the legacy-resolved resolution code, set in main()

// Captures discarded after any change of state before the one that counts.
#define SETTLE 3

// Mean of the three channels. The spread between them is white balance, which
// this probe does not touch and leaves running.
static int luma(void)
{
    cam_time_t t;
    uint32_t len = cam_capture(&CAM_RECIPE_VENDOR, m128,
                               CAM_IMAGE_PIX_FMT_RGB565,
                               raw, sizeof raw, &t);
    if (len != sizeof raw)
        return -1;
    int m[3];
    cam_frame_means(raw, len, m);
    return (m[0] + m[1] + m[2]) / 3;
}

static void write_exposure(uint32_t v)
{
    cam_write_reg(CAM_REG_MANUAL_EXPOSURE_H, (uint8_t)((v >> 16) & 0x0f));
    cam_wait_idle("exp h");
    cam_write_reg(CAM_REG_MANUAL_EXPOSURE_M, (uint8_t)((v >> 8) & 0xff));
    cam_wait_idle("exp m");
    cam_write_reg(CAM_REG_MANUAL_EXPOSURE_L, (uint8_t)(v & 0xff));
    cam_wait_idle("exp l");
}

static void write_gain(uint32_t v)
{
    cam_write_reg(CAM_REG_MANUAL_GAIN_H, (uint8_t)((v >> 8) & 0x03));
    cam_wait_idle("gain h");
    cam_write_reg(CAM_REG_MANUAL_GAIN_L, (uint8_t)(v & 0xff));
    cam_wait_idle("gain l");
}

// Discard SETTLE captures, then measure. Every point in the three passes goes
// through here, including the null, so the null measures the scene over the
// same number of captures a swept point costs and stays a fair floor.
static int settled_luma(void)
{
    for (int i = 0; i < SETTLE; i++)
        (void)luma();
    return luma();
}

static int range(const int *v, int n)
{
    int lo = 255, hi = 0;
    for (int i = 0; i < n; i++) {
        if (v[i] < 0) continue;
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    return hi >= lo ? hi - lo : 0;
}

// How far the two passes disagree at matched values. UP is ascending and DOWN
// is the same list descending, so DOWN[n-1-i] was taken at the same value as
// UP[i].
static int retrace_gap(const int *up, const int *down, int n)
{
    int worst = 0;
    for (int i = 0; i < n; i++) {
        int a = up[i], b = down[n - 1 - i];
        if (a < 0 || b < 0) continue;
        int d = a > b ? a - b : b - a;
        if (d > worst) worst = d;
    }
    return worst;
}

// Rule (c). obs[v][p] is the reading for value v on pass p. Returns true when
// the spread across values beats the worst spread within a value, and reports
// both numbers so the margin is visible and not just the boolean.
static bool between_beats_within(const int obs[][3], int n,
                                 int *out_between, int *out_within)
{
    int mean[16];
    int worst_within = 0;

    for (int v = 0; v < n; v++) {
        int three[3] = { obs[v][0], obs[v][1], obs[v][2] };
        int w = range(three, 3);
        if (w > worst_within) worst_within = w;
        mean[v] = (three[0] + three[1] + three[2]) / 3;
    }

    *out_between = range(mean, n);
    *out_within  = worst_within;
    return *out_between > worst_within;
}

static void show(const char *label, const uint32_t *vals, const int *got,
                 int n, bool descending)
{
    printf("  %-9s", label);
    for (int i = 0; i < n; i++) {
        int k = descending ? n - 1 - i : i;
        printf("  %05x:%-3d", (unsigned)vals[k], got[i]);
    }
    printf("   range %d\n", range(got, n));
}

// One register group, the whole three-pass experiment. Returns true if the
// pixels followed the number by the rule in the header.
static bool sweep(const char *what, const uint32_t *vals, int n,
                  void (*write)(uint32_t))
{
    int null_[16], up[16], down[16], mix[16];
    int obs[16][3];

    printf("\n-- %s --\n", what);

    // NULL first, so the noise floor is measured before anything is written
    // rather than after, when a write may have left the sensor somewhere else.
    for (int i = 0; i < n; i++)
        null_[i] = settled_luma();
    printf("  %-9s", "null");
    for (int i = 0; i < n; i++)
        printf("  %5s:%-3d", "-", null_[i]);
    printf("   range %d\n", range(null_, n));

    for (int i = 0; i < n; i++) {
        write(vals[i]);
        up[i] = obs[i][0] = settled_luma();
    }
    show("up", vals, up, n, false);

    for (int i = 0; i < n; i++) {
        int v = n - 1 - i;
        write(vals[v]);
        down[i] = obs[v][1] = settled_luma();
    }
    show("down", vals, down, n, true);

    // Evens ascending, then odds ascending. A third order that is monotone in
    // neither the value nor the time, so a room that is slowly darkening cannot
    // masquerade as a response in all three passes at once.
    int step = 0;
    for (int parity = 0; parity < 2; parity++)
        for (int v = parity; v < n; v += 2)
            { write(vals[v]); mix[step++] = obs[v][2] = settled_luma(); }
    printf("  %-9s", "mix");
    step = 0;
    for (int parity = 0; parity < 2; parity++)
        for (int v = parity; v < n; v += 2)
            printf("  %05x:%-3d", (unsigned)vals[v], mix[step++]);
    printf("   range %d\n", range(mix, n));

    int rnull = range(null_, n), rup = range(up, n), rdown = range(down, n);
    int gap = retrace_gap(up, down, n);
    bool bigger  = rup > rnull;
    bool retrace = gap <= rnull;

    printf("  (a) up range %d > null range %d : %s\n", rup, rnull,
           bigger ? "yes" : "NO");
    printf("  (b) worst retrace gap %d <= null range %d : %s   (down range %d)\n",
           gap, rnull, retrace ? "yes" : "NO", rdown);
    printf("  -> rule (a)(b): %s\n", bigger && retrace
           ? "RESPONDS - the pixels follow the number"
           : "does not respond");

    int between, within;
    bool resp_b = between_beats_within(obs, n, &between, &within);
    printf("  (c) spread across values %d > worst spread within a value %d : %s\n",
           between, within, resp_b ? "yes" : "NO");
    printf("  -> rule (c): %s\n", resp_b
           ? "RESPONDS - revisiting a number lands closer than changing it"
           : "does not respond");

    // Rule (c) is the verdict this binary reports. Rule (a)(b) is still run and
    // still printed because two logs already carry its answer and deleting it
    // here would quietly rewrite them.
    return resp_b;
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

    printf("\n=== Can the module be told an exposure? (cam.h:246 vs the app "
           "note) ===\n\n");
    printf("clock     : %u MHz sys%s\n",
           (unsigned)(clock_get_hz(clk_sys) / 1000000),
           at_rate ? "" : "   <- ASKED FOR ANOTHER RATE AND DID NOT GET IT");

    cam_bus_bitbang();
    uint8_t id = cam_read_reg(CAM_REG_SENSOR_ID);
    if (!cam_id_plausible(id)) {
        printf("\nRESULT : FAIL - no camera on the bus (id 0x%02x).\n", id);
        while (true) tight_loop_contents();
    }
    cam_bus_pio(8000000);
    printf("sensor id : 0x%02x\n", id);

    printf("\n-- bring-up --\n");
    cam_begin(id, true);
    m128 = cam_mode_128(id);

    // WARM UP WITH THE LOOPS RUNNING FIRST. cam.h: "a lock taken in the dark is
    // a lock at the ceiling", and a sweep started from the ceiling can only go
    // one way, which would satisfy (a) for the wrong reason. Twenty captures is
    // half of ft_acquire()'s forty-frame ramp and enough to be off the rail.
    printf("\n-- warm-up, auto loops running --\n");
    for (int i = 0; i < 20; i++) (void)luma();
    printf("  settled at luma %d\n", luma());

    // The arm #30 used: exposure and gain switched off, AWB left alone. Same
    // mask as CAM_LOCK_STEPS[1], so a result here transfers to that arm.
    cam_image_auto_mask(CAM_AUTO_WB);
    sleep_ms(200);
    printf("  auto exposure and gain off, AWB left running: luma %d\n",
           settled_luma());

    bool exp_ok = sweep("manual exposure 0x33/0x34/0x35", EXPOSURE, NEXP,
                        write_exposure);

    // THE GAIN SECTION NEEDS ITS OWN STARTING POINT. In the first run its null
    // read 17, 41, 69, 69, 68, 68 - a range of 52 on a still scene - because
    // the exposure sweep had just ended on its darkest value and the sensor was
    // still climbing out of it. A null that is really a recovery curve cannot
    // be a floor for anything. Letting the auto loops run again puts the sensor
    // back at an operating point it chose, which is the same rule cam.h states
    // for warming up before a lock; picking a mid exposure to write instead
    // would be choosing a number to make the next measurement come out.
    printf("\n-- back to auto between sections --\n");
    cam_image_auto_mask(CAM_AUTO_ALL);
    sleep_ms(500);
    for (int i = 0; i < 20; i++) (void)luma();
    printf("  settled at luma %d\n", luma());
    cam_image_auto_mask(CAM_AUTO_WB);
    sleep_ms(200);

    bool gain_ok = sweep("manual gain 0x31/0x32", GAIN, NGAIN, write_gain);

    printf("\n-- restoring --\n");
    cam_image_auto_mask(CAM_AUTO_ALL);
    sleep_ms(500);
    for (int i = 0; i < 10; i++) (void)luma();
    int back = luma();
    printf("  auto loops back on: luma %d\n", back);

    printf("\nRESULT : exposure %s, gain %s\n",
           exp_ok ? "RESPONDS" : "does not respond",
           gain_ok ? "RESPONDS" : "does not respond");
    if (exp_ok || gain_ok)
        printf("         cam.h:246 is wrong for this module: a lock CAN mean "
               "\"use this number\".\n"
               "         #30's arm can be rebuilt as a real lock instead of a "
               "loop switched off.\n");
    else
        printf("         cam.h:246 stands against the app note, for this write "
               "order at least.\n"
               "         'L' cannot become a real lock this way; the I2C "
               "passthrough is the route left.\n");
    if (back < 0)
        printf("         AND THE CAMERA DID NOT COME BACK - power-cycle before "
               "the next run.\n");

    while (true) tight_loop_contents();
}
