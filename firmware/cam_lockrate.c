// How often does the exposure lock fail to take? (#33, #32)
//
// bench/probe/20260907-awb/ established that a boot whose manual exposure
// surface looks deaf is one of two things, and that both are readable at the
// die's own exposure registers rather than guessable from the picture. What it
// could not do is say how often either happens: eleven boots is not a rate, and
// the full AWB probe takes four minutes a boot because it sweeps 1024 addresses
// twice and walks a six-rung ladder up to five times.
//
// This is the same question with everything else taken out. No sweep, no
// ladder, no recovery. Bring the camera up the way the shipping path brings it
// up, then ask the same question at four points on the way out of the boot -
// before any frame, after one, and three times after a warm-up - and
// corroborate against the picture. Fifteen seconds, so it can be run the twenty
// or thirty times a rate needs.
//
// WHEN THE QUESTION IS ASKED TURNED OUT TO MATTER MORE THAN THE BOOT. The first
// 33 runs of this probe answered `held` before the first frame on every single
// boot and `dragged` a few frames later on 32 of them, which is why the four
// points exist and why cam_image_defaults() no longer asks.
//
// WHAT IS BEING COUNTED, exactly. Not "did the run go well". The verdict comes
// off cam_exposure_lock_check(), which writes two exposures a factor of eight
// apart with every loop locked and reads 0x3002/0x3003 back:
//
//   held        the die kept both. The lock reached the sensor.
//   dragged     the die put its own value back. The AE loop is still running
//               with the mask at zero, and any 'L'-arm measurement taken on
//               this boot is measuring nothing.
//   unreadable  the passthrough never returned a stable pair. Says nothing
//               about the lock in either direction, and must not be counted as
//               either - it is this probe's own instrument failing. It has not
//               happened yet: 0 of 144 checks over the first 33 boots.
//   untested    asked before this boot's first frame, so the check declined to
//               write anything. Also not a count, and here only as the control
//               on that refusal.
//
// THE PICTURE IS A CROSS-CHECK AND NOT THE VERDICT. Two exposures a factor of
// eight apart should give two different frames if the lock took. When the die
// says held and the picture does not move, that is run 11's second shape and it
// is worth having on the record, but the count above is what the rate is made
// of.

#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/vreg.h"
#include "cam.h"
#include "qspi_park.h"

#ifndef FGX_SYS_KHZ
#define FGX_SYS_KHZ 320000
#endif

#define MANUAL_EXPOSURE_H  0x33
#define MANUAL_EXPOSURE_M  0x34
#define MANUAL_EXPOSURE_L  0x35

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;
static uint8_t cam_id;

static int luma(void)
{
    cam_time_t t;
    int m[3];
    uint32_t len = cam_capture(&CAM_RECIPE_VENDOR, m128,
                               CAM_IMAGE_PIX_FMT_RGB565,
                               raw, sizeof raw, &t);
    if (len != sizeof raw)
        return -1;
    cam_frame_means(raw, len, m);
    return (m[0] + m[1] + m[2]) / 3;
}

static void write_exposure(uint32_t v)
{
    cam_write_reg(MANUAL_EXPOSURE_H, (uint8_t)((v >> 16) & 0x0f));
    cam_wait_idle("exp h");
    cam_write_reg(MANUAL_EXPOSURE_M, (uint8_t)((v >> 8) & 0xff));
    cam_wait_idle("exp m");
    cam_write_reg(MANUAL_EXPOSURE_L, (uint8_t)(v & 0xff));
    cam_wait_idle("exp l");
}

// Capture until three in a row come back equal, the way the AWB probe does it,
// because four discarded frames after a big exposure step is not enough and a
// picture still on its way is not a measurement. Bounded, and the bound being
// hit is reported rather than hidden.
static int settled(bool *ok)
{
    int a = luma(), b = luma(), c = luma();
    for (int i = 0; i < 30; i++) {
        if (a >= 0 && a == b && b == c) { if (ok) *ok = true; return c; }
        a = b; b = c; c = luma();
    }
    if (ok) *ok = false;
    return c;
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

    printf("\n=== #33: did the exposure lock take? ===\n\n");
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

    // Was `cam_image_defaults()` calling the check itself, until the first 33
    // boots showed the answer there is fixed at `held` by the absence of frames
    // rather than by the state of the sensor. The call stays here, at the same
    // point in the boot, as the guard's own control: it must now come back
    // `untested`, and if it ever comes back `held` again the refusal in cam.c
    // has stopped working.
    cam_lock_state_t first = cam_exposure_lock_check();
    printf("\n  before the first frame:       %-10s  (%u frames triggered)\n",
           cam_lock_state_name(first), (unsigned)cam_frames_triggered);

    // ONE FRAME, AND THEN THE SAME QUESTION AGAIN.
    //
    // The first twenty-one boots said `held` at the call above every single
    // time while the warm rechecks below said `dragged` on twenty of them. A
    // check that cannot fail is not a check, and the difference between the two
    // call sites is that no frame has been captured yet when the first one runs.
    // If the AE loop only updates while the sensor is clocking frames out, then
    // the boot-path check is asking before there is anything to drag it.
    //
    // This pair decides that. Capture one frame - nothing else changes, the
    // loops are already free because cam_image_defaults() left them there - and
    // ask again. A flip from held to dragged across a single capture is the
    // mechanism; held on both is not, and would mean the warm rechecks differ
    // for some other reason.
    (void)luma();
    cam_lock_state_t after1 = cam_exposure_lock_check();
    printf("  after one capture:            %-10s  (wrote %04x, die said "
           "%04x)\n", cam_lock_state_name(after1),
           (unsigned)cam_exposure_lock_wrote, (unsigned)cam_exposure_lock_read);
    cam_image_auto(true);

    // Warm up the way a run would before asking again, because a lock taken in
    // the dark is a lock at the ceiling and this probe should not be the one
    // measurement in the directory that skips the warm-up.
    for (int i = 0; i < 20; i++) (void)luma();

    cam_lock_state_t again[3];
    for (int i = 0; i < 3; i++) {
        again[i] = cam_exposure_lock_check();
        printf("  recheck %d after warm-up:      %-10s  (wrote %04x, die said "
               "%04x)\n", i, cam_lock_state_name(again[i]),
               (unsigned)cam_exposure_lock_wrote,
               (unsigned)cam_exposure_lock_read);
        cam_image_auto(true);
        for (int k = 0; k < 8; k++) (void)luma();
    }

    bool stable = (again[0] == again[1] && again[1] == again[2]);

    // The picture, at the same two exposures the die was asked about.
    cam_image_auto_mask(0u);
    bool s1 = false, s2 = false;
    write_exposure(0x00080u);
    int lo = settled(&s1);
    write_exposure(0x00400u);
    int hi = settled(&s2);
    printf("  the picture at those two:     luma %d and %d%s\n", lo, hi,
           (s1 && s2) ? "" : "   (one of them never settled)");

    // The frame is left where a bench would want it rather than on whatever the
    // last manual write happened to be. cam.h:362: restoring the mask does not
    // undo a manual exposure, so the way back has to be walked deliberately.
    cam_image_auto(true);
    for (int i = 0; i < 20; i++) (void)luma();

    printf("\nVERDICT : %s %s picture %s\n",
           cam_lock_state_name(again[2]),
           stable ? "stable" : "UNSTABLE",
           (lo >= 0 && hi >= 0 && hi - lo > 8) ? "moved" : "flat");
    printf("          first=%s cap1=%s r0=%s r1=%s r2=%s luma=%d,%d\n",
           cam_lock_state_name(first), cam_lock_state_name(after1),
           cam_lock_state_name(again[0]),
           cam_lock_state_name(again[1]), cam_lock_state_name(again[2]),
           lo, hi);

    while (true) tight_loop_contents();
}
