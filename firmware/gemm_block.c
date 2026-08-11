// See gemm_block.h for what this is and why it is one file rather than three.

#include "gemm_block.h"

// For GW_RQP_BYTES and gw_rqp_pack(). The wire layout of an RQP entry is the
// tile's, so it is defined once next to the framing and not duplicated here -
// this file only decides *which* channels go in and in what order.
#include "gemm_wire.h"

// M7g. Same shape as gemm_wire.c's GW_HOT, and for the same reason: this file
// is Pico-free so that test_gemm_plan.c and gen_gemm_vec.c can compile it with
// a bare `cc`, and GW_PICO is defined only by firmware/CMakeLists.txt.
//
// It goes on gb_strip() and gb_weights() and nothing else. Those two are core
// 1's entire W1_HI queue - 3,364 calls a frame, 536 ms - and core 1 fetching
// them over XIP contends with core 0 for the one QSPI interface the two cores
// share, so the cost lands in core 0's stall rather than in core 1's busy time.
// gb_geom() runs 174 times a frame and gb_golden() only in the sweep; neither
// is worth the SRAM.
#ifdef GW_PICO
#include "pico.h"
#define GB_HOT(f) __not_in_flash_func(f)
#else
#define GB_HOT(f) f
#endif

const char *gb_geom(const fgx_desc_t *d, const gb_spec_t *s, gb_geom_t *g)
{
    g->layer = s->layer;
    g->H = d->h; g->W = d->w; g->OW = d->ow; g->OH = d->oh;
    g->CIN = d->cin; g->COUT = d->cout;
    g->unsigned_in = d->unsigned_in != 0;

    // There is no stride field. The descriptor records the shapes and the
    // stride is the ratio between them, which is how encoder.c reads it too.
    g->st = (d->h == d->oh) ? 1 : 2;

    g->P = s->P; g->QG = s->QG; g->Cb = s->Cb;
    g->q0 = s->q0; g->oy0 = s->oy0; g->ox0 = s->ox0;
    g->Q = g->QG * GB_NMAC;
    g->K = g->Cb * 9;
    g->w4 = (d->wbits == 4);

    if (d->ksize != 3)   return "only 3x3 layers are supported";
    if (g->P < 1 || g->QG < 1 || g->Cb < 1) return "P, QG and Cb must be positive";
    if (g->Cb > g->CIN)  return "Cb exceeds CIN";
    if (g->CIN % g->Cb)  return "Cb must divide CIN exactly";

    // A nibble-packed layer is read out of the blob at element granularity, and
    // gb_weights() only computes the odd/even phase once per output-channel
    // pair - which is right exactly when consecutive channels are CIN*9
    // elements apart and that gap is even. fgx_open() already refuses a 4-bit
    // layer whose cin*k*k is odd, so this cannot fire on a blob that loaded -
    // only on a descriptor a caller built or edited, which gen_gemm_vec.c does.
    if (g->w4 && (g->CIN & 1)) return "int4 needs an even CIN";

    g->npass = g->CIN / g->Cb;

    // How many output rows past oy0 this block reaches. OW is a power of two in
    // every layer, so the tile does this with a shift; here it is a divide
    // because nothing on the MCU side is rate-limited by it.
    g->rowspan  = (g->ox0 + g->P - 1) / g->OW;
    g->SROWS    = g->rowspan * g->st + 3;
    g->strip_rw = g->W;
    g->strip_ch = g->SROWS * g->W;
    g->iy_base  = g->oy0 * g->st - 1;

    g->a_len = g->Cb * g->strip_ch;
    // Q = QG*NMAC is always even, so the halving is exact and w_len stays the
    // number of bytes that actually go on the wire - which is what every caller
    // means by it, from gh_wgt()'s length to gemm_plan's cost model to frame.c's
    // weight cache. M14's whole Stage 1 saving is this one line.
    g->w_len = g->w4 ? (g->K * g->Q) / 2 : g->K * g->Q;
    g->nacc  = g->P * g->Q;

    // Every one of these fails as a plausible wrong tensor rather than as an
    // error, on hardware and in simulation alike, so none of them is assumed.
    if (g->P * g->QG > GB_ADEPTH)   return "P*QG exceeds the accumulator depth";
    if (g->K * g->QG > GB_WDEPTH)   return "K*QG exceeds the weight buffer";
    if (g->a_len > GB_STRIPD)       return "strip exceeds the activation buffer";
    if (g->a_len > GB_ACTMAX)       return "strip exceeds the ACT payload limit";
    if (g->w_len > GB_WGTMAX)       return "weight stream exceeds the WGT payload limit";
    if (g->q0 + g->Q > g->COUT)     return "channel block runs past COUT";
    if (g->oy0 + g->rowspan >= g->OH) return "position block runs past the tensor";

    // CFG carries P and QG as single bytes, so the tile cannot be told about a
    // block the accumulator RAM would otherwise hold. P*QG <= ADEPTH already
    // bounds nacc at 2048 = the DRAIN word ceiling, so that needs no check of
    // its own; P alone does not.
    if (g->P > 255 || g->QG > 255)  return "P or QG does not fit the CFG byte";

    // M15. Resolved last, because it reads a_len, P and Q. See gb_geom_t.
    //
    // P must be even. The tile's requantize engine takes two positions at a
    // time - the drain walk is position-innermost, so a pair is two positions
    // of one output channel and shares its (bias, M, s) - and an odd P would
    // put a pair astride a channel boundary. This is the same place that
    // already refuses a Q the params store cannot hold and a strip that would
    // reach RQBASE, so a block that fails it falls back to int32 DRAIN rather
    // than failing outright.
    g->rq = s->rq && (size_t)g->Q <= GW_RQP_MAXQ && g->a_len <= GB_RQBASE &&
            (g->P % 2) == 0;
    g->rqp_send = g->rq && s->rqp_send;

    return 0;
}

void GB_HOT(gb_strip)(const gb_geom_t *g, const uint8_t *in, int pass,
                      uint8_t *out)
{
    const int ic0 = pass * g->Cb;
    size_t o = 0;
    for (int ic = 0; ic < g->Cb; ic++)
        for (int r = 0; r < g->SROWS; r++) {
            const int iy = g->iy_base + r;
            for (int x = 0; x < g->W; x++) {
                uint8_t v = GB_STRIP_POISON;
                if (iy >= 0 && iy < g->H)
                    v = in[((size_t)(ic0 + ic) * g->H + (size_t)iy) * g->W + x];
                out[o++] = v;
            }
        }
}

// One weight out of the blob by element index, whatever the blob stores it as.
// The 4-bit form is model/export.py's: flat, two per byte, low nibble first.
// No sign extension - this is a repack, and the sign lives in gemm_tile's
// `$signed(wreg[4*j +: 4])` on the far side of the wire.
static inline uint8_t gb_nib(const int8_t *wb, size_t e)
{
    const uint8_t byte = ((const uint8_t *)wb)[e >> 1];
    return (e & 1u) ? (uint8_t)(byte >> 4) : (uint8_t)(byte & 0x0Fu);
}

void gb_weights_slow(const gb_geom_t *g, const int8_t *wb, int pass,
                     int8_t *out)
{
    const int ic0 = pass * g->Cb;
    size_t o = 0;   // counts WEIGHTS, not bytes - the two differ at w4
    for (int k = 0; k < g->K; k++) {
        const int icl = k / 9, tap = k % 9;
        for (int gg = 0; gg < g->QG; gg++)
            for (int j = 0; j < GB_NMAC; j++) {
                const int oc = g->q0 + gg * GB_NMAC + j;
                const size_t e = ((size_t)oc * g->CIN + (ic0 + icl)) * 9 + tap;
                if (!g->w4) {
                    out[o++] = wb[e];
                } else if (o & 1u) {
                    out[o >> 1] = (int8_t)((uint8_t)out[o >> 1]
                                           | (uint8_t)(gb_nib(wb, e) << 4));
                    o++;
                } else {
                    // Writes the whole byte, so the high half is defined even
                    // if a caller handed in dirty scratch. The odd branch above
                    // may then OR into it.
                    out[o >> 1] = (int8_t)gb_nib(wb, e);
                    o++;
                }
            }
    }
}

// M7h. The same permutation with the two loops the other way round.
//
// gb_weights_slow() walks k outermost, so its inner loop reads Q weights that
// are (ic0+icl)*9 + tap apart for consecutive `oc` - a stride of CIN*9 bytes
// through a blob that lives in flash and is reached over XIP. Layer 7 is the
// worst of it: CIN=192 makes the stride 1728 bytes and Q=128 makes the span
// 221 KB, against an 8 KB XIP cache, so *every one* of the Q*K loads is a line
// fill and the line's other 1727 bytes are thrown away.
//
// Swapping the loops turns that read into the contiguous run it always was.
// The destination index is k*Q + (oc - q0), so with `oc` outermost the source
// walks k = icl*9 + tap forward through wb - exactly the K bytes at
// (oc*CIN + ic0)*9, since (ic0+icl)*9 + tap is ic0*9 + k - and it is the
// *write* that becomes strided instead. That is the trade worth making: the
// write span is w_len bytes, at most 2 KB by GB_WGTMAX, and it is SRAM.
//
// The two must agree byte for byte on every block of every layer, which is what
// test_gemm_plan.c checks against the real model rather than a synthetic one -
// this is a permutation, and a permutation that is wrong in a way the shapes
// still permit produces a plausible tensor rather than an error.
// M14 adds the nibble arm. Same loop shape, two channels at a time instead of
// one, because the destination byte holds two: lane u in the low half and lane
// u+1 in the high half, so a pair can be written with no read-modify-write.
//
// The pair is what makes the phase cheap. Consecutive output channels are
// CIN*9 elements apart in the blob, CIN is even for every 4-bit layer
// (gb_geom() refuses the alternative), so both sources of a pair sit at the
// same odd/even nibble phase and `ph` is computed once outside the k loop
// rather than tested inside it.
//
// It is also *faster* than the byte arm it replaces, which is worth saying
// because the opposite is the usual price of packing: half as many trips round
// the outer loop and half as many strided stores, for one extra shift-and-mask
// per weight.
//
// M17 Stage 1 takes the same idea one step further and gathers a whole lane
// *group* - eight channels, one 32-bit store - rather than a pair. The move is
// free of any new condition, which is the point: `gb_geom()` sets
// `Q = QG * GB_NMAC` and GB_NMAC is 8, so **Q is a multiple of 8 by
// construction** and there is no blocking this arm declines. The `Q % 8` guard
// below is therefore an assertion about that invariant rather than a fallback
// anybody reaches.
//
// The destination index falls out as the thing gemm_block.h:137 already says it
// is. Eight lanes occupy four adjacent destination bytes; QB is QG*4, so the
// stride between taps is QG words; and the word written for tap k and group gi
// lands at **`k*QG + gi`** - which is verbatim "the tile's word address is
// k*QG + g, and it needs no multiplier". The wide arm is not a trick played on
// the byte layout, it is the byte layout finally being written a word at a
// time.
//
// Costs and savings, counted rather than hoped for. Loads are unchanged at Q*K
// either way, because each source nibble is read exactly once per tap however
// the destination is grouped. Stores go from Q/2*K bytes to Q/8*K words - **4x
// fewer store instructions** - and the outer loop turns over 4x less. Against
// that, composing the word costs three more shift-or pairs than composing a
// byte did, and on an M33 an ORR with a constant shift is one instruction, so
// the trade is three ALU ops for three stores plus a quarter of the loop
// overhead. Layer 7 (Q=128, K=9, 384 builds a frame) goes from 576 strided byte
// stores per pass to 144 word stores.
//
// Two things are deliberately *not* required, and both were the obvious way to
// write this:
//
//   - No alignment guard on `out`. The store goes through a 4-byte memcpy,
//     which GCC turns into a single `str` on armv8-m where unaligned word
//     access is permitted. Requiring 4-byte alignment would have been quieter
//     to write and much worse to own: `wcache_slot()` hands back
//     `wcache + pass*w_len`, and w_len is K*Q/2, so slot alignment depends on
//     the layer's K. An arm that silently declines on some passes of some
//     layers is the kind of thing that shows up as an unexplained 3% and never
//     gets found.
//   - No `#ifdef` on the SDK. This file compiles with a bare `cc` for
//     test_gemm_plan.c and gen_gemm_vec.c, which is what makes the equivalence
//     check below free.
//
// It *is* little-endian-only - nibble j goes to bits 4j of a word that is then
// stored as bytes - so that is asserted at compile time rather than assumed.
// Both the M33 and every host this repo builds on are little-endian; the guard
// exists so that a port fails loudly at the compiler instead of quietly in a
// tensor full of right values in wrong places.
//
// Correctness costs nothing to check: test_gemm_plan.c already runs
// gb_weights() against gb_weights_slow() byte for byte on every pass of every
// candidate blocking of every layer of the real model. A permutation that is
// wrong in a way the shapes still permit produces a plausible tensor, so that
// check is the whole argument and it runs on the laptop.
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "gb_weights()'s wide arm packs nibble j at bits 4j of a stored word"
#endif

void GB_HOT(gb_weights)(const gb_geom_t *g, const int8_t *wb, int pass,
                        int8_t *out)
{
    const size_t Q = (size_t)g->Q, K = (size_t)g->K;
    const int ic0 = pass * g->Cb;

    if (!g->w4) {
        for (size_t u = 0; u < Q; u++) {
            const int8_t *src = wb + ((size_t)(g->q0 + (int)u) * g->CIN + ic0) * 9;
            int8_t *dst = out + u;
            for (size_t k = 0; k < K; k++) dst[k * Q] = src[k];
        }
        return;
    }

    const uint8_t *w8 = (const uint8_t *)wb;
    const size_t QB = Q / 2;                  // destination bytes per tap
    const size_t step = (size_t)g->CIN * 9;   // elements between channels

    // CIN is even for every 4-bit layer - gb_geom() refuses the alternative -
    // so `step` is even, every channel in the group sits at the same nibble
    // phase, and the channel-to-channel distance is a whole number of bytes.
    // Both facts are what let `ph` be computed once per group and the eight
    // sources be walked with an add instead of a shift.
    const size_t hstep = step >> 1;

    if ((Q & 7u) == 0u) {
        for (size_t u = 0; u < Q; u += GB_NMAC) {
            const size_t e0 = ((size_t)(g->q0 + (int)u) * (size_t)g->CIN
                               + (size_t)ic0) * 9;
            const size_t ph   = e0 & 1u;
            const uint8_t *base = w8 + (e0 >> 1);
            uint8_t *dst = (uint8_t *)out + (u >> 1);

            for (size_t k = 0; k < K; k++) {
                const size_t   i  = (k + ph) >> 1;
                const unsigned sh = 4u * (unsigned)((k + ph) & 1u);
                const uint8_t *s  = base + i;
                uint32_t word = 0;

                for (unsigned j = 0; j < GB_NMAC; j++) {
                    word |= (uint32_t)((*s >> sh) & 0x0Fu) << (4u * j);
                    s += hstep;
                }
                __builtin_memcpy(dst + k * QB, &word, 4);
            }
        }
        return;
    }

    // Unreachable for any blocking gb_geom() will produce, and kept because it
    // is the definition the wide arm above is an optimisation of.
    for (size_t u = 0; u < Q; u += 2) {
        const size_t e0 = ((size_t)(g->q0 + (int)u) * (size_t)g->CIN
                           + (size_t)ic0) * 9;
        const size_t ph = e0 & 1u;
        const uint8_t *s0 = w8 + (e0 >> 1);
        const uint8_t *s1 = s0 + hstep;
        uint8_t *dst = (uint8_t *)out + (u >> 1);

        for (size_t k = 0; k < K; k++) {
            const size_t   i  = (k + ph) >> 1;
            const unsigned sh = 4u * (unsigned)((k + ph) & 1u);
            dst[k * QB] = (uint8_t)(((s0[i] >> sh) & 0x0Fu)
                                 | (((s1[i] >> sh) & 0x0Fu) << 4));
        }
    }
}

void gb_golden(const fgx_model_t *m, const fgx_desc_t *d, const gb_geom_t *g,
               const void *in, int32_t *out)
{
    size_t o = 0;
    for (int gg = 0; gg < g->QG; gg++)
        for (int j = 0; j < GB_NMAC; j++) {
            const int oc = g->q0 + gg * GB_NMAC + j;
            for (int p = 0; p < g->P; p++) {
                const int pos = g->ox0 + p;
                out[o++] = fgx_conv_acc(m, d, in, oc,
                                        g->oy0 + pos / g->OW, pos % g->OW);
            }
        }
}

// ---------------------------------------------------------------------------
// M15. The requantize table, and the codes it produces.
// ---------------------------------------------------------------------------
size_t gb_rqp(const fgx_model_t *m, const fgx_desc_t *d, const gb_geom_t *g,
              uint8_t *out)
{
    // The geometry conditions are gb_geom()'s, already resolved into g->rq; the
    // one it could not check is the model's.
    if (!g->rq)                                 return 0;
    if (fgx_emits_float(m, (uint32_t)g->layer)) return 0;

    const int32_t *bias = (const int32_t *)((const uint8_t *)m->biases + d->b_off);
    const float   *mult = (const float   *)((const uint8_t *)m->mults  + d->m_off);

    // gb_golden()'s loop with the position dimension removed, and that is the
    // contract: entry index is the accumulator index divided by P, which is what
    // gemm_tile's walk computes for free because the position counter is its
    // innermost.
    size_t o = 0;
    for (int gg = 0; gg < g->QG; gg++)
        for (int j = 0; j < GB_NMAC; j++) {
            const int oc = g->q0 + gg * GB_NMAC + j;
            int32_t   M;
            const int s = fgx_rq_pick(mult[oc], &M);
            if (!gw_rqp_pack(out + o, bias[oc], M, s)) return 0;
            o += GW_RQP_BYTES;
        }
    return o;
}

void gb_golden_code(const fgx_model_t *m, const fgx_desc_t *d,
                    const gb_geom_t *g, const int32_t *acc, uint8_t *out)
{
    const int32_t *bias = (const int32_t *)((const uint8_t *)m->biases + d->b_off);
    const float   *mult = (const float   *)((const uint8_t *)m->mults  + d->m_off);

    // (M, s) is picked once per channel rather than once per accumulator,
    // matching what the tile does - it reads one table entry and holds it for
    // all P positions - and matching fgx_conv_fast(), which hoists the pick out
    // of its position loop for the same reason.
    size_t o = 0;
    for (int gg = 0; gg < g->QG; gg++)
        for (int j = 0; j < GB_NMAC; j++) {
            const int oc = g->q0 + gg * GB_NMAC + j;
            int32_t   M;
            const int s = fgx_rq_pick(mult[oc], &M);
            for (int p = 0; p < g->P; p++, o++)
                out[o] = fgx_code_fixed(acc[o], bias[oc], M, s);
        }
}
