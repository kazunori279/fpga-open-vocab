// M5: the int8 student encoder, in portable C.
//
// **This file and encoder.c are the integer contract for M6.** quantize.py is
// fake quantization - it rounds to the integer grid but computes in float - so
// it is the wrong thing for the FPGA to match. What M6's GEMM tile must
// reproduce bit-exactly is `acc`, the int32 accumulator in fgx_conv().
//
// Deliberately free of Pico SDK headers, so the same source compiles as a macOS
// binary and runs against the same weights.bin. Correctness gets settled on the
// host and only the latency measurement needs hardware - a rule adopted when
// every flash cost a physical PRG-GND strap, and kept after M7i found a working
// remote reboot, because it is cheaper to be right on the laptop either way.

#ifndef ENCODER_H
#define ENCODER_H

#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Not a Pico SDK header - arm_acle.h ships with the compiler, and only exists
// when the target has the saturating instructions fgx_sat8() wants. The host
// build takes neither this nor the branch that uses it.
#if defined(__ARM_FEATURE_SAT) && __ARM_FEATURE_SAT
  #include <arm_acle.h>
#endif

#define FGX_MAGIC   0x35584746u   // "FGX5" little-endian

// 2 added per-layer weight width. The bump is not cosmetic: a v1 blob read as
// v2 would take `wbits` out of what used to be a `reserved` float and produce
// weights that are wrong rather than absent. fgx_open() rejects the mismatch,
// so a stale weights.bin is a loud parse failure and not a bad embedding.
#define FGX_VERSION 2

#define FGX_KIND_CONV   0
#define FGX_KIND_LINEAR 1

// Mirrors DESC_FMT in model/export.py, which asserts this is 32 bytes. Every
// field is naturally aligned, so no compiler needs padding to lay it out this
// way - but it is checked at load time rather than trusted.
typedef struct {
    uint8_t  kind;
    uint8_t  relu;
    uint8_t  unsigned_in;   // input codes are 0..255 rather than -127..127
    uint8_t  ksize;
    uint16_t cin, cout;
    uint16_t h, w, oh, ow;
    uint32_t w_off;         // [cout][cin][k][k], `wbits` wide - see below
    uint32_t b_off;         // int32  [cout], in accumulator units
    uint32_t m_off;         // float  [cout], dequant * requant folded together

    // 8 or 4. At 4 the tensor is packed two weights per byte, low nibble
    // first, and w_off counts bytes into a half-length section. M14 measured
    // int4 as free for accuracy provided conv0 and the head stay at 8, so this
    // is per layer and not a global flag.
    //
    // The packing runs flat across the whole tensor, which means channel `oc`
    // starts on a byte boundary only if cin*k*k is even. export.py asserts
    // that for every 4-bit layer; conv0 is the one layer with an odd count
    // (3*3*3 = 27) and it is exactly the layer the accuracy work pinned to 8.
    uint8_t  wbits;
    uint8_t  rsv[3];
} fgx_desc_t;

// Largest cin*k*k in the shipped student: conv7 is 192 -> 256 at 3x3, so 1728.
// The nibble unpack below materializes one output channel at a time and this
// bounds that buffer. fgx_open() checks it rather than trusting it, because
// widening student.py's STAGES is a one-line change that would otherwise
// overflow a stack buffer somewhere far away from the edit.
#define FGX_MAX_WCHAN 1728

typedef struct {
    uint32_t magic, version, n_layers, in_size, in_ch, embed_dim, desc_size;
    float    in_scale;       // quantizes the normalized image for conv0
    float    head_in_scale;  // quantizes the pooled vector for the head
} fgx_header_t;

typedef struct {
    const fgx_header_t *hdr;
    const fgx_desc_t   *desc;
    const int8_t       *weights;
    const int32_t      *biases;
    const float        *mults;
    size_t              scratch;   // bytes needed for EACH of the two buffers
} fgx_model_t;

// Parses the blob in place - nothing is copied, so `blob` must outlive `m`.
// Returns false and leaves *m untouched if the blob is malformed.
bool   fgx_open(fgx_model_t *m, const void *blob, size_t len);

// Worst-case bytes for one of the two ping-pong buffers fgx_run() needs.
size_t fgx_scratch_bytes(const fgx_model_t *m);

// Optional per-layer instrumentation. encoder.c ships a no-op; override it to
// get a breakdown. Called with `entering` true before a layer and false after,
// where layer n_layers-1 is the pool + head. 18 calls per frame against 159 M
// MACs, so it does not perturb what it measures.
//
// M6 has to choose which layers move to the FPGA, and that choice is made from
// this breakdown - a single ms/frame figure says the accelerator is worth
// building without saying what to build first.
void   fgx_layer_mark(uint32_t layer, bool entering);

// input: int8 codes, CHW, in_ch * in_size * in_size of them.
// embed: embed_dim floats, NOT L2-normalized (that is the caller's job, in
//        float, exactly as the PyTorch reference leaves it).
// a, b:  two buffers of fgx_scratch_bytes(m), 4-byte aligned.
void   fgx_run(const fgx_model_t *m, const int8_t *input, float *embed,
               void *a, void *b);

// --- shared with encoder_fast.c (M5b) --------------------------------------
// Exposed, not duplicated. The tuned kernel has to be checked layer by layer
// against this one - comparing only the 512-d embedding says a frame is wrong
// without saying where - and the pool + head tail is identical in both, so
// copying it would leave two versions of the arithmetic to keep in step.

// True for the layers whose output is float rather than uint8 codes.
bool   fgx_emits_float(const fgx_model_t *m, uint32_t i);

// One output channel's weights as a plain int8_t[n], whatever they are stored
// as. At wbits 8 that is a pointer into the blob and `buf` goes unused; at 4 it
// is `buf`, filled by sign-extending nibbles. `buf` must hold n bytes and n is
// bounded by FGX_MAX_WCHAN, which fgx_open() checks.
//
// **Per output channel and not per multiply-accumulate**, which is the whole
// reason this is a function and not a test inside the dot product. The inner
// loops run 159 M times a frame and M5's 31,798 ms and M5b's 3,358 ms are
// baselines the repo re-derives every boot; teaching either loop to ask how
// wide a weight is would move them. Here the unpack is amortized - over every
// output position in fgx_conv_ref(), over FGX_TILE of them in fgx_conv_fast() -
// and costs one pass over at most FGX_MAX_WCHAN bytes.
//
// Shared rather than copied for the same reason fgx_requant() is: encoder_fast.c
// has to stay bit-exact against encoder.c, and a second transcription of a
// nibble sign-extension is where that would quietly stop being true.
const int8_t *fgx_wchan(const fgx_model_t *m, const fgx_desc_t *d,
                        int oc, size_t n, int8_t *buf);

// One convolution, the reference implementation. This is the golden kernel:
// bit-exact against numpy on the host and against it on the RP2354A.
void   fgx_conv_ref(const fgx_model_t *m, const fgx_desc_t *d,
                    const void *in, void *out, bool out_float);

// The int32 accumulator for a single output value, before bias, requantization
// and clamping - which is exactly the boundary M6's GEMM tile terminates at, so
// this is what its output gets compared against.
//
// fgx_conv_ref() cannot serve that purpose itself. It only exposes the
// requantized result, and `(float)(acc + b) * mu` is lossy in the wrong
// direction: max |acc| = 1728*255*127 needs 26 bits and float32 has a 24-bit
// mantissa, so a "bit-exact" check through it would silently pass on
// accumulators that differ. Both this and fgx_conv_ref() call one shared
// static inline, so there is a single copy of the arithmetic in encoder.c.
int32_t fgx_conv_acc(const fgx_model_t *m, const fgx_desc_t *d,
                     const void *in, int oc, int oy, int ox);

// The epilogue: bias, requantize, ReLU, and - for the layers that emit codes -
// clamp to a byte. Three callers now, which is why it stopped being a copied
// fragment: fgx_conv_ref(), encoder_fast.c's tuned kernel, and M7's sequencer,
// which needs it on its own because the tile terminates at the accumulator and
// hands back nothing else.
//
// `static inline` in the header rather than a call in encoder.c, for the reason
// conv_acc_one() gives there: this runs once per output pixel, and M5's
// 31,798 ms and M5b's 3,358 ms are recorded baselines that a function call per
// pixel would move. Inlined, the code generated is what it always was.
static inline float fgx_requant(int32_t acc, int32_t bias, float mult, bool relu)
{
    const float y = (float)(acc + bias) * mult;
    return (relu && y < 0.0f) ? 0.0f : y;
}

// The two halves of the byte conversion, each with a host definition and a
// one-instruction Cortex-M33 equivalent. The host definition is the contract;
// the target path has to match it, and does so by construction rather than by
// luck - see the reasoning on each.
//
// This matters more than the instruction count suggests. scatter() in m7.c runs
// this 356,352 times per frame, once per output element of the whole encoder.

// Rounding. lrintf() is round-half-to-even, matching numpy's rint() in
// export.py; round() would be half-away-from-zero and would disagree on exact
// .5 accumulations, which do occur.
//
// arm-none-eabi-gcc will not inline it. `bl lrintf` is what comes out at -O2
// whether the call is written as lrintf(), as __builtin_lrintf(), with
// -fno-math-errno, or with -ffast-math - all four were checked - and newlib's
// lrintf is ~30 instructions of exponent extraction and bit reassembly.
// VCVTR.S32.F32 is one instruction and rounds by the current FPSCR mode, which
// is RN, round-to-nearest-even, out of reset. Nothing changes it: the linked
// image contains no `vmsr ... fpscr` at all, ours or the SDK's or newlib's,
// which is one grep and worth repeating if this ever disagrees with the host.
// Same rounding, so the substitution is exact for every input the epilogue can
// produce: acc + bias is an int32, mult is a small finite positive scale, so y
// is always finite and always well inside int32 - the range where VCVTR's
// saturating-and-flag behaviour and lrintf's undefined behaviour would differ
// is not reachable.
//
// Inline asm rather than an intrinsic because ACLE has none for it, and the
// compiler will not generate it from the C.
static inline int32_t fgx_rint(float y)
{
#if defined(__ARM_FP) && defined(__ARM_ARCH_PROFILE) && (__ARM_ARCH_PROFILE == 'M')
    int32_t r;
    __asm__("vcvtr.s32.f32 %0, %1" : "=t"(r) : "t"(y));
    return r;
#else
    return (int32_t)lrintf(y);
#endif
}

// Clamping. The ternary is two compares and up to four branches; USAT is one
// instruction with no branch at all, and unsigned saturation to 8 bits is
// exactly [0, 255] with negatives going to 0. __usat() is ACLE, so no asm here.
static inline uint8_t fgx_sat8(int32_t r)
{
#if defined(__ARM_FEATURE_SAT) && __ARM_FEATURE_SAT
    return (uint8_t)__usat(r, 8);
#else
    return r < 0 ? 0 : (r > 255 ? 255 : (uint8_t)r);
#endif
}

static inline uint8_t fgx_code(float y)
{
    return fgx_sat8(fgx_rint(y));
}

// ---------------------------------------------------------------- M15 -----
// The fixed-point requantize contract, for the epilogue that moves into the
// fabric. `mult` is replaced per output channel by (M, s) with
// M = round(mult * 2^s) held in [2^17, 2^18), so 18 bits of mantissa and a
// 6-bit shift carry every scale the exported model uses: measured over
// weights.bin the code-emitting layers span mult 1.8e-4 .. 0.162, giving
// s in [20, 30], and mult is positive in all of them.
//
// This is not an approximation of the float path - it is a different and
// slightly better one. (acc + bias) * M is an exact integer product where
// (float)(acc + bias) * mult rounds twice: once converting a 26-bit
// accumulator into a 24-bit mantissa, once on the product. The two disagree
// only where the float path's first rounding already moved the value, and
// Stage 0 counts how often that changes the byte.
//
// relu is absent on purpose. fgx_sat8() maps every negative to 0, so
// relu-then-saturate-to-[0,255] is saturate-to-[0,255]. The fabric needs no
// relu logic, and neither does this.
#define FGX_RQ_MBITS 18
#define FGX_RQ_SMAX  63

// Pick (M, s) for one channel. Returns the shift; *pm gets the multiplier.
// Chosen so M lands in [2^(MBITS-1), 2^MBITS) - the largest M that fits, i.e.
// the smallest relative quantization of `mult` the width allows.
//
// Closed form rather than a search. frexpf splits mult into f * 2^e with
// f in [0.5, 1), so f * 2^(e+s) lands in [2^17, 2^18) exactly when e + s is
// MBITS, and the shift is MBITS - e with no loop and no comparison. Both
// scalings are by powers of two and therefore exact, and (v + 0.5f) for
// v < 2^18 needs 20 mantissa bits against float32's 24, so the whole thing is
// exact in single precision - which matters, because the RP2350 would
// otherwise call into soft-float doubles ~1,000 times a frame.
//
// The obvious alternative, growing s until mult * 2^s clears 2^17, was checked
// against this on all 1,568 exported channels and agrees on every one.
//
// arm-none-eabi-gcc emits `bl frexpf` and `bl ldexpf` rather than inlining
// them, and unlike fgx_rint() that is left alone, because the call count is
// bounded by channels and not by pixels. fgx_conv_ref() picks once per channel
// (1,376 a frame); fgx_conv_fast() picks once per tile and channel, because
// its tile loop is outermost, which is 11,008 a frame - about 5.9 ms at
// 150 MHz against M5b's recorded 3,358, so 0.2%. Bit-twiddling the exponent
// field would remove it and would need its own correctness argument for a
// fifth of a percent.
static inline int fgx_rq_pick(float mult, int32_t *pm)
{
    if (!(mult > 0.0f)) {                 // never happens in the exported model
        *pm = 0;                          // but a zero scale must not shift by
        return 1;                         // -1 in fgx_code_fixed() below
    }
    int e;
    (void)frexpf(mult, &e);
    int s = FGX_RQ_MBITS - e;
    if (s > FGX_RQ_SMAX) s = FGX_RQ_SMAX;
    int32_t M = (int32_t)(ldexpf(mult, s) + 0.5f);
    // Rounding can push M to exactly 2^MBITS; back off one bit if it does.
    if (M >= (int32_t)(1u << FGX_RQ_MBITS) && s > 0) {
        s--;
        M = (int32_t)(ldexpf(mult, s) + 0.5f);
    }
    *pm = M;
    return s;
}

// The epilogue itself. 28x18 -> 46 bits, so the product is formed in 64 bits
// here and in a single LE-built multiplier there.
static inline uint8_t fgx_code_fixed(int32_t acc, int32_t bias, int32_t M, int s)
{
    const int64_t t = (int64_t)(acc + bias) * (int64_t)M;
    const int64_t r = (t + ((int64_t)1 << (s - 1))) >> s;
    return fgx_sat8((int32_t)(r < 0 ? 0 : (r > 255 ? 255 : r)));
}

// Global average pool over the last conv's float output, then the 256->512
// head. `src` is that float tensor, `codes` a scratch buffer of at least
// desc[n-2].cout bytes. Never worth tuning: 12.7 ms, which is 0.4% even of the
// tuned 3.36 s frame - and it is float, so it is also the one layer M6 cannot
// offload.
void   fgx_pool_head(const fgx_model_t *m, const float *src, uint8_t *codes,
                     float *embed);

#endif
