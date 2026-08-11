// Host-side validation of encoder_fast.c against encoder.c. No board, no PRG
// strap, same weights.bin the MCU gets.
//
//     cc -O2 -Wall -Wextra -o /tmp/tef \
//        firmware/test_encoder_fast.c firmware/encoder.c firmware/encoder_fast.c -lm
//     /tmp/tef model/runs/so400m-full-a05/export
//
// **Run it twice.** Adding -DFGX_DSP_SHIM swaps in dsp_shim.h's transcription
// of SXTB16/UXTB16/SMLAD from the ARM ARM, so the second run compiles the same
// source lines the M33 will execute - the tap pairing, the loop bounds, the
// K%4 tail - and proves them here rather than on the one PRG strap. Both runs
// must pass; they exercise different inner loops over identical arithmetic.
//
// Two checks, and the first is why this file exists rather than a flag on
// test_encoder:
//
//   per layer  - fgx_conv_ref() and fgx_conv_fast() on the same input tensor,
//                compared with memcmp over the whole output. A mismatch names
//                the layer. Comparing only the 512-d embedding would say the
//                frame is wrong without saying where, and with eight convs and
//                two padding regimes that is most of the debugging.
//   per frame  - the full pipelines, requiring all 512 floats byte-identical,
//                plus the cosine against golden so the numpy chain is not lost
//                behind a self-consistent pair of wrong kernels.
//
// This host is aarch64, which does not define __ARM_FEATURE_DSP, so what runs
// here is the portable path - i.e. this proves the *restructuring*. The DSP
// inner loop is validated on the device by m5b.c against the same golden
// vectors encoder.c already matched bit-for-bit.

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "encoder_fast.h"

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

static double cosine(const float *a, const float *b, int n)
{
    double d = 0, na = 0, nb = 0;
    for (int i = 0; i < n; i++) {
        d += (double)a[i] * b[i];
        na += (double)a[i] * a[i];
        nb += (double)b[i] * b[i];
    }
    return d / (sqrt(na) * sqrt(nb));
}

static double now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

// Walks the conv stack twice in lockstep, feeding both kernels the *reference*
// input at every layer. Feeding the fast kernel its own previous output would
// let a layer-3 bug hide behind a layer-2 bug that cancels it, and would make
// the first mismatch the only readable one.
static int compare_layers(const fgx_model_t *m, const int8_t *input,
                          void *ref_a, void *ref_b, void *fast_out, void *col)
{
    const uint32_t last_conv = m->hdr->n_layers - 2;
    const void *src = input;
    void *dst = ref_a, *other = ref_b;
    int bad = 0;

    for (uint32_t i = 0; i <= last_conv; i++) {
        const fgx_desc_t *d = &m->desc[i];
        const bool as_float = (i == last_conv);
        const size_t elems = (size_t)d->cout * d->oh * d->ow;
        const size_t bytes = as_float ? elems * sizeof(float) : elems;

        fgx_conv_ref(m, d, src, dst, as_float);
        fgx_conv_fast(m, d, src, fast_out, as_float, col, true);

        const bool same = memcmp(dst, fast_out, bytes) == 0;
        if (!same) {
            // Report the first differing element and how many, because "one
            // pixel in the padded border" and "everything" are different bugs.
            size_t first = 0, diff = 0;
            const unsigned char *p = dst, *q = fast_out;
            for (size_t j = 0; j < bytes; j++)
                if (p[j] != q[j]) { if (!diff) first = j; diff++; }
            printf("  layer %u  %ux%ux%u -> %-3u  MISMATCH  %zu/%zu bytes, "
                   "first at %zu\n", i, d->h, d->w, d->cin, d->cout,
                   diff, bytes, first);
            bad++;
        }

        src = dst;
        void *t = dst; dst = other; other = t;
    }
    return bad;
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : "model/runs/so400m-full-a05/export";
    char path[512];

    snprintf(path, sizeof path, "%s/weights.bin", dir);
    size_t wlen;
    void *wbuf = slurp(path, &wlen);

    snprintf(path, sizeof path, "%s/testvec.bin", dir);
    size_t tlen;
    uint8_t *tbuf = slurp(path, &tlen);

    fgx_model_t m;
    if (!fgx_open(&m, wbuf, wlen)) {
        fprintf(stderr, "weights.bin: rejected by fgx_open\n");
        return 1;
    }
    if (memcmp(tbuf, "FGXT", 4) != 0) {
        fprintf(stderr, "testvec.bin: bad magic\n");
        return 1;
    }

    const size_t colb = fgx_fast_col_bytes(&m);
    printf("blob      : %zu bytes, %u layers, %ux%ux%u in, %u-d out\n",
           wlen, m.hdr->n_layers, m.hdr->in_size, m.hdr->in_size,
           m.hdr->in_ch, m.hdr->embed_dim);
    printf("scratch   : 2 x %zu bytes = %.0f KiB\n",
           m.scratch, 2.0 * m.scratch / 1024.0);
    printf("im2col    : %zu bytes (tile %d) = %.0f KiB\n",
           colb, FGX_TILE, colb / 1024.0);
    printf("dsp path  : %s on this host\n",
           fgx_fast_have_dsp() ? "COMPILED IN" : "not available (portable path)");

    uint32_t n_img, code_size;
    memcpy(&n_img, tbuf + 4, 4);
    memcpy(&code_size, tbuf + 8, 4);
    const uint32_t D = m.hdr->embed_dim;

    void *a = malloc(m.scratch), *b = malloc(m.scratch);
    void *fa = malloc(m.scratch), *fb = malloc(m.scratch);
    void *fout = malloc(m.scratch), *col = malloc(colb);
    float *e_ref = malloc(D * sizeof(float));
    float *e_fast = malloc(D * sizeof(float));
    if (!a || !b || !fa || !fb || !fout || !col || !e_ref || !e_fast) {
        fprintf(stderr, "out of memory\n");
        return 1;
    }

    const size_t stride = code_size + 2 * (size_t)D * sizeof(float);
    int layer_bad = 0;
    uint32_t exact_pair = 0, exact_golden = 0;
    double worst_g = 1.0, ms_ref = 0, ms_fast = 0;

    printf("\n-- per layer: fgx_conv_fast vs fgx_conv_ref, byte for byte --\n");
    for (uint32_t i = 0; i < n_img; i++) {
        const int8_t *codes = (const int8_t *)(tbuf + 12 + (size_t)i * stride);
        int bad = compare_layers(&m, codes, a, b, fout, col);
        printf("  image %u : %s\n", i, bad ? "FAIL" : "all 8 convs identical");
        layer_bad += bad;
    }

    printf("\n%-8s %14s %10s %10s %10s\n",
           "image", "vs golden", "exact", "ref ms", "fast ms");

    for (uint32_t i = 0; i < n_img; i++) {
        const uint8_t *rec = tbuf + 12 + (size_t)i * stride;
        const int8_t *codes = (const int8_t *)rec;
        const float *golden = (const float *)(rec + code_size);

        double t0 = now_ms();
        fgx_run(&m, codes, e_ref, a, b);
        double t1 = now_ms();
        fgx_run_fast(&m, codes, e_fast, fa, fb, col, true);
        double t2 = now_ms();
        ms_ref += t1 - t0;
        ms_fast += t2 - t1;

        uint32_t ep = 0, eg = 0;
        for (uint32_t j = 0; j < D; j++) {
            if (memcmp(&e_fast[j], &e_ref[j], sizeof(float)) == 0) ep++;
            if (memcmp(&e_fast[j], &golden[j], sizeof(float)) == 0) eg++;
        }
        exact_pair += ep;
        exact_golden += eg;

        double cg = cosine(e_fast, golden, (int)D);
        if (cg < worst_g) worst_g = cg;

        printf("%-8u %14.6f %5u/%-4u %10.1f %10.1f\n",
               i, cg, ep, D, t1 - t0, t2 - t1);
    }

    printf("\nlayer mismatches               : %d\n", layer_bad);
    printf("bit-exact vs encoder.c         : %u / %u\n", exact_pair, n_img * D);
    printf("bit-exact vs numpy golden      : %u / %u\n", exact_golden, n_img * D);
    printf("worst cosine vs golden         : %.6f  (1-cos = %.2e)\n",
           worst_g, 1.0 - worst_g);
    printf("host latency  reference        : %.1f ms/frame\n", ms_ref / n_img);
    printf("host latency  im2col           : %.1f ms/frame  (%.2fx)\n",
           ms_fast / n_img, ms_ref / ms_fast);

    // Every one of the three has to hold. The pair check alone would pass if
    // both kernels drifted together, and the golden check alone would not say
    // which one moved.
    bool ok = layer_bad == 0
           && exact_pair == n_img * D
           && exact_golden == n_img * D;
    printf("\nRESULT : %s\n", ok
           ? "PASS - encoder_fast.c is bit-exact with encoder.c and with numpy"
           : "FAIL - the tuned kernel disagrees with the reference");
    return ok ? 0 : 1;
}
