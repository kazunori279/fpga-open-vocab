// M2: measure the RP2354A <-> Trion T8 link.
//
// One burst is G link clocks of LFSR data pushed out by DMA while a second DMA
// captures the FPGA's XOR-reduced, inverted, LATENCY-delayed echo. The echo is
// compared against a locally computed prediction at every plausible alignment,
// and the offset with the fewest errors wins.
//
// Searching for the alignment rather than asserting it is the important part.
// The round-trip latency is the RTL's shift register plus PIO's two-flop input
// synchroniser plus however many cycles of flight time - a number nobody should
// try to derive on paper. What matters is whether, at the best alignment, the
// error count is exactly zero.
//
// The sweep walks clock rate (via clkdiv) and sample phase (via program choice)
// independently, because they fail for different reasons and a single pass/fail
// at one guessed operating point would not tell us which.

#include <stdio.h>
#include <string.h>

#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"

#include "forgix.h"
#include "link_test.h"
#include "link.pio.h"

#define W          LINK_CFG                 // data lines, 1 or 3
#define G          (1u << 17)               // link clocks per burst
#define GROUPS_PW  (W == 1 ? 32u : 8u)      // groups packed per 32-bit TX word
#define TXW        (G / GROUPS_PW)
#define RXW        (G / 32u)
#define MAXOFF     64                       // alignment search window
#define BURSTS     8                        // bursts per operating point

#if W == 1
#  define CLK_PIN   PIN_FPGA_CLK    // GPIO2 -> F3
#  define OUT_BASE  PIN_FPGA_MOSI   // GPIO3 -> F2
#  define IN_PIN    PIN_FPGA_CS     // GPIO1 <- G3
#else
#  define CLK_PIN   PIN_HDR_PIN2    // GPIO22 -> PIN2 = jumper = PIN17 -> B3
#  define OUT_BASE  PIN_FPGA_CS     // GPIO1,2,3 -> G3,F3,F2
#  define IN_PIN    PIN_FPGA_NSTATUS// GPIO6 <- A4
#endif

static uint32_t tx_buf[TXW];
static uint32_t rx_buf[RXW];
static uint32_t expect[RXW];        // predicted return, one bit per link clock

static int dma_tx, dma_rx;

// Galois 16-bit maximal-length LFSR, taps 16/14/13/11. rtl/tb_link.v and the
// host harness generate the identical sequence, so a mismatch localises to the
// wire rather than to whoever produced the data.
static inline uint32_t lfsr_next(uint16_t *s)
{
    uint32_t bit = *s & 1u;
    *s = (uint16_t)((*s >> 1) ^ (bit ? 0xB400u : 0u));
    return bit;
}

static void build_pattern(void)
{
    memset(tx_buf, 0, sizeof tx_buf);
    memset(expect, 0, sizeof expect);

    uint16_t s = 0xACE1;
    for (uint32_t g = 0; g < G; g++) {
        uint32_t word = 0;
        for (int b = 0; b < W; b++)
            word |= lfsr_next(&s) << b;

        tx_buf[g / GROUPS_PW] |= word << ((g % GROUPS_PW) * W);

        // link_core returns ~(^data), so the prediction is the inverted parity.
        uint32_t parity = __builtin_parity(word);
        if (!parity)
            expect[g / 32u] |= 1u << (g % 32u);
    }
}

// Bits [32*i + d, 32*i + d + 31] of the received stream.
static inline uint32_t rx_shifted(uint32_t d, uint32_t i)
{
    uint32_t w = d >> 5, s = d & 31u;
    uint32_t lo = rx_buf[i + w];
    if (!s) return lo;
    uint32_t hi = (i + w + 1 < RXW) ? rx_buf[i + w + 1] : 0u;
    return (lo >> s) | (hi << (32 - s));
}

static uint32_t count_errors(uint32_t d, uint32_t words)
{
    uint32_t errs = 0;
    for (uint32_t i = 0; i < words; i++)
        errs += (uint32_t)__builtin_popcount(expect[i] ^ rx_shifted(d, i));
    return errs;
}

void link_test_init(void)
{
    build_pattern();
    dma_tx = dma_claim_unused_channel(true);
    dma_rx = dma_claim_unused_channel(true);
}

// Returns elapsed microseconds for one burst.
static uint64_t run_burst(PIO pio, uint sm)
{
    memset(rx_buf, 0, sizeof rx_buf);

    pio_sm_set_enabled(pio, sm, false);
    pio_sm_clear_fifos(pio, sm);
    pio_sm_restart(pio, sm);

    dma_channel_config ct = dma_channel_get_default_config(dma_tx);
    channel_config_set_transfer_data_size(&ct, DMA_SIZE_32);
    channel_config_set_read_increment(&ct, true);
    channel_config_set_write_increment(&ct, false);
    channel_config_set_dreq(&ct, pio_get_dreq(pio, sm, true));
    dma_channel_configure(dma_tx, &ct, &pio->txf[sm], tx_buf, TXW, false);

    dma_channel_config cr = dma_channel_get_default_config(dma_rx);
    channel_config_set_transfer_data_size(&cr, DMA_SIZE_32);
    channel_config_set_read_increment(&cr, false);
    channel_config_set_write_increment(&cr, true);
    channel_config_set_dreq(&cr, pio_get_dreq(pio, sm, false));
    dma_channel_configure(dma_rx, &cr, rx_buf, &pio->rxf[sm], RXW, false);

    uint64_t t0 = time_us_64();
    dma_start_channel_mask((1u << dma_tx) | (1u << dma_rx));
    pio_sm_set_enabled(pio, sm, true);

    // The RP drives the clock, so neither channel can be starved by the FPGA
    // and this cannot deadlock on a dead link. The timeout is only here to keep
    // a wiring mistake from hanging the report.
    absolute_time_t deadline = make_timeout_time_ms(2000);
    while (dma_channel_is_busy(dma_tx) || dma_channel_is_busy(dma_rx)) {
        if (absolute_time_diff_us(get_absolute_time(), deadline) < 0) {
            dma_channel_abort(dma_tx);
            dma_channel_abort(dma_rx);
            pio_sm_set_enabled(pio, sm, false);
            return 0;
        }
    }
    uint64_t dt = time_us_64() - t0;
    pio_sm_set_enabled(pio, sm, false);
    return dt;
}

typedef struct {
    const char          *name;
    const pio_program_t *prog;
    uint                 cycles;   // sys clocks per link clock
} variant_t;

static void run_point(PIO pio, uint sm, uint offset,
                      const variant_t *v, uint div)
{
    link_pio_init(pio, sm, offset, v->prog, CLK_PIN, OUT_BASE, W, IN_PIN,
                  (float)div);

    uint32_t sys_hz  = clock_get_hz(clk_sys);
    double   link_hz = (double)sys_hz / (v->cycles * div);

    uint32_t worst_err = 0;
    uint32_t best_off  = 0;
    uint64_t total_us  = 0;
    bool     timed_out = false;

    for (int b = 0; b < BURSTS; b++) {
        uint64_t dt = run_burst(pio, sm);
        if (!dt) { timed_out = true; break; }
        total_us += dt;

        // Search on a prefix, then verify the whole capture at that offset.
        uint32_t bo = 0, be = UINT32_MAX;
        for (uint32_t d = 0; d < MAXOFF; d++) {
            uint32_t e = count_errors(d, 256);
            if (e < be) { be = e; bo = d; }
        }
        uint32_t errs = count_errors(bo, RXW - 4);
        if (b == 0) best_off = bo;
        if (errs > worst_err) worst_err = errs;
    }

    if (timed_out) {
        printf("  %-6s div %2u  %7.2f MHz   TIMEOUT\n", v->name, div, link_hz / 1e6);
        return;
    }

    double bytes  = (double)BURSTS * G * W / 8.0;
    double mbps   = bytes / (total_us * 1e-6) / (1024 * 1024);
    printf("  %-6s div %2u  %7.2f MHz  offset %2lu  %8lu errors  %6.2f MB/s  %s\n",
           v->name, div, link_hz / 1e6, (unsigned long)best_off,
           (unsigned long)worst_err, mbps, worst_err ? "" : "CLEAN");
}

void link_test_sweep(void)
{
    static const variant_t variants[] = {
#if W == 1
        { "x4", &link_narrow_x4_program, 4 },
        { "x2", &link_narrow_x2_program, 2 },
#else
        { "x4", &link_wide_x4_program,   4 },
        { "x2", &link_wide_x2_program,   2 },
#endif
    };

    PIO  pio = pio0;
    uint sm  = 0;

    printf("\nlink sweep: %u data line%s, %u KiB per burst, %d bursts per point\n",
           W, W == 1 ? "" : "s", (unsigned)(G * W / 8 / 1024), BURSTS);
    printf("sys_clk = %.1f MHz\n", clock_get_hz(clk_sys) / 1e6);

    for (size_t i = 0; i < count_of(variants); i++) {
        // Reload between variants: the programs differ in length, so the
        // instruction memory has to be reclaimed rather than reused.
        pio_clear_instruction_memory(pio);
        uint offset = pio_add_program(pio, variants[i].prog);

        for (uint div = 16; div >= 1; div >>= 1)
            run_point(pio, sm, offset, &variants[i], div);
    }

    printf("\nRecord the highest CLEAN rate - every later estimate depends on it.\n");
}
