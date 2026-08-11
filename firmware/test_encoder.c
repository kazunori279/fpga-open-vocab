// Host-side validation of encoder.c. Builds and runs on macOS - no board, no
// PRG strap - against the same weights.bin the MCU will get.
//
//     cc -O2 -Wall -Wextra -o /tmp/test_encoder \
//        firmware/test_encoder.c firmware/encoder.c -lm
//     /tmp/test_encoder model/runs/so400m-full-a05/export
//
// Two comparisons per image, and the distinction matters:
//
//   vs golden  - encoder.c against export.py's numpy integer pipeline. Same
//                arithmetic, so this should be ~1.0; anything else is a C bug.
//                This is the real test.
//   vs fq      - encoder.c against PyTorch fake-quant. Bounded below by what
//                export.py already measured (0.999993); reported so a
//                regression cannot hide behind the golden comparison alone.

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "encoder.h"

#define GOLDEN_MIN 0.99999   // encoder.c vs numpy: same integers, so near-exact
#define FQ_MIN     0.9999    // vs PyTorch fake-quant: float32 vs float64 slack

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

    printf("blob      : %zu bytes, %u layers, %ux%ux%u in, %u-d out\n",
           wlen, m.hdr->n_layers, m.hdr->in_size, m.hdr->in_size,
           m.hdr->in_ch, m.hdr->embed_dim);
    printf("scratch   : 2 x %zu bytes = %.0f KiB\n",
           m.scratch, 2.0 * m.scratch / 1024.0);

    if (memcmp(tbuf, "FGXT", 4) != 0) {
        fprintf(stderr, "testvec.bin: bad magic\n");
        return 1;
    }
    uint32_t n_img, code_size;
    memcpy(&n_img, tbuf + 4, 4);
    memcpy(&code_size, tbuf + 8, 4);

    const uint32_t D = m.hdr->embed_dim;
    if (code_size != m.hdr->in_ch * m.hdr->in_size * m.hdr->in_size) {
        fprintf(stderr, "testvec/weights disagree on input size\n");
        return 1;
    }

    void *a = malloc(m.scratch), *b = malloc(m.scratch);
    float *embed = malloc(D * sizeof(float));
    if (!a || !b || !embed) { fprintf(stderr, "out of memory\n"); return 1; }

    printf("\n%-8s %14s %14s %8s %10s %8s\n",
           "image", "vs golden", "vs fakequant", "exact", "ms", "|embed|");

    const size_t stride = code_size + 2 * (size_t)D * sizeof(float);
    double worst_g = 1.0, worst_f = 1.0, total_ms = 0;
    uint32_t exact_total = 0;

    for (uint32_t i = 0; i < n_img; i++) {
        const uint8_t *rec = tbuf + 12 + (size_t)i * stride;
        const int8_t *codes = (const int8_t *)rec;
        const float *golden = (const float *)(rec + code_size);
        const float *fq = golden + D;

        double t0 = now_ms();
        fgx_run(&m, codes, embed, a, b);
        double ms = now_ms() - t0;
        total_ms += ms;

        double cg = cosine(embed, golden, (int)D);
        double cf = cosine(embed, fq, (int)D);
        if (cg < worst_g) worst_g = cg;
        if (cf < worst_f) worst_f = cf;

        // Bit-exact float count, not just cosine. M6 has to reproduce the int32
        // accumulator, and "the embeddings are bit-identical" is the only
        // evidence that reaches that far back through the pipeline - a cosine
        // of 1.0 is also consistent with a small systematic scale error.
        uint32_t exact = 0;
        double norm = 0;
        for (uint32_t j = 0; j < D; j++) {
            norm += (double)embed[j] * embed[j];
            if (memcmp(&embed[j], &golden[j], sizeof(float)) == 0) exact++;
        }
        exact_total += exact;

        printf("%-8u %14.6f %14.6f %5u/%u %10.1f %8.3f\n",
               i, cg, cf, exact, D, ms, sqrt(norm));
    }

    // Printed as a deficit as well, because "1.000000" at six places is also
    // what a 1e-7 disagreement looks like, and the size of that gap is the
    // difference between "float32 vs float64 rounding" and "a real bug".
    printf("\nworst vs golden (numpy int8)   : %.6f  (1-cos = %.2e, need > %.5f)\n",
           worst_g, 1.0 - worst_g, GOLDEN_MIN);
    printf("worst vs PyTorch fake-quant    : %.6f  (need > %.4f)\n",
           worst_f, FQ_MIN);
    printf("bit-exact floats vs golden     : %u / %u (%.1f%%)\n",
           exact_total, n_img * D, 100.0 * exact_total / (n_img * D));
    printf("mean host latency              : %.1f ms/frame\n", total_ms / n_img);

    bool ok = worst_g > GOLDEN_MIN && worst_f > FQ_MIN;
    printf("\nRESULT : %s\n", ok
           ? "PASS - encoder.c reproduces the golden integer pipeline"
           : "FAIL - encoder.c disagrees with the reference");
    return ok ? 0 : 1;
}
