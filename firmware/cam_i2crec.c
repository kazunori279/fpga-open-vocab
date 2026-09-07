// What does firing the I2C passthrough cost, and what buys it back? (#33)
//
// WHERE THIS PICKS UP. bench/probe/20260907-i2cpass/ fired 0x07 bit 0 for the
// first time and found 0x48 holding what comes back - spread 76 against a
// scatter of 0, retrace exact, three runs - reading 36 4c at sensor 0x300A and
// 0x300B, which is the OV3640's product ID. It could not finish. The causal
// stage needed to write an exposure through 0x33-0x35 and watch the die's own
// registers follow, and after the fires the exposure writes had stopped
// working: luma 133 at 0x00010 and 133 at 0x00200, where 20260907-manexp
// measured 11 and 233 on this board. Capture was fine. Register reads were
// fine. The write-only control block at 0x30-0x35 had gone deaf.
//
// So 0x48 is not adopted, and it cannot be until this file answers two things.
//
//   WHAT BREAKS IT. The last probe wrote 0x07 thirty-six times, always as the
//   literal 0x01. It never established whether the damage is bit 0 - the
//   disputed initiate - or writing 0x07 AT ALL. cam.c already writes that
//   register twice in the shipping path, 0x80 to flush the FIFO and 0x40 during
//   bring-up, so "writing 0x07 costs the control surface" and "initiating an
//   I2C read costs the control surface" are very different findings and only
//   one of them is about the passthrough. This file runs 0x07 = 0x00 as a
//   control: same register, same number of writes, no bits set. If the null
//   write kills the handle too, bit 0 is exonerated and something much closer
//   to the shipping path is implicated.
//
//   WHAT RESTORES IT. In cost order, because the answer decides whether a
//   readback is something m9 can do between frames or something that costs a
//   re-initialisation every time - and those two are the difference between
//   #33 getting a live instrument and getting a bench-only one.
//
// THE HANDLE, AND IT IS THE SAME SHAPE AS EVERY OTHER RULE IN THIS SERIES.
// A handle exists when two exposures four decades apart part the picture by
// more than repeated captures at one exposure wobble. Parting against wobble,
// no constant, and it is checked before it is used rather than after. The last
// probe's guard was `hi_luma > lo_luma`, which passed on 97 against 100 and let
// a dead handle condemn a live register; that is the mistake this file is built
// around not repeating.
//
// EVERY PHASE ENDS WITH A HANDLE CHECK AND EVERY DEAD HANDLE GETS THE LADDER,
// so a phase always starts from a known-live state. A phase that begins broken
// measures nothing, and running three of those in a row is how a probe reports
// four findings that are all the first one.
//
// NOTHING HERE IS PRE-REGISTERED AS A VERDICT, because there is no hypothesis
// to reject - it is a search over a recovery ladder, and the report is which
// rung worked. What IS fixed before the run is the handle rule above and the
// ladder's contents and order, so that "we tried things until one worked" is
// a written-down list and not an afternoon.
//
// ---------------------------------------------------------------------------
// WHAT RUN 1 OF THIS FILE FOUND, AND WHY THE PHASES BELOW ARE NOT THE ONES THE
// PARAGRAPHS ABOVE DESCRIBE.
//
// Nothing broke. Baseline parting 120 against a wobble of 6; after one fire,
// 176 against 0; after 36 fires, 176 against 1; after 36 null writes, 176
// against 0. The recovery ladder never ran because there was never anything to
// recover, and the premise the file was built on - that firing costs the
// control surface - did not reproduce.
//
// So the question changes from "what buys it back" to "what actually spends
// it", and the honest answer is that 20260907-i2cpass did several things at
// once and this file only reproduced one of them. Three differences survive:
//
//   THE ADDRESSES. Run 1 fired at 0x300A alone. The old probe swept twelve,
//   seven of which are LOW - 0x0000 through 0x001D. This board reports sensor
//   id 0x82 and cam.c resolves it off the legacy table, so it is not obvious
//   that 0x0B is even consulted; if the chip is latching an 8-bit address out
//   of 0x0C alone, a "read of 0x001C" is a read of sensor register 0x1C, and
//   the low map is where an OmniVision part keeps things worth not poking.
//
//   THE DUMP. The old probe read all 128 ArduChip registers after every fire,
//   3456 reads in all. Reads should be free. "Should be" is the reason to
//   check rather than the reason not to.
//
//   THE ORDER. The old probe's handle check ran once, at the end, after
//   everything. This file checks after each phase and restores in between, so
//   a phase always starts from a known-live state - which is also why it
//   cannot see a cost that only appears when several things are stacked.
//
// The phases below bisect the first two, cheapest and least suspected first,
// so that a phase which does kill the handle is the one named. A phase whose
// ladder fails ends the run: everything after a dead handle that would not
// come back measures nothing, and printing four dead rows as four findings is
// how a bisect turns into a rumour.

#include <stdio.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "hardware/vreg.h"

#include "cam.h"
#include "qspi_park.h"

#define CAM_REG_I2C_ADDR_H         0x0B
#define CAM_REG_I2C_ADDR_L         0x0C
#define CAM_I2C_INITIATE_READ      0x01u
#define CAM_I2C_RESET              0x02u   // 0x07 bit[1], the documented one
#define CAM_FPGA_RESET             0x40u   // 0x07 bit[6], what cam_begin() uses
#define CAM_CACHE_RESET            0x80u   // 0x07 bit[7], what cam.c:455 uses
#define CAM_REG_MANUAL_EXPOSURE_H  0x33
#define CAM_REG_MANUAL_EXPOSURE_M  0x34
#define CAM_REG_MANUAL_EXPOSURE_L  0x35
#define CAM_REG_I2C_DATA           0x48   // named by 20260907-i2cpass, not adopted

#define SENSOR_I2C_ADDR 0x78
#define EXP_LOW   0x00010u
#define EXP_HIGH  0x00200u
#define FIRES     36            // what the last probe did, reproduced exactly

// 0x300A is where the OV3640 keeps the high byte of its product id, and the
// last probe read 0x36 there. Used only to confirm a fire went out at all.
#define SENSOR_ID_HIGH 0x300A

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;
static uint8_t cam_id;

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

static int range3(const int *v)
{
    int lo = v[0], hi = v[0];
    for (int i = 1; i < 3; i++) {
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    return hi - lo;
}

// Self-contained on purpose: it applies the mask itself, so what it reports is
// "can the firmware control the exposure right now", start to finish, and not
// "is a mask that was set some time ago still in force".
static bool handle(const char *when)
{
    cam_image_auto_mask(CAM_AUTO_WB);
    sleep_ms(200);

    int lo[3], hi[3];
    write_exposure(EXP_LOW);
    for (int i = 0; i < 4; i++) (void)luma();
    for (int i = 0; i < 3; i++) lo[i] = luma();
    write_exposure(EXP_HIGH);
    for (int i = 0; i < 4; i++) (void)luma();
    for (int i = 0; i < 3; i++) hi[i] = luma();

    int wl = range3(lo), wh = range3(hi);
    int wobble = wl > wh ? wl : wh;
    int part = lo[0] - hi[0];
    if (part < 0) part = -part;
    bool alive = part > wobble;
    printf("  handle %-22s %3d %3d %3d | %3d %3d %3d   part %3d  wobble %2d  "
           "%s\n", when, lo[0], lo[1], lo[2], hi[0], hi[1], hi[2],
           part, wobble, alive ? "ALIVE" : "DEAD");
    return alive;
}

static void fire_read(uint16_t sensor_reg)
{
    cam_write_reg(CAM_REG_DEBUG_DEVICE_ADDRESS, SENSOR_I2C_ADDR);
    cam_wait_idle("i2c device address");
    cam_write_reg(CAM_REG_I2C_ADDR_H, (uint8_t)(sensor_reg >> 8));
    cam_wait_idle("i2c register address high");
    cam_write_reg(CAM_REG_I2C_ADDR_L, (uint8_t)(sensor_reg & 0xff));
    cam_wait_idle("i2c register address low");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_INITIATE_READ);
    cam_wait_idle("i2c direct read");
    sleep_ms(2);
}

// THE CONTROL. Same register, same count, no bits. If this is as destructive as
// the fire, the fire is not what is destructive.
static void fire_null(void)
{
    cam_write_reg(CAM_REG_SENSOR_RESET, 0x00u);
    cam_wait_idle("null write to 0x07");
    sleep_ms(2);
}

// The ladder, in cost order. Returns the rung that worked, or -1.
static int recover(void)
{
    printf("  -- recovery ladder --\n");

    // Rung 0 was added for run 4, after run 3 found the handle dead from cold
    // with nothing fired. The symptom is that luma does not move off the level
    // the auto loop settled at, which is what a mask write that did not take
    // looks like; 0x30 is write-only so there is nothing to read back and check.
    // The cheapest thing that could possibly be the fix is applying it again,
    // and it belongs below the resets because it is not one.
    printf("     rung 0: apply the auto mask a second time, no reset\n");
    cam_image_auto_mask(CAM_AUTO_ALL);
    sleep_ms(200);
    for (int i = 0; i < 8; i++) (void)luma();
    if (handle("after rung 0")) return 0;

    printf("     rung 1: 0x07 bit 1, the documented I2C reset\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_RESET);
    cam_wait_idle("i2c reset");
    sleep_ms(100);
    if (handle("after rung 1")) return 1;

    printf("     rung 2: 0x07 bit 7, reset cache - what cam.c:455 flushes with\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_CACHE_RESET);
    cam_wait_idle("cache reset");
    sleep_ms(100);
    if (handle("after rung 2")) return 2;

    printf("     rung 3: 0x07 bit 6, reset FPGA - what cam_begin() uses\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_FPGA_RESET);
    cam_wait_idle("fpga reset");
    sleep_ms(200);
    if (handle("after rung 3")) return 3;

    printf("     rung 4: the whole of cam_begin() again\n");
    cam_begin(cam_id, false);
    sleep_ms(200);
    if (handle("after rung 4")) return 4;

    printf("     nothing on the ladder worked. Only a hub power cycle is "
           "left, and\n     firmware cannot do that one.\n");
    return -1;
}

// The old probe's twelve, split at the boundary that separates them. Same
// values, same order within each half - this is a partition of 20260907-i2cpass
// and not a new address list.
static const uint16_t LOW_ADDR[]  = { 0x0000, 0x0001, 0x0002, 0x0003,
                                      0x000A, 0x001C, 0x001D };
static const uint16_t HIGH_ADDR[] = { 0x3000, 0x3002, 0x300A, 0x300B, 0x3100 };
#define NLOW   ((int)(sizeof LOW_ADDR  / sizeof LOW_ADDR[0]))
#define NHIGH  ((int)(sizeof HIGH_ADDR / sizeof HIGH_ADDR[0]))

// Three visits over an address list, which is exactly what stage A did.
static void sweep(const uint16_t *addr, int n)
{
    for (int v = 0; v < 3; v++)
        for (int i = 0; i < n; i++) {
            fire_read(addr[i]);
            (void)cam_read_reg(CAM_REG_I2C_DATA);
        }
}

static void phase_dump(void)
{
    for (int v = 0; v < 3; v++)
        for (int r = 0; r < 0x80; r++)
            (void)cam_read_reg((uint8_t)r);
}

static void phase_high(void) { sweep(HIGH_ADDR, NHIGH); }
static void phase_low(void)  { sweep(LOW_ADDR,  NLOW);  }
static void phase_null(void) { for (int i = 0; i < FIRES; i++) fire_null(); }

// PHASE 5 IS 20260907-i2cpass's STAGE A, AS IT WAS. Twelve addresses in the
// order that file lists them, three visits ascending-descending-ascending, and
// a full 256-register read after every single fire. It exists because the four
// phases above take that loop apart and find nothing, and the only way that is
// consistent with the old log is if the cost needs the pieces together.
// Nothing here is sampled or shortened: a reproduction that is cheaper than the
// thing it reproduces is not one.
static const uint16_t STAGEA_ADDR[] = {
    0x0000, 0x0001, 0x0002, 0x0003, 0x000A, 0x001C, 0x001D,
    0x3000, 0x3002, 0x300A, 0x300B, 0x3100,
};
#define NSTAGEA ((int)(sizeof STAGEA_ADDR / sizeof STAGEA_ADDR[0]))

static void phase_stagea(void)
{
    for (int v = 0; v < 3; v++) {
        for (int k = 0; k < NSTAGEA; k++) {
            int a = (v == 1) ? (NSTAGEA - 1 - k) : k;
            fire_read(STAGEA_ADDR[a]);
            for (int r = 0; r < 0x100; r++)
                (void)cam_read_reg((uint8_t)r);
        }
        printf("  visit %d done\n", v);
    }
}

// One phase: do it, then say whether the handle survived, then put it back if
// it did not. Returns true if the handle was still alive without the ladder.
// `dead_unrecoverable` is set when the ladder ran and failed, which ends the run.
static bool phase(const char *name, void (*what)(void), int *rung_out,
                  bool *dead_unrecoverable)
{
    printf("\n-- %s --\n", name);
    what();
    printf("  done. Capture still returns luma %d.\n", luma());
    bool survived = handle("after");
    *rung_out = -2;   // -2 means "the ladder was not needed"
    if (!survived) {
        *rung_out = recover();
        if (*rung_out < 0)
            *dead_unrecoverable = true;
    }
    return survived;
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

    printf("\n=== What does firing the passthrough cost, and what buys it "
           "back? (#33) ===\n\n");
    printf("clock     : %u MHz sys%s\n",
           (unsigned)(clock_get_hz(clk_sys) / 1000000),
           at_rate ? "" : "   <- ASKED FOR ANOTHER RATE AND DID NOT GET IT");

    cam_bus_bitbang();
    cam_id = cam_read_reg(CAM_REG_SENSOR_ID);
    if (!cam_id_plausible(cam_id)) {
        printf("\nRESULT : FAIL - no camera on the bus (id 0x%02x).\n", cam_id);
        while (true) tight_loop_contents();
    }
    cam_bus_pio(8000000);
    printf("sensor id : 0x%02x\n", cam_id);

    printf("\n-- bring-up --\n");
    cam_begin(cam_id, true);
    m128 = cam_mode_128(cam_id);

    // Twenty captures with the loops running, the same warm-up cam_manexp.c
    // uses. A handle measured off the rail is not a handle.
    printf("\n-- warm-up, auto loops running --\n");
    cam_image_auto_mask(CAM_AUTO_ALL);
    sleep_ms(200);
    for (int i = 0; i < 20; i++) (void)luma();
    printf("  settled at luma %d\n", luma());

    // BASELINE. Run 3 of this file found this DEAD - luma 133 133 133 against
    // 133 133 133, before a single fire, and 133 is the same byte
    // 20260907-i2cpass's stage B reported. That is the whole finding: the dead
    // control surface is not something firing causes. Two boots of this binary
    // had it and the third did not, off an identical recipe.
    //
    // So the baseline no longer halts the run. A handle that is dead before
    // anything happened is the thing step 1 was looking for, and the ladder is
    // already the right instrument pointed at the wrong target. If a rung
    // brings it back from cold, that rung is worth having in cam_begin(); if
    // none does, the phases below cannot run and the run says so.
    printf("\n-- phase 0: baseline, nothing fired yet --\n");
    bool base = handle("at rest");
    int rung_cold = -2;
    if (!base) {
        printf("\n  THE HANDLE IS DEAD BEFORE ANYTHING WAS FIRED. This is the "
               "condition\n  20260907-i2cpass blamed on the passthrough, and it "
               "is here with zero\n  fires. Running the ladder against it "
               "instead.\n");
        rung_cold = recover();
        if (rung_cold < 0) {
            printf("\nRESULT : THE HANDLE WAS DEAD FROM COLD AND NOTHING ON THE "
                   "LADDER WOKE IT.\n         Nothing was fired, so this is not "
                   "about 0x07 bit 0 at all - it is\n         the flat exposure "
                   "run 20260907-manexp saw once and could not\n         "
                   "explain, seen again. The passthrough is not the "
                   "blocker.\n");
            while (true) tight_loop_contents();
        }
        printf("\n  Recovered from cold. The phases below run on a board that "
               "needed a\n  rung to get here, which is worth knowing when "
               "reading them.\n");
    }

    // Confirm a fire actually goes out, so a "nothing broke" result cannot be
    // "nothing happened". 0x36 is what 20260907-i2cpass read here.
    fire_read(SENSOR_ID_HIGH);
    uint8_t got = cam_read_reg(CAM_REG_I2C_DATA);
    printf("\n  one fire at sensor 0x%04x returns 0x%02x through 0x48%s\n",
           SENSOR_ID_HIGH, got,
           got == 0x36 ? "   (0x36, as 20260907-i2cpass read)"
                       : "   <- NOT what the last probe read here");
    bool after_one = handle("after ONE fire");
    int rung_one = -2;
    if (!after_one)
        rung_one = recover();

    // The bisect. Cheapest and least suspected first, so the phase that does
    // kill the handle is the one named rather than the one that happened to
    // run after the killing was already done.
    struct { const char *name; void (*fn)(void); bool alive; int rung; } P[] = {
        { "phase 1: read all 128 ArduChip registers, 3 times, NO fires",
          phase_dump, false, -2 },
        { "phase 2: 36 writes of 0x07 = 0x00, no bits set",
          phase_null, false, -2 },
        { "phase 3: fire at the FIVE HIGH addresses, 3 visits (0x3000-0x3100)",
          phase_high, false, -2 },
        { "phase 4: fire at the SEVEN LOW addresses, 3 visits (0x0000-0x001D)",
          phase_low,  false, -2 },
        { "phase 5: 20260907-i2cpass's STAGE A, whole and unshortened",
          phase_stagea, false, -2 },
    };
    const int NP = (int)(sizeof P / sizeof P[0]);

    bool stuck = false;
    int ran = 0;
    for (int i = 0; i < NP && !stuck; i++, ran++)
        P[i].alive = phase(P[i].name, P[i].fn, &P[i].rung, &stuck);

    printf("\n=== what this run says ===\n\n");
    printf("  baseline                                          ALIVE\n");
    printf("  after ONE fire at 0x%04x                          %s\n",
           SENSOR_ID_HIGH, after_one ? "ALIVE" : "DEAD");
    for (int i = 0; i < ran; i++) {
        char tag[10];
        snprintf(tag, sizeof tag, "phase %d", i + 1);
        printf("  %-8s %-40s %s%s\n", tag, P[i].name + 9,
               P[i].alive ? "ALIVE" : "DEAD",
               P[i].alive ? "" : (P[i].rung >= 0 ? "   (ladder brought it back)"
                                                 : "   (LADDER FAILED)"));
    }
    if (ran < NP)
        printf("\n  %d of %d phases did not run: the handle went dead and would "
               "not come\n  back, so anything after it would have measured that "
               "and not itself.\n", NP - ran, NP);

    // Name the first phase that killed it, if any. First, not worst: after the
    // first kill the board has been through a ladder rung, and a later phase's
    // result is a result about a recovered board.
    int culprit = -1;
    for (int i = 0; i < ran; i++)
        if (!P[i].alive) { culprit = i; break; }

    printf("\n  ");
    if (!after_one) {
        printf("A SINGLE FIRE IS ENOUGH, so the bisect below is about a board "
               "that was\n  already disturbed. Read nothing else here.\n");
    } else if (culprit < 0) {
        printf("NOTHING BREAKS THE HANDLE - not the pieces and not stage A "
               "whole.\n  20260907-i2cpass's dead control surface DOES NOT "
               "REPRODUCE, so the\n  reading in that directory - that firing "
               "0x07 bit 0 costs the manual\n  exposure - is wrong, and its "
               "stage B failed for some other reason\n  this run has not "
               "found. What is now unblocked is stage B itself: the\n  handle "
               "is alive on both sides of a fire, which is the precondition "
               "it\n  needed and never had.\n");
    } else {
        printf("FIRST TO KILL IT: %s.\n", P[culprit].name);
        printf("  %s\n", P[culprit].rung >= 0
               ? "It came back on the ladder - see the RECOVERY line."
               : "It did not come back. A hub power cycle is the only known way "
                 "out,\n  which makes this unusable in any run that has to keep "
                 "going.");
    }

    // The cold rung comes first, because a handle that had to be woken before
    // anything happened is a different and more useful fact than one that had
    // to be woken after a fire.
    int best = -1;
    if (rung_cold >= 0) best = rung_cold;
    if (best < 0 && rung_one >= 0) best = rung_one;
    for (int i = 0; i < ran && best < 0; i++)
        if (P[i].rung >= 0) best = P[i].rung;
    if (best >= 0) {
        static const char *RUNG[] = {
            "applying the auto mask a second time - no reset at all",
            "0x07 bit 1, the documented I2C reset",
            "0x07 bit 7, reset cache",
            "0x07 bit 6, reset FPGA",
            "a full cam_begin()",
        };
        printf("\n  RECOVERY: %s.\n", RUNG[best]);
        if (rung_cold >= 0)
            printf("  It was needed FROM COLD, with nothing fired.\n");
        printf("  %s\n", best <= 2
               ? "That is cheap enough for m9 to do between frames."
               : "That costs a re-initialisation, so anything relying on it is "
                 "bench-only\n  until something cheaper is found.");
        if (best == 0)
            printf("  And a mask that takes on the second application and not "
                   "the first is a\n  bug in cam_image_auto_mask(), not a "
                   "property of the passthrough. 0x30 is\n  write-only, so "
                   "there is nothing to read back - the only check available "
                   "is\n  the handle itself.\n");
    }

    while (true) tight_loop_contents();
}
