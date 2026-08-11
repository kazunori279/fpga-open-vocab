// M7c/M7d/M7e/M7f: a whole frame through the T8, six ways.
//
// M7d added the second frame. M7c measured 2,164 ms and found the CPU (1,246 ms)
// and the wire (918 ms) fully serialized against each other, so M7d puts the
// first inside the second and quotes the ratio between them.
//
// M7e adds the third and fourth, and a second CPU. M7d's pipelined frame was
// 1,481 ms against 918 ms of wire, so ~563 ms of CPU still sat outside the DMA
// window with nowhere to go - because there was only one thread. **This board
// has two Cortex-M33s and until now this project used one.** Modes 2 and 3 hand
// the build and then the requantize scatter to core 1 via worker.c; see
// worker.h for the rules, which matter more here than the code does.
//
// M7f adds the fifth. M7e left 369 ms on core 0, and 197 of them were one thing:
// the locate and decode of each block's DRAIN response. It is the only decode in
// a frame the driver cannot hide inside the next transaction's DMA window, for
// the reason gh_frame() states in one line - it is the only response anybody
// reads. So mode 4 hands it to core 1 too, via a capture buffer this file owns.
//
// And mode 5 is what mode 4 measured. Moving 157 ms of decode off core 0 bought
// only 57, because 101 came back as stall: core 1 was 68% busy, not saturated,
// but one FIFO served block b's decode and scatter ahead of block b+1's build,
// which is the one job core 0 blocks on. Mode 5 splits core 1's ring into two
// priorities and gets 44 of those 101 ms back; the other ~54 is a build waiting
// out the low-priority job that was already running, which is the bound worker.h
// names as the price of not having preemption.
//
// So the harness now runs the same frame six times in one boot - serialized,
// pipelined, +core 1 build, +core 1 scatter, +core 1 DRAIN decode, +priorities -
// and each row quotes its ratio against the row above. Everything below about the
// reference pass and the blocking is unchanged from M7c.
//
// ---------------------------------------------------------------------------
// M7g adds no mode, and that is the point
//
// M7g puts the per-transaction and core-1 job code in SRAM: gw_stage(), the
// gh_* frame path, w1_post/wait/drain, gb_strip/gb_weights, and this file's
// scatter, callbacks and run_block. M5b measured 21% from nothing but moving
// code off flash XIP on one core; with two cores the argument is stronger,
// because both share one XIP cache and one QSPI interface, so core 1 taking a
// miss stalls core 0's wire.
//
// **It cannot be a mode, because placement is decided by the linker.** Every
// other step in this ladder is a runtime flag precisely so the A/B lives in one
// boot, which is M5b's rule; this one cannot, and worse, relinking perturbs
// flash layout globally - the exact variable under test. So the evidence here
// is the per-phase counters, which measure the same code doing the same 8.151
// MB and do not care what else moved: us_stage (was 50 ms), w1_busy_us_q(W1_HI)
// (536, the builds), w1_busy_us_q(W1_LO) less dprof.us_decode (314 - 156 = 158,
// the scatter), and dprof.us_decode (156, already SRAM, so the control). The
// frame time is a cross-build delta and is labelled as one wherever it appears.
//
// M6c ran one hand-blocked conv2 block. This runs all eight convolutions, 174
// blocks and 1,856 passes of them, and reports **a measured frame time** - which
// is the entire point. Every latency figure in "The road to 280 ms"
// (docs/milestones.md) is
// projected from M7a's per-byte rates applied to one block shape; this is the
// milestone that replaces those projections with data, and it is a prerequisite
// for M7d and M7e regardless of what it says.
//
// **Two passes over the same two buffers.** First the whole frame on the MCU
// with encoder_fast.c, recording a CRC32 of each layer's output tensor plus the
// 512-d embedding; then the whole frame again with every convolution on the
// tile, comparing CRCs as each layer completes. The reference therefore costs no
// extra SRAM - only the ~3.4 s it takes - and a mismatch localizes to a layer
// rather than to "the embedding is wrong".
//
// **encoder_fast.c is a legitimate reference here**, even though encoder.c is
// the contract. test_encoder_fast.c proves them byte-identical layer by layer on
// the host, and m5b.c proved it again on this silicon; using the fast one costs
// 3.4 s per boot instead of 31.8 s. The optional accumulator sweep below still
// goes through fgx_conv_acc() directly, so the strict path is available when a
// CRC actually disagrees.
//
// **Only the bitstream crosses USB**, exactly as in m6.c and for the same
// reason: the MCU already has weights.bin and testvec.bin in flash, so a passing
// run proves the FPGA agrees with encoder.c rather than with host/m7.py. It also
// keeps RTL iteration strap-free.
//
// **The blocking is computed, never transcribed.** gemm_plan.c sweeps the legal
// (P, QG, Cb) triples for each layer against the descriptor in the model blob.
// firmware/test_gemm_plan.c has already asserted, on the host and against this
// same weights.bin, that the chosen blocks tile every output tensor exactly once
// - which is the one correctness property bit-exact accumulators cannot
// establish, since every block can be right while the set of them misses a
// corner.
//
// ---------------------------------------------------------------------------
// M8c moved the engine out and left the harness
//
// The pool, run_block(), the weight cache, the scatter, the core-1 callbacks and
// the per-layer block loop are now frame.c, because m8.c runs the same frame in
// a loop and two copies of this project's only concurrent code is not a thing to
// have. What is left here is what m7 *is*: the six-rung ladder, the two link
// configurations, the MCU reference pass they are scored against, and the
// tables. Nothing about the measurement changed - which is checkable, and was
// checked, because this binary compares every layer CRC and all 512 embedding
// floats against a reference it computes in the same boot.

#include <stdio.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/vreg.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"

#include "encoder.h"
#include "encoder_fast.h"
#include "fpga_config.h"
#include "frame.h"
#include "gemm_host.h"
#include "gemm_plan.h"
#include "worker.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// ---------------------------------------------------------------------------
// Where a finished or failed run goes to sit.
//
// This used to be `while (true) tight_loop_contents();` and that cost a real
// unplug every time. The intended escape was the 1200-baud CDC touch - open the
// port, drop DTR, and the RP2350 reboots into BOOTSEL - which stops working the
// moment m7.c parks, and it took four failed variants of the host script to
// work out that the firmware was the problem rather than the incantation.
//
// M7i: the touch never worked on this board at all, parked or running, and the
// escape that does is `picotool reboot -f -u` - a different transport, the USB
// vendor reset interface, handled below the application entirely. Everything
// below stays because it costs nothing and works without picotool installed.
//
// So park by *asking* instead. 'B' on stdin reboots to BOOTSEL, 'R' restarts
// the run from the top.
//
// M7h: and then by *not* asking, because asking was not enough. A board parked
// on the M7g firmware went deaf - it stayed enumerated as PID 0x0009 with the
// same ioreg node, but it answered neither 'B' on stdin nor the 1200-baud
// touch, and it took a physical replug. Whatever wedged it, the lesson is
// structural rather than diagnostic: every exit from park() went through
// stdin, so a park() that cannot read stdin has no exit at all.
//
// The watchdog is the exit that depends on nothing. It is armed here and
// deliberately never updated, so eight seconds after a run finishes the board
// is back at the bitstream prompt - which is the state that demonstrably does
// answer the 1200-baud touch, and the state a fresh host/m7.py wants anyway.
// The report is already printed and already in the host's log by then.
//
// 'B' and 'R' stay because they are free and, when stdio is healthy, faster.
static void park(void)
{
    printf("\nparked - 'B' for BOOTSEL, 'R' to re-run; otherwise this reboots\n"
           "         to the bitstream prompt in 8 s\n");
    stdio_flush();

    // 8 s: the RP2350 load register counts microseconds in 24 bits, so this is
    // near the ceiling, and it is long enough that a keystroke aimed at the
    // line above still lands.
    watchdog_enable(8000, 1);

    for (;;) {
        const int c = getchar_timeout_us(200000);
        if (c == 'B' || c == 'b') { printf("bootsel\n"); sleep_ms(50);
                                    reset_usb_boot(0, 0); }
        if (c == 'R' || c == 'r') { printf("reboot\n");  sleep_ms(50);
                                    watchdog_reboot(0, 0, 0); }
        // No watchdog_update(). Expiring is the point.
    }
}

// The MCU reference: a CRC32 of every layer's output tensor and the 512-d
// embedding, computed once at start-up with encoder_fast.c and compared against
// on every rung of the ladder. This is the whole reason a botched refactor of
// the engine cannot pass silently.
static uint32_t ref_crc[FT_MAX_LAYERS];
static float    ref_embed[1024];
static float    got_embed[1024];

// ---------------------------------------------------------------------------
// The outcome of a frame. File-scope because run_frame() runs twice and main()
// reports once, after both.
static uint32_t frame_us;
// Every mode's frame time, kept so each row can quote its ratio against the one
// above it and main() can print the ladder as a ladder. Indexed by the mode
// number below; 0 is unset until that mode has run.
static uint32_t mode_ms[6];
// Mirrors the engine's ft_set_sweep(), because the sweep changes what the rows
// below *mean* - the frame time they print is twenty times a frame - and the
// reporting has to say so.
static bool     do_sweep;
// M15's mode, mirrored here for the same reason do_sweep is: it changes what
// every row below *means*. At rq on the tile requantizes and DRAIN returns one
// byte per accumulator instead of four, so the wire and decode rows are not
// comparable across it and the report has to say which side it is on.
static bool     do_rq;
static int      bad_layers, bad_embed;
static uint8_t  worst_status;
static gh_prof_t pf;
static uint32_t moved;

// One frame on the tile, in one of the six modes.
//
// It exists as a function rather than a loop body because M7d's whole claim is
// about *overlap*, and the only honest way to report that on this board is
// inside one boot: M7a made the same argument for its decode and m5b.c made it
// for the SMLAD kernel, since a ratio quoted across two builds of this firmware
// is not a measurement. All four calls execute identical work in an identical
// order; the flags decide only *where* the CPU's half of it runs - after the
// wire, inside the DMA window, or on the other core.
//
// M7e makes that argument load-bearing rather than tidy. A concurrency bug is
// visible or not depending on timing, and two builds of this firmware differ in
// exactly that; one binary running all six modes back to back is the only way
// the dual-core rows are comparable to the single-core ones at all.
//
// The outcome goes into the file-scope run state above, because main() reports
// it after every mode has run.
static void run_frame(const fgx_model_t *m, const void *image, uint32_t nconv,
                      uint32_t tot_blk, long plan_bytes, uint32_t ref_ms,
                      bool pipe, bool cbuild, bool cscat, bool cdec, bool cprio)
{
    ft_set_mode(pipe, cbuild, cscat, cdec, cprio);

    // The flags are a ladder, not a bitmask: each mode adds one thing to the one
    // before it, so `mode` is how far up we are and mode_ms[mode-1] is always the
    // row this one should be compared against.
    const int mode = !pipe ? 0 : !cbuild ? 1 : !cscat ? 2 : !cdec ? 3
                                                          : !cprio ? 4 : 5;
    static const char *const modename[6] = {
        "serialized (the M7c path)",
        "pipelined, one core (the M7d path)",
        "pipelined + build on core 1",
        "pipelined + build and scatter on core 1",
        "pipelined + build, scatter and DRAIN decode on core 1",
        "the same, with core 1's queue split into two priorities",
    };

    printf("\nfpga      : the same frame, %u blocks on the T8, %s%s\n",
           (unsigned)tot_blk, modename[mode],
           do_rq ? "  [rq: codes on the wire]" : "");
    printf("  %-2s %6s %6s %9s %9s %9s %9s %8s %s\n",
           "L", "blocks", "passes", "ms", "build ms", "wire ms", "stall ms",
           "status", "crc");

    ft_frame_reset();
    bad_layers = 0;

    {
        const void *src = image;
        void *dst = ft_arena();

        for (uint32_t i = 0; i < nconv; i++) {
            const ft_err_t r = ft_layer(i, src, dst);

            // A fault is a plan, a geometry or an accumulator, and none of them
            // is something another mode would get right. m7.c used to park()
            // inside the block loop for these; the engine hands the sentence
            // back instead, which is what lets m8 keep running.
            if (r.fault) {
                printf("\nRESULT : FAIL - %s\n", r.fault);
                park();
            }

            const ft_stat_t *s = ft_stat(i);
            const gh_err_t e = r.link;

            if (e) {
                printf("  %-2u %6d %6u %9s %9s %9s %9s %8s link error: %s\n",
                       (unsigned)i, s->blocks, (unsigned)s->passes, "-", "-",
                       "-", "-", "-", gh_strerror(e));
                // Everything gh_xfer_arm() was handed. A stall is the driver
                // disagreeing with itself about how many words a transaction
                // is, so the counts are the whole diagnosis and printing them
                // here is the difference between one reflash and several.
                if (e == GH_ERR_STALL) {
                    const gh_stall_t *st = gh_last_stall();
                    printf("     stalled: cmd %02x  w %u  len %u  nbuf %u  "
                           "ncap %u\n"
                           "     armed  : head %u + tail %u tx words, %u rx "
                           "words\n"
                           "     left   : rx %u  tx %u  tx2 %u   still busy: "
                           "%s%s%s\n",
                           st->cmd, st->width, (unsigned)st->len,
                           (unsigned)st->nbuf, (unsigned)st->ncap,
                           (unsigned)st->head_words, (unsigned)st->tail_words,
                           (unsigned)st->rx_words, (unsigned)st->rx_left,
                           (unsigned)st->tx_left, (unsigned)st->tx2_left,
                           (st->busy & 1u) ? "rx " : "",
                           (st->busy & 2u) ? "tx " : "",
                           (st->busy & 4u) ? "tx2" : "");
                }
                bad_layers++;
                break;
            }

            // The CRC is deliberately outside the engine's timed region: it is
            // ~3 ms a layer of pure checking that no shipped frame would pay,
            // and the number this milestone exists to report is the frame time.
            const fgx_desc_t *d = &m->desc[i];
            const size_t n = (size_t)d->cout * d->oh * d->ow
                             * (fgx_emits_float(m, i) ? sizeof(float) : 1u);
            const uint32_t crc = ft_crc32((const uint8_t *)dst, n);
            const bool ok = (crc == ref_crc[i]);
            if (!ok) bad_layers++;

            printf("  %-2u %6d %6u %9.0f %9.1f %9.1f %9.1f %8s %s\n",
                   (unsigned)i, s->blocks, (unsigned)s->passes,
                   s->us / 1000.0, s->us_build / 1000.0,
                   s->us_wire / 1000.0, s->us_stall / 1000.0,
                   (s->status & (GH_ST_UNDERRUN | GH_ST_BADFRAME)) ? "STICKY"
                                                                   : "ok",
                   ok ? "match" : "MISMATCH");
            stdio_flush();

            src = dst;
            dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                              : (void *)ft_arena();
        }

        if (!bad_layers) ft_pool_head((const float *)src, got_embed);
    }

    worst_status = ft_status();
    frame_us = ft_frame_us();

    moved = gh_bytes();
    gh_prof(&pf);

    // --- the embedding ------------------------------------------------------
    bad_embed = 0;
    if (!bad_layers)
        for (uint32_t o = 0; o < m->hdr->embed_dim; o++)
            if (got_embed[o] != ref_embed[o]) bad_embed++;

    // --- where the time went ------------------------------------------------
    // The scientific point of both milestones. M7c's version of this table said
    // the per-byte rates hold across eight shapes but that 304 ms of the frame
    // was in no window at all; the two rows M7d adds - stage and overlap - are
    // the ones that close it, so this table now has to account for the whole
    // frame rather than 86% of it.
    // Read the rate rather than assert it. This line said "150 MHz sys / 75 MHz
    // link" as a literal for as long as that was the only rate the firmware
    // could be in, which stopped being true the moment the config C ladder grew
    // rungs above 150 - and a hardcoded label on a table of measured ns/clk is
    // the exact thing that makes an overclock look like a miracle.
    const uint32_t sys_khz = clock_get_hz(clk_sys) / 1000u;
    printf("\n  phase breakdown at %u MHz sys / %.1f MHz link, whole frame\n",
           (unsigned)(sys_khz / 1000u), sys_khz / 2000.0);
    printf("  %-14s %10s %10s %10s %10s\n",
           "phase", "ms", "MB", "ns/B", "M7c");
    printf("  %-14s %10.0f %10.3f %10.1f %10s\n", "wire (elapsed)",
           pf.us_wire / 1000.0, moved / 1048576.0,
           moved ? pf.us_wire * 1000.0 / moved : 0.0, "918");
    // Serialized mode runs the same callback *after* the wait, so its cost is
    // beside us_wire rather than inside it. Same number, different meaning, and
    // labelling both "of that" would overstate the serialized row by 482 ms.
    printf("  %-14s %10.0f %10s %10s %10s\n",
           pipe ? "  of that, CPU" : "  CPU, after it",
           pf.us_overlap / 1000.0, "-", "-", "0");
    printf("  %-14s %10.0f %10.3f %10.1f %10s\n", "crc tx",
           pf.us_crc / 1000.0, pf.tx_hashed / 1048576.0,
           pf.tx_hashed ? pf.us_crc * 1000.0 / pf.tx_hashed : 0.0, "268");
    printf("  %-14s %10.0f %10.3f %10.1f %10s\n", "decode",
           pf.us_decode / 1000.0, pf.rx_body / 1048576.0,
           pf.rx_body ? pf.us_decode * 1000.0 / pf.rx_body : 0.0, "166");
    printf("  %-14s %10.0f %10s %10s %10s\n", "stage",
           pf.us_stage / 1000.0, "-", "-", "~150");
    printf("  %-14s %10.0f %10s %10s %10s\n", "locate",
           pf.us_locate / 1000.0, "-", "-", "28");
    printf("  %u transactions, offset hints %u hit / %u miss\n",
           (unsigned)pf.xfers, (unsigned)pf.hint_hit, (unsigned)pf.hint_miss);

    // M7f. The wire, per command, in link clocks rather than bytes.
    //
    // Bytes are not comparable across the two configurations and clocks are:
    // a byte at width 3 is a third of a clock, and RUN and DRAIN send exactly
    // the same *clocks* either way. So ns/clk is what says whether the wide
    // link is slower per clock or merely paying a fixed cost per transaction -
    // the first hardware run left 869 ms of wire where 657 was projected, and
    // those are the only two explanations that fit.
    //
    // M7g added the two hint columns to the same table rather than a second
    // one. A miss is a full rescan of that command's capture, so miss count
    // times capture size is locate's whole bill, and having both on one row is
    // the difference between reading the answer and doing the arithmetic.
    {
        // Slot 0 is RQP, not a hole: gemm_host.c indexes by `cmd & 7` and
        // GW_CMD_RQP is 0x08. See the HINT_N note there.
        static const char *const nm[8] = {
            "RQP", "CFG", "ACT", "WGT", "RUN", "DRAIN", "NOP", "?7",
        };
        printf("  %-14s %10s %10s %10s %10s %10s %10s\n",
               "  by command", "ms", "Mclk", "ns/clk", "count",
               "hint hit", "miss");
        for (unsigned c = 0; c < 8u; c++) {
            if (!pf.n_cmd[c]) continue;
            printf("  %-14s %10.0f %10.2f %10.2f %10u %10u %10u\n", nm[c],
                   pf.us_cmd[c] / 1000.0, pf.clk_cmd[c] / 1e6,
                   pf.clk_cmd[c] ? pf.us_cmd[c] * 1000.0 / pf.clk_cmd[c] : 0.0,
                   (unsigned)pf.n_cmd[c], (unsigned)pf.hint_hit_cmd[c],
                   (unsigned)pf.hint_miss_cmd[c]);
        }
    }

    // The deferred DRAINs are counted separately for a reason that is not
    // bookkeeping: gh_prof_t is core 0's, and two cores incrementing one counter
    // lose increments. The rows above are therefore what *core 0* paid, which is
    // the question the ladder is asking, and this row is what moved off it.
    {
        gh_dprof_t dp;
        gh_dprof(&dp);
        if (dp.calls)
            printf("  off core 0   : %u DRAIN decodes, locate %u ms + decode "
                   "%u ms = %u ms, %.3f MB, hints %u hit / %u miss\n",
                   (unsigned)dp.calls, (unsigned)(dp.us_locate / 1000u),
                   (unsigned)(dp.us_decode / 1000u),
                   (unsigned)((dp.us_locate + dp.us_decode) / 1000u),
                   dp.rx_body / 1048576.0,
                   (unsigned)dp.hint_hit, (unsigned)dp.hint_miss);
    }

    // --- and what the second core did with it ---------------------------------
    // The two numbers M7e turns on. `busy` is core 1's own occupancy and `stall`
    // is what core 0 lost waiting for it, so they answer different questions:
    // busy near the frame time says core 1 is the new bottleneck, and stall near
    // zero says the handoff is free. "The road to 280 ms" predicted ~140 ms of stall from
    // scatter arriving in lumps; this is the line that settles it, and M7d's
    // 340 ms miss is why it is measured rather than modelled.
    if (mode >= 2) {
        const uint32_t busy = w1_busy_us(), stall = w1_stall_us();
        printf("\n  core 1       : %u jobs, busy %u ms (%.0f%% of the frame), "
               "core 0 stalled %u ms\n",
               (unsigned)w1_jobs(), (unsigned)(busy / 1000u),
               frame_us ? busy * 100.0 / frame_us : 0.0,
               (unsigned)(stall / 1000u));
        // The one line that says whether the split did what it was for: the
        // low-priority work has to fit in the gaps between high-priority jobs,
        // and if it stops fitting the symptom is stall on the line above.
        if (cprio)
            printf("  priorities   : W1_HI %u ms (builds), W1_LO %u ms "
                   "(decode + scatter, in the gaps)\n",
                   (unsigned)(w1_busy_us_q(W1_HI) / 1000u),
                   (unsigned)(w1_busy_us_q(W1_LO) / 1000u));
    } else {
        printf("\n  core 1       : idle - build %u ms and scatter %u ms ran on "
               "core 0\n",
               (unsigned)(ft_us_build_frame() / 1000u),
               (unsigned)(ft_us_scatter() / 1000u));
    }

    // M17. Which of the two callbacks on W1_HI the build time belongs to. The
    // merged figure above has read as "the weight builder is immovable" twice
    // now - once for M14's int8 control, once for Stage 1's wider store - and
    // both times the reading rested on an unmeasured assumption about this
    // split. Printed for every mode, because in the core-0 modes it is the same
    // work on the other CPU and the two should agree.
    {
        const uint32_t tot = ft_us_build_frame() / 1000u;
        const uint32_t wgt = ft_us_build_wgt_frame() / 1000u;
        printf("  build split  : gb_weights %u ms, gb_strip %u ms, of %u ms "
               "total%s\n", (unsigned)wgt, (unsigned)(tot - wgt), (unsigned)tot,
               tot ? "" : "  (nothing built - every stream came from the cache)");
    }

    // M7h. The interesting number is not the ratio but which layers supply it:
    // 43% of the *bytes* is the ceiling this cache can reach, because layers 5
    // to 7 have one position block each and nothing to reuse. A figure below
    // that means the key is being evicted by something it should not be.
    {
        uint32_t built, cached;
        uint64_t bytes_all, bytes_hit;
        ft_wgt_stats(&built, &cached, &bytes_all, &bytes_hit);
        printf("\n  weight build : %u of %u passes built, %u served from the "
               "cache (%.0f%% of bytes)\n",
               (unsigned)built, (unsigned)(built + cached), (unsigned)cached,
               bytes_all ? 100.0 * (double)bytes_hit / (double)bytes_all : 0.0);
    }

    printf("\n  bytes moved  : %.3f MB measured, %.3f MB projected (%+.1f%%)\n",
           moved / 1048576.0, plan_bytes / 1048576.0,
           plan_bytes ? (moved - (double)plan_bytes) * 100.0 / plan_bytes : 0.0);
    printf("  frame        : %u ms, %s%s\n",
           (unsigned)(frame_us / 1000u), modename[mode],
           do_sweep ? " + the accumulator sweep, so not a frame time" : "");

    // The sweep pass runs the topmost mode's flags but is not that mode's
    // measurement, so it must not overwrite the row or be quoted as a ratio.
    if (do_sweep) { stdio_flush(); return; }

    mode_ms[mode] = frame_us / 1000u;
    // Against the rung below, which is the honest comparison for one step, and
    // against mode 0, which is the number the ladder in docs/milestones.md quotes.
    if (mode > 0 && mode_ms[mode - 1] && mode_ms[mode])
        printf("  vs the row up: %u -> %u ms, %.2fx, same boot\n",
               (unsigned)mode_ms[mode - 1], (unsigned)mode_ms[mode],
               (double)mode_ms[mode - 1] / mode_ms[mode]);
    if (mode > 1 && mode_ms[0] && mode_ms[mode])
        printf("  vs serialized: %u -> %u ms, %.2fx\n",
               (unsigned)mode_ms[0], (unsigned)mode_ms[mode],
               (double)mode_ms[0] / mode_ms[mode]);
    printf("  MCU baseline : %u ms this boot (encoder_fast), 3358 ms in M5b\n",
           (unsigned)ref_ms);
    if (ref_ms && frame_us)
        printf("  vs the MCU   : %.2fx\n", ref_ms * 1000.0 / frame_us);
    stdio_flush();
}

// ---------------------------------------------------------------------------
// One link configuration, end to end: the six-rung mode ladder and then, if it
// stayed bit-exact all the way up, the accumulator sweep.
//
// A function rather than main()'s tail because M7f runs it twice in one boot -
// once over one forward data line and once over three. Same argument run_frame()
// makes about the six modes, one level up: the third data line is a *ratio*, and
// a ratio quoted across two boots of two builds is not a measurement. Here both
// numbers come from the same image, the same plan, the same reference CRCs and
// the same 174 blocks; the only thing that differs between the two calls is
// which pins carry the bytes.
//
// M15 runs it twice again, and the argument is the one above with a different
// noun. `rq` is a CFG bit, so both wire formats exist in one bitstream and both
// can be measured against the same reference CRCs in the same boot - which is
// the only way the DRAIN saving is a measurement rather than two runs
// subtracted. rq off also stays the standing proof that nothing regressed: it
// is bit-for-bit the path M14 shipped.
//
// The sweep is a separate argument because ft_set_sweep() forces int32 for its
// own reasons - it compares accumulators, and at rq there are none on the wire
// to compare - so running it on both passes would cost 25 s to check the same
// thing twice.
typedef struct {
    uint32_t ms[6];      // each rung's frame time; 0 for a rung that never ran
    uint32_t swept;      // blocks checked against gb_golden(), 0 if skipped
    int      bad_layers;
    int      bad_embed;
    uint8_t  status;     // sticky link faults, OR'd over every frame
} cfg_result_t;

static void run_config(const fgx_model_t *m, const void *image, uint32_t nconv,
                       uint32_t tot_blk, long plan_bytes, uint32_t ref_ms,
                       bool rq, bool sweep, cfg_result_t *r)
{
    do_rq = rq;
    ft_set_rq(rq);
    // The ladder, cheapest mode first, each stopping the run if it broke
    // bit-exactness so the failing rung is the last thing printed. Modes 2 and 3
    // are the only ones where a wrong answer can be intermittent, which is
    // exactly why they run after two modes that have already proved this boot's
    // bitstream, plan and reference agree.
    static const struct { bool pipe, cbuild, cscat, cdec, cprio; } ladder[6] = {
        { false, false, false, false, false },
        { true,  false, false, false, false },
        { true,  true,  false, false, false },
        { true,  true,  true,  false, false },
        { true,  true,  true,  true,  false },
        { true,  true,  true,  true,  true  },
    };

    memset(r, 0, sizeof *r);
    // run_frame() reads mode_ms[] to print each row's ratio against the one
    // above it, so the second configuration has to start from a blank ladder or
    // its rung 1 would be compared against the *other* configuration's rung 0.
    memset(mode_ms, 0, sizeof mode_ms);

    for (int k = 0; k < 6; k++) {
        run_frame(m, image, nconv, tot_blk, plan_bytes, ref_ms,
                  ladder[k].pipe, ladder[k].cbuild, ladder[k].cscat,
                  ladder[k].cdec, ladder[k].cprio);
        r->status |= worst_status;
        if (bad_layers || bad_embed) break;
    }
    memcpy(r->ms, mode_ms, sizeof r->ms);
    r->bad_layers = bad_layers;
    r->bad_embed  = bad_embed;
    if (r->bad_layers || r->bad_embed) return;
    if (!sweep) return;

    // Mode 5 again, every accumulator checked. Untimed, and the frame time it
    // prints should be ignored: gb_golden() runs the naive kernel 174 times.
    // What it proves is the thing four matching CRCs do not - that no
    // *individual* block's accumulators are wrong, which is the shape a strip
    // built on the wrong core at the wrong moment would take. It runs last so
    // that a failure here still leaves six timed rows on the console rather
    // than replacing them.
    printf("\nsweep     : mode 5 again, every accumulator against "
           "gb_golden(). ~25 s, and the ms below are meaningless.\n");
    stdio_flush();
    do_sweep = true;
    ft_set_sweep(true);
    run_frame(m, image, nconv, tot_blk, plan_bytes, ref_ms,
              true, true, true, true, true);
    ft_set_sweep(false);
    do_sweep = false;
    r->status     |= worst_status;
    r->swept       = ft_sweep_blocks();
    r->bad_layers  = bad_layers;
    r->bad_embed   = bad_embed;
    printf("  %u of %u blocks swept, every accumulator exact\n",
           (unsigned)r->swept, (unsigned)tot_blk);
}

int main(void)
{
    set_sys_clock_khz(150000, true);
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== M7f: a whole frame through the Trion T8, six ways ===\n\n");
    printf("waiting for a bitstream on USB CDC (host/m7.py)");
    stdio_flush();

    const size_t blen = ft_recv_bitstream(0);
    if (!blen) {
        printf("\nRESULT : FAIL - no usable bitstream\n");
        park();
    }

    fpga_config_pins_init();
    const int cerr = fpga_configure(ft_arena(), blen);
    printf("configure : %s   CDONE=%d nSTATUS=%d\n",
           fpga_strerror(cerr), fpga_done(), fpga_nstatus());
    if (cerr != FPGA_OK) {
        printf("\nRESULT : FAIL - the tile never came up, so nothing below would mean anything\n");
        park();
    }

    fpga_release_link_pins();
    gemm_host_init();

    // The other CPU, started once and left running for the whole boot. Modes 0
    // and 1 post nothing, so it costs them a core parked in __wfe() - which is
    // what it was doing anyway for every measurement in this project so far.
    w1_start();

    // Which DMA sniffer setting reproduces gemm_link.v's CRC-32 is probed at
    // init rather than taken from the datasheet, so the answer is worth
    // printing: if it says "software" the run is still correct, just 268 ms
    // slower, and that is the difference this milestone is measuring.
    {
        uint32_t md; bool rv, iv;
        if (gh_crc_sniffer(&md, &rv, &iv))
            printf("crc       : DMA sniffer, calc=%u out_rev=%d out_inv=%d\n",
                   (unsigned)md, (int)rv, (int)iv);
        else
            printf("crc       : software - no sniffer mode matched gw_crc()\n");
    }

    // --- the model, and the plan --------------------------------------------
    fgx_model_t m;
    if (!fgx_open(&m, fgx_weights, (size_t)(fgx_weights_end - fgx_weights))) {
        printf("\nRESULT : FAIL - weights.bin is malformed\n");
        park();
    }

    // Sizes the pool has to satisfy, and a blocking for every convolution. Both
    // are the engine's business now, and both refuse with a sentence rather than
    // a code, so this call site is one branch.
    const char *why = ft_init(&m);
    if (why) {
        printf("\nRESULT : FAIL - %s\n", why);
        park();
    }

    const uint32_t nlayer = ft_nlayer();      // convs + the linear head
    const uint32_t nconv  = ft_nconv();       // everything the tile can run

    printf("model     : %u layers, %u-d embedding, %u B/buffer, %u B im2col\n",
           (unsigned)nlayer, (unsigned)m.hdr->embed_dim,
           (unsigned)m.scratch, (unsigned)fgx_fast_col_bytes(&m));

    // The blocking gp_choose() swept, rather than a table anybody typed. It runs
    // per boot so this firmware survives a re-export with different channel
    // counts; printing it is how a wrong export is caught before 174 blocks of
    // arithmetic are laid on top of it.
    printf("\nblocking  : swept from desc[], not transcribed\n");
    printf("  %-2s %-16s %4s %4s %4s %6s %6s %9s\n",
           "L", "shape", "P", "Q", "Cb", "blocks", "passes", "kB");

    long plan_bytes = 0, plan_us = 0;
    // The same plan priced at three forward data lines, for the configuration C
    // rows to be read against. Larger in bytes and smaller in time, which is the
    // whole shape of the finding: RUN's and DRAIN's bytes are clocks.
    long plan_bytes3 = 0, plan_us3 = 0;
    // M15. frame.c holds two plans and ft_plan() selects between them, so the rq
    // totals are swept rather than derived: the rq plan is a different set of
    // transactions - RQP appears, DRAIN shrinks fourfold - and not the int32 one
    // scaled. The blocking is the same in both, which is the point of gb_geom()
    // falling back to int32 DRAIN rather than re-blocking when a layer cannot be
    // requantized, so tot_blk and tot_pass are swept once from the int32 plan.
    long rq_bytes = 0, rq_us = 0, rq_bytes3 = 0, rq_us3 = 0;
    uint32_t tot_blk = 0, tot_pass = 0;
    for (uint32_t i = 0; i < nconv; i++) {
        const gp_layer_t *pl = ft_plan(i);
        const fgx_desc_t *d = &m.desc[i];
        printf("  %-2u %3ux%3ux%-3u ->%-4u %4d %4d %4d %6d %6d %9ld\n",
               (unsigned)i, d->h, d->w, d->cin, d->cout,
               pl->base.P, pl->Q, pl->base.Cb,
               pl->nblocks, pl->nblocks * pl->npass,
               pl->cost.bytes / 1024);
        plan_bytes  += pl->cost.bytes;
        plan_us     += pl->cost.us_wire + pl->cost.us_cpu;
        plan_bytes3 += pl->cost3.bytes;
        plan_us3    += pl->cost3.us_wire + pl->cost3.us_cpu;
        tot_blk     += (uint32_t)pl->nblocks;
        tot_pass    += (uint32_t)(pl->nblocks * pl->npass);
    }
    // The ms here are quoted at 150 MHz sys / 75 MHz link, because that is the
    // rate M7a calibrated GP_NS_* at and the rate this table is printed at. Say
    // so, because the config C ladder can land at 220 and then these ms are
    // ~47% high against that frame for reasons that have nothing to do with the
    // model. kB are clock-free and comparable anywhere.
    printf("  total: %u blocks, %u passes, %ld kB and %ld ms projected "
           "(ms at 150 MHz sys / 75 MHz link, the rate GP_NS_* was calibrated "
           "at; kB are rate-free)\n",
           (unsigned)tot_blk, (unsigned)tot_pass, plan_bytes / 1024,
           plan_us / 1000);
    printf("  the same plan at three data lines: %ld kB and %ld ms - more "
           "bytes, less time, because RUN's and DRAIN's bytes are clocks\n",
           plan_bytes3 / 1024, plan_us3 / 1000);

    ft_set_rq(true);
    for (uint32_t i = 0; i < nconv; i++) {
        const gp_layer_t *pl = ft_plan(i);
        rq_bytes  += pl->cost.bytes;
        rq_us     += pl->cost.us_wire + pl->cost.us_cpu;
        rq_bytes3 += pl->cost3.bytes;
        rq_us3    += pl->cost3.us_wire + pl->cost3.us_cpu;
    }
    ft_set_rq(false);
    printf("  at rq: %ld kB / %ld ms on one data line, %ld kB / %ld ms on "
           "three - DRAIN carries one byte per accumulator instead of four\n",
           rq_bytes / 1024, rq_us / 1000, rq_bytes3 / 1024, rq_us3 / 1000);

    // --- pass 1: the reference, all on the MCU ------------------------------
    // Ping-pong arena <-> scratch_b. The FPGA pass below repeats exactly this
    // alternation, so layer i writes the same buffer in both passes and the CRCs
    // are of tensors at the same addresses.
    // The camera first; the flash vector is what happens when there isn't one.
    // See ft_acquire() in frame.c for why swapping the input leaves every check
    // below intact. `void *` rather than `int8_t *` is the honest type and the one
    // m6.c uses for the same pointer: conv0's codes are signed here
    // (model/quantize.py:134 gives unsigned_input only to i > 0), but
    // fgx_conv_fast() dispatches on d->unsigned_in and a typed pointer would be
    // asserting something this call site does not know.
    //
    // testvec.bin is magic | n_img | code_size, then the records, so image 0's
    // codes start 12 bytes in.
    const void *image = ft_acquire(m.hdr->in_scale);
    const bool live = image != NULL;
    if (!image) image = (const void *)(fgx_testvec + 12);

    printf("\nreference : encoder_fast, %u convs + pool/head", (unsigned)nconv);
    stdio_flush();
    uint64_t t0 = time_us_64();
    {
        const void *src = image;
        void *dst = ft_arena();
        for (uint32_t i = 0; i < nconv; i++) {
            const bool as_float = fgx_emits_float(&m, i);
            fgx_conv_fast(&m, &m.desc[i], src, dst, as_float, ft_col(), true);
            const size_t n = (size_t)m.desc[i].cout * m.desc[i].oh * m.desc[i].ow
                             * (as_float ? sizeof(float) : 1u);
            ref_crc[i] = ft_crc32((const uint8_t *)dst, n);
            src = dst;
            dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                              : (void *)ft_arena();
        }
        ft_pool_head((const float *)src, ref_embed);
    }
    const uint32_t ref_ms = (uint32_t)((time_us_64() - t0) / 1000u);
    printf("  (%u ms)\n", (unsigned)ref_ms);

    // --- pass 2 and 3: configuration A, one forward data line ---------------
    printf("\nlink      : configuration A, %u forward data line, "
           "return on G3\n", gh_width());
    cfg_result_t ra, ra_rq;
    bool ran_a_rq = false;
    run_config(&m, image, nconv, tot_blk, plan_bytes, ref_ms, false, true, &ra);

    // M15, the same six rungs with the tile doing the epilogue. Second rather
    // than first on purpose: rq off is the path M14 shipped, so if it is not
    // bit-exact the fault is in something this milestone did not touch, and
    // there is no sense measuring a byte DRAIN against a broken int32 one. It
    // skips the sweep because ft_set_sweep() forces int32 anyway - there are no
    // accumulators on the wire at rq to compare - so running it twice would
    // spend 25 s checking the same thing.
    if (!ra.bad_layers && !ra.bad_embed) {
        printf("\nlink      : configuration A at rq, the same %u blocks - the "
               "tile requantizes and DRAIN returns one byte per accumulator\n",
               (unsigned)tot_blk);
        run_config(&m, image, nconv, tot_blk, rq_bytes, ref_ms, true, false,
                   &ra_rq);
        ran_a_rq = true;
    }

    // --- pass 4 and 5: configuration C, three forward data lines ------------
    //
    // A second bitstream over the same USB CDC channel, into the same arena -
    // which is free again, because the reference CRCs and the reference
    // embedding were computed in pass 1 and live in their own statics, and every
    // frame since has rebuilt its activations from flash.
    //
    // Optional, and silently so. Configuration C needs the PIN2 <-> PIN17
    // jumper fitted *and* a bitstream built from gemm_top_wide.v; a board
    // without the jumper is a perfectly good board, and a run that has already
    // produced six timed rows and a clean sweep should end with them rather than
    // with a timeout. So this asks, waits half a minute, and moves on.
    //
    // It runs after configuration A rather than before for the same reason the
    // ladder is a ladder: A is the known-good path, and if the wide bitstream
    // returns a wrong tensor the console already holds a complete correct run to
    // compare it against.
    cfg_result_t rc, rc_rq;
    bool ran_c = false, ran_c_rq = false;
    uint32_t c_khz = 0;
    if (!ra.bad_layers && !ra.bad_embed) {
        printf("\nSEND-WIDE-BITSTREAM\n");
        printf("config C  : send rtl/build/gemm_top_wide.hex now for the same "
               "frame over three forward data lines. 30 s, then this run "
               "finishes with configuration A alone.");
        stdio_flush();

        const size_t wlen = ft_recv_bitstream(30);
        if (!wlen) {
            printf("config C  : no second bitstream - reporting "
                   "configuration A only\n");
        } else {
            // Reconfiguring mid-run is safe by construction: claim_spi_pins()
            // takes GPIO1/2/3 back from PIO, and the PIO state machine has been
            // disabled since the last gh_xfer_wait(). GPIO22 is still an input
            // at this point, so the jumper is not driving CCK's replacement
            // while the part is being loaded.
            fpga_config_pins_init();
            const int werr = fpga_configure(ft_arena(), wlen);
            printf("configure : %s   CDONE=%d nSTATUS=%d\n",
                   fpga_strerror(werr), fpga_done(), fpga_nstatus());
            if (werr != FPGA_OK) {
                printf("config C  : the wide bitstream did not configure - "
                       "reporting configuration A only\n");
            } else {
                fpga_release_link_pins();
                gh_set_width(3);

                // The wide build misses timing harder than the narrow one -
                // P&R puts gemm_top_wide at 58.630 MHz against gemm_top's
                // 62.449 - and this board has always run bit-exact at 75 MHz
                // anyway. Which of those two facts wins is not knowable from
                // here, and finding out must not cost a strap: if the top rung
                // is too fast, step the system clock down and ask again. A rung
                // that is not bit-exact aborts run_config() immediately, so a
                // wrong guess costs about two seconds rather than a whole
                // ladder.
                //
                // A configuration C that only closes below 75 MHz is still a
                // result - it says the jumper needs timing closure before it is
                // worth anything - but it is not a *comparison*, because a
                // slower clock slows the CPU half of the frame too. The summary
                // suppresses the ratio in that case rather than printing a
                // number that flatters configuration A.
                //
                // The three rungs above 150000 were added after m6's sweep came
                // back 2048/2048 bit-exact at a 110 MHz link, three runs
                // running, which is 1.9x the static analyser's 56.654 MHz and
                // so far outside this repo's measured 1.3-1.4x pessimism that
                // it wants a frame to believe. m6 moves 39 kB through one block
                // in 28 transactions; this ladder moves every layer and the
                // embedding in about 1,856, so a rung that survives it is an
                // operating point rather than a lucky path on a cold board.
                //
                // Note what the descent costs if a top rung passes: the 150 MHz
                // row never runs, so this boot has no same-boot configuration C
                // baseline at the rate every previous milestone was measured
                // at. That comparison lives in the M16 logs instead. Config A,
                // which decides the verdict, is unaffected - it runs before
                // this block and always at 150.
                // 240 and 280 are appended on the same evidence, one audit
                // later: 220000 had become the top row, so "bit-exact at 110
                // MHz" again meant only that nothing above it had been asked.
                // m6 says 240 and 280 are 2048/2048 over three boots.
                //
                // **260 is deliberately absent, and it is not a typo.** It
                // fails m6 - "no response preamble" - in all three boots, while
                // both its neighbours pass, and the reason appears to be the
                // PLL rather than the link. check_sys_clock_khz() counts fbdiv
                // *down* from 320, so it returns the highest VCO that works:
                // 240 gets 1440 MHz, 260 gets 1560, and 280 gets 840, because
                // no higher multiple of 280 lands on an integer fbdiv from a 12
                // MHz reference. 1560 is within 2.5% of the 1600 MHz VCO
                // maximum and it is the only rung sitting there. So the ladder
                // is not monotonic in sys_clk, and a rung being faster than a
                // failing one is not a contradiction.
                static const uint32_t C_KHZ[] = {
                    280000, 240000, 220000, 200000, 176000,
                    150000, 130000, 110000, 90000,
                };
                const int NC = (int)(sizeof C_KHZ / sizeof C_KHZ[0]);

                // Same bound and same ordering rule as m6.c: voltage before
                // frequency going up, frequency before voltage coming down.
                // Staged in two steps, and stopping one short of
                // VREG_VOLTAGE_MAX: hardware/vreg.h goes 1.20 = 0b01101, 1.25,
                // 1.30 = MAX, so 1.20 is *two* steps below the cap, not the
                // four an earlier version of this comment claimed.
                //
                // The second step is to keep the MCU from becoming the
                // confound above 220. It does nothing for the Trion, which is
                // a separate device on a supply this firmware cannot reach.
                enum vreg_voltage vreg_now = VREG_VOLTAGE_DEFAULT;

                for (int r = 0; r < NC; r++) {
                    // Rung 0 is no longer the clock main() booted at, so every
                    // rung sets its own rate - including the first.
                    stdio_flush();
                    sleep_ms(20);
                    const enum vreg_voltage want =
                        C_KHZ[r] > 220000 ? VREG_VOLTAGE_1_25 :
                        C_KHZ[r] > 150000 ? VREG_VOLTAGE_1_20 :
                                            VREG_VOLTAGE_DEFAULT;
                    if (want > vreg_now) {
                        printf("config C  : core to %s V for the rungs above "
                               "%u MHz\n",
                               want == VREG_VOLTAGE_1_25 ? "1.25" : "1.20",
                               want == VREG_VOLTAGE_1_25 ? 220u : 150u);
                        stdio_flush();
                        vreg_set_voltage(want);
                        sleep_ms(10);
                        vreg_now = want;
                    }
                    if (!set_sys_clock_khz(C_KHZ[r], false)) continue;
                    sleep_ms(50);
                    // Learned in bit-times, made of nanoseconds of pad
                    // flight. They do not survive a rate change.
                    gh_rate_changed();
                    printf("\nlink      : configuration C, %u forward data "
                           "lines on G3/F3/F2, clock on the PIN2 <-> PIN17 "
                           "jumper, return on A4, %.1f MHz link\n",
                           gh_width(), C_KHZ[r] / 2000.0);
                    ran_c = true;
                    c_khz = C_KHZ[r];
                    run_config(&m, image, nconv, tot_blk, plan_bytes3, ref_ms,
                               false, true, &rc);
                    if (!rc.bad_layers && !rc.bad_embed) break;
                    printf("config C  : not bit-exact at %.1f MHz link - "
                           "stepping the system clock down\n",
                           C_KHZ[r] / 2000.0);
                }
                // The rq pass at whichever clock configuration C closed at, so
                // the two C rows are comparable to each other for the same
                // reason the two A rows are: one boot, one clock, one plan.
                if (!rc.bad_layers && !rc.bad_embed) {
                    printf("\nlink      : configuration C at rq, %.1f MHz "
                           "link\n", c_khz / 2000.0);
                    run_config(&m, image, nconv, tot_blk, rq_bytes3, ref_ms,
                               true, false, &rc_rq);
                    ran_c_rq = true;
                }
                if (c_khz != 150000) {
                    set_sys_clock_khz(150000, true);
                    sleep_ms(50);
                    gh_rate_changed();
                }
                // Frequency is already back down by here, so the voltage can
                // follow it. Doing this the other way round is the sequence
                // that browns out the core mid-instruction.
                if (vreg_now != VREG_VOLTAGE_DEFAULT) {
                    vreg_set_voltage(VREG_VOLTAGE_DEFAULT);
                    sleep_ms(10);
                    vreg_now = VREG_VOLTAGE_DEFAULT;
                }
            }
        }
    }

    // --- the verdict --------------------------------------------------------
    // Configuration A decides it. C is a measurement laid on top of an already
    // complete run, so a C that never ran is not a failure - but a C that ran
    // and got the wrong answer is, because it means the wide RTL and this
    // driver disagree about the wire.
    //
    // The rq passes join it on the same terms. rq is the milestone under test,
    // so an rq pass that returns the wrong tensor is a failure exactly like a C
    // that does - and it is the more informative one, because rq off having
    // already passed in the same boot narrows it to the fabric epilogue.
    const cfg_result_t *bad =
          (ra.bad_layers    || ra.bad_embed)                 ? &ra
        : (ran_a_rq && (ra_rq.bad_layers || ra_rq.bad_embed)) ? &ra_rq
        : (ran_c    && (rc.bad_layers    || rc.bad_embed))    ? &rc
        : (ran_c_rq && (rc_rq.bad_layers || rc_rq.bad_embed)) ? &rc_rq
        : NULL;
    const uint8_t status = ra.status | (ran_a_rq ? ra_rq.status : 0u)
                         | (ran_c ? rc.status : 0u)
                         | (ran_c_rq ? rc_rq.status : 0u);
    const char *where = (bad == &rc)    ? " in configuration C"
                      : (bad == &ra_rq) ? " at rq"
                      : (bad == &rc_rq) ? " at rq in configuration C"
                      : "";

    printf("\n");
    if (bad) {
        if (bad->bad_layers)
            printf("RESULT : FAIL - %d of %u layers did not match the "
                   "reference%s\n", bad->bad_layers, (unsigned)nconv, where);
        else
            printf("RESULT : FAIL - every layer CRC matched but %d of %u "
                   "embedding floats did not%s, which should be impossible\n",
                   bad->bad_embed, (unsigned)m.hdr->embed_dim, where);
        // Worth saying out loud when only C failed: configuration A was clean,
        // so the model, the plan, the driver's framing and the reference all
        // agree. What did not is the wide bitstream, the jumper, or the packer
        // - and the ladder already stepped the clock down as far as 45 MHz
        // without fixing it, which rules out timing and points at the format.
        if (bad == &rc)
            printf("         configuration A was bit-exact throughout, and the "
                   "system clock was stepped down to %.1f MHz without fixing "
                   "it, so this is the wide link's wire format rather than its "
                   "timing\n", c_khz / 2000.0);
    } else if (status & (GH_ST_UNDERRUN | GH_ST_BADFRAME)) {
        printf("RESULT : FAIL - bit-exact, but a sticky fault was raised (%02x)\n",
               status);
    } else {
        printf("RESULT : PASS - all %u layers bit-exact in all six modes%s%s, "
               "%u/%u embedding floats exact, on %s\n",
               (unsigned)nconv, ran_c ? " of both link configurations" : "",
               (ran_a_rq || ran_c_rq) ? ", int32 DRAIN and rq alike" : "",
               (unsigned)m.hdr->embed_dim, (unsigned)m.hdr->embed_dim,
               live ? "a frame off the camera" : "the flash test vector");
        printf("         config A: %u serialized -> %u pipelined -> %u +core1 "
               "build -> %u +core1 scatter -> %u +core1 decode -> %u "
               "+priorities ms/frame, one boot\n",
               (unsigned)ra.ms[0], (unsigned)ra.ms[1], (unsigned)ra.ms[2],
               (unsigned)ra.ms[3], (unsigned)ra.ms[4], (unsigned)ra.ms[5]);
        if (ran_a_rq)
            printf("         config A at rq: %u -> %u -> %u -> %u -> %u -> %u "
                   "ms/frame, the same boot and the same %u blocks\n",
                   (unsigned)ra_rq.ms[0], (unsigned)ra_rq.ms[1],
                   (unsigned)ra_rq.ms[2], (unsigned)ra_rq.ms[3],
                   (unsigned)ra_rq.ms[4], (unsigned)ra_rq.ms[5],
                   (unsigned)tot_blk);
        if (ran_c) {
            printf("         config C: %u -> %u -> %u -> %u -> %u -> %u "
                   "ms/frame at a %.1f MHz link, the same boot and the same "
                   "%u blocks\n",
                   (unsigned)rc.ms[0], (unsigned)rc.ms[1], (unsigned)rc.ms[2],
                   (unsigned)rc.ms[3], (unsigned)rc.ms[4], (unsigned)rc.ms[5],
                   c_khz / 2000.0, (unsigned)tot_blk);
            // The one number M7f item 2 exists to produce. Quoted off the top
            // rung, where the CPU is as far out of the way as this firmware can
            // put it and what is left is very nearly the wire.
            //
            // Only if both configurations ran at the same clock. Off 75 MHz the
            // CPU half of the frame moves too, so the difference would be part
            // lane count and part clock and this line could not say which - and
            // the honest thing to print then is the rate. That is true in both
            // directions now that the ladder has rungs above 150 MHz, but it is
            // true for opposite reasons, so it gets two messages: a low rung
            // means the wide fabric did not close, a high one means it closed
            // and the comparison is simply no longer apples to apples.
            if (c_khz < 150000)
                printf("         the third data line: no ratio - configuration "
                       "C only closed at %.1f MHz, so the two frames were not "
                       "clocked alike. The wide fabric needs timing work "
                       "before the jumper is worth anything.\n",
                       c_khz / 2000.0);
            else if (c_khz > 150000)
                printf("         the third data line: no ratio - configuration "
                       "C ran at %.1f MHz against configuration A's 75.0, so "
                       "the gap is part lane count and part clock. The result "
                       "here is the rate itself: %u MHz sys, %.1f MHz link, "
                       "every layer bit-exact over a whole frame.\n",
                       c_khz / 2000.0, (unsigned)(c_khz / 1000),
                       c_khz / 2000.0);
            else if (ra.ms[5] && rc.ms[5])
                printf("         the third data line: %u -> %u ms, %.2fx\n",
                       (unsigned)ra.ms[5], (unsigned)rc.ms[5],
                       (double)ra.ms[5] / rc.ms[5]);
            if (ran_c_rq)
                printf("         config C at rq: %u -> %u -> %u -> %u -> %u -> "
                       "%u ms/frame at the same %.1f MHz link\n",
                       (unsigned)rc_rq.ms[0], (unsigned)rc_rq.ms[1],
                       (unsigned)rc_rq.ms[2], (unsigned)rc_rq.ms[3],
                       (unsigned)rc_rq.ms[4], (unsigned)rc_rq.ms[5],
                       c_khz / 2000.0);
        } else {
            printf("         config C: not run - no wide bitstream was sent\n");
        }

        // M15's own line. Both frames are the top rung, the same boot, the same
        // clock and the same blocks, so the difference is the wire format and
        // nothing else - which is the only reason to have paid for running the
        // ladder twice. The per-transaction breakdown above each pair is where
        // DRAIN and W1_LO can be read separately; this is the total.
        if (ran_a_rq && ra.ms[5] && ra_rq.ms[5])
            printf("         the tile's epilogue: config A %u -> %u ms, %.2fx\n",
                   (unsigned)ra.ms[5], (unsigned)ra_rq.ms[5],
                   (double)ra.ms[5] / ra_rq.ms[5]);
        if (ran_c_rq && rc.ms[5] && rc_rq.ms[5])
            printf("         the tile's epilogue: config C %u -> %u ms, %.2fx\n",
                   (unsigned)rc.ms[5], (unsigned)rc_rq.ms[5],
                   (double)rc.ms[5] / rc_rq.ms[5]);
    }

    park();
}
