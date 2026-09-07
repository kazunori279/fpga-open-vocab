// cam_awb.c - DOES THE WHITE-BALANCE LOCK REACH THE DIE, AND DOES IT HOLD?
//
// This is where #30's list of suspects runs out. The walk in `common` survived
// locking exposure and gain (20260907-camlock-cold), and it survived taking the
// sensor out of the pipeline entirely (20260907-simsrc: 541 frames off the
// synthetic source, walk 0.00 against a live arm's 2.45). Everything downstream
// of the sensor readout contributes nothing. Exposure and gain are held. White
// balance is what is left, and until today there was no way to look at it: the
// mask register 0x30 is write-only, so "the AWB is locked" was a hope about a
// write nobody could read back.
//
// 20260907-i2crec changed that. 0x48 returns what the die holds - proved by
// writing sixteen bits into the WO exposure block and reading the same sixteen
// bits back at the OV3640's 0x3002/0x3003 on two consecutive boots. So the two
// questions this probe asks are now answerable:
//
//   A. Does cam_image_auto_mask()'s WB bit change anything in the die at all?
//   D. With it off, do the registers it changed then HOLD STILL?
//
// Both have to be yes before #30's AWB arm is worth writing. A is not obvious:
// the ArduChip might implement the lock somewhere the passthrough cannot see,
// or not implement it at all, and the frame going green when WB is locked
// (20260825-camlock/attribute.log: 130 127 129 -> 100 158 122) is consistent
// with either. D is the whole point - a lock that stops the loop updating but
// leaves the value it locked drifting is not a lock.
//
// WHAT THIS PROBE CANNOT DO. The passthrough is a READ path. Nothing here can
// write the die, so this probe cannot set the gains to a chosen value, cannot
// repair the green frame, and cannot answer "what should the gains be". It can
// only report what the ArduChip's own lock does. Naming the registers is what
// makes the next step - a write path, or a better use of the mask - something
// that can be aimed rather than guessed at.
//
// THE RULES ARE FIXED BEFORE THE FIRST FRAME AND CONTAIN NO CONSTANTS.
//
//   W0  POSITIVE CONTROL. 0x300A and 0x300B must read 0x36 and 0x4C in every
//       visit of both arms. A sweep that reads nothing prints the same table as
//       a sweep that is broken; this is what tells them apart. It is not a rule
//       about white balance, it is a rule about whether the stage is measuring.
//       Fail it and nothing below is reported.
//
//   W1  SEPARATION. A register responds to the WB bit only if every one of its
//       three WB-free reads differs from every one of its three WB-locked
//       reads. Disjoint sets, so the separation is larger than the wobble by
//       construction and there is no threshold to choose.
//
//   W2  STABILITY. And it must be still inside each arm: all three reads equal,
//       in both arms. W1 without W2 would let a register that is simply noisy
//       be called a response.
//
//   W3  THE LOCK HOLDS. For each register named by W1+W2, take a back-to-back
//       repeat wobble - three reads with nothing changing between them - and
//       then watch it over many rounds. The lock holds if the locked arm's
//       excursion across the whole watch is no greater than that repeat wobble,
//       and the free arm's exceeds it. Both halves matter: the second is what
//       stops "nothing moved" being a statement about a still room rather than
//       about the lock.
//
// WHY THE ARMS DIFFER BY EXACTLY ONE BIT. Free is CAM_AUTO_WB, locked is 0.
// Exposure and gain are held OFF in both, at the same written values, so the
// only thing that changes between the two sweeps is white balance. Holding them
// off rather than free also nails the frame down: a free exposure loop would
// move the picture during the sweep and every register that follows the picture
// would pass W1 for the wrong reason.
//
// WHY THE VISITS ARE ORDERED FREE LOCK LOCK FREE FREE LOCK. Three visits per
// arm, and if they ran as three of one then three of the other, anything
// drifting monotonically through the sweep would separate the arms perfectly
// and pass W1 while having nothing to do with white balance. This order gives
// each arm an early, a middle and a late visit.
//
// THE HANDLE, and it is the same instrument 20260907-i2crec ended on. Six
// exposures each double the last, up and then back down. H1: the picture rises
// at every rung by more than the worst within-rung wobble. H2: the descent
// retraces to within that same wobble. Two points were not enough twice - a
// parting of 4 against a wobble of 2 passed a gate and a verdict got printed on
// the strength of it, and that verdict is void. This is the gate the stages run
// behind, and it runs before and after so that a stage always spans a live
// surface.
//
// THE COLD-BOOT FAULT AND WHAT THIS FILE LEARNED ABOUT IT. Some boots come up
// with the manual exposure surface deaf - flat luma, nothing fired, the fault
// 20260907-manexp saw once and 20260907-i2cpass blamed on the passthrough.
// 20260907-i2crec recovered a board with 0x07 bit 7 and that was written up as
// "one register write, cheap enough for the shipping path". This file tried to
// use it that way twice and got no handle either time. See the note above
// recover(): the single write is not the cure, and what is left is the ladder.

#include <stdio.h>
#include <stdbool.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "hardware/vreg.h"

#include "cam.h"
#include "qspi_park.h"

#define CAM_I2C_RESET              0x02u   // 0x07 bit[1], the documented one
#define CAM_FPGA_RESET             0x40u   // 0x07 bit[6], what cam_begin() uses
#define CAM_CACHE_RESET            0x80u   // 0x07 bit[7], i2crec's rung 2
#define CAM_REG_MANUAL_GAIN_H      0x31    // [9:8]
#define CAM_REG_MANUAL_GAIN_L      0x32    // [7:0]
#define CAM_REG_MANUAL_EXPOSURE_H  0x33    // [19:16]
#define CAM_REG_MANUAL_EXPOSURE_M  0x34    // [15:8]
#define CAM_REG_MANUAL_EXPOSURE_L  0x35    // [7:0]

// The exposure and gain both arms are held at. Mid-ladder, so the frame is
// neither clipped black nor clipped white - a clipped frame gives the AWB loop
// nothing to work with and would make the free arm look locked.
#define HOLD_EXPOSURE  0x00080u
#define HOLD_GAIN      0x040u

// 1024 addresses from 0x3000. i2crec swept 512 and that covers the exposure
// registers; the OV3640 keeps white balance higher, so this doubles the reach
// to 0x33FF. The two product-ID addresses stay inside it, which is what W0
// needs.
#define SWEEP_BASE  0x3000
#define NSWEEP      1024
#define CTRL_A      0x300A   // reads 0x36 - OV3640 product ID high
#define CTRL_B      0x300B   // reads 0x4C - and low
#define NVISIT      3

static uint8_t free_v[NSWEEP][NVISIT];
static uint8_t lock_v[NSWEEP][NVISIT];

// W1+W2 survivors, carried into stage D.
#define MAXHIT 32
static uint16_t hit[MAXHIT];
static int nhit;

// Stage D. Forty rounds an arm, three captures a round, so each arm watches the
// die over about a hundred and twenty frames - long enough for the loop to have
// somewhere to go, short enough that the run finishes.
#define NROUND 40

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;
static uint8_t cam_id;

static int luma_ch(int m[3])
{
    cam_time_t t;
    uint32_t len = cam_capture(&CAM_RECIPE_VENDOR, m128,
                               CAM_IMAGE_PIX_FMT_RGB565,
                               raw, sizeof raw, &t);
    if (len != sizeof raw)
        return -1;
    cam_frame_means(raw, len, m);
    return (m[0] + m[1] + m[2]) / 3;
}

static int luma(void)
{
    int m[3];
    return luma_ch(m);
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

static void set_mask(uint8_t tracking)
{
    cam_image_auto_mask(tracking);
    sleep_ms(200);
}

// SETTLING IS MEASURED, NOT CHOSEN. Run 3 of this file failed its handle with a
// retrace of 77 on a surface that was plainly responding - 23 41 75 122 168 up
// the ladder and nothing like that coming back down. Four discarded captures
// after each exposure write is what 20260907-i2crec used and it is not enough
// here; the picture is still on its way when the measurement is taken.
//
// Picking a bigger number would be turning a knob until the gate passes, which
// is the failure this repo keeps writing rules to avoid. So the count is not
// picked: capture until three in a row come back EQUAL, which is what settled
// means, and report it when that does not happen inside a bound. Exact
// equality, so there is no tolerance to choose either.
#define SETTLE_MAX 30
static int settle_fail;

static int settled(void)
{
    int a = luma(), b = luma(), c = luma();
    for (int i = 0; i < SETTLE_MAX; i++) {
        if (a >= 0 && a == b && b == c)
            return c;
        a = b; b = c; c = luma();
    }
    settle_fail++;
    return c;
}

static int rangen(const int *v, int n)
{
    int lo = v[0], hi = v[0];
    for (int i = 1; i < n; i++) {
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    return hi - lo;
}

// ---- the handle -------------------------------------------------------------

static uint8_t sensor_read(uint16_t sensor_reg);   // defined below, used to
                                                   // place the ladder

// THE SIX RUNGS ARE PLACED AGAINST THE SCENE, NOT TYPED IN.
//
// 20260907-i2crec's ladder was six doublings from 0x00010 to 0x00200 and this
// file inherited them. Run 4 got 21 41 75 122 176 222 out of that, which is the
// whole scale and a clean handle. Run 5, same board, same room, later in the
// day, got 5 4 7 19 43 84 out of the identical six writes: the light had gone
// and the bottom of the ladder was sitting on the black floor, where doubling
// the exposure of nothing is still nothing. H1 asks every rung to rise and 5 ->
// 4 does not, so run 5 called NO HANDLE on a surface that was plainly answering
// - look at 7 19 43 84 - and then walked the recovery ladder into rung 3, which
// broke capture, and rung 4, which left the board flat at 114.
//
// The six writes are not the handle. The handle is H1 and H2, and those stay
// exactly as they are; loosening a gate because a run failed it is the move
// this repo keeps writing rules against. What has to go is the assumption that
// six numbers fixed in September daylight describe every scene the board will
// ever be pointed at.
//
// So the rungs are anchored to the one statement about this scene that does not
// come from the picture the ladder is about to read: the die's own automatic
// exposure. Free the AE loop, let it settle, and read 0x3002/0x3003 - that is
// the OV3640 saying what this scene needs. Then lock everything, walk a wide
// probe of manual exposures, and read 0x3002/0x3003 back after each; 20260907-
// i2crec's stage B is what makes that readback believable. Whichever manual
// write lands the die nearest its own free-running choice is the middle of the
// ladder, and the six rungs are doublings either side of it.
//
// Nothing here is fitted and nothing here looks at the picture. The units of
// the ArduChip's 20-bit manual exposure and the die's 16-bit AEC never have to
// be known, because both sides of the comparison are read in die units.
#define NRUNG   6
#define NPROBE 14
static const uint32_t PROBE[NPROBE] = {
    0x00008u, 0x00010u, 0x00020u, 0x00040u, 0x00080u, 0x00100u, 0x00200u,
    0x00400u, 0x00800u, 0x01000u, 0x02000u, 0x04000u, 0x08000u, 0x10000u,
};
static uint32_t LADDER[NRUNG] = {
    0x00010u, 0x00020u, 0x00040u, 0x00080u, 0x00100u, 0x00200u,
};
static int ladder_wobble;
static int ladder_span;

// THE FLAT SURFACE IS A MASK TRANSITION, NOT A COLD BOOT.
//
// This has been called a cold-boot fault since 20260825 and run 6 says it is
// not one. Run 6's handle at rest ran on a board that had just had fourteen
// exposures written into it by place_ladder(), and it answered: 5 5 4 7 19 43,
// dark at the bottom but rising. Rung 0 then freed every loop, took eight
// frames, and locked them again - and the very next ladder read 68 68 68 68 68
// 68. Nothing was reset. The only thing that happened between a surface that
// answered and a surface that did not was a mask going to CAM_AUTO_ALL and back
// to zero.
//
// Read the earlier runs again with that in mind and they line up. Runs 1 and 2
// applied the cache reset straight after bring-up, when the only exposure
// writes were the ladder's own, and got nothing. i2crec's rung 2 "cure" fired
// after two rungs of traffic had already gone through. Run 6's rungs 1 and 2
// each wrote one register and changed nothing, because one register write is
// not what wakes it.
//
// So before the rungs are read, the manual surface is given traffic: the bottom
// and top of the ladder, alternately, with a frame taken after each. This is
// conditioning, not a gate - H1 and H2 are unchanged and read exactly the same
// numbers they always did. If the surface is genuinely deaf this will not
// rescue it, and the handle will still say so.
#define NREP  5
#define NWAKE 4
static void wake_manual(void)
{
    for (int i = 0; i < NWAKE; i++) {
        write_exposure(LADDER[0]);
        (void)luma();
        write_exposure(LADDER[NRUNG - 1]);
        (void)luma();
    }
}

// THE HANDLE IS MEASURED WITH EVERY LOOP LOCKED, INCLUDING WHITE BALANCE.
// 20260907-i2crec measured it with CAM_AUTO_WB - white balance left running -
// and on its boards that did not matter. It matters in a probe about white
// balance: luma() is the mean of three channels, so an AWB loop chasing the
// ladder moves the very number the ladder is reading. Locking it removes a
// confound and cannot add one. The arms of stage A are unaffected; they set
// their own mask.
static bool ladder(const char *when)
{
    int up[NRUNG], down[NRUNG], wob[NRUNG];
    int fail_before = settle_fail;
    bool capture_died = false;

    set_mask(0u);
    wake_manual();
    for (int i = 0; i < NRUNG; i++) {
        int rep[NREP];
        write_exposure(LADDER[i]);
        rep[0] = settled();
        for (int k = 1; k < NREP; k++) rep[k] = luma();
        for (int k = 0; k < NREP; k++) if (rep[k] < 0) capture_died = true;
        up[i]  = rep[0];
        wob[i] = rangen(rep, NREP);
    }
    // THE DESCENT IS REPEATED TOO, AND ITS REPEATS COUNT TOWARDS THE WOBBLE.
    //
    // Run 10 held its handle through stage A and then failed H2 on a retrace of
    // 1 against a wobble of 0. Both numbers were honestly measured and the
    // verdict is still an artefact: the wobble came from three captures on the
    // way up and the retrace compares that against a single capture on the way
    // down, so H2 was asking a one-sample descent to match to a precision the
    // ascent had never been asked to demonstrate. A wobble of 0 then asserts
    // noise below one count of an 8-bit mean, which no measurement can support,
    // and one count of difference fails the rule.
    //
    // H2 is unchanged - the descent must retrace to within the worst repeat
    // wobble. What changes is that the wobble is measured over both passes and
    // over more captures, so it is an estimate of this run's noise rather than
    // of the ascent's alone. Widening a rule to pass would be the other thing;
    // this narrows what the rule was always comparing.
    for (int i = NRUNG - 1; i >= 0; i--) {
        int rep[NREP];
        write_exposure(LADDER[i]);
        rep[0] = settled();
        for (int k = 1; k < NREP; k++) rep[k] = luma();
        for (int k = 0; k < NREP; k++) if (rep[k] < 0) capture_died = true;
        down[i] = rep[0];
        int r = rangen(rep, NREP);
        if (r > wob[i]) wob[i] = r;
    }

    if (capture_died) {
        printf("  ladder %s: CAPTURE FAILED - no frame came back, so there is "
               "nothing to\n    read here either way.\n", when);
        return false;
    }

    int worst = 0;
    for (int i = 0; i < NRUNG; i++) if (wob[i] > worst) worst = wob[i];
    ladder_wobble = worst;
    ladder_span = up[NRUNG - 1] - up[0];

    bool h1 = true;
    for (int i = 1; i < NRUNG; i++)
        if (up[i] - up[i - 1] <= worst) h1 = false;

    int retrace = 0;
    for (int i = 0; i < NRUNG; i++) {
        int d = up[i] - down[i];
        if (d < 0) d = -d;
        if (d > retrace) retrace = d;
    }
    bool h2 = retrace <= worst;

    printf("  ladder %s\n    up   ", when);
    for (int i = 0; i < NRUNG; i++) printf("%4d", up[i]);
    printf("\n    down ");
    for (int i = 0; i < NRUNG; i++) printf("%4d", down[i]);
    printf("\n    wobble %d, retrace %d | H1 %s  H2 %s  -> %s",
           worst, retrace, h1 ? "yes" : "NO", h2 ? "yes" : "NO",
           (h1 && h2) ? "HANDLE" : "NO HANDLE");
    if (settle_fail > fail_before)
        printf("   (%d rung%s never settled in %d captures)",
               settle_fail - fail_before,
               settle_fail - fail_before == 1 ? "" : "s", SETTLE_MAX);
    printf("\n");
    return h1 && h2;
}

// ---- the passthrough --------------------------------------------------------

static uint8_t sensor_read(uint16_t sensor_reg)
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

// A 16-BIT READ OFF THIS PASSTHROUGH IS TWO TRANSACTIONS AND THEY CAN TEAR.
//
// Run 6 walked fourteen manual exposures and read 0x3002/0x3003 back after
// each. Most echoed the write exactly - 0x0080 -> 0080, 0x4000 -> 4000, so the
// ArduChip hands its manual exposure to the die in the die's own units. But
// 0x0400 came back 040b and 0x2000 came back 200b, and 0x030b was what the AE
// loop had been holding a moment earlier: the high byte had refreshed and the
// low byte had not. Six of the fourteen came back 030b entire, neither byte
// refreshed.
//
// So a single pair of reads is not a number. Read the pair, read it again, and
// only believe it when the two agree - the same shape as settled(), and for the
// same reason: exact equality, so there is no tolerance to pick. A stale pair
// that is stale twice will still get through, which is why the caller also
// compares against what it wrote.
#define AEC_TRIES 8
static int aec_unstable;

static uint16_t read_aec_raw(void)
{
    uint16_t h = sensor_read(CAM_OV3640_AEC_H);
    uint16_t l = sensor_read(CAM_OV3640_AEC_L);
    return (uint16_t)((h << 8) | l);
}

static uint16_t read_aec(void)
{
    uint16_t prev = 0xffff;
    for (int i = 0; i < AEC_TRIES; i++) {
        uint16_t v = read_aec_raw();
        if (i > 0 && v == prev)
            return v;
        prev = v;
    }
    aec_unstable++;
    return prev;
}

// Place the six rungs. Prints the whole probe, because the correspondence
// between what the ArduChip is written and what the die ends up holding has
// never been tabulated on this board and is worth having on the record whatever
// the ladder does with it.
//
// ONLY THE PROBES THAT ECHO ARE ALLOWED TO PLACE THE LADDER. A probe whose
// readback does not equal the low sixteen bits of what was written has told us
// nothing about the die - run 6's 0x00008 read back 0x030b, which was the AE
// value from a moment earlier and happened to match `want` exactly, so the
// stalest reading in the table won the comparison and put the ladder at the
// bottom of the scale. Requiring the echo is what makes the units shared: among
// probes where the die holds what it was handed, nearest-to-`want` is nearest
// in the die's own units, and no conversion has to be guessed.
static void place_ladder(void)
{
    set_mask(CAM_AUTO_ALL);
    for (int i = 0; i < 12; i++) (void)luma();
    uint16_t want = read_aec();
    printf("  the die's own AE, loop free and settled: 0x%04x\n",
           (unsigned)want);

    set_mask(0u);
    int best = -1, necho = 0;
    long best_d = 1L << 30;
    printf("  written  ");
    for (int i = 0; i < NPROBE; i++) printf(" %6x", (unsigned)PROBE[i]);
    printf("\n  die says ");
    for (int i = 0; i < NPROBE; i++) {
        write_exposure(PROBE[i]);
        sleep_ms(20);
        uint16_t got = read_aec();
        bool echo = (got == (uint16_t)(PROBE[i] & 0xffffu));
        printf("  %04x%c", (unsigned)got, echo ? ' ' : '?');
        if (!echo)
            continue;
        necho++;
        long d = (long)got - (long)want;
        if (d < 0) d = -d;
        if (d < best_d) { best_d = d; best = i; }
    }
    printf("\n  %d of %d echoed the write; ? marks the ones that did not and "
           "are ignored\n", necho, NPROBE);
    if (aec_unstable)
        printf("  %d readback%s never repeated inside %d tries\n",
               aec_unstable, aec_unstable == 1 ? "" : "s", AEC_TRIES);

    // A die that reports 0x0000 with its AE loop free is not telling us about
    // the scene, and the nearest probe to zero would just be the smallest one.
    // Leave the rungs where they were declared and say so, rather than anchor
    // to a number that means nothing.
    if (want == 0 || best < 0) {
        printf("  NOTE: %s, so there is nothing\n        to anchor to. The "
               "ladder keeps the declared six.\n",
               want == 0 ? "the die reported AE 0x0000 with the loop free"
                         : "no probe echoed what was written");
        return;
    }

    // SIX DOUBLINGS SPAN x32 AND THE SENSOR DOES NOT.
    //
    // Both failure modes have now been seen from the same six writes. Runs 5
    // and 6 sat too low and the bottom rungs were on the black floor: 5 4 7 19
    // 43 84, and 5 -> 4 fails H1. Run 9 sat too high and the top rungs were on
    // the white ceiling: 51 95 146 180 180 180, and 180 -> 180 fails H1 just as
    // hard. Run 4's clean 21 41 75 122 176 222 was the fixed ladder happening
    // to straddle the range in September daylight.
    //
    // Floor to ceiling is about a factor of sixteen in exposure at one gain, so
    // no placement of six rungs a factor of two apart fits inside it. The rungs
    // are still six and H1 and H2 still read them exactly as they did; what
    // changes is that the step is three halves instead of two, which spans a
    // little under eight, and the top rung is the die's own AE choice - the one
    // exposure this scene is known to expose correctly at. Everything below it
    // is darker and nothing is above it, so the ceiling is out of reach by
    // construction and the floor is a little under eight times down.
    // The top rung is `want` itself, not the nearest probe to it. The probes
    // are powers of two and the nearest one to 0x024c is 0x0400, seventy per
    // cent high - which is most of why run 9 clipped. The probes' job was to
    // establish that a write lands in the die unchanged, and having established
    // it there is no reason to round to one of them.
    uint32_t top = want;
    LADDER[NRUNG - 1] = top;
    for (int i = NRUNG - 2; i >= 0; i--) {
        uint32_t v = LADDER[i + 1] * 2u / 3u;
        LADDER[i] = v ? v : 1u;
    }

    printf("  0x%05x echoed nearest the die's choice (off by %ld), so writes "
           "land in die\n  units; rungs step by three halves down from 0x%05x:",
           (unsigned)PROBE[best], best_d, (unsigned)top);
    for (int i = 0; i < NRUNG; i++) printf(" %x", (unsigned)LADDER[i]);
    printf("\n");
}

// ---- does a lock reach the die at all? --------------------------------------
//
// THIS STAGE EXISTS BECAUSE RUN 7 SHOWED THE PICTURE WAS THE WRONG INSTRUMENT.
//
// Run 7 walked fourteen manual exposures with every loop locked and the frame
// never left 112. That reads as a deaf exposure surface until you look at what
// the die said: 0x0080 came back 0080 and 0x4000 came back 4000. The write
// reached the die's exposure registers. Ten of the fourteen came back 0x024c
// instead - and 0x024c is exactly what the AE loop had settled on with the mask
// free, thirty seconds earlier.
//
// So they were never stale reads. The loop is still running. It takes the value
// the ArduChip writes, and puts its own back. Four "echoes" are four reads that
// happened to land before it did. A picture pinned near mid-scale whatever you
// write to it is not a broken exposure path - it is a working AE loop that was
// told to stop and did not, and mid-scale is precisely where a working AE loop
// parks a frame.
//
// CAM_REG_AUTO_CONTROL is write-only, so the mask cannot be read back and this
// has never been checkable. It is checkable now, one register lower down: write
// an exposure, then watch 0x3002/0x3003 over a few seconds. Held is a lock that
// took. Dragged back is a loop still running.
//
// The pair is a control and its opposite, and neither side has a constant in
// it: locked must hold what was written, free must not.
#define NPOLL 10

static bool poll_aec(const char *label, uint8_t mask, uint32_t v, uint16_t *last)
{
    set_mask(mask);
    write_exposure(v);
    printf("  %-14s wrote %04x, die:", label, (unsigned)(v & 0xffffu));
    bool held = true;
    uint16_t got = 0;
    // Four frames a poll, not one. An AE loop moves per frame, and run 9's free
    // arm held 0080 for ten single-frame polls - which the stage correctly
    // called inconclusive rather than reading as a lock, but ten frames is thin
    // for a control that has to show movement.
    for (int i = 0; i < NPOLL; i++) {
        sleep_ms(150);
        for (int k = 0; k < 4; k++) (void)luma();
        got = read_aec_raw();
        printf(" %04x", (unsigned)got);
        if (got != (uint16_t)(v & 0xffffu))
            held = false;
    }
    *last = got;
    printf("   %s\n", held ? "held" : "dragged");
    return held;
}

// Two exposures, not one: a single value the loop happens to agree with would
// look held under either mask.
static void lock_check(void)
{
    uint16_t last;
    bool lo = poll_aec("locked", 0u, 0x00080u, &last);
    bool hi = poll_aec("locked", 0u, 0x00400u, &last);
    bool fr = poll_aec("AE free", CAM_AUTO_EXPOSURE, 0x00080u, &last);

    printf("  locked held both exposures:  %s\n", (lo && hi) ? "yes" : "NO");
    printf("  AE free dragged it back:     %s%s\n", fr ? "no" : "yes",
           fr ? "   <- the control failed, so nothing here is readable" : "");

    if (lo && hi && !fr)
        printf("  -> the exposure lock reaches the die and the die keeps it.\n");
    else if (!fr)
        printf("  -> THE EXPOSURE LOCK DOES NOT REACH THE DIE. The AE loop is "
               "still running\n     with the mask at zero, which is what pins "
               "the frame near mid-scale and\n     is what every flat handle in "
               "this directory has actually been.\n");
    else
        printf("  -> INCONCLUSIVE: the die held the write even with AE free, so "
               "this stage\n     cannot tell a lock from a loop that had "
               "nowhere to go.\n");
}

// Settle the die into the arm's state, then take one full sweep into `dst`.
static void sweep_into(uint8_t dst[NSWEEP][NVISIT], int visit)
{
    for (int a = 0; a < NSWEEP; a++)
        dst[a][visit] = sensor_read((uint16_t)(SWEEP_BASE + a));
}

// THE CACHE RESET IS NOT A PRECONDITION, AND THE FIRST TWO RUNS OF THIS FILE
// ARE EVIDENCE AGAINST TREATING IT AS ONE.
//
// 20260907-i2crec found 0x07 bit 7 recovering a flat board and this file was
// written to use it. Run 1 applied it straight after cam_begin() and got no
// handle: 134 134 134 135 134 134 up the ladder. Run 2 applied it after every
// mask write, twice at rest, and got no handle either: 137 140 141 140 140 140,
// then six identical 141s.
//
// Look again at where i2crec's rung 2 actually sat. It fired only after the
// mask had been re-applied with eight captures behind it (rung 0) and after
// 0x07 bit 1 (rung 1), and the handle that came back alive was measured after
// a THIRD application of the mask. That is one observation of a sequence, and
// this file's own README and commit message called it "one register write".
// Two runs now say the single write is not sufficient, so the honest thing is
// to stop asserting the rung and go back to walking the ladder that found it.
//
// The rungs, in the order and the form 20260907-i2crec ran them. Whichever one
// this board comes back on is reported, and a board that comes back on rung 2
// alone is the only outcome that supports the claim as written.
//
// RUNGS 3 AND 4 DESTROY WHAT RUNGS 0 TO 2 MAY HAVE BUILT, so every rung's span
// is kept and printed at the end. Run 5 walked all five and reported only the
// last, which was flat; rung 2 in the same run had gone 5 4 7 19 43 84 and that
// disappeared into the transcript. The span is reported, not gated on - a wide
// span is not a handle and nothing downstream reads it.
static int rung_span[5];

static int recover(void)
{
    for (int i = 0; i < 5; i++) rung_span[i] = -1;
    printf("  -- recovery ladder --\n");

    printf("     rung 0: apply the auto mask a second time, no reset\n");
    set_mask(CAM_AUTO_ALL);
    for (int i = 0; i < 8; i++) (void)luma();
    if (ladder("after rung 0")) return 0;
    rung_span[0] = ladder_span;

    printf("     rung 1: 0x07 bit 1, the documented I2C reset\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_RESET);
    cam_wait_idle("i2c reset");
    sleep_ms(100);
    if (ladder("after rung 1")) return 1;
    rung_span[1] = ladder_span;

    printf("     rung 2: 0x07 bit 7, reset cache - i2crec came back on this\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_CACHE_RESET);
    cam_wait_idle("cache reset");
    sleep_ms(100);
    if (ladder("after rung 2")) return 2;
    rung_span[2] = ladder_span;

    printf("     rung 3: 0x07 bit 6, reset FPGA - what cam_begin() uses\n");
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_FPGA_RESET);
    cam_wait_idle("fpga reset");
    sleep_ms(300);
    if (ladder("after rung 3")) return 3;
    rung_span[3] = ladder_span;

    printf("     rung 4: the whole of cam_begin() again\n");
    cam_begin(cam_id, true);
    m128 = cam_mode_128(cam_id);
    set_mask(CAM_AUTO_ALL);
    for (int i = 0; i < 20; i++) (void)luma();
    if (ladder("after rung 4")) return 4;
    rung_span[4] = ladder_span;

    printf("  no rung passed. Top rung minus bottom rung, by rung:");
    for (int i = 0; i < 5; i++) printf("  %d:%d", i, rung_span[i]);
    printf("\n");
    return -1;
}

// THE HOLD EXPOSURE IS PICKED OFF THIS BOARD'S OWN LADDER, NOT TYPED IN.
// HOLD_EXPOSURE as a literal gave run 4 a frame at R 39 G 46 B 39 - a quarter
// of the way up the scale - and a stage D where the WB loop had nothing to do
// even with the lock off, which made the whole stage unreadable. A near-black
// frame is not a scene an auto-white-balance loop can be observed working on.
//
// So the rung is chosen rather than assumed: walk the same six the ladder
// walks, take the one whose settled picture lands nearest the middle of the
// scale, and print which. Mid-scale is not a threshold anything passes or
// fails - no rule below reads it - it is where a sensor has the most room to
// move in both directions.
static uint32_t hold_exposure = HOLD_EXPOSURE;

static void choose_hold(void)
{
    set_mask(0u);
    int best = -1, best_d = 1 << 30;
    printf("  rung   ");
    for (int i = 0; i < NRUNG; i++) {
        write_exposure(LADDER[i]);
        int v = settled();
        printf("%4d", v);
        int d = v - 128;
        if (d < 0) d = -d;
        if (v >= 0 && d < best_d) { best_d = d; best = i; }
    }
    if (best < 0) best = NRUNG / 2;
    hold_exposure = LADDER[best];
    printf("\n  holding both arms at 0x%05x, the rung nearest mid-scale\n",
           (unsigned)hold_exposure);
}

// Both arms hold exposure and gain at the same written values. The only bit
// that differs is CAM_AUTO_WB.
static void enter_arm(bool wb_free)
{
    set_mask(wb_free ? CAM_AUTO_WB : 0u);
    write_exposure(hold_exposure);
    write_gain(HOLD_GAIN);
    for (int i = 0; i < 8; i++) (void)luma();
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

    printf("\n=== Does the white-balance lock reach the die, and does it hold? "
           "(#30, #33) ===\n\n");
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

    printf("\n-- warm-up, auto loops running --\n");
    set_mask(CAM_AUTO_ALL);
    for (int i = 0; i < 20; i++) (void)luma();
    printf("  settled at luma %d\n", luma());

    printf("\n-- placing the ladder against this scene --\n");
    place_ladder();

    printf("\n-- does a lock reach the die? asked at 0x3002/0x3003, not at the "
           "picture --\n");
    lock_check();

    // ---- stage M: does ANY mask bit reach the die? ----
    //
    // This runs before the handle because the handle can no longer be reached.
    // Runs 5 to 8 all halted on NO HANDLE, and run 8 said why: the AE loop does
    // not stop, so the frame is parked at mid-scale and the exposure ladder has
    // nothing to climb. The picture is not going to answer anything until that
    // is understood, and the sweep does not need the picture.
    //
    // 0x3002/0x3003 already showed the exposure bit not landing. The question
    // this widens it to is whether the ArduChip's auto mask reaches the die AT
    // ALL - if 1024 addresses read identically with every loop free and with
    // every loop locked, then CAM_REG_AUTO_CONTROL is not talking to this
    // sensor, and #30's white-balance arm is answered along with the exposure
    // one. Same W0 control as stage A: the product ID must come back, or the
    // sweep is not reading and the run says nothing.
    printf("\n-- stage M: sweep %d addresses from 0x%04x, every loop free "
           "against every loop locked --\n", NSWEEP, SWEEP_BASE);
    {
        static const bool MORDER[NVISIT * 2] = {
            true, false, false, true, true, false
        };
        int mfi = 0, mli = 0;
        for (int v = 0; v < NVISIT * 2; v++) {
            bool all_free = MORDER[v];
            set_mask(all_free ? CAM_AUTO_ALL : 0u);
            write_exposure(LADDER[NRUNG / 2]);
            write_gain(HOLD_GAIN);
            for (int i = 0; i < 8; i++) (void)luma();
            if (all_free) sweep_into(free_v, mfi++);
            else          sweep_into(lock_v, mli++);
            printf("  visit %d done  (loops %s)\n", v,
                   all_free ? "free" : "LOCKED");
        }

        bool mw0 = true;
        for (int v = 0; v < NVISIT; v++) {
            if (free_v[CTRL_A - SWEEP_BASE][v] != 0x36) mw0 = false;
            if (lock_v[CTRL_A - SWEEP_BASE][v] != 0x36) mw0 = false;
            if (free_v[CTRL_B - SWEEP_BASE][v] != 0x4c) mw0 = false;
            if (lock_v[CTRL_B - SWEEP_BASE][v] != 0x4c) mw0 = false;
        }
        printf("  W0 positive control, 0x%04x and 0x%04x in all six visits: "
               "%s\n", CTRL_A, CTRL_B, mw0 ? "yes" : "NO");
        if (!mw0) {
            printf("\nRESULT : THE SWEEP IS NOT READING, so stage M says "
                   "nothing in either\n         direction. Not a null "
                   "result.\n");
            while (true) tight_loop_contents();
        }

        int msep = 0, mnoisy = 0;
        for (int a = 0; a < NSWEEP; a++) {
            bool disjoint = true, still = true;
            for (int i = 0; i < NVISIT; i++) {
                if (free_v[a][i] != free_v[a][0]) still = false;
                if (lock_v[a][i] != lock_v[a][0]) still = false;
                for (int j = 0; j < NVISIT; j++)
                    if (free_v[a][i] == lock_v[a][j]) disjoint = false;
            }
            if (disjoint && still) {
                if (msep < 24)
                    printf("    0x%04x  free %02x %02x %02x   locked %02x %02x "
                           "%02x\n", (unsigned)(SWEEP_BASE + a),
                           free_v[a][0], free_v[a][1], free_v[a][2],
                           lock_v[a][0], lock_v[a][1], lock_v[a][2]);
                msep++;
            } else if (!still) {
                mnoisy++;
            }
        }
        if (msep > 24)
            printf("    ... and %d more, not printed\n", msep - 24);
        printf("  %d addresses separated and held, %d moved without "
               "separating, %d never moved\n",
               msep, mnoisy, NSWEEP - msep - mnoisy);
        if (msep == 0)
            printf("  -> NOT ONE OF %d ADDRESSES CHANGED. The ArduChip's auto "
                   "mask does not\n     reach this die, and that is one fact "
                   "about exposure, gain and white\n     balance together, not "
                   "three separate ones.\n", NSWEEP);
        else
            printf("  -> the mask reaches the die at the addresses above.\n");
    }

    printf("\n-- the handle, before anything is asked of the die --\n");
    int rung = -2;                 // -2: never needed a rung
    if (!ladder("at rest")) {
        rung = recover();
        if (rung < 0) {
            printf("\nRESULT : NO HANDLE, AND NO RUNG BROUGHT IT BACK - not "
                   "the mask again, not\n         0x07 bit 1, not bit 7, not "
                   "bit 6, not the whole of cam_begin().\n         Nothing "
                   "below can be read as a statement about white balance,\n"
                   "         and the flat boot is worse than 20260907-i2crec "
                   "left it: that\n         directory always recovered. "
                   "Power-cycle the hub and repeat.\n");
            while (true) tight_loop_contents();
        }
        printf("  recovered on rung %d. The run below stands on a board that "
               "needed one,\n  which is worth knowing when reading it.\n", rung);
        // The rungs that recover also reset the die, so the placement measured
        // before them describes a board that no longer exists. Re-place. The
        // handle verdict above is not re-scored; it stands as it was measured.
        printf("  re-placing the ladder now the die has been reset:\n");
        place_ladder();
    }

    printf("\n-- picking the exposure both arms are held at --\n");
    choose_hold();

    // ---- stage A: does the WB bit change anything in the die? ----

    printf("\n-- stage A: sweep %d addresses from 0x%04x, WB free against WB "
           "locked --\n", NSWEEP, SWEEP_BASE);

    // FREE LOCK LOCK FREE FREE LOCK. Each arm gets an early, a middle and a
    // late visit, so a monotone drift through the stage cannot separate them.
    static const bool ORDER[NVISIT * 2] = { true, false, false, true, true, false };
    int fi = 0, li = 0;
    for (int v = 0; v < NVISIT * 2; v++) {
        bool wb_free = ORDER[v];
        enter_arm(wb_free);
        if (wb_free) sweep_into(free_v, fi++);
        else         sweep_into(lock_v, li++);
        printf("  visit %d done  (WB %s)\n", v, wb_free ? "free" : "LOCKED");
    }

    // W0 first, because it decides whether the rest is reportable.
    bool w0 = true;
    for (int v = 0; v < NVISIT; v++) {
        if (free_v[CTRL_A - SWEEP_BASE][v] != 0x36) w0 = false;
        if (lock_v[CTRL_A - SWEEP_BASE][v] != 0x36) w0 = false;
        if (free_v[CTRL_B - SWEEP_BASE][v] != 0x4c) w0 = false;
        if (lock_v[CTRL_B - SWEEP_BASE][v] != 0x4c) w0 = false;
    }
    printf("\n  W0 positive control: 0x%04x and 0x%04x read 0x36 and 0x4c in "
           "all six visits: %s\n", CTRL_A, CTRL_B, w0 ? "yes" : "NO");
    if (!w0) {
        printf("\nRESULT : THE SWEEP IS NOT READING. The product ID did not "
               "come back at the\n         two addresses six boots have read "
               "it at, so this run says nothing\n         about white balance "
               "in either direction. Not a null result.\n");
        while (true) tight_loop_contents();
    }

    printf("\n  sensor   WB free        WB locked      verdict\n");
    nhit = 0;
    int nsep_unstable = 0, nnoisy = 0, nsame = 0, nover = 0;
    for (int a = 0; a < NSWEEP; a++) {
        const uint8_t *f = free_v[a], *l = lock_v[a];
        bool f_still = f[0] == f[1] && f[1] == f[2];
        bool l_still = l[0] == l[1] && l[1] == l[2];
        bool still = f_still && l_still;
        // W1: the two sets disjoint. With W2 holding this is just f[0] != l[0],
        // but it is checked as written so that an unstable register cannot
        // sneak through on one lucky pair.
        bool disjoint = true;
        for (int i = 0; i < NVISIT; i++)
            for (int j = 0; j < NVISIT; j++)
                if (f[i] == l[j]) disjoint = false;

        if (!disjoint) {
            // Not reported. Two quite different things, counted separately:
            // a register that never moved at all, and one that moved without
            // the arms ever parting - the second is noise, and a run with a
            // lot of it is a run to distrust.
            if (still) nsame++; else nnoisy++;
            continue;
        }

        printf("  0x%04x   %02x %02x %02x      %02x %02x %02x      ",
               (unsigned)(SWEEP_BASE + a), f[0], f[1], f[2], l[0], l[1], l[2]);
        if (still) {
            if (nhit < MAXHIT) {
                printf("W1 and W2 - RESPONDS\n");
                hit[nhit++] = (uint16_t)(SWEEP_BASE + a);
            } else {
                printf("W1 and W2 - RESPONDS, over the %d stage D can watch\n",
                       MAXHIT);
                nover++;
            }
        } else {
            printf("separates but is not still - not claimed\n");
            nsep_unstable++;
        }
    }
    printf("\n  %d responded to the WB bit, %d separated without being still, "
           "%d wobbled without\n  separating, %d never moved.\n",
           nhit + nover, nsep_unstable, nnoisy, nsame);
    if (nover)
        printf("  %d MORE RESPONDED THAN STAGE D CAN WATCH and are listed "
               "above but not followed.\n", nover);

    // ---- stage B: what the pixels do, alongside what the die says ----

    printf("\n-- the frame in each arm, three channels --\n");
    int fm[3], lm[3];
    enter_arm(true);
    for (int i = 0; i < 6; i++) (void)luma();
    (void)luma_ch(fm);
    enter_arm(false);
    for (int i = 0; i < 6; i++) (void)luma();
    (void)luma_ch(lm);
    printf("  WB free     R %3d  G %3d  B %3d   spread %d\n",
           fm[0], fm[1], fm[2], rangen(fm, 3));
    printf("  WB locked   R %3d  G %3d  B %3d   spread %d\n",
           lm[0], lm[1], lm[2], rangen(lm, 3));
    printf("  20260825-camlock saw 130 127 129 go to 100 158 122 on this "
           "operation.\n");

    if (!ladder("after stage A")) {
        printf("\nRESULT : THE HANDLE WAS ALIVE FOR STAGE A AND GONE AFTER IT. "
               "The table above\n         spans a change in the die that this "
               "run cannot attribute to the WB\n         bit. Repeat before "
               "reading it.\n");
        while (true) tight_loop_contents();
    }

    if (nhit == 0) {
        printf("\n=== stage A ===\n\n"
               "  NOTHING IN %d ADDRESSES RESPONDS TO THE WB BIT, with the "
               "positive control\n  passing and a handle either side. So "
               "cam_image_auto_mask()'s white\n  balance lock does not reach "
               "anything readable in 0x%04x-0x%04x.\n\n"
               "  That is a real null and it is not the end of the question: "
               "the OV3640 has\n  registers outside this window, and a lock "
               "the ArduChip implements in its\n  own pipeline would look "
               "exactly like this. What it does rule out is the\n  reading "
               "that 'L' step 2 drops the die's gains to unity - the die's "
               "gains,\n  wherever they are in this window, did not move.\n\n"
               "  Stage D has nothing to watch and does not run.\n");
        while (true) tight_loop_contents();
    }

    // ---- stage D: does the lock hold? ----

    printf("\n-- stage D: %d rounds an arm on the %d register%s stage A named "
           "--\n", NROUND, nhit, nhit == 1 ? "" : "s");

    // The null. Three back-to-back reads with nothing changing between them is
    // what "did not move" has to beat, and it is measured on this board in this
    // session rather than assumed.
    enter_arm(false);
    int repeat_wobble = 0;
    for (int h = 0; h < nhit; h++) {
        int r[3];
        for (int k = 0; k < 3; k++) r[k] = sensor_read(hit[h]);
        int w = rangen(r, 3);
        if (w > repeat_wobble) repeat_wobble = w;
    }
    printf("  repeat wobble, three back-to-back reads, worst over the %d: %d\n",
           nhit, repeat_wobble);

    // LOCKED, FREE, LOCKED. A room that brightens through the run cannot make
    // the locked arm look still and the free arm look busy in both blocks.
    static const bool BLOCK[3] = { false, true, false };
    int excursion[3][MAXHIT];
    for (int b = 0; b < 3; b++) {
        int lo[MAXHIT], hi[MAXHIT];
        enter_arm(BLOCK[b]);
        for (int h = 0; h < nhit; h++) { lo[h] = 256; hi[h] = -1; }
        for (int r = 0; r < NROUND; r++) {
            for (int k = 0; k < 3; k++) (void)luma();
            for (int h = 0; h < nhit; h++) {
                int v = sensor_read(hit[h]);
                if (v < lo[h]) lo[h] = v;
                if (v > hi[h]) hi[h] = v;
            }
        }
        for (int h = 0; h < nhit; h++) excursion[b][h] = hi[h] - lo[h];
        printf("  block %d, WB %-6s done\n", b, BLOCK[b] ? "free" : "LOCKED");
    }

    printf("\n  sensor   locked  free  locked   (excursion over %d rounds "
           "each)\n", NROUND);
    bool locked_still = true, free_moved = false;
    for (int h = 0; h < nhit; h++) {
        printf("  0x%04x   %4d   %4d   %4d\n", (unsigned)hit[h],
               excursion[0][h], excursion[1][h], excursion[2][h]);
        if (excursion[0][h] > repeat_wobble || excursion[2][h] > repeat_wobble)
            locked_still = false;
        if (excursion[1][h] > repeat_wobble)
            free_moved = true;
    }

    (void)ladder("after stage D");

    printf("\n=== what this run says ===\n\n");
    if (rung == -2)
        printf("  The handle was alive at rest and no rung was needed.\n");
    else
        printf("  THE HANDLE NEEDED RUNG %d. i2crec's claim was rung 2 alone; "
               "this board %s.\n", rung,
               rung == 2 ? "agrees" : "does not");
    printf("  W1+W2  %d register%s in 0x%04x-0x%04x respond to the WB bit.\n",
           nhit, nhit == 1 ? "" : "s", SWEEP_BASE, SWEEP_BASE + NSWEEP - 1);
    printf("  W3     free arm moved beyond the repeat wobble:     %s%s\n",
           free_moved ? "yes" : "no",
           free_moved ? "" : "   <- stage D's positive control, and it failed");
    printf("         locked arms still within it:                 %s\n",
           locked_still ? "yes" : "NO");

    // THE FREE ARM IS STAGE D's POSITIVE CONTROL AND IT IS CHECKED FIRST. Run 4
    // of this file got locked excursions of 0, 0 and 1 against a repeat wobble
    // of 0, and a free arm that did not move at all - and an earlier ordering
    // of these branches read that single count as "THE LOCK DOES NOT HOLD".
    // That was the void-verdict mistake again in a new place: a degenerate null
    // (wobble 0) turning one count into a signal, on a run where the arm that
    // was supposed to move had not moved either. If the loop had nowhere to go,
    // neither arm's stillness means anything, and that has to be said before
    // anything else is.
    if (!free_moved) {
        printf("\n  STAGE D IS INCONCLUSIVE AND THE LOCKED COLUMNS ABOVE MEAN "
               "NOTHING. With the\n  WB loop FREE the registers did not move "
               "either, so the loop had nowhere\n  to go and stillness under "
               "the lock is not evidence that the lock did it.\n  The scene "
               "has to change colour during the free block for this stage to\n"
               "  have a question in it - an operator at the rig, or a longer "
               "watch across\n  a light that is actually varying. Nothing "
               "here is a verdict either way.\n");
    } else if (locked_still) {
        printf("\n  THE LOCK IS REAL AND IT IS READABLE. The registers the WB "
               "bit moves stop\n  moving when it is off, in two separate "
               "blocks with a free block between\n  them, and the free block "
               "shows the loop had somewhere to go. #30's AWB\n  arm can be "
               "written against these addresses and verified rather than "
               "hoped\n  at. What it still cannot do is CHOOSE the value: "
               "0x48 is a read path.\n");
    } else {
        printf("\n  THE LOCK DOES NOT HOLD. Registers the WB bit moves keep "
               "moving with it\n  off, by more than a back-to-back read "
               "wobbles. Turning the AWB off is\n  therefore NOT sufficient "
               "for #30, and the walk it was supposed to remove\n  has no "
               "remaining suspect on the list this repo has been working "
               "down.\n  That is worth more than a confirmation would have "
               "been. Reproduce it\n  before acting on it.\n");
    }

    printf("\n  NOT CLAIMED: what the responding registers ARE. This probe "
           "names addresses\n  that follow a bit, which is not the same as "
           "reading a datasheet. 0x3002 and\n  0x3003 are exposure and were "
           "identified by writing a value and reading it\n  back; nothing here "
           "does that for white balance, because there is no write\n  path to "
           "the die.\n");

    while (true) tight_loop_contents();
}
