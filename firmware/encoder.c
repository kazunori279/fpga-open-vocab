// M5: the int8 student encoder. See encoder.h for why this file is the
// integer contract M6 has to match.
//
// The arithmetic, stated once so it is not spread across the loops:
//
//     acc[c]  = sum over (ic, ky, kx) of  x * w[c]        int32
//     out[c]  = (acc[c] + bias[c]) * mult[c]              float
//     out[c]  = max(out[c], 0)                            if relu
//     code    = clamp(lrintf(out[c]), 0, 255)             if the layer emits codes
//
// bias[] is already in accumulator units and mult[] already folds dequantization
// and requantization together, both computed by model/export.py. Nothing here
// needs to know a scale.
//
// **The accumulator is int32 and must stay int32.** It is the only value M6 will
// be checked against, and widening it here - which C will do silently the moment
// an int64 creeps into the expression - would make the reference unmatchable by
// hardware that accumulates in 32 bits.
//
// Layout is CHW throughout, matching numpy, so a mismatch against the golden
// vectors localizes to arithmetic rather than to indexing.

#include <math.h>
#include <string.h>

#include "encoder.h"

// conv7 emits float rather than codes: its output is 4x4x256, so keeping it in
// float costs 16 KB and avoids quantizing immediately before a global average
// pool that would wash the codes out anyway.
bool fgx_emits_float(const fgx_model_t *m, uint32_t i)
{
    return i + 2 >= m->hdr->n_layers;   // last conv, and the head
}

bool fgx_open(fgx_model_t *m, const void *blob, size_t len)
{
    if (len < sizeof(fgx_header_t) + 12)
        return false;

    const fgx_header_t *h = (const fgx_header_t *)blob;
    if (h->magic != FGX_MAGIC || h->version != FGX_VERSION)
        return false;
    // export.py asserts 32 on its side; checking here turns a format drift into
    // a clean refusal instead of garbage offsets and a plausible wrong answer.
    if (h->desc_size != sizeof(fgx_desc_t))
        return false;
    if (h->n_layers < 2 || h->n_layers > 64)
        return false;

    const uint8_t *p = (const uint8_t *)blob;
    const uint32_t *sect = (const uint32_t *)(p + sizeof(fgx_header_t));
    uint32_t w_len = sect[0], b_len = sect[1], m_len = sect[2];

    size_t off = sizeof(fgx_header_t) + 12;
    size_t desc_bytes = (size_t)h->n_layers * sizeof(fgx_desc_t);
    if (len < off + desc_bytes + w_len + b_len + m_len)
        return false;

    // Two per-layer invariants the unpack path depends on and cannot check
    // cheaply once it is running: a width it knows how to read, and a channel
    // stride that keeps each output channel byte-aligned when packed. Both are
    // asserted at export; both are refused here, because a weights.bin is a
    // file on a filesystem and the export that wrote it is not present.
    const fgx_desc_t *dd = (const fgx_desc_t *)(p + off);
    for (uint32_t i = 0; i < h->n_layers; i++) {
        const size_t n = (size_t)dd[i].cin * dd[i].ksize * dd[i].ksize;
        if (dd[i].wbits != 8 && dd[i].wbits != 4)
            return false;
        if (dd[i].wbits == 4 && (n & 1u))
            return false;
        if (n > FGX_MAX_WCHAN)
            return false;
    }

    m->hdr     = h;
    m->desc    = (const fgx_desc_t *)(p + off);
    m->weights = (const int8_t  *)(p + off + desc_bytes);
    m->biases  = (const int32_t *)(p + off + desc_bytes + w_len);
    m->mults   = (const float   *)(p + off + desc_bytes + w_len + b_len);
    m->scratch = fgx_scratch_bytes(m);
    return true;
}

size_t fgx_scratch_bytes(const fgx_model_t *m)
{
    // Every layer reads one buffer and writes the other, so each buffer has to
    // hold the largest tensor either side of any single layer. Float outputs
    // count 4 bytes per element.
    size_t worst = (size_t)m->hdr->in_ch * m->hdr->in_size * m->hdr->in_size;
    for (uint32_t i = 0; i < m->hdr->n_layers; i++) {
        const fgx_desc_t *d = &m->desc[i];
        size_t elems = (size_t)d->cout * d->oh * d->ow;
        size_t bytes = fgx_emits_float(m, i) ? elems * sizeof(float) : elems;
        if (bytes > worst)
            worst = bytes;
    }
    // Round up so both buffers can be handed out 4-byte aligned.
    return (worst + 3u) & ~(size_t)3u;
}

// The int32 accumulator for one output value, and **the only arithmetic M6 is
// checked against**. It lives in one place so that the FPGA's golden vectors and
// the reference tensor cannot drift apart: fgx_conv_ref() below and the public
// fgx_conv_acc() are both thin wrappers around this, not two transcriptions of
// the same loop.
//
// `static inline` rather than a plain call on purpose. M5 measured the reference
// at 31,798 ms/frame and m5b re-runs it once per boot to calibrate the ratio it
// prints; an extraction that cost a function call per output pixel would move
// that number and make the recorded baseline unreproducible. Inlined, the code
// generated for fgx_conv_ref() is what it always was.
static inline int32_t conv_acc_one(const int8_t *wc, const void *in,
                                   bool unsigned_in, int oy, int ox,
                                   int k, int stride, int H, int W, int CIN)
{
    const int8_t  *si = (const int8_t  *)in;
    const uint8_t *ui = (const uint8_t *)in;
    const int pad = 1;

    int32_t acc = 0;
    const int iy0 = oy * stride - pad;
    const int ix0 = ox * stride - pad;

    for (int ic = 0; ic < CIN; ic++) {
        const int8_t *wp = wc + (size_t)ic * k * k;
        const size_t plane = (size_t)ic * H * W;
        for (int ky = 0; ky < k; ky++) {
            const int iy = iy0 + ky;
            if (iy < 0 || iy >= H)
                continue;
            for (int kx = 0; kx < k; kx++) {
                const int ix = ix0 + kx;
                if (ix < 0 || ix >= W)
                    continue;
                const size_t idx = plane + (size_t)iy * W + ix;
                const int32_t v = unsigned_in ? (int32_t)ui[idx]
                                              : (int32_t)si[idx];
                acc += v * (int32_t)wp[ky * k + kx];
            }
        }
    }
    return acc;
}

// See encoder.h. Shared with encoder_fast.c rather than copied: the tuned
// kernel has to be bit-exact against this file, and two transcriptions of a
// nibble sign-extension is exactly the kind of thing that stays right until one
// of them is edited.
const int8_t *fgx_wchan(const fgx_model_t *m, const fgx_desc_t *d,
                        int oc, size_t n, int8_t *buf)
{
    if (d->wbits == 8)
        return m->weights + d->w_off + (size_t)oc * n;

    // A byte at a time rather than a nibble at a time. fgx_open() has already
    // refused any 4-bit layer with an odd channel stride, so the pair loop
    // covers the whole row and the trailing `if` is unreachable in every blob
    // this repo exports - it is there because "unreachable" is a property of
    // the loader two files away.
    //
    // The halving is worth writing out: fgx_conv_fast() runs this once per
    // (tile, output channel), which is 5.1 M nibbles a frame against 159 M
    // MACs, and the nibble-at-a-time version cost 19% of the tuned kernel's
    // host frame. Sign-extension is `(x ^ 8) - 8`, branchless, and the same
    // three instructions numpy's int4 unpack would emit.
    const uint8_t *p = (const uint8_t *)m->weights + d->w_off
                     + ((size_t)oc * n) / 2u;
    size_t i = 0;
    for (; i + 1 < n; i += 2) {
        const uint8_t byte = *p++;
        buf[i]     = (int8_t)((int8_t)((byte & 0x0Fu) ^ 0x8u) - 8);
        buf[i + 1] = (int8_t)((int8_t)((byte >> 4)    ^ 0x8u) - 8);
    }
    if (i < n)
        buf[i] = (int8_t)((int8_t)((*p & 0x0Fu) ^ 0x8u) - 8);
    return buf;
}

// See encoder.h. The requantized float cannot stand in for this: max |acc| is
// 1728*255*127 = 55,961,280, which needs 26 bits, and float32 carries 24.
int32_t fgx_conv_acc(const fgx_model_t *m, const fgx_desc_t *d,
                     const void *in, int oc, int oy, int ox)
{
    const int k = d->ksize;
    const int stride = (d->h == d->oh) ? 1 : 2;
    const size_t n = (size_t)d->cin * k * k;
    int8_t buf[FGX_MAX_WCHAN];
    const int8_t *wc = fgx_wchan(m, d, oc, n, buf);

    return conv_acc_one(wc, in, d->unsigned_in, oy, ox,
                        k, stride, d->h, d->w, d->cin);
}

// One 3x3 convolution, stride 1 or 2, pad 1. Input is either int8 or uint8
// codes; `unsigned_in` says which, and reading the wrong one is a factor-of-two
// error that still produces a plausible-looking image, so it is taken from the
// descriptor rather than inferred from the layer index.
void fgx_conv_ref(const fgx_model_t *m, const fgx_desc_t *d,
                  const void *in, void *out, bool out_float)
{
    const int32_t *bias = (const int32_t *)((const uint8_t *)m->biases + d->b_off);
    const float   *mult = (const float   *)((const uint8_t *)m->mults  + d->m_off);

    const int k = d->ksize;
    const int stride = (d->h == d->oh) ? 1 : 2;
    const int H = d->h, W = d->w, OH = d->oh, OW = d->ow;
    const int CIN = d->cin;
    const size_t n = (size_t)CIN * k * k;

    // One buffer for the whole layer, refilled per output channel. At wbits 8
    // wchan() never touches it and this is dead stack.
    int8_t wbuf[FGX_MAX_WCHAN];

    for (int oc = 0; oc < d->cout; oc++) {
        const int8_t *wc = fgx_wchan(m, d, oc, n, wbuf);
        const int32_t b = bias[oc];
        const float mu = mult[oc];
        // M15. Codes come out of the fixed-point epilogue, floats out of the
        // float one - and it is the same split the fabric makes, because a
        // float leaving conv7 has no byte for the tile to send. Picked here
        // rather than per pixel: one frexpf per channel against 4,096.
        int32_t rq_m = 0;
        const int rq_s = out_float ? 0 : fgx_rq_pick(mu, &rq_m);

        for (int oy = 0; oy < OH; oy++) {
            for (int ox = 0; ox < OW; ox++) {
                const int32_t acc = conv_acc_one(wc, in, d->unsigned_in, oy, ox,
                                                 k, stride, H, W, CIN);

                const size_t o = (size_t)oc * OH * OW + (size_t)oy * OW + ox;
                if (out_float)
                    ((float *)out)[o] = fgx_requant(acc, b, mu, d->relu != 0);
                else
                    ((uint8_t *)out)[o] = fgx_code_fixed(acc, b, rq_m, rq_s);
            }
        }
    }
}

// Weak no-op. See encoder.h; m5.c replaces this to time each layer.
__attribute__((weak)) void fgx_layer_mark(uint32_t layer, bool entering)
{
    (void)layer;
    (void)entering;
}

// Global average pool over conv7's float output, then quantize with the head's
// own scale. Pooling in float before quantizing (rather than averaging codes)
// is what quantize.py does, and at 4x4x256 it is free - 12.7 ms, unchanged
// whether the frame around it takes 25 s or 3.4 s, which is why M5b's tuned
// kernel calls this one rather than growing a second copy of it.
void fgx_pool_head(const fgx_model_t *m, const float *src, uint8_t *codes,
                   float *embed)
{
    const uint32_t n = m->hdr->n_layers;
    const fgx_desc_t *lc = &m->desc[n - 2];
    const fgx_desc_t *hd = &m->desc[n - 1];
    const int hw = lc->oh * lc->ow;

    const float inv = 1.0f / m->hdr->head_in_scale;
    for (int c = 0; c < lc->cout; c++) {
        float s = 0.0f;
        for (int j = 0; j < hw; j++)
            s += src[(size_t)c * hw + j];
        codes[c] = fgx_code(s / (float)hw * inv);
    }

    const int32_t *hb   = (const int32_t *)((const uint8_t *)m->biases + hd->b_off);
    const float   *hm   = (const float   *)((const uint8_t *)m->mults  + hd->m_off);
    int8_t hbuf[FGX_MAX_WCHAN];
    for (int o = 0; o < hd->cout; o++) {
        const int8_t *wr = fgx_wchan(m, hd, o, (size_t)hd->cin, hbuf);
        int32_t acc = 0;
        for (int j = 0; j < hd->cin; j++)
            acc += (int32_t)codes[j] * (int32_t)wr[j];
        embed[o] = fgx_requant(acc, hb[o], hm[o], false);
    }
}

void fgx_run(const fgx_model_t *m, const int8_t *input, float *embed,
             void *a, void *b)
{
    const uint32_t n = m->hdr->n_layers;
    const uint32_t last_conv = n - 2;

    const void *src = input;
    void *dst = a;

    for (uint32_t i = 0; i < last_conv + 1; i++) {
        const fgx_desc_t *d = &m->desc[i];
        const bool as_float = (i == last_conv);
        fgx_layer_mark(i, true);
        fgx_conv_ref(m, d, src, dst, as_float);
        fgx_layer_mark(i, false);
        src = dst;
        // Ping-pong. The first layer read the caller's input, so both buffers
        // are still free at that point and the choice below is what starts the
        // alternation.
        dst = (dst == a) ? b : a;
    }

    fgx_layer_mark(n - 1, true);
    fgx_pool_head(m, (const float *)src, (uint8_t *)dst, embed);
    fgx_layer_mark(n - 1, false);
}
