// M5 on-board: run the int8 student on the RP2354A and time it.
//
// The number this prints is the one M6/M7 have to beat. Everything else here is
// there so that number cannot be read out of context - the clock, the
// optimization level, and which memory the weights were fetched from all move
// it by more than the difference between "the FPGA is worth building" and "it
// is not".
//
// encoder.c is already proven bit-exact against numpy on the host, so the
// cosine column is not the point; it is a tripwire. If it moves on device, the
// suspects are the rounding step and the -ffast-math class of flags, not the
// algorithm. The rounding step is now VCVTR.S32.F32 rather than a call to
// newlib's lrintf() - see fgx_rint() in encoder.h - so the specific thing to
// check is that FPSCR's rounding mode is still RN. Nothing writes it today:
// `grep -c 'vmsr.*fpscr' build/forgix_m5.dis` is 0.
//
// Weights and test vectors are linked into flash and copied to PSRAM at boot
// rather than streamed over USB. When this was written, reflashing needed a
// physical PRG-GND strap, so the build was arranged to need exactly one: no
// host protocol has to work before the measurement can happen. M7i found that
// `picotool reboot -f -u` works after all - the SDK's vendored picotool is
// built without USB support, which is why it never had - but the arrangement
// stands, because a measurement that depends on fewer moving parts is better
// evidence.
//
// U1 *is* populated - a SOIC-8 on the underside, photographed 2026-07-30 - but
// psram_detect_size() returns 0, so the PSRAM path below does not execute today
// and the flash XIP run is the real one. M5c has since read U1's ID off the wire
// - AP Memory, known good die, 2 MiB, exactly the BOM part - so the chip is
// healthy and answering; its reply just lands 18 bit-times out of frame. That
// offset reproduces on both boards with different dies, and rev 4's path x chip
// matrix clears our driver of causing it, so it is not a missing part, not a
// timing or mode problem, and not a host bug either - the mechanism is still
// unexplained and needs a scope. See open question #10 under "Verify before
// building" in docs/history.md, closed as
// bounded. The fallback is why a strap spent on a
// board whose PSRAM would not enumerate still returned the correctness result
// and the per-layer profile, and it stays for the same reason.

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/psram.h"

#include "encoder.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// 1.4 MB today. Sized with slack so a re-export does not silently overflow -
// the runtime check below is what actually enforces it.
#define PSRAM_WEIGHTS_MAX (1536u * 1024u)
static __uninitialized_psram("weights") uint8_t psram_weights[PSRAM_WEIGHTS_MAX];

// Peak intermediate is conv0's 64x64x32 output = 128 KiB, so 2 x 132 KiB covers
// the ping-pong with room for a wider first stage later. 264 KiB of the
// RP2354A's 520 KB, which is the real reason activations never go to PSRAM.
#define SCRATCH_MAX (132u * 1024u)
static __attribute__((aligned(8))) uint8_t scratch_a[SCRATCH_MAX];
static __attribute__((aligned(8))) uint8_t scratch_b[SCRATCH_MAX];

static float embed[1024];

// --- per-layer timing ------------------------------------------------------
// Overrides the weak no-op in encoder.c. Accumulates across images so the
// breakdown is a mean, not one noisy frame.
#define MAX_LAYERS 32
static uint64_t layer_us[MAX_LAYERS];
static uint64_t layer_t0;

void fgx_layer_mark(uint32_t layer, bool entering)
{
    if (layer >= MAX_LAYERS) return;
    if (entering) layer_t0 = time_us_64();
    else          layer_us[layer] += time_us_64() - layer_t0;
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

// Walks the test vectors against one copy of the weights and reports mean
// latency. `where` names the memory the weights came from; running the same
// images twice, once per store, is what turns "PSRAM is slower" from a belief
// into a number.
static bool run_all(const void *blob, size_t blob_len, const char *where,
                    double *mean_ms_out)
{
    fgx_model_t m;
    if (!fgx_open(&m, blob, blob_len)) {
        printf("%-8s : fgx_open REJECTED the blob\n", where);
        return false;
    }
    if (m.scratch > SCRATCH_MAX) {
        printf("%-8s : needs %u scratch bytes, have %u\n",
               where, (unsigned)m.scratch, SCRATCH_MAX);
        return false;
    }

    uint32_t n_img, code_size;
    memcpy(&n_img, fgx_testvec + 4, 4);
    memcpy(&code_size, fgx_testvec + 8, 4);
    const uint32_t D = m.hdr->embed_dim;
    const size_t stride = code_size + 2u * D * sizeof(float);

    memset(layer_us, 0, sizeof layer_us);
    double worst = 1.0, total_ms = 0;
    uint32_t exact_total = 0;

    for (uint32_t i = 0; i < n_img; i++) {
        const uint8_t *rec = fgx_testvec + 12 + (size_t)i * stride;
        const int8_t *codes = (const int8_t *)rec;
        const float *golden = (const float *)(rec + code_size);

        uint64_t t0 = time_us_64();
        fgx_run(&m, codes, embed, scratch_a, scratch_b);
        double ms = (time_us_64() - t0) / 1000.0;
        total_ms += ms;

        uint32_t exact = 0;
        for (uint32_t j = 0; j < D; j++)
            if (memcmp(&embed[j], &golden[j], sizeof(float)) == 0) exact++;
        exact_total += exact;

        double c = cosine(embed, golden, D);
        if (c < worst) worst = c;

        printf("  %-6s image %u   cos %.6f   exact %u/%u   %8.1f ms\n",
               where, (unsigned)i, c, (unsigned)exact, (unsigned)D, ms);
    }

    *mean_ms_out = total_ms / n_img;
    printf("  %-6s mean                                        %8.1f ms  "
           "(%.2f fps)\n", where, *mean_ms_out, 1000.0 / *mean_ms_out);

    // Per layer, with the shape alongside so the cost can be read against the
    // MAC count rather than in isolation. The last row is pool + head.
    printf("\n  %-5s %-16s %10s %8s\n", "layer", "shape", "ms/frame", "share");
    for (uint32_t i = 0; i < m.hdr->n_layers; i++) {
        const fgx_desc_t *d = &m.desc[i];
        double ms = layer_us[i] / 1000.0 / n_img;
        char shape[32];
        if (i + 1 == m.hdr->n_layers)
            snprintf(shape, sizeof shape, "pool + %u->%u",
                     (unsigned)d->cin, (unsigned)d->cout);
        else
            snprintf(shape, sizeof shape, "%ux%ux%u -> %u",
                     (unsigned)d->h, (unsigned)d->w,
                     (unsigned)d->cin, (unsigned)d->cout);
        printf("  %-5u %-16s %10.1f %7.1f%%\n",
               (unsigned)i, shape, ms, 100.0 * ms / *mean_ms_out);
    }

    (void)exact_total;
    return worst > 0.9999;
}

int main(void)
{
    stdio_init_all();

    // Same as diag.c: block until the terminal is attached, so nothing is
    // printed into a port nobody is reading. The run takes seconds and cannot
    // be repeated without another strap, so losing the header is not an option.
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    const size_t w_len = (size_t)(fgx_weights_end - fgx_weights);
    const size_t t_len = (size_t)(fgx_testvec_end - fgx_testvec);

    printf("\n=== M5: int8 student encoder on the RP2354A ===\n\n");
    printf("clock     : %u MHz sys\n", (unsigned)(clock_get_hz(clk_sys) / 1000000));
    printf("build     : -O3, softfp ABI (FPU used)\n");
    printf("blob      : %u bytes in flash, testvec %u bytes\n",
           (unsigned)w_len, (unsigned)t_len);

    size_t psram_size = psram_get_size();
    bool have_psram = psram_is_available()
                   && psram_check_address(psram_weights + PSRAM_WEIGHTS_MAX - 1);
    printf("psram     : %s, %u bytes, window at %p\n",
           have_psram ? "detected" : "NOT AVAILABLE",
           (unsigned)psram_size, (void *)psram_weights);

    fgx_model_t probe;
    if (!fgx_open(&probe, fgx_weights, w_len)) {
        printf("\nRESULT : FAIL - flash blob is malformed\n");
        while (true) tight_loop_contents();
    }
    printf("model     : %u layers, %ux%ux%u in, %u-d out\n",
           (unsigned)probe.hdr->n_layers, (unsigned)probe.hdr->in_size,
           (unsigned)probe.hdr->in_size, (unsigned)probe.hdr->in_ch,
           (unsigned)probe.hdr->embed_dim);
    printf("scratch   : 2 x %u bytes\n", (unsigned)probe.scratch);

    bool ok_psram = false;
    double ms_psram = 0, ms_flash = 0;

    if (have_psram && w_len <= PSRAM_WEIGHTS_MAX) {
        uint64_t t0 = time_us_64();
        memcpy(psram_weights, fgx_weights, w_len);
        double copy_ms = (time_us_64() - t0) / 1000.0;
        printf("copy      : flash -> psram, %u bytes in %.1f ms (%.1f MB/s)\n",
               (unsigned)w_len, copy_ms, w_len / 1024.0 / 1024.0 / (copy_ms / 1000.0));

        // Verified rather than assumed: a PSRAM that reads back wrong would
        // otherwise surface as a wrong embedding and be blamed on the encoder.
        if (memcmp(psram_weights, fgx_weights, w_len) != 0) {
            printf("psram     : READBACK MISMATCH - not using it\n");
            have_psram = false;
        }
    } else if (have_psram) {
        printf("psram     : blob is %u bytes, array is %u - not using it\n",
               (unsigned)w_len, PSRAM_WEIGHTS_MAX);
        have_psram = false;
    }

    printf("\n-- weights resident in PSRAM (this is the Tier 3 design) --\n");
    if (have_psram)
        ok_psram = run_all(psram_weights, w_len, "psram", &ms_psram);
    else
        printf("  skipped\n");

    // The same run with weights fetched from flash XIP instead. Not the shipping
    // configuration - flash has to hold the firmware and, later, the bitstream -
    // but the gap between the two rows is the price of the PSRAM hop, and M6
    // needs that number to know whether the link or the weight fetch dominates.
    printf("\n-- same, weights fetched from flash XIP (reference) --\n");
    bool ok_flash = run_all(fgx_weights, w_len, "flash", &ms_flash);

    printf("\n");
    if (have_psram && ms_flash > 0)
        printf("psram vs flash                 : %+.1f%% slower\n",
               100.0 * (ms_psram - ms_flash) / ms_flash);
    printf("MACs                           : ~159 M per frame\n");
    if (ms_psram > 0)
        printf("throughput (psram)             : %.1f MMAC/s\n", 159.0 / (ms_psram / 1000.0));

    bool ok = ok_flash && (!have_psram || ok_psram);
    printf("\nRESULT : %s\n", ok
           ? "PASS - on-device int8 matches the host golden vectors"
           : "FAIL - on-device output disagrees with the golden vectors");

    while (true) tight_loop_contents();
}
