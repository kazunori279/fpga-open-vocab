// M8b: the Arducam Mega, as a driver rather than as a probe.
//
// Everything here was established by cam_probe.c and is written up in
// docs/milestones.md#m8a--bring-up. This file exists so there is exactly one
// copy of it: the register map, the bus, and - the part that matters - the
// capture sequence, whose one non-obvious line is load-bearing and would be
// quietly dropped by anyone re-deriving it from the datasheet.
//
// THE ONE NON-OBVIOUS LINE. Writing CAM_REG_CAPTURE_RESOLUTION with the value it
// already holds blanks the *next* capture: CAP_DONE asserts, the FIFO length is
// exactly right, CAM_REG_SENSOR_STATE says IDLE throughout, and the frame is a
// constant fill. So cam_capture() skips the write when the mode is unchanged,
// which is what ArduCAM's own cameraTakePicture() does (ArducamCamera.c:431-447)
// and which reads like tidiness. It is not.
//
// EVERY WAIT IN HERE IS BOUNDED, and one of them was not until issue #8. The
// PIO transfer loop had no exit but the bytes arriving, so a byte that never
// came back spun the core until the 8 s watchdog rebooted the board - twice, at
// 280/140, and only ever there. It now gives up after 2 ms of no progress,
// prints where and what the state machine was doing, resyncs the shift register
// and poisons the rest of the capture so the bounded loops above it do not
// rediscover the same fault twenty thousand times. The cost of a dropped byte
// is one frame. What causes the dropped byte is still open.
//
// Pins - SCK GPIO8, MISO GPIO9, MOSI GPIO12, CS GPIO13 - are on the RP header
// and disjoint from the link's (GPIO1/2/3/6, or 22 in configuration C), so the
// camera and the FPGA coexist with no arbitration. The PIO *instance* is the
// caller's to pick for the same reason: m7.c puts the link on pio0 and the
// camera on pio1 rather than sharing instruction memory between them.

#ifndef CAM_H
#define CAM_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#include "hardware/pio.h"

#define CAM_PIN_SCK  8
#define CAM_PIN_MISO 9
#define CAM_PIN_MOSI 12
#define CAM_PIN_CS   13

// --- ArduChip registers ----------------------------------------------------
// Transcribed from ArduCAM's driver - github.com/ArduCAM/Arducam_Mega,
// src/Arducam/ArducamCamera.c - and not from the application note, which
// disagrees with it.
#define ARDUCHIP_FIFO       0x04
#define ARDUCHIP_FIFO_2     0x07
#define FIFO_CLEAR_ID_MASK  0x01
#define FIFO_START_MASK     0x02
#define FIFO_CLEAR_MASK     0x80
#define ARDUCHIP_TRIG       0x44
#define CAP_DONE_MASK       0x04
#define FIFO_SIZE1          0x45
#define FIFO_SIZE2          0x46
#define FIFO_SIZE3          0x47
#define BURST_FIFO_READ     0x3C

#define CAM_REG_SENSOR_RESET         0x07
#define CAM_REG_DEBUG_DEVICE_ADDRESS 0x0A
#define CAM_REG_FORMAT               0x20
#define CAM_REG_CAPTURE_RESOLUTION   0x21
#define CAM_REG_BRIGHTNESS_CONTROL   0x22
#define CAM_REG_EV_CONTROL           0x25
#define CAM_REG_WB_MODE_CONTROL      0x26
// One register, three switches, selected by the low bits: 0 gain, 1 exposure,
// 2 white balance. Bit 7 is "auto on". So 0x81 is auto-exposure on, 0x02 is
// auto-white-balance *off*, and there is no way to read back which of the three
// you last touched - hence cam_probe.c's sweep rather than a query.
#define CAM_REG_AUTO_CONTROL         0x30
#define AUTO_SEL_GAIN                0x00
#define AUTO_SEL_EXPOSURE            0x01
#define AUTO_SEL_WHITEBALANCE        0x02
#define AUTO_ON                      0x80
#define CAM_REG_SENSOR_ID            0x40
#define CAM_REG_YEAR_ID              0x41
#define CAM_REG_MONTH_ID             0x42
#define CAM_REG_DAY_ID               0x43
#define CAM_REG_SENSOR_STATE         0x44
#define CAM_REG_FPGA_VERSION_NUMBER  0x49

#define CAM_REG_SENSOR_STATE_IDLE (1 << 1)
#define CAM_SENSOR_RESET_ENABLE   (1 << 6)
#define CAM_SET_CAPTURE_MODE      (0 << 7)

#define CAM_IMAGE_PIX_FMT_RGB565 0x02

// Resolution codes depend on which die is in the module. ArduCAM's legacyMode()
// remaps the whole table when the sensor ID reads below 0x85, and a 3MP module
// can report 0x82, 0x84 *or* 0x86 - two of which are legacy and one of which is
// not. Picking either constant at compile time would work on some modules and
// quietly capture the wrong size on others, so cam_mode_128() takes the id.
#define MODE_128X128_NEW 0x01
#define MODE_128X128_OLD 0x0b
#define MODE_QVGA_NEW    0x03   // 320x240
#define MODE_QVGA_OLD    0x01
#define SENSOR_5MP       0x85   // ids below this take the legacy table

// Which of the two FIFO bytes carries RRRRRGGG. The ArduChip's datasheet does
// not say; this is the answer from looking at the rendered PNG, and the only
// fact in this file that was settled by eye rather than by arithmetic.
#define CAM_HI_FIRST true

// A plausible Mega sensor id. Anything outside it - and 0x00 and 0xff in
// particular - is a bus that is floating, shorted or unpowered.
static inline bool cam_id_plausible(uint8_t id) { return id >= 0x81 && id <= 0x87; }

static inline uint8_t cam_mode_128(uint8_t id)
{
    return id < SENSOR_5MP ? MODE_128X128_OLD : MODE_128X128_NEW;
}

static inline uint8_t cam_mode_qvga(uint8_t id)
{
    return id < SENSOR_5MP ? MODE_QVGA_OLD : MODE_QVGA_NEW;
}

// --- the bus ---------------------------------------------------------------

// Claims CS and loads cam_spi.pio into `pio`. Call once, before anything else.
void cam_bus_init(PIO pio);

// Switch the bus. `hz` is nominal-requested; cam_bus_mhz() reports what the
// 8.8 fixed-point divider actually produced. The bit-bang is a ~500 kHz control,
// not a fallback: its job is to be obviously correct, so that when it disagrees
// with the PIO the PIO is the suspect.
//
// 8 MHZ FOR REGISTER WRITES, WHATEVER YOU USE FOR PIXELS. ArduCAM documents 8 as
// the ceiling; a 32,768-byte burst read at 16 renders a clean picture and a
// register *read* at 16 agrees with the bit-bang, so 16 looks safe and mostly
// is. It is not safe for writes. m7.c configured the camera at 16 and got a
// capture with the right FIFO length - the resolution write landed - and a
// frame that was solid black. Nothing else reports the failure: the sensor
// stays IDLE, CAP_DONE asserts on time, and only looking at the pixels catches
// it. So m7.c writes at 8, then switches to 16 for the capture that keeps the
// frame, which writes no registers because of the guard above.
void  cam_bus_pio(uint32_t hz);
void  cam_bus_bitbang(void);
float cam_bus_mhz(void);

void    cam_write_reg(uint8_t addr, uint8_t val);
uint8_t cam_read_reg(uint8_t addr);

// Arm a one-shot stall on the next transfer, in the same spirit as m9's 'W' and
// 'E': the deadline above guards a failure that appears twice in five runs and
// never on demand, and a recovery path that cannot be provoked is a recovery
// path nobody has watched work. This stops the state machine mid-transfer, which
// is what a dropped byte looks like from the loop's side - it fires once and the
// resync puts the bus back, so the run continues rather than ending.
void cam_bus_fault_inject(void);

// The sensor runs its own I2C to the die behind the ArduChip, and every
// configuration write is asynchronous to us. Bounded rather than a bare while:
// an unpopulated or miswired bus reads 0x00 or 0xff forever.
bool cam_wait_idle(const char *what);

// --- capture ---------------------------------------------------------------

// The three switches cam_probe.c's matrix varies. Production uses
// CAM_RECIPE_VENDOR and nothing else; the struct survives because the matrix
// re-proves the fault above every boot, not because the question is open.
typedef struct {
    const char *name;
    bool        rewrite;     // write FORMAT/RESOLUTION even when unchanged
    bool        flush;       // flushFifo() before clearFifoFlag()
    uint32_t    settle_ms;   // extra delay after the register writes
} cam_recipe_t;

extern const cam_recipe_t CAM_RECIPE_VENDOR;

typedef struct {
    uint32_t setup_us;    // the register writes, 0 when the mode is unchanged
    uint32_t expose_us;   // trigger to CAP_DONE. See below: not a cost when the
                          // trigger was issued before the caller went away
    uint32_t wait_us;     // time actually spent blocked in the CAP_DONE poll
    uint32_t read_us;     // the burst
} cam_time_t;

// EXPOSE_US IS AN ELAPSED TIME AND WAIT_US IS A COST, and they are the same
// number only when nothing happens in between. cam_capture() polls CAP_DONE
// immediately after triggering, so there they are equal - which is why the
// accounting that used expose_us was right for as long as the capture was
// serial. Split the two calls and expose_us spans whatever the caller did
// meanwhile, so anything summing a frame's cost wants wait_us.

// ArducamCamera.c:316 cameraBegin(), minus the model dispatch. The reset is what
// makes a run repeatable across soft resets of the RP: the camera keeps its
// configuration through our reboot, so without it the second run of a binary
// would be measuring a differently configured camera than the first.
//
// `verbose` prints the module's firmware date and fpga revision.
void cam_begin(uint8_t id, bool verbose);

// THE WHITE-BALANCE MODE REGISTER IS THE ONE THAT MATTERS, and it is not the one
// with "white balance" in the obvious place. cam_begin() leaves the camera with
// blue crushed - mean RGB (91, 82, 53) - and turning auto white balance *on*
// through CAM_REG_AUTO_CONTROL barely moves it: blue goes to 42 of 109, i.e.
// relatively worse. What fixes it is writing CAM_REG_WB_MODE_CONTROL at all.
// Writing it with 0, the value its own enum documents as the default, takes blue
// from 42 to 133 and holds it there. Either the power-on content is not 0, or
// the write is what kicks the AWB loop; either way it is not the no-op it reads
// as. Result: mean RGB (115, 107, 105) against (91, 82, 53) at the start.
void cam_image_defaults(void);

// One capture into `dst`. Returns the FIFO length in bytes, or 0 on failure;
// a length longer than `cap` is returned but not read, so the caller can report
// the mismatch. `mode` is the already-legacy-resolved resolution code.
uint32_t cam_capture(const cam_recipe_t *r, uint8_t mode, uint8_t fmt,
                     uint8_t *dst, uint32_t cap, cam_time_t *t);

// --- the same capture, as two calls -----------------------------------------
//
// THE ARDUCHIP HAS ITS OWN FRAME FIFO, AND THE SERIAL VERSION ABOVE WASTES IT.
// cam_capture() triggers, blocks until CAP_DONE, then reads - so the sensor's
// exposure and its frame boundary are dead time for whatever the caller was
// going to do with the pixels. Trigger early instead and that whole wait
// happens underneath the caller's own work; the pixels sit in the ArduChip
// until someone comes for them, which is what the FIFO is for.
//
// This costs no memory here and none in the caller. The frame lives on the
// camera, not on the RP, until cam_collect() moves it - which matters on a part
// where frame.c has to explain every buffer it owns.
//
// cam_capture() is these two back to back and keeps its exact old behaviour,
// so cam_probe.c and ft_acquire()'s exposure ramp are untouched.
//
// ONE CAPTURE IS STILL ONE UNIT OF RECOVERY (#8), it just spans two calls now.
// cam_trigger() clears the sticky bus fault and cam_collect() does not, so a
// stall anywhere in a capture poisons the rest of *that* capture and the next
// trigger starts clean. A failed collect abandons the frame in the FIFO; the
// next trigger's FIFO_CLEAR_ID discards it.
bool cam_trigger(const cam_recipe_t *r, uint8_t mode, uint8_t fmt,
                 cam_time_t *t);

// Reads the frame a cam_trigger() started. Returns what cam_capture() returns.
// Fills `t`'s wait_us, expose_us and read_us; leaves setup_us to the trigger.
uint32_t cam_collect(uint8_t *dst, uint32_t cap, cam_time_t *t);

// When the last cam_trigger() fired, on time_us_64()'s clock. Survives the
// cam_collect() that consumes the capture, so the answer to "how old is the
// frame I am holding" is a subtraction rather than a second set of counters -
// and once the trigger moves around inside the caller's compute (frame.c's
// ft_pipeline), that age stops being derivable from anything else.
uint64_t cam_last_trig_us(void);

// --- reading a frame without looking at it ---------------------------------

// Is the frame a single repeated 16-bit value? That is the blanking fault's
// signature, and testing for it *inside one frame* is what lets a boot judge
// several captures without a human comparing PNGs. A real photograph of a blank
// white wall would trip it too - and that is a scene a bench can be told not to
// arrange.
bool cam_frame_is_constant(const uint8_t *p, uint32_t len);

// Per-channel mean, 0-255, from RGB565 bytes high-byte-first. Two numbers' worth
// of information in three: the average of the three is exposure, the spread
// between them is white balance. Both are settings this camera has, and judging
// either from a 128x128 PNG on a laptop screen is the kind of eyeballing that
// produces "it looks a bit warm to me" and no decision.
void cam_frame_means(const uint8_t *p, uint32_t len, int m[3]);

#endif // CAM_H
