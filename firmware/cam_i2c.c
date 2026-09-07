// Can the ArduChip pass an I2C read through to the sensor die? (#33, and #30
// downstream of it)
//
// WHY THIS IS THE NEXT THING AND NOT A DETOUR. On 2026-09-07,
// bench/soak/20260907-simsrc-vs-live/ scored 541 frames with the sensor out of
// the loop and got a `common` walk of exactly 0.00 against a live arm's 2.45.
// Everything downstream of the sensor readout is cleared. 20260907-camlock-cold
// had already cleared the exposure and gain loops - locking them did not reduce
// the walk. What is left is the auto white balance, and it is the one loop that
// CANNOT CURRENTLY BE HELD:
//
//     bench/soak/20260825-camlock/, attribute.log, one still room:
//       exposure + gain frozen           mean RGB 130 127 129
//       + white balance frozen           mean RGB 100 158 122
//
// Clearing bit 7 on the white-balance selector drops the red and blue gains
// toward unity instead of latching what the loop converged to. That is not an
// AWB arm, it is a green cast, and it moves the background and every cosine
// with it - which is why 'L' step 2 is kept as a CONTROL and not offered as an
// arm. 0x31-0x35 do not help: they are gain and exposure and there is no manual
// white-balance register in that block. The only route to holding the AWB gains
// is to reach the sensor die's own registers, and the only route to those is
// this passthrough. THE AWB ARM IS BLOCKED ON THIS BINARY.
//
// WHAT THE TWO SOURCES SAY, AND THEY DISAGREE ON THE ONE BIT THAT MATTERS.
// cam.h's header states the rule: the register map in that file is transcribed
// from ArduCAM's driver, NOT from the application note, "which disagrees with
// it". Here is the disagreement, on 0x07 bit 0:
//
//     the driver     FIFO_CLEAR_ID_MASK 0x01, written to ARDUCHIP_FIFO_2 0x07
//     the app note   0x07 bit[0], write 1 to initiate an I2C direct read
//
// cam.c already writes 0x07 twice - 0x80 to flush the FIFO at cam.c:455, and
// 0x40 during bring-up - so this register is live in the shipping path under
// the driver's reading of it. If the app note is right about bit 0, docs and
// firmware gain a readback nothing in this repo has ever had. If the driver is
// right, writing 1 there clears a FIFO id and the passthrough is not reachable
// this way at all. NOTHING IN THE REPO HAS RUN IT, and this is the same shape
// as 0x31-0x35 last week and the 1200-baud CDC touch before that: two sources,
// one unmeasured bit, and a file stating one of them as fact.
//
// THE UNKNOWN THIS PROBE HAS THAT THE OTHERS DID NOT: WHERE THE ANSWER LANDS.
// The app note names the address registers and the initiate bit and stops.
//
//     0x0A   I2C device address        cam.c writes 0x78 in cam_begin()
//     0x0B   register address [15:8]   never written
//     0x0C   register address [7:0]    never written
//     0x07   bit[0] initiate read      never written as 1
//
// There is no data register in that list and none in cam.h. So a read can be
// fired and its result is somewhere unnamed, or nowhere. Finding it is stage A
// and it is a search, not a check.
//
// ------------------------------------------------------------------ RULES ---
//
// Fixed here, before the run, and threshold-free in the same way rule (c) of
// cam_manexp.c was - the failure of that file's rule (b) was that it carried a
// constant, and a null with range 0 made it unsatisfiable. Nothing below
// compares anything to a number chosen by me.
//
// STAGE A finds the data register, if there is one. For each of NADDR sensor
// addresses the probe writes 0x0B/0x0C, fires 0x07 = 0x01, and dumps the whole
// ArduChip space 0x00-0x7F. Every address is visited three times: ascending,
// descending, and ascending again.
//
//   An ArduChip register r IS THE DATA REGISTER iff both hold:
//
//     (A1) SPREAD BEATS SCATTER. The range of r's per-address means across the
//          NADDR addresses exceeds the worst range r shows across the three
//          visits to any single address. It has to depend on WHICH address was
//          asked for, by more than it wobbles when nothing changed.
//
//     (A2) RETRACE IS EXACT. r reads the same value on the ascending and
//          descending visits to every address - gap 0, no tolerance. This is
//          what kills a free-running counter, which passes (A1) trivially and
//          means nothing.
//
// A1 EXCLUDES THE REGISTERS THIS PROBE WRITES, AND THAT CLAUSE WAS ADDED AFTER
// THE FIRST RUN. See bench/probe/20260907-i2cpass/discover.log: A1 as first
// written named 0x0b and 0x0c, which are the two registers the probe puts the
// sensor address INTO. They passed honestly - their value does depend on which
// address was asked for - because the rule could not tell an answer from an
// echo of the question. That is the same construction defect rule (b) of
// cam_manexp.c had, and it is fixed the same way: by naming the defect and
// excluding the inputs, not by moving a threshold. There is still no constant
// anywhere in A1 or A2.
//
// The first run also measured two things this header had guessed at. The
// ArduChip space is COMPLETELY STATIC at rest - three dumps, not one byte
// moved, including 0x01, which this file expected to be a running frame counter
// and which reads 00. And firing the bit thirty-six times did not disturb the
// capture path: luma 107 afterwards. Its log is kept rather than deleted,
// because the register it named is the same one the corrected rule names.
//
// Both conditions are relations between measurements this run makes. Neither
// can be satisfied by a register that is merely constant, and neither needs the
// address ladder to have been well chosen - it only needs SOME two addresses on
// it to hold different values on the die.
//
// STAGE B runs only if stage A names a register, and it is the causal check
// that makes stage A worth believing. 0x33-0x35 were proven last week to move
// the picture, so the probe writes two exposures four decades apart and reads
// a block of sensor registers through the passthrough under each.
//
//   A sensor address CARRIES THE EXPOSURE iff its value differs between the two
//   written exposures AND is identical across three reads at each of them.
//
// STAGE B SWEEPS ITS OWN BLOCK AND NOT STAGE A'S LADDER, and the reason is a
// flaw found before the run rather than after. Stage A's twelve addresses were
// chosen to have SOME two that differ, which is all its rules need. None of
// them is an exposure register on any plausible map, so stage B over that
// ladder would have returned nothing and the verdict as first written would
// have called a real stage-A register an artefact. Widening where stage B
// LOOKS is not the same as moving what counts as an answer: the rule above is
// unchanged, and it still contains no constant.
//
// WHAT A NULL MEANS HERE, WRITTEN DOWN BEFORE IT HAPPENS. If no register passes
// A1 and A2, that is not "the passthrough is broken". It is one of three, and
// the probe distinguishes the third from the other two:
//
//   1. the result lands somewhere outside 0x00-0x7F
//   2. the read needs something the app note does not mention - a delay, a
//      different device address, a second write
//   3. bit 0 is the driver's FIFO_CLEAR_ID_MASK and no read was ever fired
//
// For 3 the probe takes a frame after the last fire and checks the camera still
// captures a sane picture. A bit that quietly broke the FIFO and a bit that did
// nothing are different findings and only one of them is safe to leave in the
// shipping path.
//
// SAFETY. Every I2C transaction here is a READ. Nothing is written to the die.
// 0x07 is written as the literal 0x01 and never read-modify-write, because bit
// 6 is reset FPGA and bit 7 is reset cache and carrying a stale one of those
// into an initiate would reset the part mid-probe.

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "hardware/vreg.h"

#include "cam.h"
#include "qspi_park.h"

// Application note only, and deliberately not in cam.h - same reason
// cam_simsrc.c kept 0x05 and 0x06 out of it until one of them was adopted.
#define CAM_REG_I2C_ADDR_H         0x0B
#define CAM_REG_I2C_ADDR_L         0x0C
#define CAM_I2C_INITIATE_READ      0x01u   // 0x07 bit[0], the disputed bit
#define CAM_I2C_RESET              0x02u   // 0x07 bit[1], the recovery
#define CAM_REG_MANUAL_EXPOSURE_H  0x33
#define CAM_REG_MANUAL_EXPOSURE_M  0x34
#define CAM_REG_MANUAL_EXPOSURE_L  0x35

// The device address cam_begin() writes. Restated rather than read back,
// because 0x0A's readback is one of the things this probe is testing.
#define SENSOR_I2C_ADDR 0x78

// THE LADDER IS A GUESS AND THE RULES DO NOT DEPEND ON IT. The die is unknown -
// 0x40 reads 0x82, which cam.h only resolves as "below 5MP" - so its register
// map is unknown too, and so is whether it takes 8-bit or 16-bit addresses.
// The ladder therefore covers both conventions rather than betting on one: the
// low block that an 8-bit map would use, and the 0x300x block that is where an
// OmniVision-style 16-bit map keeps its chip id. All the rules need is that
// SOME two of these hold different values on the die.
static const uint16_t ADDR[] = {
    0x0000, 0x0001, 0x0002, 0x0003, 0x000A, 0x001C, 0x001D,
    0x3000, 0x3002, 0x300A, 0x300B, 0x3100,
};
#define NADDR ((int)(sizeof ADDR / sizeof ADDR[0]))

// The dump covers the whole 8-bit space, which closes null 1. Only the low half
// can be a CANDIDATE, because the second run showed the space is seven bits
// wide and the top half is a mirror: 0x8b/0x8c/0xc8 reproduced 0x0b/0x0c/0x48
// byte for byte across all twelve addresses and all three visits, spreads 49,
// 29 and 76 identical. Reporting an alias as a second register would have been
// a finding about the probe. The full dump is still taken and the mirror is
// still checked, because "the space is 7 bits" is itself worth stating once.
#define NREG   0x100
#define NCAND  0x80
#define VISITS 3      // up, down, up

// THE PROBE'S OWN INPUTS CANNOT BE ITS OUTPUT, AND THIS IS WHY THE LIST EXISTS.
// The first run of this file named 0x0b and 0x0c as data registers. They passed
// A1 honestly - their value does depend on which address was asked for - and
// they are the two registers the probe WRITES that address into. A1 as first
// written could not tell an answer from an echo of the question, which is the
// same construction defect rule (b) of cam_manexp.c had and is fixed the same
// way: by naming it, not by tuning a threshold. 0x0a is the device address and
// is written too. 0x07 is written as the initiate strobe.
static inline bool probe_writes(int r)
{
    return r == CAM_REG_DEBUG_DEVICE_ADDRESS || r == CAM_REG_I2C_ADDR_H ||
           r == CAM_REG_I2C_ADDR_L || r == CAM_REG_SENSOR_RESET;
}

// Two exposures four decades apart, both inside the working range 0x31-0x35 was
// measured to have (below 0x400 at room light; the 0x33 nibble goes dark).
#define EXP_LOW   0x00010u
#define EXP_HIGH  0x00200u

// STAGE B'S BLOCK. Wide rather than aimed, because the point of stage B is to
// find an address without being told one. 0x3400-0x35ff is 512 addresses and
// covers where an OmniVision-style map keeps its AEC and AGC. It costs about
// eight seconds and the rule that reads it does not know what is in it.
#define SWEEP_BASE  0x3400
#define NSWEEP      512

static uint8_t lo_v[NSWEEP][VISITS], hi_v[NSWEEP][VISITS];

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;

// [visit][address][register]
static uint8_t dump[VISITS][NADDR][NREG];
// The dump taken before any initiate was fired, three times, to learn which
// registers move on their own. Printed but NOT used as a rule: a register that
// is volatile at rest can still be the data register, and A2 is what excludes
// the ones that are only volatile.
static uint8_t rest[VISITS][NREG];

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

// One passthrough read attempt. Returns nothing: WHERE the answer lands is the
// question, so the caller dumps the whole space afterwards rather than being
// handed a byte this function guessed at.
static void fire_read(uint16_t sensor_reg)
{
    cam_write_reg(CAM_REG_DEBUG_DEVICE_ADDRESS, SENSOR_I2C_ADDR);
    cam_wait_idle("i2c device address");
    cam_write_reg(CAM_REG_I2C_ADDR_H, (uint8_t)(sensor_reg >> 8));
    cam_wait_idle("i2c register address high");
    cam_write_reg(CAM_REG_I2C_ADDR_L, (uint8_t)(sensor_reg & 0xff));
    cam_wait_idle("i2c register address low");
    // The literal, not a read-modify-write. See the SAFETY note in the header.
    cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_INITIATE_READ);
    // The app note gives no completion signal for this and cam_wait_idle()
    // watches the SENSOR_STATE bit, which is about captures. So: both. The
    // sleep is not a tuned constant - it is longer than any plausible 100 kHz
    // two-byte transaction and costs nothing at twelve addresses.
    cam_wait_idle("i2c direct read");
    sleep_ms(2);
}

static void dump_space(uint8_t *into)
{
    for (int r = 0; r < NREG; r++)
        into[r] = cam_read_reg((uint8_t)r);
}

static int range_int(const int *v, int n)
{
    int lo = v[0], hi = v[0];
    for (int i = 1; i < n; i++) {
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    return hi - lo;
}

static int range_u8(const uint8_t *v, int n)
{
    int lo = v[0], hi = v[0];
    for (int i = 1; i < n; i++) {
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    return hi - lo;
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

    printf("\n=== Does 0x07 bit 0 fire an I2C read, or clear a FIFO id? "
           "(#33) ===\n\n");
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
    printf("  luma before anything is fired: %d\n", luma());

    // ---------------------------------------------------------------- rest --
    // What moves when nothing is asked of it. Not a rule, a reader's aid: the
    // registers listed here are the ones a reader should be suspicious of
    // further down, and 0x01 being among them is the point of A2.
    printf("\n-- the space at rest, three dumps, nothing fired --\n");
    for (int v = 0; v < VISITS; v++) {
        dump_space(rest[v]);
        sleep_ms(50);
    }
    printf("  registers that moved on their own:");
    int nrest = 0;
    for (int r = 0; r < NREG; r++) {
        uint8_t three[VISITS];
        for (int v = 0; v < VISITS; v++) three[v] = rest[v][r];
        if (range_u8(three, VISITS)) {
            printf(" %02x(%d)", r, range_u8(three, VISITS));
            nrest++;
        }
    }
    printf("%s\n", nrest ? "" : " none");

    // ------------------------------------------------------------- stage A --
    printf("\n-- stage A: fire a read at %d addresses, three visits, "
           "up down up --\n", NADDR);
    for (int v = 0; v < VISITS; v++) {
        // Visit 1 ascending, visit 2 descending, visit 3 ascending. A2 compares
        // visit 0 against visit 1, which are the two opposite orders.
        for (int k = 0; k < NADDR; k++) {
            int a = (v == 1) ? (NADDR - 1 - k) : k;
            fire_read(ADDR[a]);
            dump_space(dump[v][a]);
        }
        printf("  visit %d done\n", v);
    }

    printf("\n  %-6s %-38s %-8s %-8s %s\n",
           "reg", "per-address means, all 12", "spread", "scatter", "retrace");
    int found = -1, nfound = 0;
    for (int r = 0; r < NCAND; r++) {
        uint8_t mean[NADDR];
        int worst_within = 0, retrace = 0;
        for (int a = 0; a < NADDR; a++) {
            uint8_t three[VISITS];
            int sum = 0;
            for (int v = 0; v < VISITS; v++) {
                three[v] = dump[v][a][r];
                sum += three[v];
            }
            int w = range_u8(three, VISITS);
            if (w > worst_within) worst_within = w;
            int g = dump[0][a][r] - dump[1][a][r];
            if (g < 0) g = -g;
            if (g > retrace) retrace = g;
            mean[a] = (uint8_t)(sum / VISITS);
        }
        int spread = range_u8(mean, NADDR);
        bool a1 = spread > worst_within && !probe_writes(r);
        bool a2 = retrace == 0;
        // A register that is the same byte at every address and on every visit
        // is neither a candidate nor a hazard, and there are two hundred of
        // them. Printing them buried the one row that mattered on the first
        // run. Anything that MOVED gets a line, whichever rule it fails.
        if (spread == 0 && worst_within == 0)
            continue;
        char vals[NADDR * 3 + 1];
        int n = 0;
        for (int a = 0; a < NADDR; a++)
            n += snprintf(vals + n, sizeof vals - (size_t)n, "%02x ", mean[a]);
        char name[8];
        snprintf(name, sizeof name, "%02x%s", r, probe_writes(r) ? "^" : "");
        printf("  %-6s %-38s %-8d %-8d %s%s\n",
               name, vals, spread, worst_within, retrace ? "NO" : "yes",
               (a1 && a2) ? "   <- A1 and A2"
                          : probe_writes(r) ? "   (the probe writes this one)"
                                            : "");
        if (a1 && a2) { found = r; nfound++; }
    }

    // Stated once, and checked rather than assumed. If the top half ever stops
    // mirroring, NCAND is hiding a register and this line is how a reader finds
    // out instead of never seeing it.
    int mirror_breaks = 0;
    for (int r = 0; r < NCAND; r++)
        for (int a = 0; a < NADDR; a++)
            for (int v = 0; v < VISITS; v++)
                if (dump[v][a][r] != dump[v][a][r + NCAND])
                    mirror_breaks++;
    printf("\n  the space is 7 bits: 0x80-0xff mirrored 0x00-0x7f in %s of "
           "%d reads%s\n",
           mirror_breaks ? "NOT all" : "all",
           NCAND * NADDR * VISITS,
           mirror_breaks ? "   <- SOMETHING IS UP THERE, widen NCAND" : "");

    // -------------------------------------------------------- the FIFO test --
    // Distinguishes null case 3 from nulls 1 and 2. Runs either way, because a
    // bit that broke the FIFO while ALSO looking like it fired a read is a
    // thing this probe should not hide.
    printf("\n-- did firing it break the capture path? --\n");
    int after = luma();
    printf("  luma after %d fires: %d\n", VISITS * NADDR, after);
    bool capture_ok = after >= 0;
    if (!capture_ok) {
        printf("  capture FAILED. Resetting I2C (0x07 bit 1) and retrying.\n");
        cam_write_reg(CAM_REG_SENSOR_RESET, CAM_I2C_RESET);
        cam_wait_idle("i2c reset");
        sleep_ms(100);
        after = luma();
        printf("  luma after the reset: %d\n", after);
        capture_ok = after >= 0;
    }

    if (nfound != 1) {
        printf("\nRESULT : stage A names %s.\n", nfound ? "MORE THAN ONE "
               "register, which is not a result - see the table" : "NOTHING");
        if (!nfound) {
            printf("         Three things it can be and this run separates "
                   "one of them:\n");
            printf("           1. the answer lands outside 0x00-0x7f\n");
            printf("           2. the read needs something the app note does "
                   "not mention\n");
            printf("           3. bit 0 is the driver's FIFO_CLEAR_ID_MASK "
                   "and no read was fired\n");
            printf("         The capture path %s after %d fires, which %s.\n",
                   capture_ok ? "still works" : "is BROKEN",
                   VISITS * NADDR,
                   capture_ok
                     ? "does not settle it - a FIFO id clear is harmless here "
                       "because\n         every capture in this probe clears "
                       "the FIFO on its own anyway"
                     : "is case 3 and is the one that matters: this bit is NOT "
                       "safe to\n         fire in the shipping path");
        }
        printf("\n         THE AWB ARM STAYS BLOCKED. Do not write an arm "
               "against a\n         readback this probe did not find.\n");
        while (true) tight_loop_contents();
    }

    printf("\n  stage A names register 0x%02x.\n", found);

    // ------------------------------------------------------------- stage B --
    printf("\n-- stage B: does anything read through it track an exposure "
           "we control? --\n");

    // WARM UP WITH THE LOOPS RUNNING FIRST, which cam_manexp.c does and the
    // first version of this file did not. Thirty-six writes to 0x07 have just
    // gone past and 0x07 carries reset bits at 6 and 7; whatever that left the
    // auto-control state in, twenty captures with the loops on puts it back
    // somewhere known before the mask is applied.
    cam_image_auto_mask(CAM_AUTO_ALL);
    sleep_ms(200);
    for (int i = 0; i < 20; i++) (void)luma();
    cam_image_auto_mask(CAM_AUTO_WB);   // exposure and gain manual, AWB running
    sleep_ms(200);

    int lo_luma, hi_luma;

    // DOES STAGE B HAVE A HANDLE AT ALL? Checked before it is used, and by the
    // same shape as A1 rather than by a constant: the two exposures must part
    // the picture by more than repeated captures at ONE exposure wobble. The
    // first run of this file skipped the check, got luma 97 against 100 - a
    // three-count separation where cam_manexp.c measured 11 against 233 - and
    // then blamed stage A for finding nothing. A stage that cannot move the
    // thing it is testing with does not get to return a verdict.
    int lo_rep[3], hi_rep[3];
    write_exposure(EXP_LOW);
    for (int i = 0; i < 4; i++) (void)luma();
    for (int i = 0; i < 3; i++) lo_rep[i] = luma();
    write_exposure(EXP_HIGH);
    for (int i = 0; i < 4; i++) (void)luma();
    for (int i = 0; i < 3; i++) hi_rep[i] = luma();
    int wob_lo = range_int(lo_rep, 3), wob_hi = range_int(hi_rep, 3);
    int worst_wobble = wob_lo > wob_hi ? wob_lo : wob_hi;
    int part = lo_rep[0] - hi_rep[0];
    if (part < 0) part = -part;
    bool has_handle = part > worst_wobble;
    printf("  handle check: luma %d %d %d at 0x%05x, %d %d %d at 0x%05x - "
           "parting %d against a wobble of %d, %s\n",
           lo_rep[0], lo_rep[1], lo_rep[2], EXP_LOW,
           hi_rep[0], hi_rep[1], hi_rep[2], EXP_HIGH,
           part, worst_wobble,
           has_handle ? "so stage B has one" : "SO STAGE B HAS NONE");

    if (!has_handle) {
        printf("\nRESULT : stage A names 0x%02x, and STAGE B COULD NOT TEST IT."
               " The two\n         exposures did not part the picture, so the "
               "value stage B was\n         going to look for was never put on "
               "the die. THIS IS NOT A VERDICT\n         ON 0x%02x EITHER WAY - "
               "do not read it as one, and do not adopt\n         0x%02x on "
               "stage A alone.\n", found, found, found);
        while (true) tight_loop_contents();
    }

    write_exposure(EXP_LOW);
    for (int i = 0; i < 4; i++) (void)luma();
    lo_luma = luma();
    for (int v = 0; v < VISITS; v++)
        for (int a = 0; a < NSWEEP; a++) {
            fire_read((uint16_t)(SWEEP_BASE + a));
            lo_v[a][v] = cam_read_reg((uint8_t)found);
        }

    write_exposure(EXP_HIGH);
    for (int i = 0; i < 4; i++) (void)luma();
    hi_luma = luma();
    for (int v = 0; v < VISITS; v++)
        for (int a = 0; a < NSWEEP; a++) {
            fire_read((uint16_t)(SWEEP_BASE + a));
            hi_v[a][v] = cam_read_reg((uint8_t)found);
        }

    printf("  exposure 0x%05x -> luma %d,  0x%05x -> luma %d%s\n",
           EXP_LOW, lo_luma, EXP_HIGH, hi_luma,
           (hi_luma > lo_luma) ? "" :
           "   <- THE PICTURE DID NOT BRIGHTEN, so stage B has no handle");

    printf("\n  %d addresses swept from 0x%04x. Only the ones that moved:\n",
           NSWEEP, SWEEP_BASE);
    printf("\n  %-8s %-14s %-14s %s\n", "sensor", "at low exp", "at high exp",
           "verdict");
    int tracks = 0, unstable = 0;
    for (int a = 0; a < NSWEEP; a++) {
        bool stable = range_u8(lo_v[a], VISITS) == 0 &&
                      range_u8(hi_v[a], VISITS) == 0;
        bool differs = lo_v[a][0] != hi_v[a][0];
        if (!stable) unstable++;
        if (stable && !differs)
            continue;   // five hundred unchanged rows is not a report
        printf("  0x%04x   %02x %02x %02x      %02x %02x %02x      %s\n",
               (unsigned)(SWEEP_BASE + a),
               lo_v[a][0], lo_v[a][1], lo_v[a][2],
               hi_v[a][0], hi_v[a][1], hi_v[a][2],
               (stable && differs) ? "TRACKS THE EXPOSURE" : "unstable");
        if (stable && differs) tracks++;
    }
    printf("\n  %d tracked, %d unstable, %d unchanged and not listed.\n",
           tracks, unstable, NSWEEP - tracks - unstable);

    printf("\nRESULT : ");
    if (tracks && hi_luma > lo_luma) {
        printf("0x%02x IS THE DATA REGISTER and %d sensor address%s track%s an "
               "exposure\n         this board writes. The passthrough works, "
               "#33 has its readback,\n         and the AWB arm is unblocked "
               "once the die's white-balance gain\n         addresses are found "
               "the same way.\n", found, tracks,
               tracks == 1 ? "" : "es", tracks == 1 ? "es" : "");
    } else {
        printf("stage A named 0x%02x and stage B found NOTHING that tracks a "
               "value this\n         board controls. That is an artefact, not "
               "a readback. Do not adopt\n         0x%02x, and THE AWB ARM "
               "STAYS BLOCKED.\n", found, found);
    }

    while (true) tight_loop_contents();
}
