// One frame through the tile: the engine M7 measured and M8c loops.
//
// Everything here was m7.c until M8c, and it moved rather than being rewritten.
// The reason is not tidiness. ~600 lines of m7.c *are* the engine - run_block(),
// the weight cache, the requantize scatter, the core-1 callbacks and the
// ping-pong pool - and they are the trickiest concurrency code in this project:
// two cores, two priority queues, three double buffers, and one wait that makes
// all of it safe. A second copy in m8.c would be two copies of that, which is
// the same argument M8b's cam.h split was written for, one milestone later.
//
// So m7.c keeps what it is - a harness that runs the same frame six ways and
// reports the ladder - and m8.c is a thin loop over the same calls. The prefix
// is ft_, for "one frame through the tile".
//
// THE EXTRACTION IS CHECKED RATHER THAN TRUSTED. m7 is a self-checking binary:
// it computes every layer and the 512-d embedding twice in one boot, once on the
// MCU with encoder_fast.c and once with every convolution on the T8, and
// compares CRCs layer by layer. A botched move therefore prints FAIL rather than
// hiding, and the ladder's six timings say whether anything moved that should
// not have. That is why this refactor could be done at all.
//
// ---------------------------------------------------------------------------
// What is *not* here
//
// Reporting. Not one printf in the frame path, because the two callers want
// different reports - m7 prints a per-layer table with wire and stall columns,
// m8 prints one line a frame - and a library that prints is a library only one
// of them can use. The counters are all readable; the sentences are the
// caller's.
//
// And park(). A library must not exit: where m7.c called park() on a bad plan or
// a failed accumulator sweep, ft_layer() returns the reason and lets the caller
// decide. That is the one behavioural change in the move, and it is the change
// that makes a *loop* possible - m8 recovers from a link error and keeps going.

#ifndef FRAME_H
#define FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cam_pixel.h"   // cam_rot_t, for FT_MOUNT_ROT below
#include "encoder.h"
#include "gemm_host.h"
#include "gemm_plan.h"

// The camera's frame, and the network's input, which are the same 128x128 only
// because the student was trained at the resolution the Mega can capture
// natively. FT_ rather than CAM_ because cam.h's include guard is CAM_H.
#define FT_FRAME_W 128u
#define FT_FRAME_H 128u

// How the module sits on the bench, as a quarter-turn applied on the way into
// the tensor. Set from a rendered snapshot, not from the datasheet, and the
// value has already changed once: M9's first pictures came out a quarter turn
// clockwise because the module was being held a quarter turn to the left, so
// this read CAM_ROT_90 until the camera was straightened on 2026-08-07.
//
// It is a named constant rather than a soldering note because the ribbon
// decides the orientation and the ribbon gets moved. Whichever way it ends up,
// **check it against a rendered frame** - host/cam.py --rot takes the same
// value, and a sideways tensor is invisible in every log the board prints.
#define FT_MOUNT_ROT CAM_ROT_0

// The pool, and the sizes the caller has to know about it: ft_recv_bitstream()
// fills the arena and stops FT_BITSTREAM_MAX short of the frame, and the
// reference pass in m7 hands ft_col() to fgx_conv_fast(). See frame.c for why
// these three tenants share one array.
#define FT_ARENA_MAX     (132u * 1024u)
#define FT_SCRATCH_MAX   (132u * 1024u)
#define FT_FRAME_BYTES   (3u * FT_FRAME_W * FT_FRAME_H)
#define FT_BITSTREAM_MAX (FT_ARENA_MAX + FT_SCRATCH_MAX)
#define FT_COL_MAX       ( 56u * 1024u)

// The most layers ft_init() will plan for. The model has 9; the slack is for a
// re-export, and ft_init() says so rather than overrunning.
#define FT_MAX_LAYERS 32

uint8_t *ft_arena(void);
uint8_t *ft_scratch(void);
int8_t  *ft_frame(void);
uint8_t *ft_col(void);

// ---------------------------------------------------------------------------
// Start-up

// Checks the model against the pool and picks a blocking for every convolution.
// Returns NULL, or why it could not - with the numbers in it, so the caller can
// print the message rather than reconstruct one.
const char *ft_init(const fgx_model_t *m);

// The chosen blocking, for the caller's table. Valid for i < ft_nconv().
const gp_layer_t *ft_plan(uint32_t i);
uint32_t ft_nlayer(void);      // convs + the linear head
uint32_t ft_nconv(void);       // everything the tile can run

// ---------------------------------------------------------------------------
// Configuration
//
// Which frame to run. Both flags off is M7c's serialized path, `pipe` alone is
// M7d, and each core-1 flag moves one more piece of work onto the other CPU.
// They are runtime state rather than build options for the reason M5b
// established: a ratio quoted across two builds of this firmware is not a
// measurement, and with concurrency it is worse than not a measurement, because
// the two builds differ in exactly the timing that decides whether a race is
// visible. m8 sets the top rung once and never touches this again.
void ft_set_mode(bool pipe, bool cbuild, bool cscat, bool cdec, bool cprio);

// The per-block accumulator sweep: every block's accumulators against
// gb_golden(), inside the block loop where they still exist. ~25 s a frame, so
// m7 runs it once as an untimed pass and m8 never turns it on.
void ft_set_sweep(bool on);

// M15. Let the tile apply the requantize epilogue and drain one byte per
// accumulator instead of four. A mode and not a build option, for M5b's reason
// and one more: **int32 DRAIN is the verification story.** With this off,
// ft_set_sweep()'s 174-of-174 accumulator check runs exactly as M14 shipped, and
// that is the standing guarantee that the MAC array was not disturbed. So the
// sweep pass overrides this to off - see ft_rq() - and rq mode is checked
// separately, against codes, by the layer CRCs.
//
// It selects a different *blocking*, chosen at ft_init(): quartering DRAIN moves
// which (P, QG, Cb) is cheapest. Two layers are unaffected either way - conv7
// emits floats and has no code to compute.
void ft_set_rq(bool on);

// What the next ft_layer() will actually do, the two vetoes above applied. For
// the caller's mode label, so a row cannot claim rq while running int32.
bool ft_rq(void);

// ---------------------------------------------------------------------------
// One frame

// Per-frame counters, the ping-pong index, the deferred-DRAIN descriptors and
// the driver's own profiling. Called once before the first ft_layer().
void ft_frame_reset(void);

// How a layer ended. `link` is the wire; `fault` is everything the old code
// called park() over - a plan that does not tile, a geometry gb_geom() rejects,
// an accumulator that disagrees with gb_golden() - as a printable sentence.
// Both clear means the layer is in `dst`.
typedef struct {
    gh_err_t    link;
    const char *fault;
} ft_err_t;

// Layer `i` of the model, from `src` into `dst`, every block on the tile. The
// caller owns the ping-pong: pass ft_arena() and ft_scratch() alternately, which
// is what makes the MCU reference pass and the tile pass write each layer to the
// same address and their CRCs comparable.
ft_err_t ft_layer(uint32_t i, const void *src, void *dst);

// The pooling and the linear head, on the MCU - there is no tile version, and at
// ~2 ms there is no reason to want one. Adds its own time to ft_frame_us().
void ft_pool_head(const float *src, float *embed);

// ---------------------------------------------------------------------------
// What it cost
//
// Read after the layer or the frame, never during: in the core-1 modes the last
// writer of several of these was the other CPU, and ft_layer()'s barrier is the
// first point core 0 knows that.
typedef struct {
    uint32_t us;         // the layer, barrier included, CRC excluded
    uint32_t passes;
    uint32_t us_build;   // gb_strip + gb_weights, wherever they ran
    uint32_t us_wire;    // the driver's elapsed wire time across this layer
    uint32_t us_stall;   // core 0 waiting on core 1
    int      blocks;
    uint8_t  status;     // the tile's sticky bits after the last block
} ft_stat_t;

const ft_stat_t *ft_stat(uint32_t i);

uint32_t ft_frame_us(void);       // the sum of the above, plus the head
uint8_t  ft_status(void);         // OR of every block's status this frame
uint32_t ft_us_build_frame(void);
// M17. The gb_weights() half of the above, so the two callbacks that share the
// W1_HI queue can be told apart. See frame.c for why this got its own counter.
uint32_t ft_us_build_wgt_frame(void);
uint32_t ft_us_scatter(void);     // core 0's share only; core 1's is w1_busy_us()
uint32_t ft_sweep_blocks(void);

// Passes built against passes served from the weight cache, and the same by
// bytes - which is the figure that matters, since the passes differ in size.
void ft_wgt_stats(uint32_t *built, uint32_t *cached,
                  uint64_t *bytes_all, uint64_t *bytes_hit);

// ---------------------------------------------------------------------------
// Getting a bitstream in and an image out. Neither is frame work; both live here
// because both write the pool, and the pool's whole safety property is the order
// of its three tenants.

uint32_t ft_crc32(const uint8_t *p, size_t n);

// n bytes off stdin with a one-second-per-byte timeout, complaining if it runs
// out. Public because M9 receives a second framed message on the same wire and a
// third copy of this loop would be a third thing to keep in step - the same
// argument the bitstream receive itself makes below.
bool ft_recv_exact(uint8_t *p, size_t n);

// "FGXB" | len u32 LE | crc32 u32 LE | len bytes, into ft_arena(). Returns the
// length, or 0. `hunt_s` bounds the wait for the magic in seconds; 0 waits
// forever.
size_t ft_recv_bitstream(int hunt_s);

// A live 128x128 frame as int8 CHW codes, or NULL meaning "use the flash test
// vector". Brings the camera up, ramps the exposure and keeps the first settled
// frame. Prints its own verdict either way. Costs the arena, which must
// therefore not be holding anything.
const void *ft_acquire(float in_scale);

// Every frame after that one: capture, length and blank checks, convert. NULL if
// the frame was not usable. Silent, and only valid after ft_acquire() returned
// non-NULL - the bring-up set the sensor mode this reuses. Also costs the arena.
const void *ft_capture(float in_scale);

// The last capture's mean RGB and its two halves of latency. Any argument may be
// NULL.
void ft_cap_stats(int mean[3], uint32_t *expose_us, uint32_t *read_us);

// How long the last ft_capture() actually stood still waiting for CAP_DONE, as
// against how long the exposure took. WHATEVER IS ADDING UP A FRAME'S COST
// WANTS THIS ONE, not expose_us - see cam.h, and see ft_pipeline() for why they
// stopped being the same number.
uint32_t ft_cap_wait_us(void);

// Overlap the next capture with the caller's own work. Off by default.
//
// WHAT THIS IS FOR. The appliance frame did not scale with the system clock -
// the inference did, exactly, but the frame landed on a grid about one sensor
// period wide, so 332 MHz measured the same 420 ms as 320 (issue #10). The
// suspect was ft_capture() standing still while the sensor finished a frame on
// its own boundary. With this on, ft_capture() triggers the *next* capture
// before it returns, so that boundary is reached underneath the caller's
// compute and the collect that follows finds CAP_DONE already asserted.
//
// IT WAS THE SENSOR. Back to back on one boot, one build and one scene, by m9's
// wall clock: 429 ms serial against 372 overlapped, the encode 346 ms in both
// and the burst 16 ms in both. What moved is a 56 ms wait that becomes 0.
//
// It costs no memory: the frame waits in the ArduChip's FIFO, not in the arena.
//
// IT COSTS LATENCY, AND MOST OF THAT COST WAS AVOIDABLE. The first version
// armed the moment it collected, which put the trigger a whole compute ahead of
// the frame that used it and doubled photon-to-LED for no gain: the sensor only
// needs its exposure and one frame boundary, and the rest of that window was
// the frame simply going stale. Issue #14 moved the trigger to the *end* of the
// compute instead - see frame.c - so the wait stays at 0 and the frame is as
// fresh as the lead is small. Measured the same way, on one boot and one scene
// with m9's 'D': 725 ms old arming at the collect, 494 ms arming late, 435 ms
// serial, and 373 ms/frame in both overlapped windows. What remains is a real
// trade and not a bug: 59 ms, which is the exposure.
//
// Turn it on after ft_acquire() and leave it on. ft_cap_wait_us() says whether
// the overlap is working, ft_cap_age_us() says what it costs, and a wait that
// stays high means the frame time was never the sensor's boundary and #10 needs
// a different answer.
void ft_pipeline(bool on);

// How old the frame in hand is: the time since the trigger that exposed it, read
// at the moment the caller asks. Photon-to-answer, near enough - the exposure is
// inside it - and the number issue #14 exists to move. Nothing measured it
// before, which is how the first overlap shipped having doubled it.
uint32_t ft_cap_age_us(void);

// The lead the arming schedule is currently running with: how much compute it
// tries to leave after the trigger. It moves on its own, so a report that quotes
// the age wants this beside it to say whether the loop has settled.
uint32_t ft_cap_lead_us(void);

// Put the trigger back where it was before #14 - immediately after the collect,
// a whole encode ahead of where it is needed. This exists to be measured against
// and for no other reason: "the frame got fresher" is a claim about a number,
// and the only honest before is one taken on the same boot and the same scene as
// the after. Off at reset; m9's 'D' is the only caller.
void ft_cap_eager(bool on);
bool ft_cap_is_eager(void);

// Arm a one-shot stall on the camera bus, so the next ft_capture() takes the
// failure path on purpose and returns NULL. See cam.h: the deadline this
// provokes guards issue #8, whose real trigger appears twice in five runs and
// never when asked. Behind ft_ rather than called directly because m9 reaches
// the camera through this header and nothing else.
void ft_cam_fault_inject(void);

#endif // FRAME_H
