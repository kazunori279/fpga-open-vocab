// One frame through the tile. See frame.h for why this is its own file.
//
// Moved out of m7.c at M8c, comments included, because the comments are the
// record of why each line is there and several of them are the only surviving
// account of a fault that took a day to find. The behavioural changes are two
// and both are consequences of being a library rather than a program:
//
//   1. Nothing here prints except ft_recv_bitstream() and ft_acquire(), which
//      are start-up and not frame work.
//   2. Nothing here calls park(). The four failures m7.c parked on - a plan that
//      does not tile, a geometry gb_geom() rejects, a mismatched accumulator,
//      a link error - come back as ft_err_t, and every one of those paths now
//      drains core 1 first. m7.c did not have to: park() never returned, so a
//      job still holding a pointer into this stack frame was somebody else's
//      problem forever. A caller that carries on makes it ours.

#include <stdio.h>
#include <string.h>

#include "hardware/watchdog.h"
#include "pico/stdlib.h"

#include "cam.h"
#include "cam_pixel.h"
#include "encoder.h"
#include "encoder_fast.h"
#include "frame.h"
#include "gemm_block.h"
#include "gemm_host.h"
#include "gemm_plan.h"
#include "gemm_wire.h"
#include "worker.h"

// ---------------------------------------------------------------------------
// Memory. arena and scratch_b are m6.c's two ping-pong buffers, and the arena
// is also where the bitstream lands: the FPGA reads its configuration over SPI
// as fpga_configure() feeds it and never touches host memory afterwards, so the
// buffer is free again the moment that call returns.
//
// M8B TURNED THEM INTO ONE POOL, because m6.c's sizing does not survive a third
// tenant. arena was 192 KB - sized for the 173,124-byte T8F49 bitstream, not for
// buffer A, which never needs more than m.scratch (131,072 B: conv0's output is
// 64x64x32). That is 60 KB of slack that exists only during the two downloads,
// and 48 KB of it is exactly what a 128x128x3 int8 frame costs. Carving both
// buffers plus the frame out of one array spends that slack instead of asking
// the linker for RAM the part does not have - the naive version overflowed by
// 38,296 bytes.
//
// The pool's order is what makes it safe: the frame sits ABOVE both buffers, and
// ft_recv_bitstream() caps the download at FT_BITSTREAM_MAX = everything below
// it. So the second bitstream - configuration C, arriving long after the camera
// ran - cannot reach the frame that all six of its modes are about to be scored
// on.
//
// col_buf stays its own array: it is the reference pass's alone, 55,296 B worst
// case (conv6: K = 1728 taps x FGX_TILE columns), asserted in ft_init() rather
// than assumed, and it is live at the same time as everything in the pool.
// ---------------------------------------------------------------------------
#define POOL_MAX (FT_BITSTREAM_MAX + FT_FRAME_BYTES)

static __attribute__((aligned(8))) uint8_t pool[POOL_MAX];

static uint8_t *const arena     = pool;
static uint8_t *const scratch_b = pool + FT_ARENA_MAX;
// The frame, and the only tenant of the pool that outlives a pass: it is read by
// the reference pass and again by all six FPGA modes in both link
// configurations, so it cannot share with the buffers that turn over per layer.
static int8_t  *const frame     = (int8_t *)(pool + FT_BITSTREAM_MAX);

// The ping-pong alternates, so both halves hold layer outputs and both need
// m.scratch bytes - but the model-header check in ft_init() tests it against
// FT_SCRATCH_MAX once. That is a shortcut only while the two are the same number.
_Static_assert(FT_ARENA_MAX == FT_SCRATCH_MAX, "one m.scratch check, two buffers");

static __attribute__((aligned(8))) uint8_t col_buf[FT_COL_MAX];

uint8_t *ft_arena(void)   { return arena; }
uint8_t *ft_scratch(void) { return scratch_b; }
int8_t  *ft_frame(void)   { return frame; }
uint8_t *ft_col(void)     { return col_buf; }

// Doubled for M7d: pass p+1 is built while pass p is still on the wire, so the
// two passes cannot share a buffer. 4 KB, against the ~80 KB the linker leaves.
static uint8_t strip[2][GB_STRIPD];
static int8_t  wstream[2][GB_WGTMAX];

// Doubled again for M7e: core 1 requantizes block b out of one half while core 0
// drains block b+1 into the other. 8 KB more, and it is the only new memory the
// second core costs - the build buffers M7d already doubled for the same reason,
// one milestone early. nacc = P*QG*8 <= 2048.
static int32_t got[2][GB_ADEPTH * GB_NMAC];
static int     gcur;          // the half the next block drains into

// M7f. Where a deferred DRAIN lands. The driver's own rxb[] cannot host it -
// it is two deep and alternates every transaction, and the two transactions
// after a DRAIN are a NOP and the next block's NOP, ~20 us against a ~966 us
// decode. So the capture is the caller's, and it is double-buffered on exactly
// the index and exactly the wait that already protect got[]: the decode and the
// scatter are both W1_LO and posted in that order, so the scatter of block b
// retiring implies the decode of block b retired first.
// 16 KB, and it moved 156 ms off core 0.
#define RXD_BYTES GH_DRAIN_CAP(GB_ADEPTH * GB_NMAC)
// aligned(4) for the same reason rxb[] is: the RX channel writes 32-bit words.
static __attribute__((aligned(4))) uint8_t rxd[2][RXD_BYTES];
static gh_defer_t dfr[2];

// The per-block accumulator sweep. A layer CRC localizes a mismatch to a layer;
// this localizes it to a block, and to the accumulator rather than to the
// epilogue. It costs 8 KB of bss and ~25 s a frame, because gb_golden() goes
// through fgx_conv_acc() one output at a time on purpose.
//
// **M7e promotes it from a build option to a pass of its own.** Under M7c and
// M7d it was a separate binary, which was fine when the only thing it could
// catch was a deterministic blocking error: reproduce, reflash, localize.
// Core 1 changes that. The bug class that milestone introduced is one where
// reflashing is itself a perturbation, and where the check that matters is the
// one that ran *in the same boot as the failure*. So the sweep runs as an
// untimed pass after the timed ones, in the topmost mode, and reaching it costs
// no extra strap.
//
// It cannot be folded into the timed modes: it sits inside the block loop and
// would multiply the frame figure by twenty, which is the whole reason M7c kept
// it out. m8 leaves it off and never sees the 8 KB again - the price of one
// engine instead of two, against ~25 KB of free SRAM.
static int32_t golden[GB_ADEPTH * GB_NMAC];
static bool     do_sweep;
static uint32_t sweep_blocks;

// The most blocks any one layer needs. Layer 0 is the worst at 64; 128 leaves
// room for a re-export with different channel counts, and gp_blocks() returns
// -1 rather than truncating if it is ever not enough.
#define MAXBLK 128
static gb_spec_t specs[MAXBLK];

static const fgx_model_t *mdl;
static uint32_t nlayer_, nconv_;

// M15. Two plans, not one, and both are chosen at start-up.
//
// DRAIN is the largest byte component of most blocks, so quartering it moves the
// optimum: the blocking that is cheapest at four bytes an accumulator is not the
// one that is cheapest at one. A single plan chosen at int32 and then run in rq
// mode would be a plan for the other wire. And ft_set_rq() is a *runtime* mode -
// m7 runs both in one boot, for the same reason ft_set_mode() is runtime - so
// the choice cannot be deferred to the caller either.
//
// Storing both costs ~2.5 KB of bss and makes ft_set_rq() a selector that cannot
// fail. Re-running gp_choose() on each mode change would cost nothing but would
// make a mode change able to return "layer 3 wants 192 blocks, MAXBLK is 128",
// which ft_init() is the right place to discover. Index with plan_ix().
static gp_layer_t plan[2][FT_MAX_LAYERS];
static ft_stat_t  st_[FT_MAX_LAYERS];
static uint8_t pool_codes[512];

// Where ft_init()'s and ft_layer()'s refusals are formatted. One buffer because
// only one of them is ever live: each is returned straight to a caller that
// prints it and stops using the engine.
static char fault_msg[160];

// M15's mode; see ft_set_rq(). Set here rather than beside the ft_set_mode()
// flags because plan_ix() has to be in scope for ft_plan() below.
static bool rq_on;

// Which of the two plans this pass is running, and the single place the two
// conditions that can veto rq are combined.
//
// The sweep is the second of them. It compares got[] against gb_golden(), which
// is int32 by contract - M15 deliberately left the accumulator path alone so
// that the 174-of-174 sweep keeps guarding the MAC array - so a sweep pass runs
// the int32 plan no matter what the mode says. The third veto, conv7's floats,
// is per layer and lives in ft_init().
static inline int plan_ix(void) { return (rq_on && !do_sweep) ? 1 : 0; }

const gp_layer_t *ft_plan(uint32_t i)  { return &plan[plan_ix()][i]; }
const ft_stat_t  *ft_stat(uint32_t i)  { return &st_[i]; }
uint32_t ft_nlayer(void) { return nlayer_; }
uint32_t ft_nconv(void)  { return nconv_; }

// ---------------------------------------------------------------------------
// Bitstream receive. Byte for byte m6.c's, including the framing:
//
//   "FGXB" | len u32 LE | crc32 u32 LE | len bytes
//
// m7.c carried its own copy and said why: 60 lines, and m6.c is a finished
// milestone that should keep working unchanged. M8c makes that argument the
// other way round for m7 and m8 - they are two live binaries that must agree
// about the wire with the *same* host script, and a divergence between them
// would show up as a bitstream that loads on one and not the other. So there are
// now two copies rather than three, and m6.c still owns its own.
// ---------------------------------------------------------------------------
uint32_t ft_crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
    }
    return ~c;
}

bool ft_recv_exact(uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int c = getchar_timeout_us(1000000);
        if (c == PICO_ERROR_TIMEOUT) {
            printf("\nrecv: timed out after %u of %u bytes\n",
                   (unsigned)i, (unsigned)n);
            return false;
        }
        p[i] = (uint8_t)c;
    }
    return true;
}

// `hunt_s` bounds the wait for the magic, in seconds; 0 means wait forever.
//
// M7f is why it is a parameter. The second bitstream of a run is *optional* -
// configuration C needs a jumper that may not be fitted and a bitstream that
// may not have been built, and a run that has already produced six timed rows
// and a clean sweep must not then hang forever waiting for one. So the first
// call blocks (nothing below it can happen without a bitstream) and the second
// gives up and reports configuration A alone.
size_t ft_recv_bitstream(int hunt_s)
{
    uint8_t hdr[8];

    uint32_t w = 0;
    for (int quiet = 0;;) {
        int c = getchar_timeout_us(1000000);
        if (c == PICO_ERROR_TIMEOUT) {
            printf("."); stdio_flush();
            watchdog_update();       // the host has not arrived; that is not a hang
            if (hunt_s && ++quiet >= hunt_s) { printf("\n"); return 0; }
            continue;
        }
        quiet = 0;
        w = (w << 8) | (uint8_t)c;
        if (w == 0x46475842u) break;      // "FGXB"
    }
    if (!ft_recv_exact(hdr, 8)) return 0;

    uint32_t len, crc;
    memcpy(&len, hdr + 0, 4);
    memcpy(&crc, hdr + 4, 4);
    printf("\nbitstream : %u bytes announced, crc32 %08x\n",
           (unsigned)len, (unsigned)crc);
    // FT_BITSTREAM_MAX, not FT_ARENA_MAX: the download spans both ping-pong
    // buffers, which are dead here and will be overwritten from layer 0 onwards
    // anyway. It stops one buffer short of the frame, and that is the check
    // rather than a comment - the second bitstream arrives with a live frame
    // above it.
    if (len == 0 || len > FT_BITSTREAM_MAX) {
        printf("bitstream : rejected - does not fit %u bytes below the frame\n",
               (unsigned)FT_BITSTREAM_MAX);
        return 0;
    }
    if (!ft_recv_exact(arena, len)) return 0;

    const uint32_t have = ft_crc32(arena, len);
    if (have != crc) {
        printf("bitstream : CRC mismatch - got %08x\n", (unsigned)have);
        return 0;
    }
    printf("bitstream : received intact\n");
    return len;
}

// ---------------------------------------------------------------------------
// One block over the link. m6.c's run_block(), minus the golden comparison -
// M7c checks a whole layer's CRC instead, because comparing 2,048 int32 against
// a naive-kernel reference 174 times would cost ~25 s a frame and would be
// measuring gb_golden() rather than the tile.
// ---------------------------------------------------------------------------
// Written by whichever core is running the build callbacks, read by core 0 at
// the layer barrier. volatile because in the core-1 modes those are not the same
// core, and the read has to see what w1_drain() made visible.
static volatile uint32_t us_build;    // gb_strip + gb_weights, per layer
static uint32_t us_build_frame;       // the same, summed at each layer barrier

// M17. The same total, split by which of the two callbacks spent it.
//
// The split is worth its two counters because the merged figure has now
// misdirected two experiments in a row. M14's int8 control doubled the weight
// blob and moved W1_HI by 2%; M17 Stage 1 cut gb_weights()'s instruction count
// by ~1.4x and moved it by 2% again. Read against a merged W1_HI both look like
// "the weight builder is immovable", when the available reading is that
// gb_weights() is not most of W1_HI in the first place - the plan model says
// gb_strip() is 206 ms of the 356 it projects and gb_weights() 150, and nobody
// has ever checked that ratio against the board. Stage 2 is a large change that
// deletes gb_weights() entirely, so it should not be started until this says
// what deleting it is worth.
static volatile uint32_t us_build_wgt;   // gb_weights() alone, per layer
static uint32_t us_build_wgt_frame;

// Which frame we are running; see ft_set_mode() in frame.h for why these are
// runtime state.
static bool c1_build;     // gb_strip + gb_weights on core 1
static bool c1_scatter;   // the requantize epilogue on core 1
static bool c1_decode;    // M7f: the DRAIN response's locate + decode on core 1

// M7f, and the reason it is a flag rather than an edit: mode 4 measured core 1
// at 68% busy and core 0 stalling 141 ms anyway, because one FIFO put work
// nobody waits for in front of work core 0 blocks on. Splitting the ring into
// two priorities is the fix, and quoting it against mode 4 from a *previous*
// build is exactly the kind of ratio M5b's bring-up entry says is not a
// measurement. So both orderings ship, and off means every job goes to the low
// queue - which, with nothing in the high one, is the old single FIFO exactly.
static bool c1_prio;

void ft_set_mode(bool pipe, bool cbuild, bool cscat, bool cdec, bool cprio)
{
    gh_set_pipelined(pipe);
    c1_build   = cbuild;
    c1_scatter = cscat;
    c1_decode  = cdec;
    c1_prio    = cprio;
}

// Turning it on zeroes the count, so ft_sweep_blocks() is what this pass swept
// rather than what the boot has swept.
void ft_set_sweep(bool on) { do_sweep = on; if (on) sweep_blocks = 0; }

// M15. Selects the plan and, through it, every downstream decision: gb_geom()
// resolves the per-block rq flag, run_block() sets the CFG bit and sends the
// table, and scatter() takes the memcpy arm. Nothing here reads it directly.
void ft_set_rq(bool on) { rq_on = on; }
bool ft_rq(void)        { return plan_ix() != 0; }

// Which queue the builds go to. The one thing core 0 waits on by name.
static inline int build_q(void) { return c1_prio ? W1_HI : W1_LO; }

// Errors raised on core 1. A job cannot return one - w1_post() hands back a
// ticket, not a result - so they are stashed here and read at the layer barrier,
// which is the first point core 0 knows every job has retired. Sticky and first-
// wins, matching gh_sync()'s contract: a deferred failure is reported late, but
// it is never lost.
static volatile gh_err_t c1_err;

static void __not_in_flash_func(decode_cb)(void *arg)
{
    gh_defer_t *d = *(gh_defer_t *const *)arg;
    const gh_err_t e = gh_decode_defer(d);
    if (e && !c1_err) c1_err = e;
}

// M7d. The build is the largest single piece of CPU in the frame - 526 ms of
// the 1,246 M7c measured - and it needs nothing from the link, so it belongs in
// the window where the DMA is clocking bytes and the CPU is spinning on a flag.
//
// It is split across two windows rather than one because the windows are not
// large. Per pass the wire spends ~106 us on ACT and ~135 us on WGT, and the
// build is ~124 us of strip and ~159 us of weights: one job per transaction
// nearly fits, and one job in one window would overrun by more than it hides.
//
// M7d also measured the consequence: the build overran its windows by 117 ms
// across the frame, because the window is per *transaction*. That is the term
// core 1 deletes - not by making the build faster, but by giving it a whole
// core's worth of window instead of a transaction's worth.
typedef struct {
    const gb_geom_t *g;
    const uint8_t   *in;
    const int8_t    *wb;
    int8_t          *wdst;   // M7h: wstream[buf], or a weight-cache slot
    int pass, buf;
} build_job_t;

static build_job_t job;

static void __not_in_flash_func(build_strip_cb)(void *arg)
{
    const build_job_t *j = (const build_job_t *)arg;
    const uint64_t t = time_us_64();
    gb_strip(j->g, j->in, j->pass, strip[j->buf]);
    us_build += (uint32_t)(time_us_64() - t);
}

static void __not_in_flash_func(build_wgt_cb)(void *arg)
{
    const build_job_t *j = (const build_job_t *)arg;
    const uint64_t t = time_us_64();
    gb_weights(j->g, j->wb, j->pass, j->wdst);
    const uint32_t dt = (uint32_t)(time_us_64() - t);
    us_build     += dt;
    us_build_wgt += dt;
}

// ---------------------------------------------------------------------------
// The weight stream, built once per channel block instead of once per block.
//
// gb_weights() reads nothing that varies with position: its output is a pure
// function of (wb, q0, Q, K, CIN, Cb, pass). gp_blocks() enumerates the channel
// block outermost and the position block innermost, so the nposblk blocks of one
// q0 are consecutive and every one of them rebuilds a byte-identical stream.
//
// Across the frame that is 847 of the 1,856 weight builds and 43% of their
// bytes. It is not all of them: layers 5, 6 and 7 have nposblk == 1, so their
// streams genuinely are used once each and there is nothing to reuse. Those are
// also the layers with the largest npass - 32, 32 and 192 - so the pool that
// would hold them is 221 KB for no gain at all, and the size test below excludes
// them by size rather than by layer number. 18,432 B covers layers 0-4 exactly:
// npass <= 16 and w_len <= 1,152.
//
// The build writes *into* the slot rather than into wstream[buf] and then
// copying, which also means the double buffer is not needed while caching -
// pass p owns slot p, and core 1 filling slot p+1 cannot touch the slot core 0
// is sending. Correctness rests on the key, so the key is every input
// gb_weights() reads, not the subset that happens to vary today.
// ---------------------------------------------------------------------------
#define WCACHE_BYTES 18432

static int8_t wcache[WCACHE_BYTES];
static uint32_t wgt_built, wgt_cached;          // passes
static uint64_t wgt_bytes_all, wgt_bytes_hit;   // w_len, weighted by cost
static struct {
    const int8_t *wb;
    int q0, Q, K, CIN, Cb, npass;
    bool full;               // every slot written, so a hit may skip the build
} wck;

void ft_wgt_stats(uint32_t *built, uint32_t *cached,
                  uint64_t *bytes_all, uint64_t *bytes_hit)
{
    *built = wgt_built; *cached = wgt_cached;
    *bytes_all = wgt_bytes_all; *bytes_hit = wgt_bytes_hit;
}

// Does this block's weight stream live in the cache, and where? Returns NULL
// when the layer does not fit, which is the layers-5-to-7 case. `pass` may be
// npass, which names one past the last slot and is never dereferenced - the
// caller computes the next pass's destination before checking whether there is
// a next pass.
static inline int8_t *wcache_slot(const gb_geom_t *g, int pass)
{
    if ((size_t)g->npass * (size_t)g->w_len > sizeof wcache) return NULL;
    return wcache + (size_t)pass * (size_t)g->w_len;
}

// True when the slots already hold this block's streams. Called once per block,
// before any of them is handed to gh_wgt().
static bool wcache_hit(const gb_geom_t *g, const int8_t *wb)
{
    if (wck.full && wck.wb == wb && wck.q0 == g->q0 && wck.Q == g->Q &&
        wck.K == g->K && wck.CIN == g->CIN && wck.Cb == g->Cb &&
        wck.npass == g->npass)
        return true;
    wck.full = false;        // a different key: whatever is in there is stale
    return false;
}

static void wcache_claim(const gb_geom_t *g, const int8_t *wb)
{
    wck.wb = wb; wck.q0 = g->q0; wck.Q = g->Q; wck.K = g->K;
    wck.CIN = g->CIN; wck.Cb = g->Cb; wck.npass = g->npass; wck.full = true;
}

// ---------------------------------------------------------------------------
// M15's requantize table, on the same argument one paragraph up and with one
// difference that matters.
//
// gb_rqp() reads (bias, mult) per output channel, so its output is a pure
// function of (layer, q0, Q) - no position, no pass. The enumeration is q0-major,
// so the table is sent 31 times a frame rather than 174.
//
// **The difference is what is being cached.** wcache remembers what is in *host*
// memory, and host memory is still there whatever the tile does. This remembers
// what is in the *tile's* strip array, which a reconfiguration wipes - and m7
// reconfigures, twice, with a second bitstream partway through the run. So it is
// invalidated in ft_frame_reset() and not only on a key change.
static struct { int layer, q0, Q; bool full; } rqk;

// 192 bytes at the worst Q. Not double-buffered and does not need to be:
// gh_rqp() goes through gh_simple(), which stages the payload into the driver's
// own buffer before it arms the DMA, and no core-1 job ever reads this.
static uint8_t rqp_buf[GW_RQP_BYTES * GW_RQP_MAXQ];

static bool rqp_hit(const gb_geom_t *g)
{
    if (rqk.full && rqk.layer == g->layer && rqk.q0 == g->q0 && rqk.Q == g->Q)
        return true;
    rqk.full = false;
    return false;
}

// `d` non-NULL clocks the DRAIN into `cap` and leaves it undecoded, armed on
// `d`; `acc` is not written until somebody calls gh_decode_defer(). The caller
// owns that, and owns keeping `cap` still until it has.
//
// __noinline is load-bearing, not style. GCC had inlined this into
// run_frame.constprop.0 - 3,876 bytes in flash - so __not_in_flash_func alone
// would have moved nothing: the attribute lands on a section for a symbol that
// no longer exists as a call target. Marking the caller instead would drag the
// whole per-layer scaffolding into SRAM for the sake of the inner loop, so the
// inner loop is separated out and only it moves.
static gh_err_t __noinline __not_in_flash_func(run_block)(
    const gb_geom_t *g, const uint8_t *in, const int8_t *wb,
    uint8_t *status_out, int32_t *acc, gh_defer_t *d, uint8_t *cap)
{
    gh_cfg_t c = {
        .H = (uint16_t)g->H, .W = (uint16_t)g->W, .OW = (uint16_t)g->OW,
        .strip_rw = (uint16_t)g->strip_rw, .strip_ch = (uint16_t)g->strip_ch,
        .oy0 = (uint16_t)g->oy0, .ox0 = (uint16_t)g->ox0, .K = (uint16_t)g->K,
        .P = (uint8_t)g->P, .QG = (uint8_t)g->QG,
        .stride2 = (g->st == 2), .unsigned_in = (g->unsigned_in != 0),
        .w4 = (g->w4 != 0),
        // M15. g->rq and never the mode flag: gb_geom() may have refused this
        // geometry, and this bit is what makes the tile drain bytes - so it has
        // to agree with the length the drain below asks for or the transaction
        // is short and the response is a timeout.
        .rq = (g->rq != 0),
    };

    // Per sweep the tile spends 1 cycle in S_LOAD, P in S_SWEEP and FLUSH in
    // S_FLUSH. These are clocks we have to supply, not a timeout: link_clk is
    // the tile's only clock, so under-budgeting strands it mid-sweep. The
    // expression moved to gemm_plan.h at M16, because it now depends on how the
    // bitstream was built and three files were carrying their own copy.
    const uint32_t sweep = (uint32_t)gp_sweep_cycles(g);

    gh_err_t e;
    gh_overlap(NULL, NULL);      // nothing left registered by a failed block
    if ((e = gh_nop(status_out)))  return e;
    if ((e = gh_cfg(&c)))          return e;

    // M15. Misses on the first block of a channel group and hits on the other
    // five; gp_blocks() marks the same blocks rqp_send so the cost model can
    // answer this without running the sequencer. A divergence between the two
    // under-prices the wire, which is a wrong projection and not a wrong tensor.
    //
    // It goes out before the ACTs and it has to: the table lives at RQBASE
    // inside the tile's strip array, and although gb_geom() capped a_len below
    // that boundary for exactly this reason, the tile reads the table during the
    // drain and there is nothing to reread it from afterwards.
    if (g->rq && !rqp_hit(g)) {
        const size_t n = gb_rqp(mdl, &mdl->desc[g->layer], g, rqp_buf);
        // gb_geom() cleared g->rq for both geometry refusals and ft_init()
        // cleared it for conv7, so the only way here is a channel whose
        // (bias, M, s) does not fit the wire fields. Loud, and correctly a link
        // error: the tile cannot produce this layer's codes.
        if (!n)                      return GH_ERR_TOOBIG;
        if ((e = gh_rqp(rqp_buf, n))) return e;
        rqk.layer = g->layer; rqk.q0 = g->q0; rqk.Q = g->Q; rqk.full = true;
    }

    // M7h. Decided once per block, before any build is posted, so that every
    // pass of this block agrees about where its stream lives. `hit` implies
    // wcache_slot() is non-NULL: the key it matched on includes npass and
    // w_len's two factors, so a hit cannot be a layer the pool does not fit.
    const bool w_hit = wcache_hit(g, wb);
    int8_t *const w0  = wcache_slot(g, 0);

    if (w_hit) { wgt_cached += (uint32_t)g->npass;
                 wgt_bytes_hit += (uint64_t)g->npass * (uint64_t)g->w_len; }
    else         wgt_built  += (uint32_t)g->npass;
    wgt_bytes_all += (uint64_t)g->npass * (uint64_t)g->w_len;

    // Pass 0 has no earlier window to hide in, so it is built here and paid for.
    // Across a frame that is 174 passes of the 1,856 - the rest are free.
    job = (build_job_t){ g, in, wb, w0 ? w0 : wstream[0], 0, 0 };
    build_strip_cb(&job);
    if (!w_hit) build_wgt_cb(&job);

    // Tickets for the build of each half, so the wait below is on the one job
    // that has to have finished rather than on the queue being empty. Only read
    // when pass > 0, which is exactly when they have been set. A ticket belongs
    // to a queue, so `bq` is read once here and used for both the post and the
    // wait; changing c1_prio mid-block would strand these.
    uint32_t tk_s[2] = { 0, 0 }, tk_w[2] = { 0, 0 };
    const int bq = build_q();

    for (int pass = 0; pass < g->npass; pass++) {
        const int cur = pass & 1;
        const bool more = (pass + 1 < g->npass);

        // Each gh_* call stages its payload into the driver's own buffer before
        // arming, so strip[cur] and wstream[cur] are free the moment the call is
        // made - but only the other half is safe to *write*, which is what the
        // second buffer is for. That holds identically for both executors: the
        // difference below is only *who* runs the two callbacks.
        // Where this pass's weights are, and where the next pass's go. A cache
        // slot needs no double buffer: pass p owns slot p for the whole block,
        // so core 1 filling p+1 cannot be writing what core 0 is sending. Only
        // the wstream[] fallback still alternates.
        int8_t *const wsrc = w0 ? wcache_slot(g, pass)     : wstream[cur];
        int8_t *const wnxt = w0 ? wcache_slot(g, pass + 1) : wstream[cur ^ 1];

        if (c1_build) {
            // Post both up front. Core 1 runs jobs in order, so everything
            // between here and the wait is lead time, and there is no reason to
            // hand it the weight build later than the strip build.
            if (more) {
                const build_job_t j = { g, in, wb, wnxt, pass + 1, cur ^ 1 };
                tk_s[cur ^ 1] = w1_post(bq, build_strip_cb, &j, sizeof j);
                if (!w_hit)
                    tk_w[cur ^ 1] = w1_post(bq, build_wgt_cb, &j, sizeof j);
            }
            if (pass) w1_wait(bq, tk_s[cur]);
            if ((e = gh_act(strip[cur], (size_t)g->a_len)))    return e;
            if (pass && !w_hit) w1_wait(bq, tk_w[cur]);
            if ((e = gh_wgt(wsrc, (size_t)g->w_len)))          return e;
        } else {
            if (more) job = (build_job_t){ g, in, wb, wnxt, pass + 1, cur ^ 1 };
            if (more) gh_overlap(build_strip_cb, &job);
            if ((e = gh_act(strip[cur], (size_t)g->a_len)))    return e;
            if (more && !w_hit) gh_overlap(build_wgt_cb, &job);
            if ((e = gh_wgt(wsrc, (size_t)g->w_len)))          return e;
        }
        if ((e = gh_run(pass == 0, sweep)))                    return e;
    }

    // Every pass has now been through gh_wgt(), so every slot has been written
    // and waited on. Claimed here rather than at the first build because a
    // block that returns early above leaves the pool half filled, and `full`
    // has to mean all of it - an early return is a failed link, but the run
    // reports the failure and carries on to the next mode.
    if (w0) wcache_claim(g, wb);

    // M15. One byte per accumulator when the tile ran the epilogue, four when it
    // did not, and that is the whole milestone on the wire. The buffer is the
    // same either way - got[] is sized for the int32 case and the codes are a
    // quarter of it - so only the count and the entry point change.
    const size_t dn = (size_t)g->nacc;
    if (d) {
        if ((e = g->rq ? gh_drain_defer_b(acc, dn, cap, RXD_BYTES, d)
                       : gh_drain_defer(acc, dn, cap, RXD_BYTES, d)))
            return e;
    } else if ((e = g->rq ? gh_drain_b(acc, dn, status_out)
                          : gh_drain(acc, dn, status_out))) {
        return e;
    }

    // underrun is raised during T_DATA, so the status byte at the head of the
    // DRAIN response predates it. The flag is sticky for exactly that reason;
    // this is the read that can see it.
    return gh_nop(status_out);
}

// ---------------------------------------------------------------------------
// The epilogue the tile does not do. got[] is in drain order - channel group
// outer, lane next, position inner - and that order is gb_golden()'s, not a
// transcription of it: both walk the same three loops, and test_gemm_plan.c
// asserted on the host that across a layer these writes cover every output
// exactly once.
//
// The epilogue is encoder.h's, shared with fgx_conv_ref() and encoder_fast.c
// rather than copied, so there is one epilogue in the project.
// ---------------------------------------------------------------------------
// M7d hoists the address arithmetic, and it collapses rather than moves. The
// M7c inner loop computed, for pos = ox0 + p,
//
//     k = oc*OH*OW + (oy0 + pos/OW)*OW + pos%OW
//
// which is two integer divisions and three multiplies per output element, over
// 356,352 of them a frame. But (pos/OW)*OW + pos%OW is just pos, for any
// non-negative pos, so the whole expression is
//
//     k = oc*OH*OW + oy0*OW + ox0 + p
//
// - a base per channel and a walk. The division was never computing anything;
// it split a flat index apart so the next two terms could put it back together.
// Blocks are guaranteed in range because test_gemm_plan.c asserts on the host
// that they tile each output tensor exactly once.
// M7e flattens the arguments into a descriptor. The geometry it used to read
// through `g` is a loop local that the next block overwrites, and core 1 may
// still be scattering block b while core 0 is setting up b+1 - so what core 1
// gets is a copy of the seven numbers it actually uses, taken while they are
// still true. This is worker.h's rule 2, and the reason it is a rule: passing
// `g` by pointer would work on every run where core 1 happened to be fast
// enough.
typedef struct {
    // got[] half this block drained into. void because M15 drains two different
    // things into it: int32 accumulators, or one code per accumulator. Typed as
    // int32_t* it would have been a cast at every use anyway, and a cast that
    // reads as "the pointer is right, the count is the question" - which is the
    // opposite of true.
    const void    *acc;
    const int32_t *bias;
    const float   *mult;
    void          *out;
    uint32_t plane, off;
    int  q0, QG, P;
    bool relu, out_float;
    bool from_code;              // M15: acc is bytes and the epilogue is done
} scat_job_t;

static uint32_t us_scatter;      // core 0's copy; core 1's is in w1_busy_us()

static void __not_in_flash_func(scatter)(const scat_job_t *s)
{
    const bool relu = s->relu, as_float = s->out_float;
    const int P = s->P;

    // M15. The tile already applied the epilogue, so this is placement and not
    // arithmetic. The drain walk is channel-major with the P positions innermost,
    // and those same P positions are contiguous in the output plane too - the
    // whole reason M7d's index arithmetic collapsed to a base and a walk - so a
    // channel is one memcpy and the 356,352 fgx_code_fixed() calls are gone.
    //
    // bias and mult are unread here, and deliberately still in the descriptor:
    // rq is a runtime mode, the int32 arm below runs in the same boot, and a
    // descriptor whose fields depended on the mode would be a second thing to
    // keep in step with it.
    if (s->from_code) {
        const uint8_t *src = (const uint8_t *)s->acc;
        for (int gg = 0; gg < s->QG; gg++)
            for (int j = 0; j < GB_NMAC; j++) {
                const int oc = s->q0 + gg * GB_NMAC + j;
                memcpy((uint8_t *)s->out + (size_t)oc * s->plane + s->off,
                       src, (size_t)P);
                src += P;
            }
        return;
    }

    const int32_t *acc = (const int32_t *)s->acc;
    size_t o = 0;
    for (int gg = 0; gg < s->QG; gg++)
        for (int j = 0; j < GB_NMAC; j++) {
            const int oc = s->q0 + gg * GB_NMAC + j;
            const int32_t b = s->bias[oc];
            const float mu = s->mult[oc];
            const size_t base = (size_t)oc * s->plane + s->off;
            if (as_float) {
                float *dst = (float *)s->out + base;
                for (int p = 0; p < P; p++)
                    dst[p] = fgx_requant(acc[o++], b, mu, relu);
            } else {
                // M15's fixed-point epilogue, the same one fgx_conv_ref()
                // now uses. It has to be: the whole point of m7 is that the
                // fabric and the MCU produce the same bytes, and an epilogue
                // that differed here would break that in a way no accumulator
                // sweep could see. This is the arm rq mode replaces: same
                // fgx_code_fixed(), same (M, s) from the same fgx_rq_pick(),
                // computed here instead of in the fabric. That is what makes an
                // rq-on run checkable against an rq-off one byte for byte.
                int32_t rq_m;
                const int rq_s = fgx_rq_pick(mu, &rq_m);
                uint8_t *dst = (uint8_t *)s->out + base;
                for (int p = 0; p < P; p++)
                    dst[p] = fgx_code_fixed(acc[o++], b, rq_m, rq_s);
            }
        }
}

static void __not_in_flash_func(scatter_cb)(void *arg)
{
    scatter((const scat_job_t *)arg);
}

// Fills the descriptor from the block's geometry, at the one moment everything
// in it is still in scope.
static scat_job_t scat_of(const gb_geom_t *g, const fgx_desc_t *d,
                          const int32_t *bias, const float *mult,
                          const void *acc, void *out, bool out_float)
{
    return (scat_job_t){
        .acc = acc, .bias = bias, .mult = mult, .out = out,
        .plane = (uint32_t)g->OH * (uint32_t)g->OW,
        .off   = (uint32_t)g->oy0 * (uint32_t)g->OW + (uint32_t)g->ox0,
        .q0 = g->q0, .QG = g->QG, .P = g->P,
        .relu = (d->relu != 0), .out_float = out_float,
        // Read from the geometry and not from the mode, so this cannot disagree
        // with the drain run_block() just issued into that buffer.
        .from_code = (g->rq != 0),
    };
}

// ---------------------------------------------------------------------------
// Start-up and per-frame state

static uint32_t frame_us;
static uint8_t  worst_status;

uint32_t ft_frame_us(void)       { return frame_us; }
uint8_t  ft_status(void)         { return worst_status; }
uint32_t ft_us_build_frame(void) { return us_build_frame; }
uint32_t ft_us_build_wgt_frame(void) { return us_build_wgt_frame; }
uint32_t ft_us_scatter(void)     { return us_scatter; }
uint32_t ft_sweep_blocks(void)   { return sweep_blocks; }

const char *ft_init(const fgx_model_t *m)
{
    mdl     = m;
    nlayer_ = m->hdr->n_layers;      // convs + the linear head
    nconv_  = nlayer_ - 1;           // everything the tile can run

    const size_t colb = fgx_fast_col_bytes(m);
    if (nlayer_ > FT_MAX_LAYERS || m->scratch > FT_SCRATCH_MAX ||
        colb > FT_COL_MAX) {
        snprintf(fault_msg, sizeof fault_msg,
                 "%u layers needing %u B scratch and %u B im2col, have %u/%u/%u",
                 (unsigned)nlayer_, (unsigned)m->scratch, (unsigned)colb,
                 (unsigned)FT_MAX_LAYERS, (unsigned)FT_SCRATCH_MAX,
                 (unsigned)FT_COL_MAX);
        return fault_msg;
    }

    // gp_choose() runs here rather than being baked in, so this firmware
    // survives a re-export with different channel counts. It costs microseconds.
    //
    // Twice over since M15, once per drain width - see the plan[2] note. Both
    // are validated here even though a boot may only ever run one of them: a
    // mode change must not be able to fail, and MAXBLK is a property of the
    // blocking, which is exactly what rq moves.
    for (int rq = 0; rq < 2; rq++)
        for (uint32_t i = 0; i < nconv_; i++) {
            // conv7 emits floats and has no code to compute, so it drains int32
            // in both plans. gp_choose() cannot ask this itself - it is given a
            // descriptor, and "is this the last conv" is a question about the
            // model.
            const int lrq = rq && !fgx_emits_float(m, i);
            const char *why = gp_choose(&m->desc[i], (int)i, lrq, &plan[rq][i]);
            if (why) {
                snprintf(fault_msg, sizeof fault_msg, "layer %u: %s",
                         (unsigned)i, why);
                return fault_msg;
            }
            if (plan[rq][i].nblocks > MAXBLK) {
                snprintf(fault_msg, sizeof fault_msg,
                         "layer %u%s wants %d blocks, MAXBLK is %d",
                         (unsigned)i, rq ? " (rq)" : "",
                         plan[rq][i].nblocks, MAXBLK);
                return fault_msg;
            }
        }
    return NULL;
}

void ft_frame_reset(void)
{
    gh_bytes_reset();
    gh_prof_reset();
    gh_dprof_reset();
    w1_prof_reset();

    frame_us = 0;
    worst_status = 0;
    us_scatter = 0;
    us_build_frame = 0;
    us_build_wgt_frame = 0;
    gcur = 0;
    c1_err = GH_OK;
    wgt_built = wgt_cached = 0;
    wgt_bytes_all = wgt_bytes_hit = 0;
    // The pool itself survives - the weights are the same every frame, so a key
    // that still matches is still right - but a frame that broke out mid-block
    // left it partly written, and `full` is the only thing that says otherwise.
    wck.full = false;
    // M15's is not the same argument. The weights are in host memory and survive
    // anything; the requantize table is in the tile's strip array, and this is
    // called after a reconfiguration - m7 downloads a second bitstream partway
    // through a run - so what the tile holds is whatever the previous
    // configuration left, which is nothing.
    rqk.full = false;
    // A frame that broke out of a block loop can leave a descriptor armed, and
    // gh_drain_defer() refuses to arm over one - deliberately, since silently
    // overwriting would lose a whole block of accumulators. This is where the
    // next run gets a clean slate.
    dfr[0].armed = dfr[1].armed = false;
}

// One layer, every block on the tile.
//
// The failure paths all w1_drain() before returning, which m7.c's park() made
// unnecessary and a loop makes essential: `g`, `src` and `wb` are read by jobs
// core 1 may still be holding, and two of the three are this frame's, not the
// program's.
ft_err_t ft_layer(uint32_t i, const void *src, void *dst)
{
    ft_err_t r = { GH_OK, NULL };

    const fgx_desc_t *d = &mdl->desc[i];
    const bool as_float = fgx_emits_float(mdl, i);
    const int32_t *bias =
        (const int32_t *)((const uint8_t *)mdl->biases + d->b_off);
    const float *mult =
        (const float *)((const uint8_t *)mdl->mults + d->m_off);
    const int8_t *wb = mdl->weights + d->w_off;

    // Read once. plan_ix() is a function of two mode flags, and a layer that
    // enumerated one plan's blocks and reported against the other's would be a
    // fault nothing here could see.
    const gp_layer_t *const pl = &plan[plan_ix()][i];

    const int nb = gp_blocks(d, pl, specs, MAXBLK);
    if (nb != pl->nblocks) {
        snprintf(fault_msg, sizeof fault_msg,
                 "layer %u: gp_blocks gave %d, planned %d",
                 (unsigned)i, nb, pl->nblocks);
        r.fault = fault_msg;
        return r;
    }

    gh_prof_t p0, p1;
    gh_prof(&p0);
    us_build = 0;
    us_build_wgt = 0;
    const uint32_t stall0 = w1_stall_us();
    const uint64_t tl = time_us_64();
    uint8_t st = 0;
    gh_err_t e = GH_OK;

    // The scatter still reading each half of got[], if any. Ticket 0 is a
    // real ticket, so the liveness is a separate flag rather than a
    // sentinel value.
    uint32_t tk_scat[2] = { 0, 0 };
    bool     scat_live[2] = { false, false };

    for (int b = 0; b < nb && !e; b++) {
        gb_geom_t g;
        const char *why = gb_geom(d, &specs[b], &g);
        if (why) {
            w1_drain();
            snprintf(fault_msg, sizeof fault_msg, "layer %u block %d: %s",
                     (unsigned)i, b, why);
            r.fault = fault_msg;
            return r;
        }

        // Block b+2 lands back in the half block b's scatter was reading,
        // and the half its DRAIN was decoded out of. **This is the wait
        // that makes that safe, and under M7f it is the only one.** M7e
        // wrote it while a single FIFO still implied it - waiting on b+1's
        // build implied b's scatter had retired - on the grounds that the
        // loop should not depend on which modes the ladder contains. The
        // priority split deletes that implication outright: a build posted
        // later now runs earlier by design. See rule 5 in worker.h.
        //
        // Waiting on the scatter covers the decode too, because both are
        // W1_LO and order does hold inside a queue.
        if (scat_live[gcur]) {
            w1_wait(W1_LO, tk_scat[gcur]);
            scat_live[gcur] = false;
        }
        int32_t *acc = got[gcur];
        e = run_block(&g, (const uint8_t *)src, wb, &st, acc,
                      c1_decode ? &dfr[gcur] : NULL, rxd[gcur]);
        // Core 1 may still hold a job that reads `g`, `src` or `wb`, and
        // breaking out of the loop is where those stop being valid.
        if (e) { w1_drain(); break; }
        worst_status |= st;

        // Posted before the scatter, and that ordering is the whole
        // handoff: core 1 runs jobs in order, so the scatter cannot read
        // acc[] before the decode that fills it has returned. Nothing on
        // core 0 waits here.
        uint32_t tk_dec  = 0;
        bool     dec_live = false;
        if (c1_decode) {
            gh_defer_t *const dp = &dfr[gcur];
            tk_dec   = w1_post(W1_LO, decode_cb, &dp, sizeof dp);
            dec_live = true;
        }

        if (do_sweep) {
            // The sweep reads acc[] on this core, so here - and only
            // here - the decode stops being asynchronous.
            if (dec_live) { w1_wait(W1_LO, tk_dec); dec_live = false; }
            gb_golden(mdl, d, &g, src, golden);
            for (int k = 0; k < g.nacc; k++)
                if (acc[k] != golden[k]) {
                    w1_drain();
                    snprintf(fault_msg, sizeof fault_msg,
                             "layer %u block %d acc %d: got %ld, want %ld",
                             (unsigned)i, b, k, (long)acc[k], (long)golden[k]);
                    r.fault = fault_msg;
                    return r;
                }
            sweep_blocks++;
        }
        const scat_job_t sj =
            scat_of(&g, d, bias, mult, acc, dst, as_float);
        if (c1_scatter) {
            // The blocks of a layer write disjoint regions of `dst` -
            // test_gemm_plan.c asserts on the host that they tile it
            // exactly once - so the only buffer two blocks contend for
            // is got[] - and now rxd[] - and that is what gcur
            // alternates. Waiting on this ticket covers both, because
            // the decode was posted first.
            tk_scat[gcur]   = w1_post(W1_LO, scatter_cb, &sj, sizeof sj);
            scat_live[gcur] = true;
            gcur ^= 1;
        } else {
            // Scattering here means reading acc[] here. The ladder never
            // combines these two flags this way, but the loop should not
            // depend on which rungs the ladder happens to contain.
            if (dec_live) { w1_wait(W1_LO, tk_dec); dec_live = false; }
            const uint64_t t = time_us_64();
            scatter(&sj);
            us_scatter += (uint32_t)(time_us_64() - t);
        }
    }

    // The layer barrier. It has to be inside the timed region: core 1's
    // tail is frame time like any other, and a frame figure that stopped
    // the clock while the other core was still requantizing would be the
    // measurement telling us what we wanted to hear.
    w1_drain();

    // And the first point every job core 1 was holding has retired, so
    // the first point this is safe to read. A decode that failed left
    // acc[] - and therefore the layer - undefined, so it is an error for
    // the layer even though the transaction that carried it succeeded.
    if (!e && c1_err) { e = c1_err; c1_err = GH_OK; }

    gh_prof(&p1);
    st_[i].us       = (uint32_t)(time_us_64() - tl);
    st_[i].passes   = (uint32_t)(nb * pl->npass);
    st_[i].us_build = us_build;
    st_[i].us_wire  = (uint32_t)(p1.us_wire - p0.us_wire);
    st_[i].us_stall = w1_stall_us() - stall0;
    st_[i].blocks   = nb;
    st_[i].status   = st;

    frame_us += st_[i].us;
    // Safe to read after the barrier and not before: in the core-1 modes
    // this counter's last writer was the other CPU.
    us_build_frame += us_build;
    us_build_wgt_frame += us_build_wgt;

    r.link = e;
    return r;
}

void ft_pool_head(const float *src, float *embed)
{
    const uint64_t th = time_us_64();
    fgx_pool_head(mdl, src, pool_codes, embed);
    frame_us += (uint32_t)(time_us_64() - th);
}

// ---------------------------------------------------------------------------
// M8b: where the frame comes from.
//
// Every measurement up to M8a was taken on fgx_testvec - a frame model/export.py
// quantized on the host and blobs.S linked into flash. That was the right input
// while the question was "does the tile compute what encoder.c computes": the
// codes are byte-identical to the ones PyTorch's fake-quant saw, so a
// disagreement could not be blamed on a resize filter or a JPEG decoder.
//
// It is the wrong input now, in one specific way - it proves nothing about the
// camera. M8a established that the pixels are the pixels
// (docs/milestones.md#m8a--bring-up) and left exactly one thing undone: handing
// them to the frame loop. This is that.
//
// THE COMPARISON SURVIVES THE SWAP, which is why this was a small change rather
// than a milestone's worth of rework. m7 never checks the frame against a golden
// embedding; it runs the same frame twice - once on the MCU with encoder_fast.c,
// once with every convolution on the tile - and compares those two to each
// other. Both CRCs are of tensors this boot computed from the same bytes, so any
// image serves, and a live one makes the run an end-to-end test of a pipeline
// that until now started three quarters of the way along.
//
// It falls back to the flash vector when no camera answers, and every report
// says which was used. A run whose input is ambiguous is worse than either run:
// the six timed rows are comparable across boots only if the frames were.
//
// The frame buffer itself is up in the pool - see the memory block near the top
// for why it could not simply be a new 48 KB array.

// What the sensor and the last capture were. cam_mode is set by the bring-up in
// ft_acquire() and read by ft_capture() afterwards, which is the whole reason
// the two are separate calls: the mode write is what cam.h's guard suppresses on
// a repeat capture, and repeating it would put every frame of m8's loop back
// into M8a's blanking fault.
static uint8_t  cam_mode;
static uint32_t cap_len;
static int      cap_mean[3];
static uint32_t cap_expose_us, cap_read_us;

// One frame, at whatever rate the bus is already running, into ft_frame(). No
// printing and no fallback: m8 calls this once per loop iteration and a bad
// frame there is a line in its log rather than a change of input.
//
// It costs the arena - 32 KB of RGB565 land there before the convert - so the
// caller must not have anything live in it. In m8's loop that is free, because
// layer 0 writes the arena immediately afterwards.
const void *ft_capture(float in_scale)
{
    const uint32_t want = FT_FRAME_W * FT_FRAME_H * 2u;
    cam_time_t t;
    cap_len = cam_capture(&CAM_RECIPE_VENDOR, cam_mode, CAM_IMAGE_PIX_FMT_RGB565,
                          arena, FT_ARENA_MAX, &t);
    cap_expose_us = t.expose_us;
    cap_read_us   = t.read_us;
    if (cap_len != want || cam_frame_is_constant(arena, cap_len)) return NULL;

    cam_frame_means(arena, cap_len, cap_mean);
    cam_frame_to_chw_rot(arena, FT_FRAME_W, FT_FRAME_H, CAM_HI_FIRST,
                         FT_MOUNT_ROT, in_scale, frame);
    return frame;
}

void ft_cap_stats(int mean[3], uint32_t *expose_us, uint32_t *read_us)
{
    if (mean) memcpy(mean, cap_mean, sizeof cap_mean);
    if (expose_us) *expose_us = cap_expose_us;
    if (read_us)   *read_us   = cap_read_us;
}

void ft_cam_fault_inject(void) { cam_bus_fault_inject(); }

// Returns ft_frame() on success and NULL to mean "use the flash vector". Prints
// its own verdict either way, because a silent fallback is how a camera that
// stopped answering turns into six perfectly good rows about the wrong input.
const void *ft_acquire(float in_scale)
{
    // pio1. The link owns pio0 - gemm_host.c:36 - and while cam_spi and
    // link_narrow_x2 would very likely both fit in one instruction memory, the
    // two have no reason to share it: this part has three PIOs and the second
    // one is empty.
    cam_bus_init(pio1);

    // 8 MHz for everything up to and including the register writes. That is
    // ArduCAM's documented ceiling and the rate every one of M8a's register
    // experiments ran at, and the first version of this function, which used 16
    // MHz throughout, is why it is not a matter of taste - see the warm-up note
    // below. The keeper is the 16 MHz one.
    cam_bus_pio(8000000);

    const uint8_t id = cam_read_reg(CAM_REG_SENSOR_ID);
    if (!cam_id_plausible(id)) {
        printf("camera    : nothing answering (id 0x%02x) - using the flash "
               "test vector\n", (unsigned)id);
        return NULL;
    }
    cam_begin(id, false);
    cam_image_defaults();

    cam_mode = cam_mode_128(id);
    const uint8_t  mode = cam_mode;
    const uint32_t want = FT_FRAME_W * FT_FRAME_H * 2u;
    cam_time_t t;
    uint32_t len = 0;

    // TWO FAULTS LIVE HERE, AND THE FIRST VERSION OF THIS FUNCTION HAD BOTH.
    //
    // ONE: 16 MHZ IS FINE FOR PIXELS AND NOT FOR REGISTER WRITES. That version
    // ran the whole sequence at 16 and got a constant fill of 08 01 with the
    // FIFO holding exactly 32,768 bytes. The right length is what makes it
    // interesting - the CAPTURE_RESOLUTION write had plainly landed, so this was
    // neither a dead bus nor cam.h's repeat-write fault, and the sensor stayed
    // IDLE and CAP_DONE asserted on time throughout. Moving only the writes down
    // to 8 MHz fixed it. Registers at 8, pixels at 16; see cam.h.
    //
    // TWO: AUTO-EXPOSURE CONVERGES OVER FRAMES, AND THERE IS NOTHING TO POLL.
    // With fault one fixed the first frame came back non-constant and mean RGB
    // 14 10 5 - a real picture of a well-lit room, four stops under. cam_probe.c
    // reports 100-155 on the same bench, because it takes thirty-odd captures
    // before the one it keeps and this takes one. cam_image_defaults() turns AE
    // and AGC on and they walk towards the scene a frame at a time; no register
    // says "converged".
    //
    // So the loop waits for the mean to stop moving rather than merely for the
    // frame to stop being constant, which is the predicate that let 14 10 5
    // through. Stability, not a target brightness: a genuinely dark scene is a
    // legitimate answer and the printed mean is how you tell the two apart.
    //
    // It costs the ArduChip nothing, unlike the warm-up M8a threw out - that one
    // predated cam.h's rewrite guard, so a throwaway capture rewrote FORMAT and
    // CAPTURE_RESOLUTION and shoved the kept frame into the blanking fault,
    // making every mode constant. With the guard, a repeat capture writes no
    // registers at all.
    int mean[3] = { 0, 0, 0 };
    int warm = 0, was = -1000, stable = 0, first = -1;
    bool rose = false;
    printf("camera    : exposure ramp");
    // 24 was sized on M8b's fifteen-frame ramp with a little slack. M8c raised
    // it because the blank frames now come first and eat into that slack, and
    // because the whole loop is ~150 ms a frame and happens once a boot: six
    // seconds of worst case against a run that is minutes long.
    for (; warm < 40; warm++) {
        len = cam_capture(&CAM_RECIPE_VENDOR, mode, CAM_IMAGE_PIX_FMT_RGB565,
                          arena, FT_ARENA_MAX, &t);
        if (len != want) break;
        // 40 frames at ~150 ms is six seconds of worst case, against m9's eight
        // second watchdog - too close to leave alone, and a sensor walking its
        // exposure towards the room is not a hang. Fed once a frame rather than
        // once for the ramp, so a wedge inside cam_capture itself still trips.
        watchdog_update();
        cam_frame_means(arena, len, mean);
        const int luma = (mean[0] + mean[1] + mean[2]) / 3;
        printf(" %d", luma);
        // Two counts of 255, three comparisons running, and a floor of six
        // frames. Every part of that is because the first version broke on the
        // first stable pair and stopped at luma 9: a sensor sitting at the
        // bottom of its ramp is perfectly stable for a frame or two before it
        // starts climbing, so "unchanged since last time" is not "converged".
        //
        // AND STABILITY ONLY COUNTS ONCE THERE IS A FRAME TO BE STABLE ABOUT.
        // M8c walked into the failure this guards: the ramp read 5 5 5 5 5 5
        // and stopped, and 5 is not a dark room - it is the mean of the 08 01
        // constant fill, which is what the FIFO returns when the sensor has not
        // written a frame yet. Six identical readings of *no frame* satisfied
        // every test above, so the run fell back to the flash test vector on a
        // bench where the camera was working perfectly; cam_probe.c got pictures
        // from the same module minutes either side of it.
        //
        // The reason it took until M8c to show up is that the predicate is only
        // wrong while the sensor is slow to start. M8b's ramp was
        // 5 27 64 ... 130 - it left the floor on frame two and settled on
        // fifteen, so the blank frames never lasted long enough to look stable.
        // A colder start, and stability is reached before the first picture is.
        //
        // A genuinely dark scene is still a legitimate answer and still breaks
        // this loop, because a dark scene has sensor noise and is therefore not
        // constant. "Constant" and "dark" are different states and only one of
        // them means the camera has nothing to say.
        //
        // AND THE SAME FAULT CAME BACK THROUGH THE OTHER DOOR ONE RUN LATER.
        // The next cold start read 4 5 5 5 4 5 - *not* constant, one pixel of
        // noise away from it - and the check above passed it as settled at mean
        // RGB 8 0 7. Three hundred frames later it was still 8 0 8, every cosine
        // pinned at 1.000, which is the exact signature of a demo that is not
        // looking at anything. So "constant or not" was never the real predicate
        // either. The sensor is at the bottom of its range in both cases; only
        // the last bit of noise differs, and that bit is not the difference
        // between a camera that works and one that does not.
        //
        // What actually separates every good run from every bad one is whether
        // the exposure ever *rose*: the good ones go 5 8 48 53 ... 79 and are
        // done in a dozen frames, the bad ones never leave single digits no
        // matter how long they are given. So early exit now needs a luma clear
        // of the floor as well as a stable one, and a sensor that never climbs
        // spends the whole bound trying rather than declaring victory on frame
        // six.
        //
        // FLOOR is deliberately low. It is not "correctly exposed", which is a
        // judgement this code has no business making - it is "the sensor is
        // doing something", and a genuinely dark room still clears it once the
        // gain has walked up. A room that does not is reported rather than
        // refused, by the warning after the loop: refusing would mean firmware
        // deciding it knows the lighting better than the person standing in it.
        //
        // AND A FLOOR IS STILL NOT A RISE, WHICH M9 FOUND THE HARD WAY. The
        // paragraph above says the discriminator is whether the exposure ever
        // *rose*, and then the code tested `luma >= 16`, which is a different
        // sentence. On this bench the sensor's cold reading is 36 - already over
        // the floor - so the guard was inert, and M9's first two acquires read
        //
        //     36 36 36 37 37 37     settled after 6 frames, mean RGB 62 47 2
        //
        // while m8, three minutes earlier in the same room, read
        //
        //     36 77 85 92 97 108 109 105 101 102 104 105   mean RGB 139 138 40
        //
        // Same code, same lighting, opposite outcomes. m8 was not passing the
        // test; it was winning a race. Its second sample happened to jump to 77,
        // which reset `stable` and forced the loop to keep going until the AEC
        // had finished - and when the first samples happen to be flat instead,
        // three of them satisfy everything above before the AEC has moved at
        // all. Which means M8c's lens test and its 300-frame endurance run were
        // both done on a lucky acquire.
        //
        // So `rose` is the check the comment always described: latched, because
        // an AEC that overshoots and comes back has still demonstrably acted,
        // and measured against the first real reading rather than against a
        // constant, because that is the number the sensor starts from. A sensor
        // that never moves now spends the whole bound trying, which is 6 s once
        // a boot against a run that is minutes long.
        //
        // The margin is 8 because the two populations are not close: the stuck
        // runs move by 1, the live ones by 41 on the first step. Anything in
        // between would be a tolerance, and the lesson of the three failures
        // above is that the fix is never a better tolerance.
        //
        // A genuinely dark room whose correct exposure is the cold reading is
        // the one case this costs, and it costs it the full bound and then
        // reports rather than
        // refused, by the warning after the loop: refusing would mean firmware
        // deciding it knows the lighting better than the person standing in it.
        const int FLOOR = 16, MOVED = 8;
        const bool flat = cam_frame_is_constant(arena, len);
        if (!flat && first < 0) first = luma;
        if (first >= 0 && (luma - first > MOVED || first - luma > MOVED))
            rose = true;
        if (!flat && luma - was <= 2 && was - luma <= 2) stable++; else stable = 0;
        was = flat ? -1000 : luma;
        if (warm >= 5 && stable >= 3 && luma >= FLOOR && rose) break;
        sleep_ms(50);
    }
    printf("\n");
    if (len != want) {
        printf("camera    : id 0x%02x answered, then the FIFO held %u bytes "
               "rather than %u - using the flash test vector\n",
               (unsigned)id, (unsigned)len, (unsigned)want);
        return NULL;
    }
    if (cam_frame_is_constant(arena, len)) {
        printf("camera    : still a constant fill (%02x %02x) after %d frames "
               "- using the flash test vector\n", arena[0], arena[1], warm + 1);
        return NULL;
    }

    // Now 16 MHz, and only now: this capture writes no registers - same mode,
    // same format, cam.h's guard - so it is M8a's f128fast row exactly, which is
    // the run that settled the rate on the evidence that matters for a
    // 32,768-byte burst held under one CS. That evidence is the rendered
    // picture: a burst that drops or doubles a bit tears visibly and looks fine
    // in a CRC the device computed over its own corrupt buffer. Worth 16 ms.
    //
    // The RGB565 lands in the arena. It held the bitstream until fpga_configure()
    // consumed it and does not become buffer A until the reference pass starts,
    // so all 132 KB of it are free exactly now and the 32 KB the FIFO holds cost
    // nothing - which on a part with no 32 KB to spare is the whole reason the
    // capture happens here rather than before the download.
    cam_bus_pio(16000000);
    // Checked, not trusted, twice: a frame that blanks is precisely the frame
    // this run would report clean CRCs on - both passes agreeing perfectly about
    // a picture of nothing is still both passes agreeing. ft_capture() does both
    // checks, which is why it is the same call the loop makes.
    if (!ft_capture(in_scale)) {
        printf("camera    : %u bytes and %s at 16 MHz after %d good frames at 8 "
               "- using the flash test vector\n", (unsigned)cap_len,
               cap_len == want ? "a constant fill" : "the wrong length",
               warm + 1);
        return NULL;
    }
    memcpy(mean, cap_mean, sizeof mean);

    printf("camera    : live %ux%u RGB565, id 0x%02x, %.1f MHz, expose %u ms, "
           "read %u ms, exposure settled after %d frame%s\n",
           (unsigned)FT_FRAME_W, (unsigned)FT_FRAME_H, (unsigned)id,
           (double)cam_bus_mhz(), (unsigned)(cap_expose_us / 1000u),
           (unsigned)(cap_read_us / 1000u), warm + 1, warm ? "s" : "");
    // Exposure and white balance in three numbers: the mean of the three is
    // exposure, the spread is white balance. M8a's tuned camera sits near
    // (115, 107, 105); a frame far off that is a scene or a lens cap, and either
    // way it is the first thing to know when an embedding looks wrong.
    printf("            mean RGB %d %d %d  (tuned camera on a neutral scene: "
           "about 115 107 105)\n", mean[0], mean[1], mean[2]);
    // The frame is returned either way - see FLOOR above for why this warns
    // rather than refuses - but it is worth a sentence at start-up rather than
    // three hundred frames of cosine 1.000 and no explanation. Everything
    // downstream of here works perfectly on a picture of nothing.
    if ((mean[0] + mean[1] + mean[2]) / 3 < 16)
        printf("            ^ that is the bottom of the sensor's range after "
               "%d frames of auto-exposure.\n"
               "              If the room is not actually dark, the sensor has "
               "not started: check the lens cap,\n"
               "              then cold-power-cycle the board - USB out for ten "
               "seconds, not a reflash.\n", warm + 1);
    // Distinct from the above, and quieter, because it is not necessarily a
    // fault: the exposure never moved, so either the cold reading was already
    // right for this room or the AEC is asleep. The ramp printed above is what
    // tells them apart, and this says where to look at it.
    else if (!rose)
        printf("            ^ the exposure never moved from its first reading "
               "in %d frames, so the auto-exposure\n"
               "              either had nothing to correct or never started. "
               "The ramp above is the evidence.\n", warm + 1);
    return frame;
}
