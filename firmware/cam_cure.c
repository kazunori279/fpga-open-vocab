// Is there anything on the board that puts the exposure lock back? (#33, #32)
//
// bench/probe/20260907-lockrate/ settled the rate: 32 boots of 33 lose the
// exposure lock, the die keeps overwriting what the mask was supposed to stop
// it writing, and the passthrough is a read path so nothing here can reach the
// die's own AE enable. That reads like a dead end, and it is one - for the
// passthrough. It says nothing about `0x02`, which is on the ArduChip, is
// writable, and can put the sensor die through reset, sleep or a power cycle on
// its own. Nobody has tried it. It is the last untried cure.
//
//   bit [2] cam_power_en   1 normal, 0 power off
//   bit [1] cam_pwdn       1 sleep,  0 normal
//   bit [0] cam_rst_n      1 normal, 0 reset
//
// FOUR ARMS, INTERLEAVED, INSIDE ONE BOOT. This is the part that needs care,
// because the fault moves on its own: nine of those 33 boots gave two different
// answers to the same question inside ten seconds, and 84 of 99 checks dragged.
// Against a background like that a before-and-after pair proves nothing - an arm
// that changes nothing will still "cure" a boot about one time in six, and an
// arm that cures perfectly will still look like it failed if the drag comes
// back before the next check.
//
//   N  null. Everything the other arms do EXCEPT the write to 0x02.
//   R  reset:        clear bit 0, wait, restore
//   P  sleep:        set bit 1, wait, restore
//   C  power cycle:  clear bits 2 and 0, wait, restore
//
// N is not "do nothing". Every arm re-runs cam_begin() and cam_image_defaults()
// afterwards, because a sensor that has been reset or power-cycled has lost its
// register state and would otherwise be answering a different question from the
// one N answers. So N is a re-bring-up with the 0x02 write left out, which makes
// the only difference between N and the other three the thing being tested.
// (Re-running cam_begin() on its own is already known not to help: 0 of 7 in
// bench/probe/20260907-awb/. N is here to reproduce that inside this probe
// rather than to inherit it.)
//
// The twelve slots visit each arm three times in spread-out positions, so a
// drift through the boot - the scene, the die warming up, anything monotone -
// cannot separate the arms.
//
// THE RULE, WRITTEN DOWN BEFORE THE FIRST BOOT. An arm is a cure if it comes
// back `held` more often than N does, and it has to do it across boots, not on
// one. Nothing here is compared against a fixed number: N is measured on the
// same board, in the same scene, in the same minute, and it is the only thing
// the other three are compared against. If all four arms land together, the
// answer is that 0x02 does not touch this, and #33 has no cure on this board.
//
// WHAT WOULD MAKE A RUN VOID. `0x02` can plainly break the camera - bit 6 of
// 0x07 broke capture on every boot it was tried in the AWB probe - so every slot
// checks that a picture still comes back, and a slot whose capture died is
// reported as `dead` and counted as neither.

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

// Not in cam.h, and deliberately: that header's register map is transcribed
// from ArduCAM's driver, which never touches this one, and that is the
// provenance line it keeps. Probes declare what they reach for themselves.
#define CAM_REG_SENSOR_POWER  0x02
#define SENSOR_POWER_EN       (1u << 2)
#define SENSOR_PWDN           (1u << 1)
#define SENSOR_RST_N          (1u << 0)

#define NSLOT 12
static const char ARM[NSLOT] = {
    'N', 'R', 'P', 'C',
    'C', 'P', 'R', 'N',
    'R', 'N', 'C', 'P',
};

static uint8_t raw[128 * 128 * 2];
static uint8_t m128;
static uint8_t cam_id;
static uint8_t power_at_boot;

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

// READ-MODIFY-WRITE, and restore to what this board powered on with rather than
// to the datasheet's 0x05. Bits 3-7 of this register are not documented and this
// probe has no business deciding what they should be.
static void power_write(uint8_t bits_low, uint8_t bits_high)
{
    uint8_t v = (uint8_t)((power_at_boot & ~bits_low) | bits_high);
    cam_write_reg(CAM_REG_SENSOR_POWER, v);
    cam_wait_idle("sensor power");
}

static void power_restore(void)
{
    cam_write_reg(CAM_REG_SENSOR_POWER, power_at_boot);
    cam_wait_idle("sensor power restore");
}

// The same 50 ms down and 100 ms up for every arm, N included, so the arms
// differ by the register write and not by how long the slot took.
#define DOWN_MS 50
#define UP_MS  100

static void apply_arm(char a)
{
    switch (a) {
    case 'R': power_write(SENSOR_RST_N, 0u);                    break;
    case 'P': power_write(0u, SENSOR_PWDN);                     break;
    case 'C': power_write(SENSOR_POWER_EN | SENSOR_RST_N, 0u);  break;
    default:  break;   // 'N': the wait below and nothing else
    }
    sleep_ms(DOWN_MS);
    if (a != 'N')
        power_restore();
    sleep_ms(UP_MS);
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

    printf("\n=== #33: does 0x02 put the exposure lock back? ===\n\n");
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

    power_at_boot = cam_read_reg(CAM_REG_SENSOR_POWER);
    cam_wait_idle("sensor power read");
    printf("  0x02 at boot : 0x%02x%s\n", power_at_boot,
           power_at_boot == 0x05 ? "  (the documented default)"
                                 : "  <- NOT the documented 0x05");

    for (int i = 0; i < 20; i++) (void)luma();

    // DOES 0x02 DO ANYTHING AT ALL. Asked first, because if it does not then
    // the twelve slots below are four copies of the same null arm and their
    // agreement means nothing. Hold each line down - do not restore it - and
    // take a picture. A sensor held in reset, asleep or unpowered must not
    // return a normal frame; if it does, the write is not reaching the die and
    // this probe has no arms.
    printf("\n  0x02 positive control - each line held down, not pulsed:\n");
    static const struct { const char *name; uint8_t low, high; } CTRL[3] = {
        { "reset held",    SENSOR_RST_N,                 0u          },
        { "sleep held",    0u,                           SENSOR_PWDN },
        { "power off held", SENSOR_POWER_EN | SENSOR_RST_N, 0u        },
    };
    int ctrl_answered = 0;
    for (int i = 0; i < 3; i++) {
        power_write(CTRL[i].low, CTRL[i].high);
        uint8_t rb = cam_read_reg(CAM_REG_SENSOR_POWER);
        cam_wait_idle("sensor power readback");
        sleep_ms(100);
        int v = luma();
        bool constant = (v >= 0) && cam_frame_is_constant(raw, sizeof raw);
        printf("    %-15s 0x02 reads %02x, picture %s\n", CTRL[i].name, rb,
               v < 0 ? "did not come back" : constant ? "one flat colour"
                                                      : "came back normally");
        if (v < 0 || constant) ctrl_answered++;

        power_restore();
        cam_begin(cam_id, false);
        cam_image_defaults();
        for (int k = 0; k < 12; k++) (void)luma();
    }
    printf("    %d of 3 changed the picture%s\n", ctrl_answered,
           ctrl_answered ? "" : "   <- 0x02 IS NOT REACHING THE DIE. "
                                "The arms below are all the same arm.");

    // The state the arms have to improve on, measured before any of them runs.
    cam_lock_state_t before = cam_exposure_lock_check();
    cam_image_auto(true);
    for (int i = 0; i < 8; i++) (void)luma();
    printf("  before any arm: %s\n\n", cam_lock_state_name(before));

    printf("  slot arm  0x02  lock       luma\n");

    int held[128] = { 0 }, drag[128] = { 0 }, dead[128] = { 0 };

    for (int s = 0; s < NSLOT; s++) {
        const char a = ARM[s];

        apply_arm(a);

        // A reset or a power cycle has thrown the sensor's registers away, so
        // the bring-up has to be walked again. N walks it too; that is the point
        // of N.
        cam_begin(cam_id, false);
        cam_image_defaults();

        int v = -1;
        for (int i = 0; i < 8; i++) v = luma();

        if (v < 0) {
            dead[(int)a]++;
            printf("  %4d  %c   %02x    dead        -\n", s, a,
                   cam_read_reg(CAM_REG_SENSOR_POWER));
            // A dead capture path is not left for the next slot to inherit.
            power_restore();
            cam_begin(cam_id, false);
            cam_image_defaults();
            for (int i = 0; i < 8; i++) (void)luma();
            continue;
        }

        cam_lock_state_t st = cam_exposure_lock_check();
        if (st == CAM_LOCK_HELD)         held[(int)a]++;
        else if (st == CAM_LOCK_DRAGGED) drag[(int)a]++;

        printf("  %4d  %c   %02x    %-10s %3d\n", s, a,
               cam_read_reg(CAM_REG_SENSOR_POWER), cam_lock_state_name(st), v);

        cam_image_auto(true);
        for (int i = 0; i < 8; i++) (void)luma();
    }

    power_restore();
    cam_begin(cam_id, false);
    cam_image_defaults();
    for (int i = 0; i < 20; i++) (void)luma();

    printf("\n  arm  held drag dead\n");
    const char *names = "NRPC";
    for (int i = 0; i < 4; i++) {
        const int a = names[i];
        printf("  %c    %4d %4d %4d\n", (char)a, held[a], drag[a], dead[a]);
    }

    printf("\nVERDICT : before=%s  N=%d/3  R=%d/3  P=%d/3  C=%d/3   (held)  "
           "control %d/3\n",
           cam_lock_state_name(before),
           held['N'], held['R'], held['P'], held['C'], ctrl_answered);

    while (true) tight_loop_contents();
}
