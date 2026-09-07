// The other two loops. Does the gain lock take, and does the white balance
// lock hold? (#33, #30)
//
// Everything measured for #33 so far has been exposure: written at 0x33-0x35,
// read back at the die's 0x3002/0x3003, and found not to stick on 32 boots of
// 33 (bench/probe/20260907-lockrate/). But cam_image_auto_mask(0) masks three
// loops, not one, and `'L'`'s whole claim is that the camera is held still.
// Gain and white balance have never been asked the same question, and they
// cannot be asked it the same way:
//
//   GAIN can be written (0x31/0x32) but nobody knows where the die keeps it, so
//   there is nothing to read back. Stage G goes and finds out, the way the AWB
//   probe found the white-balance bit: write two values, sweep 1024 addresses,
//   and keep the addresses that separate and hold still.
//
//   WHITE BALANCE cannot be written at all - there is no manual WB register on
//   this surface, only the loop's on-off - so "did the write stick" is not even
//   the question. The question is whether the loop, once switched off, stays
//   off. bench/probe/20260825-camlock/ says it does not; 20260907-awb/ found
//   the two addresses that move when the bit is written but could never run the
//   over-time stage because its positive control needed an operator changing
//   the scene colour. Stage W is that stage with the control built in.
//
// STAGE W'S CONTROL IS THE OTHER ARM. The reason the old stage D could not run
// is that a locked WB reading the same value for a minute proves nothing in a
// still scene - a free WB would read the same value too. So the arms are
// interleaved instead: free, locked, free, locked, over about a minute, and the
// separation between them at each visit is the control. If the two arms stop
// separating as the minute goes on, the lock is decaying; if they never
// separated, the run says nothing and says so.
//
// NEITHER STAGE WRITES A THRESHOLD. Stage G keeps an address only if all three
// visits inside an arm agree and no value in one arm appears in the other.
// Stage W compares locked visits against the free visits either side of them,
// on the same boot, in the same scene.

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

#define MANUAL_GAIN_H  0x31
#define MANUAL_GAIN_L  0x32

// 0x55 and 0xaa. Two values that are each other's complement, so an address
// that happens to hold a counter or a checksum will not pass by accident, and
// both fit in the low byte so 0x31 stays zero throughout.
#define GAIN_A 0x055u
#define GAIN_B 0x0aau

#define SWEEP_BASE 0x3000u
#define NSWEEP     1024
#define NVISIT     3
static const bool GORDER[NVISIT * 2] = { true, false, false, true, true, false };

// The two addresses 20260907-awb/ found moving on the white-balance bit. Not
// named, and deliberately not named: 0x301b tracked exposure in that same
// sweep, which is warning enough about reading a meaning into an address.
#define WB_A 0x332bu
#define WB_B 0x33cau
#define NWVISIT 8

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;
static uint8_t cam_id;
static uint8_t gv[NVISIT * 2][NSWEEP];

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

static void write_gain(uint32_t v)
{
    cam_write_reg(MANUAL_GAIN_H, (uint8_t)((v >> 8) & 0x03));
    cam_wait_idle("manual gain high");
    cam_write_reg(MANUAL_GAIN_L, (uint8_t)(v & 0xff));
    cam_wait_idle("manual gain low");
}

// =========================================================================
// Stage G: where does the die keep the gain, and does a written one stay put?
// =========================================================================
static uint16_t stage_g(void)
{
    printf("\n== stage G: the gain, and where the die keeps it ==\n");

    cam_image_auto_mask(0u);

    for (int v = 0; v < NVISIT * 2; v++) {
        write_gain(GORDER[v] ? GAIN_A : GAIN_B);
        for (int k = 0; k < 6; k++) (void)luma();
        for (int i = 0; i < NSWEEP; i++)
            gv[v][i] = cam_sensor_read((uint16_t)(SWEEP_BASE + i));
        printf("  visit %d (%s) swept\n", v, GORDER[v] ? "0x055" : "0x0aa");
    }

    int nresp = 0, necho = 0;
    uint16_t found = 0;
    printf("\n  sensor   gain 0x055     gain 0x0aa     verdict\n");
    for (int i = 0; i < NSWEEP; i++) {
        uint8_t a[NVISIT], b[NVISIT];
        int na = 0, nb = 0;
        for (int v = 0; v < NVISIT * 2; v++) {
            if (GORDER[v]) a[na++] = gv[v][i];
            else           b[nb++] = gv[v][i];
        }

        bool still = true;
        for (int k = 1; k < NVISIT; k++)
            if (a[k] != a[0] || b[k] != b[0]) still = false;
        if (a[0] == b[0]) continue;              // never moved: not interesting
        if (!still) continue;                    // moved, but not attributably

        bool echo = (a[0] == (uint8_t)GAIN_A && b[0] == (uint8_t)GAIN_B);
        nresp++;
        if (echo) { necho++; if (!found) found = (uint16_t)(SWEEP_BASE + i); }
        printf("  0x%04x   %02x %02x %02x      %02x %02x %02x      %s\n",
               (unsigned)(SWEEP_BASE + i), a[0], a[1], a[2], b[0], b[1], b[2],
               echo ? "RESPONDS, and echoes the write byte for byte"
                    : "RESPONDS");
    }
    printf("  %d address%s separated and held still; %d of them echoed\n",
           nresp, nresp == 1 ? "" : "es", necho);
    if (nresp == 0)
        printf("  NOTE: the gain write reaches no readable address in this "
               "range,\n        so this boot cannot say whether a gain lock "
               "takes.\n");
    return found;
}

// Finding where the gain lands is not the same as finding that it stays there.
// The sweep above reads each address once an arm; the AE loop's habit is to put
// its own value back within a second or so of frames, which a single read taken
// right after the write can walk straight past. So the same poll the exposure
// check uses, at the address the sweep found.
#define GAIN_POLL 10
static void stage_gh(uint16_t addr)
{
    printf("\n== stage GH: does a written gain stay written, at 0x%04x? ==\n",
           (unsigned)addr);

    cam_image_auto_mask(0u);
    int dragged = 0;
    for (int arm = 0; arm < 2; arm++) {
        const uint32_t g = arm ? GAIN_B : GAIN_A;
        write_gain(g);
        printf("  wrote %03x, die:", (unsigned)g);
        for (int i = 0; i < GAIN_POLL; i++) {
            for (int k = 0; k < 4; k++) (void)luma();
            sleep_ms(150);
            uint8_t got = cam_sensor_read(addr);
            printf(" %02x", got);
            if (got != (uint8_t)g) dragged++;
        }
        printf("\n");
    }
    printf("  %d of %d polls came back changed\n", dragged, GAIN_POLL * 2);
    printf("\nGAIN    : %s\n", dragged ? "DRAGGED" : "held");
}

// =========================================================================
// Stage W: does the white-balance lock hold, or decay?
// =========================================================================
static void stage_w(void)
{
    printf("\n== stage W: does the white-balance lock hold over a minute? ==\n");

    // Alternating, starting free, so every locked visit has a free visit either
    // side of it and the comparison is always local in time.
    printf("\n   t(s)  arm     0x332b  0x33ca  channel means\n");

    uint8_t fa[NWVISIT], fb[NWVISIT], la[NWVISIT], lb[NWVISIT];
    int nf = 0, nl = 0;
    const uint64_t t0 = time_us_64();

    for (int v = 0; v < NWVISIT * 2; v++) {
        const bool locked = (v & 1) != 0;
        cam_image_auto_mask(locked ? (uint8_t)(CAM_AUTO_EXPOSURE | CAM_AUTO_GAIN)
                                   : CAM_AUTO_ALL);
        // Frames, because a loop that is not being asked for pictures is a loop
        // that is not running, and this stage is about what happens while it is.
        for (int k = 0; k < 10; k++) (void)luma();
        sleep_ms(1500);
        for (int k = 0; k < 4; k++) (void)luma();

        uint8_t a = cam_sensor_read(WB_A);
        uint8_t b = cam_sensor_read(WB_B);
        int m[3];
        cam_frame_means(raw, sizeof raw, m);

        if (locked) { la[nl] = a; lb[nl] = b; nl++; }
        else        { fa[nf] = a; fb[nf] = b; nf++; }

        printf("  %5u  %-6s  %02x      %02x      R %3d G %3d B %3d\n",
               (unsigned)((time_us_64() - t0) / 1000000u),
               locked ? "LOCKED" : "free", a, b, m[0], m[1], m[2]);
    }

    // Did the locked arm hold one value, or wander?
    bool hold_a = true, hold_b = true;
    for (int i = 1; i < nl; i++) {
        if (la[i] != la[0]) hold_a = false;
        if (lb[i] != lb[0]) hold_b = false;
    }

    // COUNTED PER VISIT, NOT POOLED, and this is a change of reporting made
    // after the first boot and before the second. The pooled version - no value
    // in one arm may appear in the other - voided that boot, correctly, and
    // then could not say why: the locked arm had read 18/41 on all eight
    // visits, and it was the FREE arm that came back reading 18/41 on five of
    // its eight. Writing CAM_AUTO_ALL had not switched the loop back on. The
    // rule is unchanged and the void stands; what is added is which arm failed.
    int freed_a = 0, freed_b = 0;
    for (int k = 0; k < nf; k++) {
        bool same_a = false, same_b = false;
        for (int i = 0; i < nl; i++) {
            if (fa[k] == la[i]) same_a = true;
            if (fb[k] == lb[i]) same_b = true;
        }
        if (!same_a) freed_a++;
        if (!same_b) freed_b++;
    }

    printf("\n  0x332b: %d of %d free visits differed from the locked arm; "
           "locked arm %s\n", freed_a, nf,
           hold_a ? "holds one value" : "wanders");
    printf("  0x33ca: %d of %d free visits differed from the locked arm; "
           "locked arm %s\n", freed_b, nf,
           hold_b ? "holds one value" : "wanders");

    // THE THREE OUTCOMES, fixed before the second boot.
    //   no free visit differed        -> VOID. The control never fired, so
    //                                    nothing here is about the lock.
    //   some did, locked arm still    -> the lock holds, and the count of free
    //                                    visits that did not differ is the
    //                                    UNLOCK's own failure rate.
    //   some did, locked arm wandered -> the lock does not hold.
    const bool ctrl = (freed_a > 0) || (freed_b > 0);
    const bool still = ((freed_a > 0) && hold_a) || ((freed_b > 0) && hold_b);

    printf("\nWB      : %s\n",
           !ctrl  ? "VOID - no free visit differed, so no positive control"
           : still ? "HOLDS - the lock is not what fails here"
                   : "DOES NOT HOLD");
    if (ctrl)
        printf("          and the unlock failed %d of %d times at 0x332b\n",
               nf - freed_a, nf);
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

    printf("\n=== the other two loops: gain and white balance ===\n\n");
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
    for (int i = 0; i < 20; i++) (void)luma();

    // Exposure, for comparison, so this boot's gain and WB answers sit next to
    // this boot's exposure answer rather than next to another run's.
    cam_lock_state_t exp = cam_exposure_lock_check();
    cam_image_auto(true);
    for (int i = 0; i < 8; i++) (void)luma();
    printf("  exposure lock on this boot: %s\n", cam_lock_state_name(exp));

    // The product ID, read the same way as everything below it, so a stage that
    // reports nothing can be told from a passthrough that is not answering.
    uint8_t pid_h = cam_sensor_read(0x300au);
    uint8_t pid_l = cam_sensor_read(0x300bu);
    printf("  passthrough control: 0x300a/0x300b = %02x %02x%s\n",
           pid_h, pid_l,
           (pid_h == 0x36 && pid_l == 0x4c) ? "  (OV3640, as expected)"
                                            : "  <- NOT THE OV3640 ID");

    const uint16_t gain_addr = stage_g();
    if (gain_addr)
        stage_gh(gain_addr);
    else
        printf("\nGAIN    : VOID - no address echoed the write\n");

    cam_image_auto(true);
    for (int i = 0; i < 20; i++) (void)luma();

    stage_w();

    cam_image_auto(true);
    for (int i = 0; i < 20; i++) (void)luma();

    printf("\nVERDICT : exposure=%s  (gain and WB above)\n",
           cam_lock_state_name(exp));

    while (true) tight_loop_contents();
}
