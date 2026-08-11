// M6: golden vectors for rtl/tb_gemm.v, cut from the real model.
//
//     cc -O2 -Wall -Wextra -o /tmp/gen_gemm_vec \
//        firmware/gen_gemm_vec.c firmware/encoder.c -lm
//     /tmp/gen_gemm_vec model/runs/so400m-full-a05/export /tmp/gemm_vec
//
// Host-only. Emits, for each test case, the activation strip and the weight
// byte stream exactly as the link will deliver them, plus the int32
// accumulators the tile must return.
//
// **The golden values come from fgx_conv_acc(), not from a loop written here.**
// That is the whole point of the extraction in encoder.c: a transcription of
// the convolution into this file would be a second implementation, and a
// testbench that agrees with a second implementation proves only that two of my
// loops match. Every golden word below is the same `static inline` the MCU
// runs, over the same weights.bin, on real intermediate tensors produced by
// running the net up to the layer under test.
//
// The layout itself lives in firmware/gemm_block.c, which the MCU also links:
// this file only chooses the cases and writes the results out. That is
// deliberate. The vectors below are what tb_gemm and tb_gemm_link check the RTL
// against, so building them from the same gb_strip()/gb_weights()/gb_golden()
// the firmware runs makes those two PASSes evidence about the shipping code
// rather than about a transcription of it.
//
// For reference, what gemm_block.c produces:
//
//   strip   ic_local*strip_ch + (iy - iy_base)*strip_rw + ix,
//           iy_base = oy0*stride - 1, rows outside the image filled with
//           GB_STRIP_POISON
//   weights k-major, g-minor, lane-innermost; the tile's word counter is then
//           just k*QG + g and needs no multiplier
//   golden  drain order: channel group g outer, lane j next, position p inner,
//           so output channel = q0 + g*NMAC + j
//
// Data goes into four flat files rather than one set per case, with the
// descriptor carrying offsets. Verilog can then load each file with a single
// $readmemh and index into it, instead of building filenames at runtime.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "encoder.h"
#include "gemm_block.h"
#include "gemm_wire.h"

#define LABEL    16   // bytes per case label, right-justified
#define NFIELD   24   // descriptor words per case

// One test case: a gb_spec_t plus what this file needs on top of it.
typedef struct {
    const char *label;
    gb_spec_t   s;
    int synth;      // 1 = saturating synthetic data instead of the real tensor
} case_spec_t;

// Chosen to cover, between them, everything M6a has to show: both strides, both
// input signednesses, a K that is not a multiple of anything (conv0, K=27), two
// consecutive blocks so the strip origin moves and the halo rows are shared, a
// block that starts mid-tensor with P not a multiple of OW, the Q=128 blocking
// where QG hits its maximum, split-K over 8 and 192 passes, and an accumulator
// driven to 26 bits.
//
// M15 asks every case for rq and lets gb_geom() answer. That is not laziness
// about coverage - it is the coverage: the two refusals are Q > 32 and a strip
// that would reach the table, and conv7 q128 trips the first of them while
// emitting floats as well, so the negative case is a real one and is checked
// below rather than assumed.
//
// Designated initializers from here on. The list was positional through M14 and
// gb_spec_t has grown twice since; a positional row silently means something
// different after each growth, and the compiler only warns about the tail.
static const case_spec_t CASES[] = {
    // conv0: stride 2, signed input, K = 27, single pass, Q = 32.
    { "conv0 blk0",   { .layer = 0, .P =  64, .QG =  4, .Cb =   3,
                        .oy0 = 0, .ox0 = 0, .q0 =  0, .rq = 1 }, 0 },
    // The same layer one block later. iy_base moves from -1 to 1, so rows 1
    // and 2 appear in both strips at different strip addresses - the one place
    // an off-by-one in the origin hides.
    { "conv0 halo",   { .layer = 0, .P =  64, .QG =  4, .Cb =   3,
                        .oy0 = 1, .ox0 = 0, .q0 =  0, .rq = 1 }, 0 },
    // conv2: stride 1, unsigned input, 8 passes of Cb = 8, P spans four output
    // rows, and a non-zero channel base so q0 is not silently assumed to be 0.
    { "conv2 splitK", { .layer = 2, .P = 128, .QG =  2, .Cb =   8,
                        .oy0 = 0, .ox0 = 0, .q0 = 16, .rq = 1 }, 0 },
    // conv5: stride 2, a partial block - 20 positions starting at row 5, so P
    // is neither a power of two nor a multiple of OW and the last output row is
    // only half covered.
    { "conv5 part",   { .layer = 5, .P =  20, .QG =  2, .Cb =   8,
                        .oy0 = 5, .ox0 = 0, .q0 = 32, .rq = 1 }, 0 },
    // conv7: QG at its maximum, Q = 128, and 192 passes of a single input
    // channel each. The slowest case by far, and the one that proves the weight
    // stream survives being rewound 192 times.
    //
    // It is also M15's negative: Q = 128 is four times the table, and the layer
    // emits floats and has no code to compute. Two independent reasons, and the
    // vector generator asserts below that gb_geom() found the first of them.
    { "conv7 q128",   { .layer = 7, .P =  16, .QG = 16, .Cb =   1,
                        .oy0 = 0, .ox0 = 0, .q0 =  0, .rq = 1 }, 0 },
    // conv4's geometry with every activation 255 and every weight -128: the
    // accumulator reaches 128*9*255*128 = 37,601,280, which needs 26 bits.
    // Synthetic data, but still through fgx_conv_acc(), so the arithmetic being
    // checked is the real one.
    //
    // At rq it is also the epilogue's extreme: a 26-bit accumulator against a
    // real channel's (M, s) saturates the clamp from below at every position,
    // which is the one direction fgx_sat8() and a fabric clamp can disagree in.
    { "sat 255x-128", { .layer = 4, .P = 128, .QG =  2, .Cb =   8,
                        .oy0 = 0, .ox0 = 0, .q0 =  0, .rq = 1 }, 1 },
};
#define NCASE ((int)(sizeof CASES / sizeof CASES[0]))

static void die(const char *msg)
{
    fprintf(stderr, "gen_gemm_vec: %s\n", msg);
    exit(1);
}

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

static FILE *out(const char *dir, const char *name)
{
    char path[512];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    FILE *f = fopen(path, "w");
    if (!f) { perror(path); exit(1); }
    return f;
}

// Right-justified inside LABEL bytes so Verilog's %0s, which skips leading
// zeros, prints the label and nothing else.
static void emit_label(FILE *f, const char *s)
{
    char b[LABEL];
    size_t n = strlen(s);
    if (n > LABEL) n = LABEL;
    memset(b, 0, sizeof b);
    memcpy(b + (LABEL - n), s, n);
    for (int i = 0; i < LABEL; i++)
        fprintf(f, "%02x", (unsigned char)b[i]);
    fputc('\n', f);
}

// Scratch for one pass. Sized by gemm_block.h's own ceilings rather than by the
// largest case, so adding a case can never silently overrun them - gb_geom()
// refuses anything that would.
static uint8_t  strip[GB_STRIPD];
static int8_t   wstream[GB_WGTMAX];
static int32_t  golden[GB_ADEPTH * GB_NMAC];
static uint8_t  gcode[GB_ADEPTH * GB_NMAC];              // M15's byte golden
static uint8_t  rqp[GW_RQP_BYTES * GW_RQP_MAXQ];         // and its table

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "model/runs/so400m-full-a05/export";
    const char *odir = argc > 2 ? argv[2] : "/tmp/gemm_vec";
    char path[512];

    snprintf(path, sizeof path, "%s/weights.bin", dir);
    size_t wlen;
    void *wbuf = slurp(path, &wlen);

    snprintf(path, sizeof path, "%s/testvec.bin", dir);
    size_t tlen;
    uint8_t *tbuf = slurp(path, &tlen);
    if (tlen < 12 || memcmp(tbuf, "FGXT", 4) != 0)
        die("testvec.bin: bad magic");

    fgx_model_t m;
    if (!fgx_open(&m, wbuf, wlen))
        die("weights.bin: rejected by fgx_open");

    const uint32_t n = m.hdr->n_layers;
    const int8_t *codes = (const int8_t *)(tbuf + 12);   // image 0

    mkdir(odir, 0755);

    // Real intermediate tensors, computed once. Layers 0 .. n-3 emit uint8
    // codes; layer n-2 (the last conv) emits float, but only its *input* is
    // needed here, so the chain stops one short.
    void **act = calloc(n, sizeof *act);
    if (!act) die("out of memory");
    const void *src = codes;
    for (uint32_t i = 0; i + 2 < n; i++) {
        const fgx_desc_t *d = &m.desc[i];
        act[i] = malloc((size_t)d->cout * d->oh * d->ow);
        if (!act[i]) die("out of memory");
        fgx_conv_ref(&m, d, src, act[i], false);
        src = act[i];
    }

    FILE *fc = out(odir, "cases.hex");
    FILE *fl = out(odir, "labels.hex");
    FILE *fa = out(odir, "act.hex");
    FILE *fw = out(odir, "wgt.hex");
    FILE *fg = out(odir, "gold.hex");
    // M15. Two more, and code.hex is indexed by the *same* offset as gold.hex -
    // one entry per accumulator, one byte instead of four - so the descriptor
    // needs no second golden offset.
    FILE *fq = out(odir, "rqp.hex");
    FILE *fk = out(odir, "code.hex");

    fprintf(fc, "%08x\n", (unsigned)NCASE);

    uint32_t act_pos = 0, wgt_pos = 0, gold_pos = 0, rqp_pos = 0;
    int nrq = 0;

    printf("%-13s %5s %5s %4s %4s %4s %5s %6s %7s %9s %4s\n",
           "case", "layer", "P", "Q", "Cb", "K", "pass", "strip", "wgt B",
           "golden", "rq");

    for (int c = 0; c < NCASE; c++) {
        const case_spec_t *cs = &CASES[c];
        if (cs->s.layer < 0 || (uint32_t)cs->s.layer + 1 >= n)
            die("case names a layer that is not a convolution");

        // The synthetic case borrows a real layer's geometry but substitutes
        // its own weights and input. Both the tile and fgx_conv_acc() then see
        // the same fabricated data, so the comparison is still end to end.
        fgx_desc_t  d  = m.desc[cs->s.layer];
        fgx_model_t sm = m;
        int8_t *synth_w = NULL;
        uint8_t *synth_in = NULL;
        const void *in;

        if (cs->synth) {
            size_t nw = (size_t)d.cout * d.cin * d.ksize * d.ksize;
            size_t ni = (size_t)d.cin * d.h * d.w;
            synth_w = malloc(nw);
            synth_in = malloc(ni);
            if (!synth_w || !synth_in) die("out of memory");
            memset(synth_w, (int)(unsigned char)0x80, nw);   // -128
            memset(synth_in, 0xff, ni);                      //  255
            sm.weights = synth_w;
            d.w_off = 0;
            d.unsigned_in = 1;

            // M14 pins this one to 8 bits, and it is not because the case
            // cannot be run at 4. It is because at 4 it stops being this case:
            // the blob byte 0x80 is read as the nibble pair (0, -8), so
            // K * 255 * -8 = -146,880 instead of -2,350,080, and the accumulator
            // extreme this case exists to reach is no longer reached. The
            // narrower weight cannot produce the wider product, so there is no
            // int4 version of a widest-product test - the 8-bit one is strictly
            // stronger and it stays.
            //
            // What int4 needs tested instead is the sign extension and the
            // byte-to-lane-pair mapping, and the three real 4-bit cases above
            // do that against golden values computed through fgx_wchan() on the
            // shipped weights, which certainly contain the 0x8 nibble that a
            // zero-extending tile would turn into +8.
            d.wbits = 8;
            in = synth_in;
        } else {
            in = cs->s.layer == 0 ? (const void *)codes : act[cs->s.layer - 1];
        }
        if (!in) die("layer input tensor was never computed");

        gb_geom_t g;
        const char *why = gb_geom(&d, &cs->s, &g);
        if (why) {
            fprintf(stderr, "gen_gemm_vec: %s: %s\n", cs->label, why);
            exit(1);
        }

        const uint32_t a_off = act_pos, w_off = wgt_pos, g_off = gold_pos;
        const uint32_t a_len = (uint32_t)g.a_len;
        const uint32_t w_len = (uint32_t)g.w_len;

        const uint8_t *ub = (const uint8_t *)in;
        for (int pass = 0; pass < g.npass; pass++) {
            gb_strip(&g, ub, pass, strip);
            for (int i = 0; i < g.a_len; i++)
                fprintf(fa, "%02x\n", strip[i]);
            act_pos += a_len;
        }

        const int8_t *wb = sm.weights + d.w_off;
        for (int pass = 0; pass < g.npass; pass++) {
            gb_weights(&g, wb, pass, wstream);
            for (int i = 0; i < g.w_len; i++)
                fprintf(fw, "%02x\n", (unsigned char)wstream[i]);
            wgt_pos += w_len;
        }

        gb_golden(&sm, &d, &g, in, golden);
        for (int i = 0; i < g.nacc; i++)
            fprintf(fg, "%08x\n", (uint32_t)golden[i]);

        // M15. The codes the tile must return at cfg_rq, and the table it must
        // return them from. Both are gb_rqp()/gb_golden_code() and not loops
        // written here, for the reason the accumulators are fgx_conv_acc(): a
        // transcription would make the testbench agree with a transcription.
        //
        // gb_golden_code() takes gb_golden()'s output, so the two goldens are
        // the same arithmetic with an epilogue on the end - which is exactly the
        // relationship the tile is being asked to reproduce.
        const uint32_t q_off = rqp_pos;
        uint32_t q_len = 0;
        if (g.rq) {
            const size_t nq = gb_rqp(&sm, &d, &g, rqp);
            if (nq != (size_t)g.Q * GW_RQP_BYTES) {
                fprintf(stderr, "gen_gemm_vec: %s: gb_rqp gave %zu bytes, "
                        "wanted %d\n", cs->label, nq, g.Q * GW_RQP_BYTES);
                exit(1);
            }
            for (size_t i = 0; i < nq; i++)
                fprintf(fq, "%02x\n", rqp[i]);
            q_len = (uint32_t)nq;
            rqp_pos += q_len;
            nrq++;

            gb_golden_code(&sm, &d, &g, golden, gcode);
            for (int i = 0; i < g.nacc; i++)
                fprintf(fk, "%02x\n", gcode[i]);
        } else {
            // Nothing to compare against, and code.hex is indexed by gold.hex's
            // offset - so the rows still have to exist. Poisoned rather than
            // zeroed, on GB_STRIP_POISON's argument: a testbench that read them
            // by mistake must not get a plausible answer.
            for (int i = 0; i < g.nacc; i++)
                fprintf(fk, "%02x\n", GB_STRIP_POISON);
        }
        gold_pos += (uint32_t)g.nacc;

        const uint32_t f[NFIELD] = {
            (uint32_t)cs->s.layer, (uint32_t)g.H, (uint32_t)g.W, (uint32_t)g.OW,
            (uint32_t)(g.st == 2), (uint32_t)g.unsigned_in,
            (uint32_t)g.strip_rw, (uint32_t)g.strip_ch,
            (uint32_t)g.oy0, (uint32_t)g.ox0,
            (uint32_t)g.P, (uint32_t)g.QG, (uint32_t)g.K, (uint32_t)g.npass,
            a_off, a_len, w_off, w_len, g_off, (uint32_t)g.nacc,
            // M14, appended rather than slotted in beside unsigned_in: the
            // field order is positional in two testbenches, and a case file
            // written by a newer generator than the tb reading it then
            // misaligns silently instead of failing to parse.
            (uint32_t)g.w4,
            // M15, appended on the same rule.
            (uint32_t)g.rq, q_off, q_len
        };
        for (int i = 0; i < NFIELD; i++)
            fprintf(fc, "%08x\n", f[i]);
        emit_label(fl, cs->label);

        printf("%-13s %5d %5d %4d %4d %4d %5d %6u %7u %9u %4d\n",
               cs->label, cs->s.layer, g.P, g.Q, g.Cb, g.K, g.npass,
               a_len, w_len, (unsigned)g.nacc, g.rq);

        free(synth_w);
        free(synth_in);
    }

    fclose(fc); fclose(fl); fclose(fa); fclose(fw); fclose(fg);
    fclose(fq); fclose(fk);

    // Every case above asks for rq, so what the set actually covers is decided
    // by gb_geom() rather than by the table - which is the point, and also the
    // reason it has to be checked. A change that made gb_geom() refuse
    // everything would leave both testbenches green while testing none of M15.
    if (!nrq || nrq == NCASE)
        die(nrq ? "every case runs rq: the refusal path is untested"
                : "no case runs rq: the vectors do not cover M15");

    // Exact array bounds for the testbench. Sizing its memories by hand would
    // mean either $readmemh warnings on every run - which is where a genuinely
    // missing file would then hide - or a silent truncation the day a case
    // outgrows the envelope.
    FILE *fs = out(odir, "vecsizes.vh");
    fprintf(fs, "// Generated by firmware/gen_gemm_vec.c. Do not edit.\n");
    fprintf(fs, "`define VEC_NCASE %d\n", NCASE);
    fprintf(fs, "`define VEC_NFIELD %d\n", NFIELD);
    fprintf(fs, "`define VEC_LABEL %d\n", LABEL);
    fprintf(fs, "`define VEC_ACTN %u\n", act_pos);
    fprintf(fs, "`define VEC_WGTN %u\n", wgt_pos);
    fprintf(fs, "`define VEC_GOLDN %u\n", gold_pos);
    fprintf(fs, "`define VEC_RQPN %u\n", rqp_pos);
    fclose(fs);

    printf("\n%s: %d cases (%d at rq), %u activation bytes, %u weight bytes, "
           "%u accumulators, %u table bytes\n",
           odir, NCASE, nrq, act_pos, wgt_pos, gold_pos, rqp_pos);
    return 0;
}
