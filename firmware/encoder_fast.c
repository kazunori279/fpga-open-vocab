// M5b: im2col + blocked int8 GEMM. See encoder_fast.h for why.
//
// **Why this is allowed to be bit-exact with encoder.c despite reordering the
// sum.** The accumulator is int32 on both sides, and int32 addition is
// associative and commutative, so the order the taps arrive in cannot change
// the result. It cannot overflow into implementation-defined territory either:
// the widest layer is conv6 at K = 192*3*3 = 1728 taps, and the largest
// possible term is 255 * 127 = 32,385, so |acc| <= 55,961,280 - 38x inside
// int32. That bound is a property of the model shape, so it is stated here
// rather than asserted at runtime; a re-export that widened a layer past
// ~66,000 taps would need it rechecked.
//
// The other place exactness could quietly go is padding. fgx_conv_ref() does
// not pad - it *skips* out-of-range taps with `continue`. im2col has to
// materialize something, and the only value that means "contributed nothing"
// is a literal 0 byte. That is right for the unsigned layers too, which is
// worth saying because it looks wrong: code 0 does not dequantize to 0.0. But
// the reference adds no term at all for those taps, so a 0 byte - which
// contributes 0 * w = 0 to the accumulator - is the match. The zero point
// never enters the accumulator on either side.

#include <math.h>
#include <string.h>

#include "encoder_fast.h"

#if defined(FGX_DSP_SHIM)
  // Host build with the intrinsics emulated, so the SMLAD loop below can be
  // proven bit-exact on the laptop instead of on the one PRG strap we get. See
  // dsp_shim.h - it is the same source lines either way.
  #include "dsp_shim.h"
  #define FGX_HAVE_DSP 1
#elif defined(__ARM_FEATURE_DSP) && __ARM_FEATURE_DSP
  #include <arm_acle.h>
  #define FGX_HAVE_DSP 1
#else
  #define FGX_HAVE_DSP 0
#endif

bool fgx_fast_have_dsp(void)
{
    return FGX_HAVE_DSP != 0;
}

// The two ways a column byte can be read. Which one applies is d->unsigned_in,
// and getting it wrong is a factor-of-two error that still produces a
// plausible-looking tensor, so it is threaded through the kernel by name
// rather than inferred anywhere.
static inline int32_t ldb_u(const uint8_t *p, int j) { return (int32_t)p[j]; }
static inline int32_t ldb_s(const uint8_t *p, int j) { return (int32_t)(int8_t)p[j]; }

// --- the inner loop --------------------------------------------------------
// Two columns against one weight row. Blocking the columns rather than the
// output channels is what shares the weight unpack, and the weight row is the
// operand coming from flash XIP - the columns are in SRAM.

#define FGX_DEF_DOT2_C(NAME, LDB)                                             \
static void NAME(const uint8_t *a0, const uint8_t *a1, const int8_t *w,       \
                 int K, int32_t *o0, int32_t *o1)                             \
{                                                                             \
    int32_t s0 = 0, s1 = 0;                                                   \
    for (int j = 0; j < K; j++) {                                             \
        const int32_t wj = (int32_t)w[j];                                     \
        s0 += LDB(a0, j) * wj;                                                \
        s1 += LDB(a1, j) * wj;                                                \
    }                                                                         \
    *o0 = s0;                                                                 \
    *o1 = s1;                                                                 \
}

FGX_DEF_DOT2_C(dot2_c_u, ldb_u)
FGX_DEF_DOT2_C(dot2_c_s, ldb_s)

#if FGX_HAVE_DSP
// A 32-bit load that does not assume alignment. Weight rows start at oc*K and
// K is 27 for conv0, so they are not all word-aligned. ARMv8-M Mainline does
// unaligned LDR in hardware and GCC compiles this memcpy to exactly that - the
// disassembly is a plain `ldr` - so it costs nothing and removes a whole class
// of "works until conv0" bug.
static inline uint32_t ld32(const void *p)
{
    uint32_t v;
    memcpy(&v, p, sizeof v);
    return v;
}

// CMSIS-NN's read_and_pad shape. __sxtb16 pulls bytes 0 and 2 out of a word as
// a 16-bit pair; __sxtb16(__ror(x, 8)) pulls bytes 1 and 3. Applying the same
// split to both operands makes the two SMLADs cover all four products - in the
// order 0,2,1,3 rather than 0,1,2,3, which is exactly the reordering the int32
// argument at the top of this file licenses.
//
// __uxtb16 for the unsigned layers. Its result is uint16x2_t but every lane is
// 0..255, so reading it back as int16 lanes is value-preserving, not a
// reinterpretation that happens to work.
#define FGX_DEF_DOT2_DSP(NAME, UNPACK, LDB)                                   \
static void NAME(const uint8_t *a0, const uint8_t *a1, const int8_t *w,       \
                 int K, int32_t *o0, int32_t *o1)                             \
{                                                                             \
    int32_t s0 = 0, s1 = 0;                                                   \
    int j = 0;                                                                \
    for (; j + 4 <= K; j += 4) {                                              \
        const uint32_t wv  = ld32(w + j);                                     \
        const int16x2_t w02 = __sxtb16(wv);                                   \
        const int16x2_t w13 = __sxtb16(__ror(wv, 8));                         \
        const uint32_t v0 = ld32(a0 + j);                                     \
        s0 = __smlad(UNPACK(v0),            w02, s0);                         \
        s0 = __smlad(UNPACK(__ror(v0, 8)),  w13, s0);                         \
        const uint32_t v1 = ld32(a1 + j);                                     \
        s1 = __smlad(UNPACK(v1),            w02, s1);                         \
        s1 = __smlad(UNPACK(__ror(v1, 8)),  w13, s1);                         \
    }                                                                         \
    /* Scalar epilogue for the K % 4 tail - only conv0, whose K is 27. The     \
       alternative, zero-padding the rows out to a multiple of 4, would read   \
       past the last output channel's weights and, on the last layer, past the \
       end of the blob. */                                                    \
    for (; j < K; j++) {                                                      \
        const int32_t wj = (int32_t)w[j];                                     \
        s0 += LDB(a0, j) * wj;                                                \
        s1 += LDB(a1, j) * wj;                                                \
    }                                                                         \
    *o0 = s0;                                                                 \
    *o1 = s1;                                                                 \
}

FGX_DEF_DOT2_DSP(dot2_dsp_u, __uxtb16, ldb_u)
FGX_DEF_DOT2_DSP(dot2_dsp_s, __sxtb16, ldb_s)
#endif

typedef void (*fgx_dot2_fn)(const uint8_t *, const uint8_t *, const int8_t *,
                            int, int32_t *, int32_t *);

// --- im2col ----------------------------------------------------------------

size_t fgx_fast_col_bytes(const fgx_model_t *m)
{
    size_t worst = 0;
    for (uint32_t i = 0; i + 1 < m->hdr->n_layers; i++) {
        const fgx_desc_t *d = &m->desc[i];
        size_t K = (size_t)d->cin * d->ksize * d->ksize;
        size_t kpad = (K + 3u) & ~(size_t)3u;   // keeps every row word-aligned
        size_t bytes = kpad * FGX_TILE;
        if (bytes > worst)
            worst = bytes;
    }
    return worst;
}

// Lays out FGX_TILE output positions as rows of K taps each, in the same tap
// order fgx_conv_ref() visits: ic major, then ky, then kx. Bytes are copied
// raw; the layer's signedness is applied in the dot product, not here, so this
// function is identical for signed and unsigned inputs.
static void fgx_im2col(const fgx_desc_t *d, const void *in, uint8_t *col,
                       int kpad, int t0, int n)
{
    const uint8_t *src = (const uint8_t *)in;
    const int k = d->ksize, H = d->h, W = d->w, OW = d->ow;
    const int CIN = d->cin;
    const int stride = (d->h == d->oh) ? 1 : 2;
    const int pad = 1;

    for (int t = 0; t < n; t++) {
        uint8_t *c = col + (size_t)t * kpad;
        const int p   = t0 + t;
        const int oy  = p / OW;
        const int ox  = p - oy * OW;
        const int iy0 = oy * stride - pad;
        const int ix0 = ox * stride - pad;

        // Interior: the whole kxk window is inside the image, so the row copy
        // needs no per-tap test at all. That is 94% of positions at 32x32 and
        // is where the win over fgx_conv_ref() actually comes from - the
        // reference pays those two bounds tests once per output *channel*,
        // this pays them once per output *pixel*.
        if (iy0 >= 0 && ix0 >= 0 && iy0 + k <= H && ix0 + k <= W) {
            for (int ic = 0; ic < CIN; ic++) {
                const uint8_t *s = src + (size_t)ic * H * W
                                       + (size_t)iy0 * W + ix0;
                uint8_t *dp = c + (size_t)ic * k * k;
                for (int ky = 0; ky < k; ky++, s += W, dp += k)
                    memcpy(dp, s, (size_t)k);
            }
        } else {
            for (int ic = 0; ic < CIN; ic++) {
                const size_t plane = (size_t)ic * H * W;
                uint8_t *dp = c + (size_t)ic * k * k;
                for (int ky = 0; ky < k; ky++) {
                    const int iy = iy0 + ky;
                    for (int kx = 0; kx < k; kx++) {
                        const int ix = ix0 + kx;
                        const bool inside = iy >= 0 && iy < H && ix >= 0 && ix < W;
                        dp[ky * k + kx] = inside
                            ? src[plane + (size_t)iy * W + ix]
                            : 0;
                    }
                }
            }
        }
        // [K, kpad) is never read: the dot products stop at K and the tail is
        // handled scalar. Nothing to clear.
    }
}

// --- the layer -------------------------------------------------------------

void fgx_conv_fast(const fgx_model_t *m, const fgx_desc_t *d,
                   const void *in, void *out, bool out_float,
                   void *col, bool use_dsp)
{
    const int32_t *bias = (const int32_t *)((const uint8_t *)m->biases + d->b_off);
    const float   *mult = (const float   *)((const uint8_t *)m->mults  + d->m_off);

    const int k    = d->ksize;
    const int K    = d->cin * k * k;
    const int kpad = (K + 3) & ~3;
    const int N    = d->oh * d->ow;
    const int COUT = d->cout;

    uint8_t *cols = (uint8_t *)col;

    fgx_dot2_fn dot2;
#if FGX_HAVE_DSP
    if (use_dsp) dot2 = d->unsigned_in ? dot2_dsp_u : dot2_dsp_s;
    else         dot2 = d->unsigned_in ? dot2_c_u   : dot2_c_s;
#else
    (void)use_dsp;
    dot2 = d->unsigned_in ? dot2_c_u : dot2_c_s;
#endif

    int32_t acc[FGX_TILE];

    // Only touched by the 4-bit layers; at wbits 8 fgx_wchan() hands back a
    // pointer into the blob and this is dead stack. 1.7 KB, which is why it is
    // here and not one per output channel.
    //
    // The unpack lands inside both loops, so a 4-bit layer pays it once per
    // (tile, output channel) rather than once per output channel: the tile loop
    // is outermost because im2col has to be, and a weight row unpacked for one
    // tile is gone by the next. That is ceil(N/FGX_TILE) passes instead of one,
    // and it is why the measured cost below is a few percent rather than nil -
    // 5.1 M nibble expansions against 159 M MACs. Holding a whole layer's
    // weights unpacked would remove it and cost 442 KB for conv7, which the
    // RP2354A does not have.
    int8_t wbuf[FGX_MAX_WCHAN];

    for (int t0 = 0; t0 < N; t0 += FGX_TILE) {
        const int n = (N - t0 < FGX_TILE) ? N - t0 : FGX_TILE;
        fgx_im2col(d, in, cols, kpad, t0, n);

        for (int oc = 0; oc < COUT; oc++) {
            const int8_t *wrow = fgx_wchan(m, d, oc, (size_t)K, wbuf);

            int t = 0;
            for (; t + 1 < n; t += 2)
                dot2(cols + (size_t)t * kpad, cols + (size_t)(t + 1) * kpad,
                     wrow, K, &acc[t], &acc[t + 1]);
            if (t < n) {
                // Odd tail. No shape in this model reaches it - every N is a
                // multiple of 32 or is 16 - but a re-export with an odd OW
                // would, and silently dropping a column is not a failure mode
                // worth leaving open. Second slot is a duplicate and discarded.
                int32_t spare;
                dot2(cols + (size_t)t * kpad, cols + (size_t)t * kpad,
                     wrow, K, &acc[t], &spare);
            }

            // Requantize. This used to be a copy of fgx_conv_ref()'s epilogue,
            // marked "verbatim" and therefore something to keep in step by
            // hand; M7c's sequencer needed a third copy, so it became
            // fgx_requant() in encoder.h instead. Still inlined, so the
            // generated code - and M5b's recorded 3,358 ms - is unchanged.
            //
            // M15 split it: codes take the fixed-point path, floats keep the
            // multiply. On this core that is SMULL + ASR + USAT against
            // VMUL + VCVTR + USAT, so the substitution is not a slowdown even
            // though its reason for existing is the fabric, not this loop.
            // The pick is per (tile, channel) rather than per channel because
            // the tile loop is outermost - one frexpf against FGX_TILE codes.
            const int32_t b  = bias[oc];
            const float   mu = mult[oc];
            int32_t rq_m = 0;
            const int rq_s = out_float ? 0 : fgx_rq_pick(mu, &rq_m);
            const size_t base = (size_t)oc * N + t0;
            for (int i = 0; i < n; i++) {
                if (out_float)
                    ((float *)out)[base + i] =
                        fgx_requant(acc[i], b, mu, d->relu != 0);
                else
                    ((uint8_t *)out)[base + i] =
                        fgx_code_fixed(acc[i], b, rq_m, rq_s);
            }
        }
    }
}

void fgx_run_fast(const fgx_model_t *m, const int8_t *input, float *embed,
                  void *a, void *b, void *col, bool use_dsp)
{
    const uint32_t n = m->hdr->n_layers;
    const uint32_t last_conv = n - 2;

    const void *src = input;
    void *dst = a;

    for (uint32_t i = 0; i < last_conv + 1; i++) {
        fgx_layer_mark(i, true);
        fgx_conv_fast(m, &m->desc[i], src, dst, i == last_conv, col, use_dsp);
        fgx_layer_mark(i, false);
        src = dst;
        dst = (dst == a) ? b : a;
    }

    // Shared with fgx_run() rather than copied: 12.7 ms, and the one part of
    // the pipeline that is float end to end, so there is nothing here for
    // im2col to restructure. It does not speed up at all, which is why it goes
    // from 0.05% of the reference frame to 0.4% of the tuned one.
    fgx_layer_mark(n - 1, true);
    fgx_pool_head(m, (const float *)src, (uint8_t *)dst, embed);
    fgx_layer_mark(n - 1, false);
}
