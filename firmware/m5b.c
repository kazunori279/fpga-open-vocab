// M5b on-board: the tuned int8 encoder on the RP2354A, against the naive one.
//
// M5 measured 31,798 ms/frame - 30 cycles/MAC - and that number is correct and
// unusable as a baseline. It is the cost of a loop that re-tests a flag and
// bounds-checks two axes on every tap, not the cost of the arithmetic. Quoted
// as "the MCU baseline" it would make the T8 look like a 140x win; the honest
// figure is 8-10x, and M6 has to be justified against the honest one.
//
// This is also why the reference is re-run *here*, in the same boot, instead of
// quoting M5's number: it turns out not to survive the quote. Extracting
// fgx_conv_ref() out of fgx_run() stopped GCC inlining a 1086-byte monolith and
// left a 636-byte hot kernel, and on a part that fetches instructions from
// flash XIP that alone moved the identical arithmetic from 31.8 s to 25.0 s.
// Ratios across builds on this board are not measurements.
//
// **Every reflash of this board costs a physical PRG-GND strap**, so this is
// built to answer everything in one boot:
//
//   reference   fgx_run, image 0 only            ~32 s
//   portable    fgx_run_fast, use_dsp = false    all 4 images
//   dsp         fgx_run_fast, use_dsp = true     all 4 images
//
// The reference runs once rather than four times because it is not being
// re-validated - M5 already proved it 2048/2048 bit-exact on this silicon. It
// runs at all so the speedup printed at the bottom is a ratio measured in one
// boot at one clock with one set of flags, rather than quoted across builds.
//
// The two fast rows are separate because the DSP inner loop is the only part
// that cannot be checked on the host: macOS aarch64 does not define
// __ARM_FEATURE_DSP, so test_encoder_fast.c exercises the portable path. If the
// intrinsics are wrong, the portable row still returns a usable tuned baseline
// and localizes the fault - which is worth more than 4 KB of flash, and is why
// use_dsp is a runtime flag and not two builds.
//
// Correctness is checked against the golden float vectors baked into
// testvec.bin, not against a re-run of the reference. Those vectors came from
// numpy via export.py and encoder.c already matched them bit-for-bit, so they
// are the stronger comparison as well as the cheaper one.
//
// No PSRAM path, unlike m5.c. U1 answers its ID but 18 bit-times out of frame
// so XIP never comes up (open question #10 in docs/history.md, closed as
// bounded), and weights live in
// the RP2354A's stacked flash either way.

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"

#include "encoder_fast.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// Same 132 KiB ping-pong as m5.c - peak intermediate is conv0's 64x64x32 = 128
// KiB - plus the im2col tile. Worst case for the tile is conv6: K = 192*3*3 =
// 1728 taps x 32 columns = 54 KiB. 318 KiB of the RP2354A's 520 KB, which is
// the headroom FGX_TILE was chosen against.
#define SCRATCH_MAX (132u * 1024u)
#define COL_MAX      (64u * 1024u)
static __attribute__((aligned(8))) uint8_t scratch_a[SCRATCH_MAX];
static __attribute__((aligned(8))) uint8_t scratch_b[SCRATCH_MAX];
static __attribute__((aligned(8))) uint8_t col_buf[COL_MAX];

static float embed[1024];

#define MAX_LAYERS 32
static uint64_t layer_us[MAX_LAYERS];
static uint64_t layer_t0;

void fgx_layer_mark(uint32_t layer, bool entering)
{
    if (layer >= MAX_LAYERS) return;
    if (entering) layer_t0 = time_us_64();
    else          layer_us[layer] += time_us_64() - layer_t0;
}

// Per-layer profiles are kept for the reference and the DSP path so the two can
// be read side by side. A tuned kernel that wins overall while losing on one
// shape is exactly what M6 needs to know about, and a single ms/frame hides it.
static double ref_layer_ms[MAX_LAYERS];
static double dsp_layer_ms[MAX_LAYERS];

static const double MMAC_PER_FRAME = 159.0;

// cycles/MAC is the figure M6 gets compared against, so it gets a function
// rather than four copies of the same expression: ms is milliseconds and the
// count is *mega*MACs, and dropping either factor of 1000 silently scales the
// answer by 1000 without making it look wrong.
static double cycles_per_mac(double ms)
{
    const double cycles = (clock_get_hz(clk_sys) / 1e6) * (ms * 1e3);
    return cycles / (MMAC_PER_FRAME * 1e6);
}

static double cosine(const float *a, const float *b, uint32_t n)
{
    double d = 0, na = 0, nb = 0;
    for (uint32_t i = 0; i < n; i++) {
        d += (double)a[i] * b[i];
        na += (double)a[i] * a[i];
        nb += (double)b[i] * b[i];
    }
    return d / (sqrt(na) * sqrt(nb));
}

typedef enum { RUN_REF, RUN_PORTABLE, RUN_DSP } run_mode_t;

// Returns mean ms/frame; *exact_out accumulates bit-exact floats against
// golden, which is the pass criterion.
static double run_images(const fgx_model_t *m, run_mode_t mode, const char *tag,
                         uint32_t n_run, uint32_t *exact_out, double *worst_cos)
{
    uint32_t n_img, code_size;
    memcpy(&n_img, fgx_testvec + 4, 4);
    memcpy(&code_size, fgx_testvec + 8, 4);
    const uint32_t D = m->hdr->embed_dim;
    const size_t stride = code_size + 2u * D * sizeof(float);
    if (n_run > n_img) n_run = n_img;

    memset(layer_us, 0, sizeof layer_us);
    double total_ms = 0;

    for (uint32_t i = 0; i < n_run; i++) {
        const uint8_t *rec = fgx_testvec + 12 + (size_t)i * stride;
        const int8_t *codes = (const int8_t *)rec;
        const float *golden = (const float *)(rec + code_size);

        uint64_t t0 = time_us_64();
        if (mode == RUN_REF)
            fgx_run(m, codes, embed, scratch_a, scratch_b);
        else
            fgx_run_fast(m, codes, embed, scratch_a, scratch_b, col_buf,
                         mode == RUN_DSP);
        double ms = (time_us_64() - t0) / 1000.0;
        total_ms += ms;

        uint32_t exact = 0;
        for (uint32_t j = 0; j < D; j++)
            if (memcmp(&embed[j], &golden[j], sizeof(float)) == 0) exact++;
        *exact_out += exact;

        double c = cosine(embed, golden, D);
        if (c < *worst_cos) *worst_cos = c;

        printf("  %-9s image %u   cos %.6f   exact %u/%u   %9.1f ms\n",
               tag, (unsigned)i, c, (unsigned)exact, (unsigned)D, ms);
    }

    double mean = total_ms / n_run;
    printf("  %-9s mean                                          %9.1f ms  "
           "(%.2f fps, %.1f cycles/MAC)\n", tag, mean,
           1000.0 / mean, cycles_per_mac(mean));

    double *sink = (mode == RUN_REF) ? ref_layer_ms
                 : (mode == RUN_DSP) ? dsp_layer_ms : NULL;
    if (sink)
        for (uint32_t i = 0; i < MAX_LAYERS; i++)
            sink[i] = layer_us[i] / 1000.0 / n_run;

    return mean;
}

int main(void)
{
    stdio_init_all();

    // Block until the terminal is attached: the run cannot be repeated without
    // another strap, so losing the header is not an option.
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    const size_t w_len = (size_t)(fgx_weights_end - fgx_weights);
    const size_t t_len = (size_t)(fgx_testvec_end - fgx_testvec);

    printf("\n=== M5b: tuned int8 encoder (im2col + SMLAD) on the RP2354A ===\n\n");
    printf("clock     : %u MHz sys\n", (unsigned)(clock_get_hz(clk_sys) / 1000000));
    printf("build     : -O3, softfp ABI (FPU used), weights from flash XIP\n");
    printf("blob      : %u bytes in flash, testvec %u bytes\n",
           (unsigned)w_len, (unsigned)t_len);
    printf("dsp       : %s\n", fgx_fast_have_dsp()
           ? "__ARM_FEATURE_DSP compiled in (SMLAD path available)"
           : "NOT COMPILED IN - the dsp row below is a duplicate of portable");

    fgx_model_t m;
    if (!fgx_open(&m, fgx_weights, w_len)) {
        printf("\nRESULT : FAIL - flash blob is malformed\n");
        while (true) tight_loop_contents();
    }
    printf("model     : %u layers, %ux%ux%u in, %u-d out\n",
           (unsigned)m.hdr->n_layers, (unsigned)m.hdr->in_size,
           (unsigned)m.hdr->in_size, (unsigned)m.hdr->in_ch,
           (unsigned)m.hdr->embed_dim);

    const size_t colb = fgx_fast_col_bytes(&m);
    printf("scratch   : 2 x %u bytes\n", (unsigned)m.scratch);
    printf("im2col    : %u bytes (tile %u)\n", (unsigned)colb, (unsigned)FGX_TILE);
    if (m.scratch > SCRATCH_MAX || colb > COL_MAX) {
        printf("\nRESULT : FAIL - buffers too small (have %u / %u)\n",
               SCRATCH_MAX, COL_MAX);
        while (true) tight_loop_contents();
    }

    uint32_t n_img;
    memcpy(&n_img, fgx_testvec + 4, 4);
    const uint32_t D = m.hdr->embed_dim;

    // One reference frame. ~32 s of the run, and the only reason to spend it is
    // to make the ratio at the bottom a measurement rather than a quotation.
    printf("\n-- reference: encoder.c, image 0 only (this is the ~32 s one) --\n");
    uint32_t exact_ref = 0;
    double worst_ref = 1.0;
    double ms_ref = run_images(&m, RUN_REF, "reference", 1, &exact_ref, &worst_ref);

    printf("\n-- im2col + blocked GEMM, portable C inner loop --\n");
    uint32_t exact_por = 0;
    double worst_por = 1.0;
    double ms_por = run_images(&m, RUN_PORTABLE, "portable", n_img,
                               &exact_por, &worst_por);

    printf("\n-- im2col + blocked GEMM, SMLAD inner loop (the M5b number) --\n");
    uint32_t exact_dsp = 0;
    double worst_dsp = 1.0;
    double ms_dsp = run_images(&m, RUN_DSP, "dsp", n_img, &exact_dsp, &worst_dsp);

    // Reference and DSP side by side. The reference column is one frame and the
    // DSP column is the mean of four; both are per-frame, so they compare, and
    // the ratio column is what says whether any shape resisted tuning.
    printf("\n  %-5s %-16s %11s %11s %8s\n",
           "layer", "shape", "ref ms", "dsp ms", "speedup");
    for (uint32_t i = 0; i < m.hdr->n_layers; i++) {
        const fgx_desc_t *d = &m.desc[i];
        char shape[32];
        if (i + 1 == m.hdr->n_layers)
            snprintf(shape, sizeof shape, "pool + %u->%u",
                     (unsigned)d->cin, (unsigned)d->cout);
        else
            snprintf(shape, sizeof shape, "%ux%ux%u -> %u",
                     (unsigned)d->h, (unsigned)d->w,
                     (unsigned)d->cin, (unsigned)d->cout);
        double r = ref_layer_ms[i], s = dsp_layer_ms[i];
        printf("  %-5u %-16s %11.1f %11.1f %7.1fx\n",
               (unsigned)i, shape, r, s, s > 0 ? r / s : 0.0);
    }

    printf("\n%-30s : %9.1f ms/frame  %5.2f cycles/MAC\n",
           "reference (encoder.c)", ms_ref, cycles_per_mac(ms_ref));
    printf("%-30s : %9.1f ms/frame  %5.2f cycles/MAC  %5.1fx\n",
           "im2col, portable C", ms_por, cycles_per_mac(ms_por),
           ms_ref / ms_por);
    printf("%-30s : %9.1f ms/frame  %5.2f cycles/MAC  %5.1fx\n",
           "im2col + SMLAD", ms_dsp, cycles_per_mac(ms_dsp),
           ms_ref / ms_dsp);
    printf("%-30s : %9.1f MMAC/s\n", "throughput (SMLAD)",
           MMAC_PER_FRAME / (ms_dsp / 1000.0));

    printf("\n%-30s : %u / %u\n", "bit-exact, reference",
           (unsigned)exact_ref, (unsigned)D);
    printf("%-30s : %u / %u\n", "bit-exact, portable",
           (unsigned)exact_por, (unsigned)(n_img * D));
    printf("%-30s : %u / %u\n", "bit-exact, SMLAD",
           (unsigned)exact_dsp, (unsigned)(n_img * D));

    // Bit-exact, not "cosine is 1.0". A cosine of 1.000000 is also what a small
    // systematic scale error looks like, and the int32 accumulator contract M6
    // has to reproduce is only visible in the exact float count.
    bool ok = exact_ref == D
           && exact_por == n_img * D
           && exact_dsp == n_img * D;
    printf("\nRESULT : %s\n", ok
           ? "PASS - both tuned kernels reproduce the golden vectors exactly"
           : "FAIL - a tuned kernel disagrees with the golden vectors");

    while (true) tight_loop_contents();
}
