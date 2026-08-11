// Host test for gemm_plan.c. No Pico, no board, no strap.
//
//   cc -O2 -Wall -Wextra -o /tmp/test_gemm_plan \
//      firmware/test_gemm_plan.c firmware/gemm_plan.c firmware/gemm_block.c \
//      firmware/gemm_wire.c firmware/encoder.c -lm
//   /tmp/test_gemm_plan model/runs/so400m-full-a05/export
//
// Three claims, in increasing order of how badly they fail on hardware:
//
//   1. Every block gp_choose() picks is one gb_geom() accepts. A block that is
//      not gets refused at run time, and a sequencer that skips a refused block
//      returns a tensor with a hole in it.
//
//   2. **The blocks tile each output tensor exactly once.** This is the one
//      correctness property a sequencer can get wrong that per-block
//      bit-exactness will not catch: every block can return all 2048 of its
//      accumulators correctly while the set of blocks misses a corner of the
//      tensor or writes one twice.
//
//   3. The cost model reproduces M6c's measured transaction lengths. gemm_plan.c
//      restates gemm_host.c's framing constants because that file needs Pico
//      headers; this is what keeps the restatement honest.
//
// It also prints the table, which is the deliverable: the ~8.5 MB/frame figure
// every M7 latency projection is scoped against is checkable here, before the
// board is touched at all.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "encoder.h"
#include "gemm_plan.h"

static int fails;

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        if (!(cond)) {                                                        \
            printf("  FAIL %s:%d  ", __func__, __LINE__);                     \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
            fails++;                                                          \
        }                                                                     \
    } while (0)

static void *slurp(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *p = malloc((size_t)n);
    if (!p || fread(p, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "%s: short read\n", path);
        exit(1);
    }
    fclose(f);
    *len = (size_t)n;
    return p;
}

// ---------------------------------------------------------------------------
// Claim 3, first, because if the framing is wrong every number below is wrong
// in the same direction and the table looks fine.
//
// M6c ran conv2 with P=128 QG=2 Cb=8 and reported, from the board:
//   ACT 1588   WGT 1204   RUN 2528   DRAIN 8244   block 50,980 bytes
// and 32 x 50,980 = 1,631,360 for the layer. Nothing here is a round number, so
// agreement is not a coincidence.
// ---------------------------------------------------------------------------
static void test_framing(const fgx_model_t *m)
{
    const gb_spec_t s = {
        .layer = 2, .P = 128, .QG = 2, .Cb = 8, .oy0 = 0, .ox0 = 0, .q0 = 16,
    };
    gb_geom_t g;
    const char *why = gb_geom(&m->desc[2], &s, &g);
    CHECK(!why, "M6c's own block no longer fits the tile: %s", why ? why : "");
    if (why) return;

    // The three payload lengths and the sweep budget, which are what the four
    // measured transaction sizes are made of.
    //
    // M14 moved one of them and only one. conv2 is a 4-bit layer now, so its
    // WGT payload is half of what the board sent - and the anchor is kept in
    // that form, `1152 / 2`, rather than restated as 576, so this stays a check
    // against a measurement instead of becoming a check against the code's own
    // arithmetic. Everything else is width-independent and must not move:
    // int4 is a weight format, not a different convolution.
    const int w_exp = m->desc[2].wbits == 4 ? 1152 / 2 : 1152;
    CHECK(g.a_len == 1536, "a_len = %d, M6c sent 1536", g.a_len);
    CHECK(g.w_len == w_exp, "w_len = %d, M6c sent 1152 at 8 bits, so %d at %u",
          g.w_len, w_exp, m->desc[2].wbits);
    CHECK(g.nacc  == 2048, "nacc = %d, M6c drained 2048", g.nacc);
    CHECK(g.npass == 8,    "npass = %d, M6c ran 8", g.npass);
    // Pinned at kpack = 0 explicitly rather than at whatever GP_KPACK the build
    // carries: 19808 is a number the board produced on an unpacked tile, and
    // checking it against the packed model would be checking the code against
    // itself.
    const long sweep = gp_sweep_cycles_k(&g, 0);
    CHECK(sweep == 19808, "sweep = %ld, M6c budgeted 19808", sweep);

    // M16 has no board number of its own yet, so there is nothing to anchor the
    // packed budget *to*. What can be checked without inventing one is the shape
    // of the change: the pairing removes a third of the sweeps and lengthens
    // each survivor by one clock for stage 2c, so the budget has to land a
    // little above two thirds of M6c's and certainly below it. A wrong tap
    // count or a dropped FLUSH breaks one bound or the other.
    const long sweep_k = gp_sweep_cycles_k(&g, 1);
    CHECK(sweep_k > (sweep - 512) * 2 / 3 + 512 && sweep_k < sweep,
          "packed sweep = %ld, expected just over two thirds of M6c's %ld",
          sweep_k, sweep);

    // 50,980 was the board's byte count with 1152-byte WGT payloads; the only
    // difference at int4 is the eight passes' worth of weights that no longer
    // travel, so the expected total is the measurement minus exactly that.
    //
    // M16 adds a second such term, in the same form and for the same reason:
    // the anchor stays the measurement plus each modelled change, never a
    // restated total. The RUN delta is taken from the parts rather than
    // computed here because rounding the idle budget up to the link's transfer
    // granularity is gp_block_parts_kw()'s arithmetic and not this test's.
    gp_parts_t p_flat, p_now;
    gp_block_parts_kw(&g, 1, 0, &p_flat);
    gp_block_parts_kw(&g, 1, GP_KPACK, &p_now);

    const long b_exp = 50980 - (long)g.npass * (1152 - w_exp)
                             + (p_now.run - p_flat.run);
    gp_cost_t c;
    gp_block_cost(&g, &c);
    CHECK(c.bytes == b_exp, "block = %ld bytes; the board moved 50980 at 8 bits "
          "on an unpacked tile, so %ld here", c.bytes, b_exp);
}

// ---------------------------------------------------------------------------
// Claims 1 and 2, plus the table.
// ---------------------------------------------------------------------------
static gb_spec_t specs[16384];

// `rq` is M15's mode, and it is threaded through rather than swept inside
// because the two runs are two different tables: gp_choose() picks a different
// blocking when DRAIN is a quarter the size, so every claim below - the tiling,
// the shape drift, the component split - has to be re-established for it and not
// inherited from the int32 pass.
static void test_layer(const fgx_model_t *m, int i, int rq, gp_cost_t *total,
                       gp_parts_t *parts, gp_cost_t *total3, gp_parts_t *parts3)
{
    const fgx_desc_t *d = &m->desc[i];

    gp_layer_t pl;
    const char *why = gp_choose(d, i, rq && !fgx_emits_float(m, (uint32_t)i),
                                &pl);
    if (why) {
        printf("  FAIL layer %d: %s\n", i, why);
        fails++;
        return;
    }

    const int n = gp_blocks(d, &pl, specs, (int)(sizeof specs / sizeof specs[0]));
    CHECK(n == pl.nblocks, "layer %d: gp_blocks returned %d, planned %d",
          i, n, pl.nblocks);
    if (n != pl.nblocks) return;

    // The output tensor, one byte per (oc, oy, ox), counting writes.
    const size_t nout = (size_t)d->cout * d->oh * d->ow;
    uint8_t *seen = calloc(nout, 1);
    if (!seen) { fprintf(stderr, "out of memory\n"); exit(1); }

    // gp_choose() costed one representative block and multiplied. That is only
    // legal if every block really does have the same shape, which is what
    // gp_p_ok() restricts P for - so it is checked rather than assumed.
    gb_geom_t g0;
    CHECK(!gb_geom(d, &pl.base, &g0), "layer %d: the base block does not fit", i);

    gp_cost_t sum = { 0, 0, 0 }, sum3 = { 0, 0, 0 };
    int geom_bad = 0, shape_bad = 0, wgt_bad = 0;

    // GB_WGTMAX is the payload ceiling gb_geom() already enforces on w_len, so
    // these are the largest either call can write.
    const int8_t *const wb = (const int8_t *)m->weights + d->w_off;
    static int8_t wfast[GB_WGTMAX], wslow[GB_WGTMAX];

    for (int b = 0; b < n; b++) {
        gb_geom_t g;
        const char *bad = gb_geom(d, &specs[b], &g);
        if (bad) {
            if (!geom_bad++)
                printf("  FAIL layer %d block %d: %s\n", i, b, bad);
            fails++;
            continue;
        }

        if (g.a_len != g0.a_len || g.w_len != g0.w_len ||
            g.nacc != g0.nacc || g.npass != g0.npass) {
            if (!shape_bad++)
                printf("  FAIL layer %d block %d: shape drifts from the base "
                       "(a_len %d/%d, w_len %d/%d, nacc %d/%d, npass %d/%d)\n",
                       i, b, g.a_len, g0.a_len, g.w_len, g0.w_len,
                       g.nacc, g0.nacc, g.npass, g0.npass);
            fails++;
        }

        // M7h swapped gb_weights()'s two loops to make the flash side of the
        // gather sequential. It is a permutation of the same bytes, so getting
        // it wrong produces a tensor of the right size full of the right
        // values in the wrong places - which the tile will happily multiply.
        // Every block of every pass of the real model, against the loop order
        // that shipped through M7g.
        for (int pass = 0; pass < g.npass; pass++) {
            gb_weights(&g, wb, pass, wfast);
            gb_weights_slow(&g, wb, pass, wslow);
            if (memcmp(wfast, wslow, (size_t)g.w_len) != 0) {
                if (!wgt_bad++) {
                    size_t k = 0;
                    while (wfast[k] == wslow[k]) k++;
                    printf("  FAIL layer %d block %d pass %d: gb_weights "
                           "differs from gb_weights_slow at byte %zu of %d "
                           "(%d vs %d)\n", i, b, pass, k, g.w_len,
                           wfast[k], wslow[k]);
                }
                fails++;
            }
        }

        gp_cost_t c;
        gp_block_cost(&g, &c);
        gp_cost_add(&sum, &c);

        gp_parts_t bp;
        gp_block_parts(&g, &bp);
        CHECK(bp.act + bp.wgt + bp.run + bp.drain + bp.rqp + bp.framing
                  == c.bytes,
              "layer %d block %d: the component split loses bytes", i, b);
        CHECK(bp.us_act_build + bp.us_wgt_build + bp.us_crc + bp.us_decode
                  == c.us_cpu,
              "layer %d block %d: the component split loses CPU time", i, b);
        gp_parts_add(parts, &bp);

        // M7f. The same block over three forward data lines, accumulated so the
        // configuration C projection at the bottom is the same arithmetic and
        // not a scaling of the total. The two properties worth asserting are
        // the ones the whole argument for the jumper rests on: the CPU half
        // does not move at all, and RUN's bytes grow rather than its time.
        gp_cost_t  c3;
        gp_parts_t bp3;
        gp_block_cost_w(&g, 3, &c3);
        gp_block_parts_w(&g, 3, &bp3);
        CHECK(c3.us_cpu == c.us_cpu,
              "layer %d block %d: three lanes changed the CPU cost, %ld -> %ld",
              i, b, c.us_cpu, c3.us_cpu);
        CHECK(bp3.run > bp.run,
              "layer %d block %d: RUN did not grow with the width, %ld -> %ld",
              i, b, bp.run, bp3.run);
        gp_cost_add(&sum3, &c3);
        gp_parts_add(parts3, &bp3);

        // Claim 2: exactly the words gb_golden() would produce, in its order.
        for (int gg = 0; gg < g.QG; gg++)
            for (int j = 0; j < GB_NMAC; j++) {
                const int oc = g.q0 + gg * GB_NMAC + j;
                for (int p = 0; p < g.P; p++) {
                    const int pos = g.ox0 + p;
                    const int oy  = g.oy0 + pos / g.OW;
                    const int ox  = pos % g.OW;
                    if (oc < 0 || oc >= d->cout || oy < 0 || oy >= d->oh ||
                        ox < 0 || ox >= d->ow) {
                        printf("  FAIL layer %d block %d writes outside the "
                               "tensor: oc=%d oy=%d ox=%d\n", i, b, oc, oy, ox);
                        fails++;
                        goto done;
                    }
                    seen[((size_t)oc * d->oh + oy) * d->ow + ox]++;
                }
            }
    }

    {
        size_t never = 0, twice = 0;
        for (size_t k = 0; k < nout; k++) {
            if (seen[k] == 0) never++;
            else if (seen[k] > 1) twice++;
        }
        CHECK(never == 0 && twice == 0,
              "layer %d: %zu outputs never written, %zu written more than once",
              i, never, twice);
    }

    CHECK(sum.bytes == pl.cost.bytes,
          "layer %d: blocks sum to %ld bytes, gp_choose predicted %ld",
          i, sum.bytes, pl.cost.bytes);
    CHECK(sum3.bytes == pl.cost3.bytes,
          "layer %d: blocks sum to %ld bytes at three lanes, gp_choose "
          "predicted %ld", i, sum3.bytes, pl.cost3.bytes);

    printf("  %2d  %3dx%3dx%-4d -> %-4d  %d  %3d %4d %4d  %5d %5d  %9.3f %8.0f %8.0f\n",
           i, d->h, d->w, d->cin, d->cout, (d->h == d->oh) ? 1 : 2,
           pl.base.P, pl.Q, pl.base.Cb, pl.nblocks, pl.npass,
           pl.cost.bytes / 1048576.0,
           pl.cost.us_wire / 1000.0, pl.cost.us_cpu / 1000.0);

    gp_cost_add(total, &pl.cost);
    gp_cost_add(total3, &pl.cost3);

done:
    free(seen);
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "model/runs/so400m-full-a05/export";
    char path[512];
    snprintf(path, sizeof path, "%s/weights.bin", dir);

    size_t len;
    void *blob = slurp(path, &len);

    fgx_model_t m;
    if (!fgx_open(&m, blob, len)) {
        fprintf(stderr, "%s: not a usable model blob\n", path);
        return 1;
    }

    printf("model   : %s, %u layers, %u-d embedding\n\n",
           path, (unsigned)m.hdr->n_layers, (unsigned)m.hdr->embed_dim);

    printf("framing ... "); fflush(stdout);
    test_framing(&m);
    printf("done\n\n");

    printf("  %-2s  %-18s %s  %3s %4s %4s  %5s %5s  %9s %8s %8s\n",
           "L", "HxWxCIN -> COUT", "s", "P", "Q", "Cb", "blks", "pass",
           "MB", "wire ms", "cpu ms");

    gp_cost_t  total = { 0, 0, 0 }, total3 = { 0, 0, 0 };
    gp_parts_t parts = { 0 },       parts3 = { 0 };
    // The last two descriptors are the float-output conv and the linear head;
    // fgx_emits_float() names them, and the head is not a 3x3 convolution at
    // all. Every conv, including the float one, goes to the tile - the tile
    // stops at the accumulator either way and the epilogue is the MCU's.
    for (uint32_t i = 0; i + 1 < m.hdr->n_layers; i++)
        test_layer(&m, (int)i, 0, &total, &parts, &total3, &parts3);

    printf("\n  total : %.3f MB   wire %.0f ms   cpu %.0f ms   serial %.0f ms\n",
           total.bytes / 1048576.0, total.us_wire / 1000.0,
           total.us_cpu / 1000.0,
           (total.us_wire + total.us_cpu) / 1000.0);
    printf("  MCU baseline (M5b, encoder_fast) : 3358 ms/frame\n");

    // Which component each optimisation past M7c is actually attacking. The
    // wire column is the whole story for a component only if it is data; RUN is
    // the exception and the reason the split is printed at all.
    {
        const long p[6] = { parts.act, parts.wgt, parts.run, parts.drain,
                            parts.rqp, parts.framing };
        const char *nm[6] = { "ACT", "WGT", "RUN (idle = the tile computing)",
                              "DRAIN", "RQP (M15's table)",
                              "framing (NOP + CFG)" };
        long sum = 0;
        for (int k = 0; k < 6; k++) sum += p[k];
        CHECK(sum == total.bytes,
              "the component split sums to %ld, the frame is %ld", sum,
              total.bytes);

        printf("\n  %-34s %9s %9s\n", "component", "MB", "wire ms");
        for (int k = 0; k < 6; k++)
            printf("  %-34s %9.3f %9.0f\n", nm[k], p[k] / 1048576.0,
                   p[k] * GP_NS_WIRE / 1000000.0);

        printf("\n  %-34s %9s\n", "CPU, and what removes it", "ms");
        printf("  %-34s %9.0f\n", "gb_strip()      M7d overlaps it",
               parts.us_act_build / 1000.0);
        printf("  %-34s %9.0f\n", "gb_weights()    M7e deletes it",
               parts.us_wgt_build / 1000.0);
        printf("  %-34s %9.0f\n", "outbound CRC    M7d sniffs it",
               parts.us_crc / 1000.0);
        printf("  %-34s %9.0f\n", "response decode 4x less DRAIN",
               parts.us_decode / 1000.0);
    }

    // M7f, configuration C: the same plan, the same blocks, three forward data
    // lines instead of one.
    //
    // The point of printing it beside the table above is that the byte column
    // goes the *wrong* way. Three lanes move more bytes, not fewer, because RUN
    // and DRAIN spend theirs on clocks rather than data - so a jumper that
    // sounds like a 3x is worth whatever the ACT and WGT rows are worth and
    // nothing more. This is the arithmetic that says how big the strap's payoff
    // can be before anyone fits the strap.
    {
        const long p1[6] = { parts.act,  parts.wgt,  parts.run,
                             parts.drain,  parts.rqp,  parts.framing };
        const long p3[6] = { parts3.act, parts3.wgt, parts3.run,
                             parts3.drain, parts3.rqp, parts3.framing };
        const char *nm[6] = { "ACT", "WGT", "RUN (idle = the tile computing)",
                              "DRAIN", "RQP (M15's table)",
                              "framing (NOP + CFG)" };
        long sum3 = 0;
        for (int k = 0; k < 6; k++) sum3 += p3[k];
        CHECK(sum3 == total3.bytes,
              "the three-lane component split sums to %ld, the frame is %ld",
              sum3, total3.bytes);

        printf("\n  configuration C, three forward data lines, same plan\n");
        printf("  %-34s %9s %9s %9s\n", "component", "MB", "wire ms", "was");
        for (int k = 0; k < 6; k++)
            printf("  %-34s %9.3f %9.0f %9.0f\n", nm[k], p3[k] / 1048576.0,
                   p3[k] * GP_NS_WIRE / 3000000.0,
                   p1[k] * GP_NS_WIRE / 1000000.0);
        printf("  %-34s %9.3f %9.0f %9.0f\n", "total",
               total3.bytes / 1048576.0, total3.us_wire / 1000.0,
               total.us_wire / 1000.0);
        // What the frame does with those 300 ms is a separate question and this
        // model cannot answer it, so it says what it knows and stops. Mode 0
        // adds wire and CPU, so it gets the whole 300. Mode 5 overlaps them and
        // splits the CPU half across two cores, so it gets 300 only if the wire
        // is still the binding constraint afterwards - and against the ~900 ms
        // this model charges the CPU, at three lanes it is not. m7.c runs both
        // configurations in one boot precisely because that question has to be
        // measured rather than modelled.
        printf("  CPU unchanged at %.0f ms; the wire loses %.0f ms, and how "
               "much of that the frame keeps depends on the mode\n",
               total.us_cpu / 1000.0,
               (total.us_wire - total3.us_wire) / 1000.0);
    }

    // M15. The same model with the tile running the requantize epilogue, and it
    // is a second full pass rather than a scaling of the one above: quartering
    // DRAIN changes which blocking is cheapest, so this re-establishes every
    // claim - the tiling, the shape drift, the split - against the plan that
    // firmware actually runs in rq mode.
    //
    // conv7 is not in it. fgx_emits_float() names the last conv, which has no
    // code to compute and keeps int32 DRAIN; test_layer() applies that, so its
    // rows below are identical to the ones above and that is the check.
    {
        gp_cost_t  rtot = { 0, 0, 0 }, rtot3 = { 0, 0, 0 };
        gp_parts_t rp = { 0 },         rp3 = { 0 };

        printf("\n  M15, rq on: the tile requantizes, DRAIN returns one byte "
               "per accumulator\n");
        printf("  %-2s  %-18s %s  %3s %4s %4s  %5s %5s  %9s %8s %8s\n",
               "L", "HxWxCIN -> COUT", "s", "P", "Q", "Cb", "blks", "pass",
               "MB", "wire ms", "cpu ms");
        for (uint32_t i = 0; i + 1 < m.hdr->n_layers; i++)
            test_layer(&m, (int)i, 1, &rtot, &rp, &rtot3, &rp3);

        const long p[6] = { rp.act, rp.wgt, rp.run, rp.drain, rp.rqp,
                            rp.framing };
        const long q[6] = { parts.act, parts.wgt, parts.run, parts.drain,
                            parts.rqp, parts.framing };
        const char *nm[6] = { "ACT", "WGT", "RUN (idle = the tile computing)",
                              "DRAIN", "RQP (M15's table)",
                              "framing (NOP + CFG)" };
        long sum = 0;
        for (int k = 0; k < 6; k++) sum += p[k];
        CHECK(sum == rtot.bytes,
              "the rq component split sums to %ld, the frame is %ld",
              sum, rtot.bytes);

        // The milestone's claim, as an assertion and not as a printed number:
        // DRAIN has to fall and RQP has to be small enough that it does not
        // matter. 31 tables of at most 192 B against 1.4 MB of accumulators.
        CHECK(p[3] * 3 < q[3],
              "rq DRAIN is %ld bytes against %ld - less than the 4x the "
              "contract promises", p[3], q[3]);
        CHECK(p[4] > 0 && p[4] < q[3] / 100,
              "rq RQP is %ld bytes, which is not small against DRAIN's %ld",
              p[4], q[3]);

        printf("\n  %-34s %9s %9s %9s\n", "component", "MB", "wire ms", "was");
        for (int k = 0; k < 6; k++)
            printf("  %-34s %9.3f %9.0f %9.0f\n", nm[k], p[k] / 1048576.0,
                   p[k] * GP_NS_WIRE / 1000000.0,
                   q[k] * GP_NS_WIRE / 1000000.0);
        printf("  %-34s %9.3f %9.0f %9.0f\n", "total",
               rtot.bytes / 1048576.0, rtot.us_wire / 1000.0,
               total.us_wire / 1000.0);
        printf("  %-34s %9s %9.0f %9.0f\n", "response decode", "",
               rp.us_decode / 1000.0, parts.us_decode / 1000.0);
        // At three lanes, which is the configuration the frame is actually
        // scored in. DRAIN is one lane in both configurations - GPIO6 has no
        // contiguous neighbour - so this is the only place the two milestones
        // compound rather than overlap.
        printf("  %-34s %9s %9.0f %9.0f\n", "same, three forward data lines",
               "", rtot3.us_wire / 1000.0, total3.us_wire / 1000.0);
    }

    printf("\n%s\n", fails ? "FAIL" : "PASS");
    return fails ? 1 : 0;
}
