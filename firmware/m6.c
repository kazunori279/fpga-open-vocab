// M6c: the GEMM tile on real silicon.
//
// Runs one real conv2 block on the T8 and compares all 2048 returned int32
// accumulators against fgx_conv_acc() computed on the MCU, over the same
// weights.bin. Anything short of 2048/2048 is a failure - the tile terminates
// at the accumulator precisely so there is no tolerance to fall back on.
//
// **Only the bitstream crosses USB.** The obvious harness would have the host
// send activations, weights and golden values; this one sends 173 KB of
// bitstream and nothing else. The MCU already has weights.bin and testvec.bin
// in flash, so it runs layers 0 and 1 with the reference kernel to produce
// conv2's actual input tensor, lays out the strip and the weight stream itself,
// and computes its own golden values. That removes an entire host-side
// transcription of the layout from the trust boundary: if m6.py had to build
// the strip, a passing run would prove the FPGA agrees with m6.py, not with
// encoder.c.
//
// Streaming the bitstream rather than compiling it in is what makes RTL
// iteration free. Reflashing this board costs a physical PRG-GND strap;
// reconfiguring the FPGA costs a USB write, and fpga_configure() takes a plain
// pointer, so every RTL revision after this one is strap-free.
//
// **The arena is reused, not doubled.** The 192 KB that receives the bitstream
// becomes conv2's input tensor the moment fpga_configure() returns - the FPGA
// reads its configuration over SPI as it goes and never touches host memory
// afterwards. Holding both at once would not fit beside the 132 KB layer-0
// buffer in 520 KB.
//
// **What the sweep is for.** M6b closed at 64.973 MHz modeled on link_clk
// against a 75 MHz constraint - so the honest thing, as rtl/gemm_top.sdc says
// in its own words, is to lower the clock and record the slower frame time
// rather than relax the constraint. But the model is a static analysis with a
// C2 timing corner, and this board is one specific die. So instead of picking
// one operating point, this sweeps six, from above the modeled Fmax to well
// below, and reports bit-exactness at each. That measures where the silicon
// actually stops working, which is worth strictly more than a pass at a rate
// chosen to be safe.
//
// The rate knob is sys_clk and not the PIO divider, which is not obvious. The
// x2 program spends two sys cycles per link clock, so link_clk = sys_clk/2 at
// clkdiv 1. A fractional clkdiv looks like the natural fine adjustment and
// cannot work: PIO implements it by stretching some state-machine cycles and
// not others, so the *shortest* link period stays two sys cycles however large
// the divider - and Fmax is a constraint on the shortest period. The x4 program
// would give a genuine 37.5 MHz but nothing between. set_sys_clock_khz() is
// therefore the only continuous control, and USB survives it because RP2350's
// USB runs from PLL_USB.

#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/pll.h"
#include "hardware/vreg.h"
#include "pico/stdlib.h"

#include "encoder.h"
#include "fpga_config.h"
#include "gemm_block.h"
#include "gemm_plan.h"    // gp_sweep_cycles()
#include "gemm_host.h"

// Linked by blobs.S; see CMakeLists.txt.
extern const uint8_t fgx_weights[], fgx_weights_end[];
extern const uint8_t fgx_testvec[], fgx_testvec_end[];

// ---------------------------------------------------------------------------
// The block under test, written as a gb_spec_t so it is the same kind of object
// gen_gemm_vec.c's cases are. conv2's blocking from the M6 plan: P=128, Q=16,
// split over 8 passes of Cb=8 input channels. Chosen over the others because it
// is the one that proves the interesting things at once - stride 1, unsigned
// input, split K, a channel base that is not zero, and P spanning four output
// rows so the im2col row walk has to be right.
//
// It is also, deliberately, byte for byte the "conv2 splitK" case that
// tb_gemm_link passed on. So a hardware failure here cannot be a layout
// disagreement: the simulator saw these exact activations, weights and golden
// values, through the same gemm_block.c, and returned them exactly.
// ---------------------------------------------------------------------------
static const gb_spec_t BLOCK = {
    .layer = 2, .P = 128, .QG = 2, .Cb = 8, .oy0 = 0, .ox0 = 0, .q0 = 16,
};

#define MAXWORDS    2048u   // the DRAIN ceiling; P*QG <= GB_ADEPTH implies it
#define STRIPMAX    GB_STRIPD
#define WGTMAX      GB_WGTMAX

// ---------------------------------------------------------------------------
// Memory. See the header note on arena reuse.
// ---------------------------------------------------------------------------
#define ARENA_MAX   (192u * 1024u)   // bitstream, then conv2's input tensor
#define SCRATCH_MAX (132u * 1024u)   // layer 0's output, 64x64x32

static __attribute__((aligned(8))) uint8_t arena[ARENA_MAX];
static __attribute__((aligned(8))) uint8_t scratch_b[SCRATCH_MAX];

static uint8_t strip[STRIPMAX];
static int8_t  wstream[WGTMAX];
static int32_t golden[MAXWORDS];
static int32_t got[MAXWORDS];

// ---------------------------------------------------------------------------
// Bitstream receive. Its own tiny framing rather than host/forge.py's: that
// file speaks the Adiuvo Forge Loader's bootloader protocol, which this board
// is not running.
//
//   "FGXB" | len u32 LE | crc32 u32 LE | len bytes
// ---------------------------------------------------------------------------
static uint32_t crc32_of(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
    }
    return ~c;
}

// Blocks until `n` bytes have arrived or the host has been quiet for a second.
// USB CDC provides the flow control: the host stalls on a full endpoint for as
// long as we are not reading, so no windowing is needed above it.
static bool recv_exact(uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int c = getchar_timeout_us(1000000);
        if (c == PICO_ERROR_TIMEOUT) {
            printf("\nrecv: timed out after %u of %u bytes\n",
                   (unsigned)i, (unsigned)n);
            return false;
        }
        p[i] = (uint8_t)c;
    }
    return true;
}

static size_t recv_bitstream(void)
{
    uint8_t hdr[12];

    // Hunt for the magic rather than demanding it first. Anything the terminal
    // typed before m6.py connected is still in the CDC buffer, and discarding
    // it here is cheaper than telling the operator not to press keys.
    uint32_t w = 0;
    for (;;) {
        int c = getchar_timeout_us(1000000);
        if (c == PICO_ERROR_TIMEOUT) { printf("."); stdio_flush(); continue; }
        w = (w << 8) | (uint8_t)c;
        if (w == 0x46475842u) break;      // "FGXB"
    }
    if (!recv_exact(hdr, 8)) return 0;

    uint32_t len, crc;
    memcpy(&len, hdr + 0, 4);
    memcpy(&crc, hdr + 4, 4);
    printf("\nbitstream : %u bytes announced, crc32 %08x\n",
           (unsigned)len, (unsigned)crc);
    if (len == 0 || len > ARENA_MAX) {
        printf("bitstream : rejected - does not fit %u bytes of arena\n",
               (unsigned)ARENA_MAX);
        return 0;
    }
    if (!recv_exact(arena, len)) return 0;

    const uint32_t have = crc32_of(arena, len);
    if (have != crc) {
        printf("bitstream : CRC mismatch - got %08x\n", (unsigned)have);
        return 0;
    }
    printf("bitstream : received intact\n");
    return len;
}

// ---------------------------------------------------------------------------
// One full block over the link, at whatever rate sys_clk is currently at.
// Returns the number of accumulators that disagree, or -1 on a link error.
// ---------------------------------------------------------------------------
static uint32_t us_build;   // gb_strip + gb_weights, which is not driver cost

// ---------------------------------------------------------------------------
// What the PLL was actually programmed to, read back rather than assumed.
//
// This exists because of the 260 MHz hole. A table of sys_clk rates does not
// say which VCO produced each rate, and the VCO turned out to be what the
// failure was about - so the non-monotonicity had to be *inferred* from the
// SDK's source after the fact. One column would have shown it as a fact. The
// general form: when a sweep drives hardware through a translation layer,
// print what the layer chose, not only what was asked of it.
// ---------------------------------------------------------------------------
// Core voltage as a string, because the sweep now moves it in both directions
// and "the rows above 220" no longer describes what happened to a given row.
static const char *volt_name(enum vreg_voltage v)
{
    switch (v) {
    case VREG_VOLTAGE_1_10: return "1.10";
    case VREG_VOLTAGE_1_15: return "1.15";
    case VREG_VOLTAGE_1_20: return "1.20";
    case VREG_VOLTAGE_1_25: return "1.25";
    case VREG_VOLTAGE_1_30: return "1.30";
    default:                return "?";
    }
}

static void pll_now(unsigned *vco_mhz, unsigned *pd1, unsigned *pd2)
{
    const uint32_t refdiv = (pll_sys->cs & PLL_CS_REFDIV_BITS) >> PLL_CS_REFDIV_LSB;
    const uint32_t fbdiv  = pll_sys->fbdiv_int & PLL_FBDIV_INT_BITS;
    *pd1 = (unsigned)((pll_sys->prim & PLL_PRIM_POSTDIV1_BITS) >> PLL_PRIM_POSTDIV1_LSB);
    *pd2 = (unsigned)((pll_sys->prim & PLL_PRIM_POSTDIV2_BITS) >> PLL_PRIM_POSTDIV2_LSB);
    *vco_mhz = refdiv ? (unsigned)(((uint64_t)(XOSC_HZ / MHZ) * fbdiv) / refdiv) : 0u;
}

static int run_block(const gb_geom_t *g, const uint8_t *in, const int8_t *wb,
                     uint32_t *ms_out, uint8_t *status_out)
{
    gh_cfg_t c = {
        .H = (uint16_t)g->H, .W = (uint16_t)g->W, .OW = (uint16_t)g->OW,
        .strip_rw = (uint16_t)g->strip_rw, .strip_ch = (uint16_t)g->strip_ch,
        .oy0 = (uint16_t)g->oy0, .ox0 = (uint16_t)g->ox0, .K = (uint16_t)g->K,
        .P = (uint8_t)g->P, .QG = (uint8_t)g->QG,
        .stride2 = (g->st == 2), .unsigned_in = (g->unsigned_in != 0),
        .w4 = (g->w4 != 0),
    };

    // Per sweep the tile spends 1 cycle in S_LOAD, P in S_SWEEP and FLUSH in
    // S_FLUSH. These are clocks we have to supply, not a timeout: link_clk is
    // the tile's only clock, so under-budgeting strands it mid-sweep. See
    // gemm_plan.h - the expression depends on how the bitstream was built.
    const uint32_t sweep = (uint32_t)gp_sweep_cycles(g);

    gh_err_t e;
    uint8_t st = 0;
    us_build = 0;
    const uint64_t t0 = time_us_64();

    // Clear the sticky faults first, so anything reported at the end belongs to
    // this block and not to a previous rate's failure.
    if ((e = gh_nop(&st)))            goto fail;
    if ((e = gh_cfg(&c)))             goto fail;

    for (int pass = 0; pass < g->npass; pass++) {
        const uint64_t tb = time_us_64();
        gb_strip(g, in, pass, strip);
        gb_weights(g, wb, pass, wstream);
        us_build += (uint32_t)(time_us_64() - tb);
        if ((e = gh_act(strip, (size_t)g->a_len)))    goto fail;
        if ((e = gh_wgt(wstream, (size_t)g->w_len)))  goto fail;
        if ((e = gh_run(pass == 0, sweep)))           goto fail;
    }

    if ((e = gh_drain(got, (size_t)g->nacc, &st)))    goto fail;
    *ms_out = (uint32_t)((time_us_64() - t0) / 1000u);

    // underrun is raised during T_DATA, so the status byte at the head of the
    // DRAIN response predates it. The flag is sticky for exactly that reason;
    // this is the read that can see it.
    if ((e = gh_nop(status_out)))     goto fail;

    int bad = 0;
    for (int i = 0; i < g->nacc; i++)
        if (got[i] != golden[i]) bad++;
    return bad;

fail:
    printf("      link error: %s\n", gh_strerror(e));
    return -1;
}

// ---------------------------------------------------------------------------
int main(void)
{
    set_sys_clock_khz(150000, true);
    stdio_init_all();

    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== M6c: gemm_top on the Trion T8, bit-exact against encoder.c ===\n\n");
    printf("waiting for a bitstream on USB CDC (host/m6.py)");
    stdio_flush();

    const size_t blen = recv_bitstream();
    if (!blen) {
        printf("\nRESULT : FAIL - no usable bitstream\n");
        while (true) tight_loop_contents();
    }

    fpga_config_pins_init();
    const int cerr = fpga_configure(arena, blen);
    printf("configure : %s   CDONE=%d nSTATUS=%d\n",
           fpga_strerror(cerr), fpga_done(), fpga_nstatus());
    if (cerr != FPGA_OK) {
        printf("\nRESULT : FAIL - the tile never came up, so nothing below would mean anything\n");
        while (true) tight_loop_contents();
    }

    fpga_release_link_pins();
    gemm_host_init();

    // --- the reference side, all on the MCU --------------------------------
    fgx_model_t m;
    if (!fgx_open(&m, fgx_weights, (size_t)(fgx_weights_end - fgx_weights))) {
        printf("\nRESULT : FAIL - weights.bin is malformed\n");
        while (true) tight_loop_contents();
    }
    if (m.hdr->n_layers <= (uint32_t)BLOCK.layer + 1) {
        printf("\nRESULT : FAIL - the model has no layer %d\n", BLOCK.layer);
        while (true) tight_loop_contents();
    }

    const fgx_desc_t *d = &m.desc[BLOCK.layer];
    gb_geom_t g;
    const char *why = gb_geom(d, &BLOCK, &g);
    if (why) {
        printf("\nRESULT : FAIL - the block does not fit the tile: %s\n", why);
        while (true) tight_loop_contents();
    }

    printf("\nblock     : layer %d  %dx%dx%d -> %d, stride %d, %s input\n",
           BLOCK.layer, g.H, g.W, g.CIN, g.COUT, g.st,
           g.unsigned_in ? "unsigned" : "signed");
    printf("blocking  : P=%d Q=%d (q0=%d)  Cb=%d  K=%d  %d passes\n",
           g.P, g.Q, g.q0, g.Cb, g.K, g.npass);
    printf("buffers   : strip %d B/pass, weights %d B/pass, %d accumulators\n",
           g.a_len, g.w_len, g.nacc);

    // conv2's input is layer 1's output, so the chain has to be run for real.
    // Two buffers and not three: layer 0's output is 128 KB and is dead the
    // moment layer 1 has consumed it.
    printf("\nreference : running layers 0..%d on the MCU", BLOCK.layer - 1);
    stdio_flush();
    const void *src = (const void *)(fgx_testvec + 12);   // image 0's codes
    uint64_t t0 = time_us_64();
    for (int i = 0; i < BLOCK.layer; i++) {
        // Ping-pong so the last layer's output lands in the arena. With an even
        // BLOCK.layer the first write goes to scratch_b and the last to arena.
        void *dst = ((BLOCK.layer - 1 - i) & 1) ? (void *)scratch_b : (void *)arena;
        fgx_conv_ref(&m, &m.desc[i], src, dst, false);
        src = dst;
    }
    printf("  (%u ms)\n", (unsigned)((time_us_64() - t0) / 1000u));
    const uint8_t *in = (const uint8_t *)src;

    printf("golden    : %d accumulators from fgx_conv_acc()", g.nacc);
    stdio_flush();
    t0 = time_us_64();
    gb_golden(&m, d, &g, in, golden);
    printf("  (%u ms)\n", (unsigned)((time_us_64() - t0) / 1000u));

    // --- the sweep ---------------------------------------------------------
    // Descending, and printed row by row, so a rate that hangs the link still
    // leaves every faster rate's verdict on the terminal. M6b's model says
    // 64.973 MHz, which sits between rows 2 and 3.
    //
    // The three rates past 76000 are the overclock, and they are **appended
    // rather than sorted into place** for the same reason the rest descends:
    // the table is walked in order, so putting the risky rows last means a hang
    // costs only the rows that were going to be new information anyway. The
    // sweep is not monotonic in rate any more; the summary below therefore
    // picks the winner by kHz instead of by table position.
    //
    // 150 MHz is RP2350's guaranteed maximum at the default 1.10 V core, so
    // everything above it needs VREG_ABOVE_150 as well - see the loop. Flash is
    // not a constraint: PICO_FLASH_SPI_CLKDIV is 4 on RP2350, so XIP at 220 MHz
    // sys is 55 MHz, below the 75 it already runs at nothing.
    //
    // I wrote here, before running it, that 176/200/220 ask the FPGA for
    // 88/100/110 MHz and "are expected to fail on the wire", on the grounds
    // that M16 had measured this board bit-exact at 75.0 MHz and that was
    // therefore its ceiling. **All three came back 2048/2048, three runs
    // running, and m7 then confirmed 110 MHz over a whole frame.** The
    // prediction was wrong in an instructive way, so it is recorded rather than
    // deleted: 75.0 MHz was never a measured limit, it was the top row of this
    // table. Every sweep since M6 has descended from 150000 sys, so the fastest
    // rate ever *attempted* was 75, and "bit-exact at 75" was read back as
    // "bit-exact up to 75". A ladder that only steps down cannot find a
    // ceiling; it can only confirm the rung it starts on.
    //
    // What the rows can and cannot show is still bounded by link_clk =
    // sys_clk/2, which makes this one knob and not two. A row conflates the MCU
    // and the fabric: 220000 passing says core 1's builds ran at 220 MHz *and*
    // the tile closed at 110, and it cannot separate them. That is fine for the
    // question actually being asked - both halves are wanted - but it is the
    // reason the x4 PIO programs sitting unused in link.pio still matter. They
    // are what would let sys and link move apart if one of the two runs out
    // first.
    //
    // 240/260/280 are appended for the same reason 176/200/220 were, and the
    // reason is that the paragraph above now applies to *this table*: 220000 is
    // the top row, so "bit-exact at 110 MHz link" means only that nothing above
    // it was asked. Auditing that is what the last milestone's own conclusion
    // asks for, and it costs three array entries.
    //
    // The prediction this time, again written before running it: **the MCU will
    // be fine and the Trion will be what stops.** RP2350 at 240-280 MHz is
    // ordinary; the fabric is already closing at 1.94x its signed-off Fmax and
    // these rows ask it for 120/130/140 MHz. If a row fails, the first guess is
    // the tile, not the core - and the way to tell them apart is that a tile
    // failure shows as wrong accumulators while a core failure shows as a hang.
    //
    // That prediction was half wrong, and the half it got wrong is why this
    // table now carries a VCO. 240 and 280 passed; **260 failed, all three
    // boots**, between two rates that work.
    //
    // The first theory was the PLL. check_sys_clock_khz() counts fbdiv *down*
    // from 320, so it returns the *highest* VCO that produces the requested
    // rate, and 260 was the one rung sitting near the 1600 MHz ceiling:
    //
    //   240 -> VCO 1440 / 6 / 1        the rung below, comfortable
    //   260 -> VCO 1560 / 6 / 1        2.5% under the ceiling
    //   280 -> VCO  840 / 3 / 1        no higher multiple of 280 is an
    //                                  integer fbdiv from 12 MHz, so the
    //                                  search falls all the way down
    //
    // 260 is also reachable from **VCO 780 / 3 / 1** (fbdiv 65), which the SDK
    // will never pick because 1560 is found first, so the row below forces it -
    // two adjacent rows in one boot differing only in the VCO.
    //
    // **That experiment was run and the theory is dead.** Both 260 rows fail,
    // 1560 and 780 alike, with 240 and 280 passing on either side of them. The
    // same table refutes it a second way for free: 176 MHz runs at VCO 1584 and
    // passes, which is *higher* than the 1560 that was supposed to be too close
    // to the ceiling. The forced rows stay in the table as the recorded control.
    //
    // So the hole is not the PLL, and it is not a ceiling either - 280 is above
    // it and works. What is left is something *periodic* in the link rate. The
    // return path has a roughly fixed flight time, so the phase of the response
    // against the sampling clock rotates as the rate rises and revisits the same
    // bad alignment. That predicts a **narrow dead band**, not a wall, and the
    // way to tell a band from a wall is to measure its width: the rows below
    // walk 240 -> 280 in 4 MHz steps.
    //
    // The failure text is consistent with phase rather than a dead link. Two of
    // the 260 rows saw no preamble at all, but the forced-780 fast row got as
    // far as "command CRC mismatch" with 5 kB moved - bytes crossed the wire and
    // arrived wrong, which is what a mis-sampled bit looks like and not what an
    // unclocked link looks like.
    //
    // These steps also re-test the VCO idea at no extra cost, because the two
    // families interleave: 248-264 land on the high VCOs (1488/1512/1536/1584)
    // and 268/272 fall to the low ones (804/816), the way 280 does. If the
    // failures track link_clk and ignore that split, the PLL is ruled out twice.
    // ---- the grid ----------------------------------------------------------
    //
    // Hand-picked rungs are what produced the wrong answer, so the rungs are no
    // longer hand-picked. **76 -> 400 MHz, uniform 4 MHz steps.** Two properties
    // follow from the spacing and they are the whole reason for it:
    //
    //   - 4 MHz of sys is 2 MHz of link, and the known band is at least 6 MHz
    //     wide in link. A band that wide **cannot** hide between samples. The
    //     previous 20 MHz spacing could and did.
    //   - it is uniform, so a second band anywhere between 38 and 200 MHz link
    //     is found by the same pass. That is the actual test of the sampling-
    //     phase story: phase faults are periodic in frequency, so either there
    //     is another band at a fixed spacing or the period is longer than the
    //     reachable range. The current estimate says the latter - no second band
    //     was visible in the old coarse data, which puts the period above about
    //     100 MHz and the next band out past link 230, unreachable. **So this
    //     grid predicts exactly one band.** A second one refutes the model.
    //
    // Rates the PLL cannot express are skipped at run time and cost nothing, so
    // there is no reason to pre-filter the grid by hand - and pre-filtering by
    // hand is how a rung gets quietly omitted.
    //
    // ---- the top of the grid, and where it stops ---------------------------
    //
    // 280 is a pass, which means it is not a bound - the same mistake this
    // section has now caught twice. So the grid runs to 400 and lets the board
    // say where it ends.
    //
    // **The stop rule is the core voltage and it is hard.** 1.30 V is
    // VREG_VOLTAGE_MAX; hardware/vreg.h has 1.35 / 1.40 / 1.50 above it, and all
    // three need POWMAN_VREG_CTRL_DISABLE_VOLTAGE_LIMIT cleared first.
    // vreg_disable_voltage_limit() is never called here. If the ladder ends
    // because 1.30 V is not enough, that is the answer and it gets written down.
    //
    // Reading a failure up there needs one extra care, because this binary runs
    // from flash - there is no copy_to_ram, the image does not fit in SRAM - and
    // XIP timing was fixed by boot2 at the boot clock. Tripling sys_clk past
    // that point is a real candidate for the first thing that breaks, and it is
    // neither the core nor the tile. **The log separates them by itself:** a row
    // that prints "link error" proves the MCU is alive and executing, so the
    // fault is the link or the tile; a row that prints nothing and takes the
    // board with it is the core or the flash. Only the second kind is ambiguous.
    // ---- what the uniform grid found, and what it turned into --------------
    //
    // Three boots of the grid above, identical to the row: **two bands, not
    // one.** link 40 MHz (sys 80) and link 122-130 MHz (sys 244-260), with
    // every other rung from link 38 to 172 passing. The "exactly one band"
    // prediction written above is therefore **wrong**, and what replaced it is
    // better because it has numbers in it:
    //
    //   band centres      link 40 and 126 MHz     ->  spacing 86 MHz
    //   round-trip delay  tau = 1/86 MHz          ->  11.6 ns
    //   next band         link 212 (sys 424)      ->  past the wall, untestable
    //
    // The widths fit too, and they are the part that picks a mechanism. A bad
    // phase window that is a fixed *fraction* of a bit would give both bands the
    // same width in MHz. A fixed *absolute* setup/hold window dt gives a width
    // of dt * f * (1/tau), which grows linearly with frequency: 126/40 = 3.15x
    // wider at the upper band. Measured, band 1 is at most 4 MHz wide and band 2
    // is 8-12, so the ratio brackets 3.15. Solving for dt gives **about 0.9 ns**,
    // which is an ordinary number for a setup-plus-hold window and not a fitted
    // absurdity. Two bands is two points, so the width law still has a degree of
    // freedom in it - this is a model that fits, not a model that is proven.
    //
    // ---- the upper bound ---------------------------------------------------
    //
    // **344 MHz sys / 172 MHz link is bit-exact, three boots. 348 takes the
    // board with it** - and note *how*: no "link error" row is printed at all,
    // the output simply stops. By the rule above that is the core or the flash,
    // not the link and not the tile. The Trion is still returning 2048/2048 at
    // 2.65x its signed-off Fmax when the MCU quits.
    //
    // ---- did the band move? Yes, and slowly ---------------------------------
    //
    // Swept as an explicit voltage axis - the band-2 edges at 1.20 / 1.25 /
    // 1.30 V, cold and warm, three boots. The band **slides upward at about
    // +1 MHz per 0.05 V** and keeps its width, which is the direction the tau
    // model requires: a faster core settles sooner, tau shrinks, the bands go
    // up. Cold versus warm was a null result. The punchline is that **260 MHz
    // passes at 1.20 V**, through the very VCO that the refuted theory blamed.
    // The shipped 140 MHz link would need +0.45 V of that slide and the ceiling
    // is 0.05 V away, so this margin is not something to buy with the rail.
    // The generation code for that sweep is in git at 44ff604; the findings are
    // in docs/milestones.md and do not need re-running.
    //
    // ---- what this block does: the voltage *floor*, 276 to 344 --------------
    //
    // Everything above 280 MHz so far was measured at 1.30 V, because that is
    // what VREG_ABOVE_280 says - and **VREG_ABOVE_280 is a number this file
    // invented, not one it measured.** Same for the 220 -> 1.25 step. Both were
    // set generously on purpose, to keep the MCU from becoming the confound in
    // a question about the Trion, and that was the right call for those runs.
    // It is the wrong thing to inherit into an operating point: whether an
    // appliance sits permanently at VREG_VOLTAGE_MAX or two steps below it is a
    // reliability decision, and right now nobody knows which one 320 MHz needs.
    //
    // So: 276 to 344 by 4, three times, at a forced 1.30 then 1.25 then 1.20 V.
    // The output is a floor per rate - the lowest step that is still bit-exact.
    //
    // **The ordering is the design,** because the failure mode here is not the
    // polite one. Under-volting the core does not print "link error"; it stops
    // the board, forfeiting every row after it and costing a power cycle.
    //
    // The first version of this list ran three separate passes, 1.30 then 1.25
    // then 1.20, rates ascending inside each. That ordering was chosen against
    // a specific fear - that 1.20 V might quit somewhere down at 290 and take
    // the untested top of the *other* two curves with it - and grouping by
    // voltage is the arrangement that survives that. It cost one pass to find
    // out the fear was unfounded: the 1.30 pass went clean to 344, and the 1.25
    // pass went clean to 340 and then stopped dead at 344, forfeiting all of
    // 1.20. **The core's ceiling barely moves with the rail** - one 4 MHz rung
    // between 1.25 and 1.30 - so a hang can only ever land at the very top,
    // where the answer is already known.
    //
    // Which made interleaving strictly better - rate ascending outermost, the
    // voltages inside it - and three boots of that arrangement gave the top
    // half of the curve, identical every time:
    //
    //   1.30 V   276-344 bit-exact
    //   1.25 V   276-340 bit-exact, 344 wedges the board
    //   1.20 V   276-332 bit-exact, 336 marginal (its rows printed in 1 boot
    //            of 3 and vanished in the other 2 while the board carried on),
    //            340 wedges
    //
    // From those three rows alone the ceiling looks almost independent of the
    // rail - three rungs across two whole voltage steps - and that reading was
    // written here before the bottom half was measured. **It is wrong, and the
    // 1.15 V row below is what corrects it.** The curve is not flat; it is
    // saturating, and the flat part is the top.
    //
    // What does survive from these three rows: **the >220 -> 1.25 and
    // >280 -> 1.30 thresholds this file invented are not needed by any rate at
    // or below 332.** Zero non-exact transactions in any of the three boots -
    // every failure up here is the core going quiet, never the link.
    //
    // 336 at 1.20 V is worth its own line because it broke the diagnostic rule.
    // The rule had two cases - a printed "link error" means the MCU is alive
    // and the link or tile failed; silence means the core or the flash. This is
    // a third: silence for three consecutive printfs, and then the board
    // carrying on correctly at the next rate. Execution continued, so the core
    // did not stop; the output simply did not arrive. Whatever the mechanism,
    // **a missing row is not proof of a stopped board**, and only the wedge -
    // silence that never ends, with the board still enumerated and refusing the
    // 1200-baud touch - means what the rule said silence meant.
    //
    // ---- what remains: how far *down* does the rail go? --------------------
    //
    // The above says a 320 MHz operating point does not need VREG_VOLTAGE_MAX.
    // It does not say it needs anything at all, and that is now the question
    // with something riding on it: if 320 holds at 1.10 V - VREG_VOLTAGE_DEFAULT,
    // the power-on rail - then an operating point needs no vreg_set_voltage()
    // call anywhere, and the entire reliability argument about running an
    // appliance off-nominal disappears rather than being managed.
    //
    // So this list finishes the curve downward: 1.15 V and then 1.10 V over the
    // same rates. **Grouped by voltage, not interleaved** - which is a reversal
    // of the paragraph above and for a reason that is the same principle, not a
    // change of mind. Interleaving wins when the curves being compared end at
    // nearly the same rate, because then the truncation a wedge causes is at
    // the top where everything is known anyway. These two are expected to end
    // far apart, and interleaved, the weaker rail would truncate the stronger
    // one's curve at every rate above its own ceiling. Grouped, each gets its
    // own run at the full range, and 1.15 goes first so the more likely wedge
    // is last.
    //
    // Three known-good rows lead, at 1.30 V, as a health check: a boot that
    // cannot do 276 / 304 / 332 there has a bad bitstream or a bad board and
    // its floor numbers mean nothing.
    //
    // ---- and the answer, three boots, identical -----------------------------
    //
    //   1.15 V   276-312 bit-exact, 316 takes the board - twice mid-way through
    //            its own first row, once mid-way through its second
    //
    // **1.15 V is 20 MHz worse than 1.20 V,** against the 4 and 8 MHz that the
    // three steps above it are worth. So the ceiling saturates: the rail buys a
    // lot up to 1.20 and almost nothing after, which is why the top three rows
    // on their own gave the wrong impression of the shape.
    //
    // Two things follow, and they are the point of the whole probe:
    //
    //  - **A 320 MHz operating point needs 1.20 V and cannot have 1.15.** The
    //    ceiling is monotone in the rail across every step measured, so this
    //    also settles 1.10 V without running it: 1.10 cannot exceed 1.15's 312,
    //    and 312 < 320. The 1.10 pass is in the list below and never reached,
    //    because 316 at 1.15 wedges first; it is left there rather than deleted
    //    so that a future edit that lowers FLOOR_HI gets it for free.
    //  - **But 1.20 V is two steps below VREG_VOLTAGE_MAX,** and that is the
    //    reliability question answered. An appliance at 320/160 does not sit at
    //    the regulator's ceiling; it sits one step above the minimum that works,
    //    with 12 MHz of clean margin under it and the whole of 1.25 in reserve.
    //
    // 344 and 348 are both out of the list now. 348 hangs at VREG_VOLTAGE_MAX
    // and the steps above it need vreg_disable_voltage_limit(), which the
    // static assert below forbids; 344 is measured and would only cost the boot
    // its BOOTSEL park. Neither has an experiment left in it.
    typedef struct {
        uint32_t khz;      // requested sys_clk
        uint32_t vco_khz;  // 0 = let set_sys_clock_khz() choose; else force
        uint8_t  pd1, pd2; // read only when vco_khz != 0
        uint8_t  vreg;     // 0 = derive from the rate; else force this step
        const char *banner;// printed before the row when non-NULL
    } rate_t;
    static rate_t RATES[192];
    int NRATE = 0;

    // 276 is the bottom rather than 284, so the curve joins up with something
    // already measured: 260 at 1.20 V passed in the band sweep, and 264-272 sit
    // between that and here. 332 is the top because that is where 1.20 V ended,
    // and a rail below it is not going to do better.
    #define FLOOR_LO  276000u
    #define FLOOR_HI  332000u
    #define FLOOR_STEP  4000u

    // Descending, safest first, and grouped rather than interleaved - see above.
    static const uint8_t  VSTEP[]   = { VREG_VOLTAGE_1_15, VREG_VOLTAGE_1_10 };
    static const char * const VBANNER[] = {
        "  -- floor, 276-332 by 4, core 1.15 V; a missing row is not a wedge --",
        "  -- floor, same rates, core 1.10 V = VREG_VOLTAGE_DEFAULT, no rail change --",
    };

    // The stock rate leads, because the phase breakdown at the end anchors to
    // the first row and has always been quoted at 150 MHz.
    RATES[NRATE++] = (rate_t){ 150000, 0, 0, 0, 0, NULL };

    // Health check. Three rungs at the rail that held all of them, three boots
    // running, so a floor number is only read from a boot that earned it.
    static const uint32_t WELL[] = { 276000, 304000, 332000 };
    for (int i = 0; i < 3; i++)
        RATES[NRATE++] = (rate_t){ WELL[i], 0, 0, 0, VREG_VOLTAGE_1_30,
            i ? NULL : "  -- health check: known-good rungs at 1.30 V --" };

    for (int v = 0; v < (int)(sizeof VSTEP / sizeof VSTEP[0]); v++)
        for (uint32_t k = FLOOR_LO; k <= FLOOR_HI && NRATE < 188;
             k += FLOOR_STEP)
            RATES[NRATE++] = (rate_t){ k, 0, 0, 0, VSTEP[v],
                k == FLOOR_LO ? VBANNER[v] : NULL };

    // Raising the core voltage is the one thing here that is not undone by a
    // power cycle being cheap, so it is bounded and staged: applied per row
    // rather than for the whole sweep, so rates that do not need it do not get
    // it, and it stops one step short of VREG_VOLTAGE_MAX.
    //
    // (An earlier version of this comment said 1.20 V was "four steps below"
    // 1.30. It is two - hardware/vreg.h goes 1.20 = 0b01101, 1.25, 1.30 = MAX.
    // The margin was half what it claimed.)
    //
    // The second step exists to keep the MCU from becoming the confound. The
    // rows above 220 are a question about the *Trion*, and core voltage does
    // nothing for the Trion - it is a separate device on its own supply, which
    // this firmware cannot touch. So the core gets enough headroom to stay out
    // of the way and no more, and a failure above 220 can be read as the tile.
    //
    // A third step exists now only because the ladder runs past 280. It is
    // VREG_VOLTAGE_MAX exactly - the sanctioned ceiling, one step below the
    // range that needs the voltage limit disabled - and the static assert is
    // there so that a future edit cannot walk past it by changing one constant.
    const enum vreg_voltage VREG_ABOVE_150 = VREG_VOLTAGE_1_20;
    const enum vreg_voltage VREG_ABOVE_220 = VREG_VOLTAGE_1_25;
    const enum vreg_voltage VREG_ABOVE_280 = VREG_VOLTAGE_1_30;
    static_assert(VREG_ABOVE_280 <= VREG_VOLTAGE_MAX,
                  "the sweep must never need vreg_disable_voltage_limit()");
    enum vreg_voltage vreg_now = VREG_VOLTAGE_DEFAULT;

    // Every rate is run twice: the M6 decode, then M7a's. Both in the same
    // boot, off one strap, because a ratio quoted across two builds of this
    // firmware is not a measurement - M5b learned that the expensive way.
    //
    // "Both agree word for word" is not checked directly, because it does not
    // need to be: each path is checked against gb_golden(), which is strictly
    // stronger. Two runs that both return 2048/2048 are identical.
    printf("\n  %-9s %-9s %-12s %-5s %-5s %8s %10s %9s %7s   %s\n",
           "sys MHz", "link MHz", "pll", "V", "path", "ms", "kB moved", "MB/s",
           "status", "exact");

    int best = -1;
    uint32_t ms_at_best = 0, kb_at_best = 0;
    unsigned best_vco = 0, best_pd1 = 0, best_pd2 = 0;
    gh_prof_t pf[2] = { 0 };            // the 150 MHz row, slow then fast
    uint32_t  pf_ms[2] = { 0 }, pf_build[2] = { 0 };

    for (int r = 0; r < NRATE; r++) {
        stdio_flush();
        sleep_ms(20);
        // Voltage before frequency, always. The reverse order is the one that
        // browns out the core mid-instruction, and it is a single `if` away.
        // Two thresholds now, so this picks the step the row needs and only
        // writes the regulator when that differs from where it already is.
        if (RATES[r].banner) {
            printf("\n%s\n", RATES[r].banner);
            stdio_flush();
        }
        const enum vreg_voltage want = RATES[r].vreg
            ? (enum vreg_voltage)RATES[r].vreg
            : RATES[r].khz > 280000 ? VREG_ABOVE_280 :
              RATES[r].khz > 220000 ? VREG_ABOVE_220 :
              RATES[r].khz > 150000 ? VREG_ABOVE_150 : VREG_VOLTAGE_DEFAULT;
        assert(want <= VREG_VOLTAGE_MAX);
        if (want != vreg_now) {
            // The edge probe made this bidirectional, and down is the direction
            // with a hazard in it. Going up, the core is already running at a
            // rate its present supply sustains, so the regulator can move first
            // and the rate follows. Going down, the *current* rate may be one
            // the lower supply cannot hold, so the frequency has to come off
            // first - otherwise there is a window where the core is fast on a
            // rail that no longer supports it, which is the classic brownout
            // mid-instruction. 125 MHz is safe at every step in VSTEP[].
            if (want < vreg_now) {
                set_sys_clock_khz(125000, true);
                sleep_ms(2);
            }
            printf("  (core %s V -> %s V)\n",
                   volt_name(vreg_now), volt_name(want));
            stdio_flush();
            vreg_set_voltage(want);
            sleep_ms(10);
            vreg_now = want;
        }
        if (RATES[r].vco_khz) {
            // set_sys_clock_pll() returns void and asserts its arguments, so
            // the sanity check is here rather than after: VCO in range, both
            // post-dividers legal, pd1 >= pd2, and the arithmetic landing on
            // the rate the row claims.
            const uint32_t vco = RATES[r].vco_khz;
            const unsigned p1 = RATES[r].pd1, p2 = RATES[r].pd2;
            if (vco < 750000u || vco > 1600000u || p1 < 1 || p1 > 7 ||
                p2 < 1 || p2 > 7 || p2 > p1 || vco % (p1 * p2) ||
                vco / (p1 * p2) != RATES[r].khz) {
                printf("  %-9.1f  (forced PLL %u/%u/%u is not %u kHz - skipped)\n",
                       RATES[r].khz / 1000.0, (unsigned)vco, p1, p2,
                       (unsigned)RATES[r].khz);
                continue;
            }
            set_sys_clock_pll(vco * KHZ, p1, p2);
        } else if (!set_sys_clock_khz(RATES[r].khz, false)) {
            printf("  %-9.1f  (PLL cannot reach it - skipped)\n",
                   RATES[r].khz / 1000.0);
            continue;
        }
        sleep_ms(50);
        unsigned vco_mhz, pd1, pd2;
        pll_now(&vco_mhz, &pd1, &pd2);
        char pll_s[16];
        snprintf(pll_s, sizeof pll_s, "%u/%u/%u", vco_mhz, pd1, pd2);
        // The learned response offsets are in bit-times, but the pad flight
        // they are made of is in nanoseconds. They do not survive a rate
        // change, and a stale one costs a rescan on every transaction.
        gh_rate_changed();

        for (int fast = 0; fast <= 1; fast++) {
            gh_set_fast(fast != 0);
            gh_bytes_reset();
            gh_prof_reset();

            uint32_t ms = 0;
            uint8_t st = 0;
            const int bad = run_block(&g, in, m.weights + d->w_off, &ms, &st);
            const uint32_t nb = gh_bytes();

            const double sysm = clock_get_hz(clk_sys) / 1e6;
            const char *tag = fast ? "fast" : "slow";
            if (bad < 0) {
                printf("  %-9.1f %-9.3f %-12s %-5s %-5s %8s %10u %9s %7s   link error\n",
                       sysm, sysm / 2.0, pll_s, volt_name(vreg_now), tag,
                       "-", (unsigned)(nb / 1024), "-", "-");
                continue;
            }
            printf("  %-9.1f %-9.3f %-12s %-5s %-5s %8u %10u %9.2f  %02x     %d/%d%s\n",
                   sysm, sysm / 2.0, pll_s, volt_name(vreg_now), tag,
                   (unsigned)ms, (unsigned)(nb / 1024),
                   ms ? (nb / 1024.0 / 1024.0) / (ms / 1000.0) : 0.0,
                   st, g.nacc - bad, g.nacc,
                   (st & (GH_ST_UNDERRUN | GH_ST_BADFRAME))
                       ? "  (sticky fault)" : "");

            if (r == 0) {
                gh_prof(&pf[fast]);
                pf_ms[fast]    = ms;
                pf_build[fast] = us_build;
            }
            // The verdict belongs to the path that ships. Highest kHz rather
            // than first-to-pass, because the overclock rows are appended out
            // of order - see the table. Ties keep the *earlier* row, so a rate
            // that appears twice reports the configuration the SDK would have
            // chosen unless only the forced one passed, which is the reading
            // that does not overstate the forced row.
            if (fast && bad == 0 && !(st & (GH_ST_UNDERRUN | GH_ST_BADFRAME))
                && (best < 0 || RATES[r].khz > RATES[best].khz)) {
                best       = r;
                ms_at_best = ms;
                kb_at_best = nb / 1024u;
                best_vco   = vco_mhz;
                best_pd1   = pd1;
                best_pd2   = pd2;
            }
        }
    }

    // Back to a known clock before the summary, so the numbers printed below
    // are not read at whatever rate the last row happened to leave behind.
    // Frequency down first, then voltage - the mirror of the loop's order, and
    // wrong in the same way if swapped.
    set_sys_clock_khz(150000, true);
    sleep_ms(50);
    if (vreg_now != VREG_VOLTAGE_DEFAULT) {
        vreg_set_voltage(VREG_VOLTAGE_DEFAULT);
        sleep_ms(10);
        vreg_now = VREG_VOLTAGE_DEFAULT;
    }

    // --- where the time went, at 150 MHz -----------------------------------
    // M6 could only estimate this split. Everything below is measured, at 1 us
    // resolution: a phase that reads 0 cost under ~30 us across 28
    // transactions, which is not the same as free.
    printf("\n  phase breakdown at 150 MHz sys / 75 MHz link, per block\n");
    printf("  %-10s %9s %9s %9s %9s %9s %9s\n",
           "path", "total ms", "wire ms", "locate", "crc tx", "decode", "build");
    for (int f = 0; f <= 1; f++) {
        if (!pf[f].xfers) continue;
        printf("  %-10s %9u %9.2f %9.2f %9.2f %9.2f %9.2f\n",
               f ? "fast" : "slow", (unsigned)pf_ms[f],
               pf[f].us_wire / 1000.0, pf[f].us_locate / 1000.0,
               pf[f].us_crc / 1000.0, pf[f].us_decode / 1000.0,
               pf_build[f] / 1000.0);
    }
    printf("  %u transactions, %u B hashed outbound, %u B of response body\n",
           (unsigned)pf[1].xfers, (unsigned)pf[1].tx_hashed,
           (unsigned)pf[1].rx_body);
    // One miss per command class per rate is the cost of learning the offset.
    // Anything more means a prediction that does not hold, and the fast path is
    // quietly running the slow scan underneath.
    printf("  offset hints: %u hit, %u miss  (2 misses expected: one per class)\n",
           (unsigned)pf[1].hint_hit, (unsigned)pf[1].hint_miss);

    printf("\n");
    if (best < 0) {
        printf("RESULT : FAIL - no rate returned all %d accumulators exactly\n",
               g.nacc);
    } else {
        const double link = RATES[best].khz / 2000.0;
        printf("highest bit-exact link_clk : %.3f MHz  (sys %u kHz, "
               "VCO %u MHz / %u / %u%s)\n",
               link, (unsigned)RATES[best].khz, best_vco, best_pd1, best_pd2,
               RATES[best].vco_khz ? ", forced" : "");
        printf("M6b modeled Fmax           : 64.973 MHz, C2 corner, seed 2\n");
        // 8.94 MB/s is M2's measurement at 75 MHz, and it scales with the clock
        // because the link is one bit per clock in each direction. This is the
        // floor the driver is being pushed towards, not a prediction of it.
        printf("wire-bound frame time      : %.0f ms for 3.57 MB forward\n",
               3.57 / (8.94 * link / 75.0) * 1000.0);
        // What the block actually did, scaled by how many of it a frame needs.
        if (kb_at_best)
            printf("measured frame time        : %.0f ms  (%u ms/block x %.1f blocks)\n",
                   ms_at_best * (3570.0 / kb_at_best), (unsigned)ms_at_best,
                   3570.0 / kb_at_best);
        printf("MCU baseline (M5b)         : 3358 ms/frame\n");
        printf("\nRESULT : PASS - %d/%d accumulators bit-exact at %.3f MHz\n",
               g.nacc, g.nacc, link);
    }

    while (true) tight_loop_contents();
}
