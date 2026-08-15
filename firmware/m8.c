// M8c: capture -> encode -> embed, continuously.
//
// Everything before this milestone measured. m7.c runs one frame six ways, twice
// over, against an MCU reference computed in the same boot, and then parks: one
// image per boot, which is what a measuring harness should be and not what a
// demo is. This is the same engine - frame.c, shared with m7.c, moved rather
// than copied - in the shape M9 needs:
//
//     a frame off the camera -> 8 convolutions on the T8 -> pool and head ->
//     a 512-d embedding -> repeat
//
// M9 (fpga-open-vocab) is then this loop plus a text embedding from the host and a
// comparison. The comparison is already here, pointed at the previous frame
// rather than at a sentence: `cos(e_t, e_{t-1})` is exactly the arithmetic the
// text match will use, on exactly the vectors it will use, so a demo that
// tracks the room is also a test of the last piece of M9's arithmetic.
//
// ---------------------------------------------------------------------------
// Three start-up checks, then nothing that can lie
//
// A loop printing numbers nobody can check is worse than no loop, because the
// numbers look like data. So the demo does not start until all three of these
// have passed, and each of them fails loudly:
//
//   1. THE REFERENCE. encoder_fast.c encodes the flash test vector on the MCU,
//      once, ~3.3 s. That is the 512 floats the tile has to reproduce.
//
//   2. THE WIDTH PROBE, which identifies the wire *by measurement*. It runs the
//      whole test vector at three forward data lines and compares all 512
//      floats; bit-exact means the wide bitstream is loaded and the PIN2 <->
//      PIN17 jumper is fitted, so this is configuration C. Otherwise it does the
//      same at one data line, which is configuration A. Neither is a FAIL rather
//      than a fallback. This is one self-test doing two jobs - it names the link
//      and it proves the tile is configured right - and it means host/m8.py can
//      send whichever bitstream is to hand without a flag saying which.
//
//   3. THE CAMERA, through ft_acquire(): sensor id, exposure ramp, and a frame
//      that is neither the wrong length nor a constant fill. A board with no
//      camera says so and stops, because a loop over the flash test vector would
//      print cos 1.000 forever and look perfect.
//
// After that the loop never checks anything against a reference, because there
// is nothing to check it against - the frame is new. What it watches instead is
// the link's sticky bits and the cosine itself: a cosine pinned at exactly 1.000
// means the capture is not reaching the tile.
//
// ---------------------------------------------------------------------------
// One rung, not six
//
// m7's ladder exists to attribute the frame time; this sets the top rung once -
// pipelined, with the build, the requantize scatter and the DRAIN decode all on
// core 1, and core 1's queue split into two priorities - and never touches it
// again. That is the 845 ms/frame configuration C row m7 last measured, and the
// only reason it is safe to take on faith here is that m7 measured it in the
// same firmware from the same frame.c.

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"

#include "encoder.h"
#include "encoder_fast.h"
#include "fpga_config.h"
#include "frame.h"
#include "gemm_host.h"
#include "worker.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// The MCU reference, and the two most recent tile embeddings. emb[] alternates
// so the cosine always has the previous frame to compare against without a copy;
// the probe borrows emb[0] before the loop starts, which is why there is no
// third buffer. 12 KB of the ~24 KB the linker leaves.
static float ref_embed[1024];
static float emb[2][1024];

// ---------------------------------------------------------------------------
// Where a failed start-up goes to sit. m7.c's park(), with its reasoning: the
// watchdog is the exit that depends on nothing, because a board that has gone
// deaf on stdin has no other one. 'B' and 'R' stay because they are free.
static void park(void)
{
    printf("\nparked - 'B' for BOOTSEL, 'R' to re-run; otherwise this reboots\n"
           "         to the bitstream prompt in 8 s\n");
    stdio_flush();
    watchdog_enable(8000, 1);
    for (;;) {
        const int c = getchar_timeout_us(200000);
        if (c == 'B' || c == 'b') { printf("bootsel\n"); sleep_ms(50);
                                    reset_usb_boot(0, 0); }
        if (c == 'R' || c == 'r') { printf("reboot\n");  sleep_ms(50);
                                    watchdog_reboot(0, 0, 0); }
    }
}

// ---------------------------------------------------------------------------
// One frame through the tile, from `image` to `embed`. Returns NULL, or why it
// stopped - a fault or a link error, both already sentences.
//
// This is the whole of what m7.c's run_frame() does minus the reporting, which
// is the point of the frame.c split: the harness prints a table per layer and
// this prints one line per frame, and neither of them is the engine's business.
static const char *encode(const void *image, float *embed)
{
    ft_frame_reset();

    const void *src = image;
    void *dst = ft_arena();

    for (uint32_t i = 0; i < ft_nconv(); i++) {
        const ft_err_t r = ft_layer(i, src, dst);
        if (r.fault) return r.fault;
        if (r.link)  return gh_strerror(r.link);
        src = dst;
        dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                          : (void *)ft_arena();
    }
    ft_pool_head((const float *)src, embed);
    return NULL;
}

// The cosine of two embeddings. In double because the sums are over 512 terms of
// unnormalized head output and this runs once a frame, so there is nothing to
// buy by being clever: the arithmetic that matters for speed is all on the tile.
static double cosine(const float *a, const float *b, uint32_t n)
{
    double dot = 0.0, na = 0.0, nb = 0.0;
    for (uint32_t i = 0; i < n; i++) {
        dot += (double)a[i] * (double)b[i];
        na  += (double)a[i] * (double)a[i];
        nb  += (double)b[i] * (double)b[i];
    }
    if (na <= 0.0 || nb <= 0.0) return 0.0;
    return dot / (sqrt(na) * sqrt(nb));
}

// ---------------------------------------------------------------------------
// Which wire this is, measured rather than declared. Returns 3, 1, or 0 for
// neither, and prints a line per attempt so the console holds the evidence
// either way - a configuration C board that quietly ran as A would be a 1.6x
// slower demo with nothing on screen to say so.
static unsigned probe(const fgx_model_t *m, const void *image)
{
    static const unsigned tries[2] = { 3, 1 };

    for (int k = 0; k < 2; k++) {
        if (!gh_set_width(tries[k])) continue;

        const uint64_t t0 = time_us_64();
        const char *why = encode(image, emb[0]);
        const uint32_t ms = (uint32_t)((time_us_64() - t0) / 1000u);

        int bad = 0;
        if (!why)
            for (uint32_t o = 0; o < m->hdr->embed_dim; o++)
                if (emb[0][o] != ref_embed[o]) bad++;

        printf("probe     : %u forward data line%s -> ", tries[k],
               tries[k] == 1 ? "" : "s");
        if (why)          printf("%s\n", why);
        else if (bad)     printf("%d of %u embedding floats wrong, %u ms\n",
                                 bad, (unsigned)m->hdr->embed_dim,
                                 (unsigned)ms);
        else              printf("%u/%u floats exact, %u ms\n",
                                 (unsigned)m->hdr->embed_dim,
                                 (unsigned)m->hdr->embed_dim,
                                 (unsigned)ms);
        stdio_flush();

        if (!why && !bad) return tries[k];
    }
    return 0;
}

int main(void)
{
    set_sys_clock_khz(150000, true);
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== M8c: capture -> encode -> embed, continuously ===\n\n");
    printf("waiting for a bitstream on USB CDC (host/m8.py)");
    stdio_flush();

    // One bitstream, whichever the host has. The probe below works out which it
    // was, so there is no second download and no flag to get wrong.
    const size_t blen = ft_recv_bitstream(0);
    if (!blen) {
        printf("\nRESULT : FAIL - no usable bitstream\n");
        park();
    }

    fpga_config_pins_init();
    const int cerr = fpga_configure(ft_arena(), blen);
    printf("configure : %s   CDONE=%d nSTATUS=%d\n",
           fpga_strerror(cerr), fpga_done(), fpga_nstatus());
    if (cerr != FPGA_OK) {
        printf("\nRESULT : FAIL - the tile never came up\n");
        park();
    }

    fpga_release_link_pins();
    gemm_host_init();
    w1_start();

    {
        uint32_t md; bool rv, iv;
        if (gh_crc_sniffer(&md, &rv, &iv))
            printf("crc       : DMA sniffer, calc=%u out_rev=%d out_inv=%d\n",
                   (unsigned)md, (int)rv, (int)iv);
        else
            printf("crc       : software - no sniffer mode matched gw_crc()\n");
    }

    fgx_model_t m;
    if (!fgx_open(&m, fgx_weights, (size_t)(fgx_weights_end - fgx_weights))) {
        printf("\nRESULT : FAIL - weights.bin is malformed\n");
        park();
    }
    const char *why = ft_init(&m);
    if (why) {
        printf("\nRESULT : FAIL - %s\n", why);
        park();
    }
    printf("model     : %u layers, %u-d embedding, %u B/buffer\n",
           (unsigned)ft_nlayer(), (unsigned)m.hdr->embed_dim,
           (unsigned)m.scratch);

    // The rung, set once. See the header comment.
    ft_set_mode(true, true, true, true, true);
    ft_set_sweep(false);
    // With m9, and for m9's reason - see the long note there. Changed here too
    // because m8 exists to be m9 without the queries, and an m8 running a
    // different engine mode is not a baseline for anything.
    ft_set_rq(true);

    // --- check 1: the reference ---------------------------------------------
    // On the flash test vector rather than on a camera frame, deliberately: it
    // is the same 12-byte-offset image m5b, m6 and m7 have all been scored
    // against, so a probe failure below is comparable to every earlier run
    // rather than being about today's lighting.
    printf("\nreference : encoder_fast on the flash test vector");
    stdio_flush();
    const void *testvec = (const void *)(fgx_testvec + 12);
    const uint64_t t0 = time_us_64();
    {
        const void *src = testvec;
        void *dst = ft_arena();
        for (uint32_t i = 0; i < ft_nconv(); i++) {
            const bool as_float = fgx_emits_float(&m, i);
            fgx_conv_fast(&m, &m.desc[i], src, dst, as_float, ft_col(), true);
            src = dst;
            dst = (dst == (void *)ft_arena()) ? (void *)ft_scratch()
                                              : (void *)ft_arena();
        }
        ft_pool_head((const float *)src, ref_embed);
    }
    printf("  (%u ms)\n", (unsigned)((time_us_64() - t0) / 1000u));

    // --- check 2: the wire ---------------------------------------------------
    printf("\nlink      : probing the wire by running the whole test vector "
           "over it\n");
    const unsigned w = probe(&m, testvec);
    if (!w) {
        printf("\nRESULT : FAIL - neither width reproduced the reference, so "
               "the tile and this driver disagree about the wire\n");
        park();
    }
    printf("link      : configuration %s, %u forward data line%s, %.1f MHz\n",
           w == 3 ? "C" : "A", w, w == 1 ? "" : "s",
           clock_get_hz(clk_sys) / 2e6);

    // --- check 3: the camera -------------------------------------------------
    printf("\n");
    if (!ft_acquire(m.hdr->in_scale)) {
        printf("\nRESULT : FAIL - no camera. m7 falls back to the flash test "
               "vector because it is measuring; this is a demo, and a loop over "
               "one flash image would print cos 1.000 forever\n");
        park();
    }

    // --- the loop ------------------------------------------------------------
    printf("\nloop      : capture, %u convs on the T8, pool and head. "
           "'B' for BOOTSEL, 'R' to restart.\n", (unsigned)ft_nconv());
    printf("            cos is this frame's embedding against the last one: "
           "near 1.0 on a still scene, and it drops when the view changes.\n\n");
    stdio_flush();

    uint32_t n = 0, cur = 0, good = 0;
    uint64_t sum_us = 0;
    bool said_sticky = false;

    for (;;) {
        // The capture lands RGB565 in the arena and leaves int8 CHW codes in
        // ft_frame(); encode() then writes layer 0 over the arena, which is safe
        // because the convert has already happened.
        if (!ft_capture(m.hdr->in_scale)) {
            printf("frame %5u : no usable frame off the camera\n", (unsigned)n);
            stdio_flush();
            sleep_ms(200);
            n++;
            continue;
        }
        uint32_t exp_us, rd_us;
        ft_cap_stats(NULL, &exp_us, &rd_us);

        const char *stopped = encode(ft_frame(), emb[cur]);
        if (stopped) {
            // A link error is survivable here in a way it was not in m7: the
            // engine returns instead of parking, the next capture is a fresh
            // frame, and a demo that drops one frame and carries on is better
            // than one that stops. If it is not survivable the cosine line will
            // say so by never appearing.
            printf("frame %5u : %s\n", (unsigned)n, stopped);
            stdio_flush();
            sleep_ms(200);
            n++;
            continue;
        }

        const uint32_t fr_us = ft_frame_us();
        sum_us += fr_us + exp_us + rd_us;

        if (good)
            printf("frame %5u : cos to previous %.3f\n", (unsigned)n,
                   cosine(emb[cur], emb[cur ^ 1], m.hdr->embed_dim));
        else
            printf("frame %5u : cos to previous -\n", (unsigned)n);
        stdio_flush();

        // Not on the frame line, because it should never happen and a column
        // that is blank forever is a column nobody reads. A sticky bit means the
        // link under-ran or framed badly at some point in this frame; every
        // number since is suspect, and the run should be looked at rather than
        // watched.
        const uint8_t st = ft_status();
        if (!said_sticky && (st & (GH_ST_UNDERRUN | GH_ST_BADFRAME))) {
            said_sticky = true;
            printf("            sticky link fault (%02x) at frame %u - the "
                   "embeddings after this are not to be trusted\n",
                   st, (unsigned)n);
            stdio_flush();
        }

        cur ^= 1;
        n++;
        good++;

        // Once a frame, which at ~1 s a frame is as responsive as this needs to
        // be, and costs nothing: getchar_timeout_us(0) does not block.
        const int c = getchar_timeout_us(0);
        if (c == 'B' || c == 'b' || c == 'R' || c == 'r') {
            int mean[3];
            ft_cap_stats(mean, NULL, NULL);
            printf("\nstopped   : %u frames, %u good, %u ms/frame mean "
                   "(capture included), configuration %s\n",
                   (unsigned)n, (unsigned)good,
                   good ? (unsigned)(sum_us / good / 1000u) : 0u,
                   w == 3 ? "C" : "A");
            printf("            last frame mean RGB %d %d %d\n",
                   mean[0], mean[1], mean[2]);
            stdio_flush();
            sleep_ms(50);
            if (c == 'B' || c == 'b') reset_usb_boot(0, 0);
            watchdog_reboot(0, 0, 0);
        }
    }
}
