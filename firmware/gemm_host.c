// MCU side of the gemm_top link. See gemm_host.h and rtl/gemm_link.v.
//
// The whole driver is one primitive: clock N bytes out and N bytes back, in one
// DMA pair, with the FPGA's only clock coming from our own PIO. Everything else
// is buffer arithmetic on top of that - and since M7a that arithmetic lives in
// gemm_wire.c, which has no Pico headers and is checked on the host by
// firmware/test_gemm_wire.c. What is left here is the PIO, the DMA, and the six
// commands.
//
// **Stopping the clock stops the tile**, and that is a feature rather than a
// hazard. gemm_top is entirely synchronous to link_clk, so between transactions
// the FPGA is frozen mid-state, not racing us. It is why this driver can decode
// a response at leisure, in C, with no ready line and no timing requirement of
// any kind - and why the only real sizing question is how many idle bytes to
// append to each command, since those bytes ARE the tile's compute time.
//
// Bit order, stated once because three layers have to agree on it. PIO shifts
// right with the OSR/ISR thresholds at 32, so bit 0 of a TX word goes out first
// and the first RX bit lands in bit 0 of an RX word. Little-endian words then
// make byte i of a buffer the (i)th byte on the wire, LSB-first within the
// byte - exactly what gemm_link.v's rx_nx/tx_sr assume, and what
// firmware/link.pio's comment describes. So the TX buffer is just the byte
// stream, and no packing step exists anywhere in this file.

#include <string.h>

#include "hardware/dma.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"

#include "forgix.h"
#include "gemm_host.h"
#include "gemm_wire.h"
#include "link.pio.h"

#define LINK_PIO   pio0
#define LINK_SM    0

// M7g. Everything on the per-transaction path runs out of SRAM.
//
// gemm_wire.c has had GW_HOT since M7a for the single-core version of the
// argument - M5b measured 21% from nothing but moving code off flash XIP. The
// two-core version is stronger: **both cores share one XIP cache and one QSPI
// interface**, so core 1 and core 0 instruction fetches are not independent.
// A miss on either side stalls the other, and w1_main() has been
// __not_in_flash_func since M7e for exactly that reason while the jobs it runs
// were still in flash.
//
// This macro goes on functions called once or more per *transaction* - 6,264 a
// frame. It deliberately does not go on gw_decode_slow(), gb_golden(),
// fgx_conv_fast() or fgx_pool_head(): the first two run only in the A/B and the
// sweep, and the encoder path must keep its M5b flash behaviour, because
// "3,358 ms" is a recorded baseline that every "vs the MCU" ratio in the README
// is quoted against. Making the reference faster would silently deflate them.
#define GH_HOT(f) __not_in_flash_func(f)

// Idle bytes clocked past the end of a response, so the whole of it lands
// inside the capture. Three things push it later: the return path's fixed
// pipeline latency through the PIO input synchroniser and the pad (M2 measured
// 10 bits), the two clocks gemm_link spends in R_CHK and R_EXEC, and the
// preamble hunt, which cannot lock until the marker has fully arrived. 32 bytes
// is 256 bits, an order of magnitude more than any of them, and the only cost
// is 32 clocks per transaction.
#define GH_SLACK   32u

// Response bytes before the payload: 4 preamble + 1 status + 4 rxcrc, and 4
// txcrc after it.
#define GH_RESP_FIX 13u

// The longest transaction is a full DRAIN. Kept a multiple of 4 so the DMA's
// word rounding can never push a legal transaction past the end.
//
// **Capture** bytes, and therefore not a function of the forward width: the
// return line is one bit per link clock in both configurations. This is the
// width-1 value, which is the larger of the two, so it bounds both.
#define GH_CAP_MAX ((5u + GH_RESP_FIX + 4u * GH_MAXWORDS + GH_SLACK + 3u) & ~3u)

// The transmit buffer is **not** sized by the longest transaction, and M7f is
// where that stopped being affordable. The transaction that sets the bound is
// DRAIN, whose forward traffic is pure idle - and at width 3 idle triples,
// because idle is *clocks* and three lanes need three times the bytes to supply
// them, then link.pio's autopull threshold of 24 wastes the fourth byte of
// every word on top. Sizing txb for that is 33 KB of SRAM holding zeros, which
// left this image 4 KB of free RAM.
//
// So the buffer holds only what is not zero - header plus the longest payload -
// and a second DMA channel with read_increment off supplies the tail from one
// word, chained behind the first. It costs a channel and about fifteen lines,
// and it is smaller than the M7d code was at width 1 as well.
//
// 4 KB against a worst case of 2,740 (six header bytes and a 2,048-byte ACT or
// WGT payload, packed four-for-three). gh_frame() rejects anything longer, so
// the margin is checked rather than trusted.
#define GH_TXB_MAX 4096u

// rxb is doubled because M7d decodes a response while the *next* transaction is
// already on the wire, and gw_decode() realigns in place. txb is not: staging
// happens after the previous wait, so only one command is ever being read.
static __attribute__((aligned(4))) uint8_t txb[GH_TXB_MAX];
static __attribute__((aligned(4))) uint8_t rxb[2][GH_CAP_MAX];
static int rx_cur;

// The idle tail, read over and over by the chained channel. In bss, not
// .rodata: a DMA read of a flash address goes through the XIP cache and shares
// it with both cores' instruction fetch, which is the thing GH_HOT exists to
// avoid.
static uint32_t tx_zero;

// M7f. 1 = configuration A, 3 = configuration C (the PIN2 <-> PIN17 jumper).
//
// Runtime, not a build switch, and for the reason M5b insisted on: a ratio
// quoted across two builds of this firmware is not a measurement. The bitstream
// arrives over USB CDC and the pin set and PIO program are already arguments to
// link_pio_init(), so one image can run both configurations back to back in one
// boot - which is the only way to say what the third data line was worth.
static unsigned link_w = 1u;

// How far past the header txb has ever been written. Everything above this is
// still the zeros bss gave us, and the idle tail of a command is *defined* to be
// zeros - so the tail only has to be cleared where a longer payload dirtied it.
//
// M7c measured what the naive version cost: gh_frame() used to memset the whole
// tail of every transaction, 4.35 MB a frame, 2.778 MB of which is RUN's sweep
// budget. Those bytes are zero, stay zero, and are consumed by the tile as
// *clock* rather than as data. It sat outside every prof window, which is the
// only reason it survived M7a.
static size_t txb_dirty;

static int dma_tx = -1, dma_tx2 = -1, dma_rx = -1, dma_crc = -1;
static uint pio_off;
static uint32_t byte_count;

void gh_bytes_reset(void) { byte_count = 0; }
uint32_t gh_bytes(void)   { return byte_count; }

// ---------------------------------------------------------------------------
// M7a state: which decode to run, where the last response was found, and what
// each phase cost.
//
// Two hints, because the two command classes have different reference points.
// See the header comment on gw_locate(): everything but RUN has a response
// whose position follows from the frame length, while RUN's follows the sweep,
// because gemm_link parks in R_WAIT until `busy` falls.
// ---------------------------------------------------------------------------
// M7f split DRAIN off from HINT_LEN as well. Not for the hit rate - locate is
// 29 ms of a frame - but because a deferred DRAIN is decoded on another core,
// and a hint slot shared across cores is a write race for no benefit. DRAIN uses
// this slot on whichever path it takes, so the two paths differ in *where* the
// decode runs and in nothing else.
//
// M7g gave every command its own slot, indexed by the command byte. Once the
// reference is rounded correctly the delta really is the same constant for all
// of CFG/ACT/WGT/NOP, so sharing one slot between them should now be free - but
// "should be" is what the truncated reference also looked like, and four slots
// cost 64 bytes. The accounting is the better half of the bargain: per-command
// hit and miss counts say *which* command is thrashing without another reflash.
//
// M15's CMD_RQP is 0x08 and lands in slot 0, which was the one the 1..7 command
// codes left empty - so it gets a private slot after all, and the table does not
// have to grow. The next command to be added does collide, and the fix then is
// HINT_N 16 and `cmd & 15`; it is not done pre-emptively because the miss
// accounting above is per slot and a slot nothing indexes is a row of zeros
// somebody has to explain.
#define HINT_N      8    // indexed by cmd & 7; GW_CMD_* are 1..8
#define HINT_DRAIN  GW_CMD_DRAIN

static bool      fast_decode = true;
static gw_hint_t hint[HINT_N];
static gh_prof_t prof;

// Written only by the core that calls gh_decode_defer().
static gh_dprof_t dprof;

void gh_set_fast(bool on) { fast_decode = on; }

static inline int hint_of(uint8_t cmd) { return (int)(cmd & 7u); }

void gh_rate_changed(void)
{
    gh_sync();     // a response still in the pipeline was captured at the old rate
    for (int i = 0; i < HINT_N; i++) gw_hint_reset(&hint[i]);
}

void gh_prof_reset(void) { memset(&prof, 0, sizeof prof); }

void gh_dprof_reset(void) { memset(&dprof, 0, sizeof dprof); }

void gh_dprof(gh_dprof_t *out)
{
    *out = dprof;
    out->hint_hit  = hint[HINT_DRAIN].hits;
    out->hint_miss = hint[HINT_DRAIN].misses;
}

// ---------------------------------------------------------------------------
// M7d state: the pipeline, and the work the caller wants done inside it.
// ---------------------------------------------------------------------------
static bool pipelined = true;

// A transaction whose bytes have been clocked but whose response has not been
// looked at yet. Only commands with no return payload and no status of interest
// can be left here - CFG, ACT, WGT, RUN, which is 5,568 of a frame's 6,264
// transactions. DRAIN and NOP retire the pipeline first, so a caller that reads
// got[] or a status byte always reads a settled one.
static struct {
    bool     armed;
    uint8_t  cmd;
    size_t   n, ref;
    uint32_t want_rxcrc;
    int      buf;
} pend;

// An error found while retiring, held until someone returns. Sticky so that
// deferring cannot lose one, only delay it by a transaction.
static gh_err_t pend_err;

static void (*overlap_fn)(void *);
static void  *overlap_arg;

void gh_overlap(void (*fn)(void *), void *arg)
{
    overlap_fn  = fn;
    overlap_arg = arg;
}

void gh_prof(gh_prof_t *out)
{
    *out = prof;
    // Everything but DRAIN, which gh_dprof() reports on its own because it is
    // the one that may have been decoded on the other core.
    out->hint_hit = out->hint_miss = 0;
    for (unsigned c = 0; c < HINT_N; c++) {
        out->hint_hit_cmd[c]  = hint[c].hits;
        out->hint_miss_cmd[c] = hint[c].misses;
        if (c == HINT_DRAIN) continue;
        out->hint_hit  += hint[c].hits;
        out->hint_miss += hint[c].misses;
    }
}

// ---------------------------------------------------------------------------
// The outbound CRC, in hardware.
//
// Every command carries a CRC-32 of its payload that gemm_link.v checks, and
// M7c measured 268 ms a frame computing it - 67 ns per byte, on bytes the DMA
// is about to read anyway. The RP2350's DMA has a checksum accumulator that can
// snoop a channel in flight, so the CRC costs a third channel and nothing else:
// it runs unpaced at a byte a clock, ~11 us on the longest payload, against a
// wire transaction of hundreds. It is not merely faster, it is concurrent.
//
// It cannot snoop the wire channel itself, because that carries header and idle
// tail as well and the CRC covers the payload alone. So it is a separate
// memory-to-nowhere transfer over the payload, started just before the wire and
// read just after.
//
// **Which combination of (calc mode, output reverse, output invert) reproduces
// the reflected CRC-32 that gemm_link.v implements is not guessed.** Getting it
// wrong costs a strap, and the datasheet's description of CRC32R leaves the
// output stage ambiguous. So the driver measures: at init it runs every
// plausible combination over two buffers of different length and content, and
// keeps one only if it agrees with gw_crc() on both. If none does, the software
// CRC stays and the only cost is the 268 ms.
// ---------------------------------------------------------------------------
static bool     sniff_ok;
static uint32_t sniff_mode;
static bool     sniff_rev, sniff_inv;
static uint8_t  crc_sink;

static void GH_HOT(crc_dma_start)(const uint8_t *p, size_t n)
{
    dma_channel_config c = dma_channel_get_default_config(dma_crc);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_8);
    channel_config_set_read_increment(&c, true);
    channel_config_set_write_increment(&c, false);
    channel_config_set_sniff_enable(&c, true);
    dma_sniffer_set_data_accumulator(0xffffffffu);
    dma_channel_configure(dma_crc, &c, &crc_sink, p, n, true);
}

static uint32_t GH_HOT(crc_dma_finish)(void)
{
    dma_channel_wait_for_finish_blocking(dma_crc);
    return dma_sniffer_get_data_accumulator();
}

static bool crc_probe_one(uint32_t mode, bool rev, bool inv,
                          const uint8_t *p, size_t n)
{
    dma_sniffer_enable((uint)dma_crc, mode, true);
    dma_sniffer_set_output_reverse_enabled(rev);
    dma_sniffer_set_output_invert_enabled(inv);
    crc_dma_start(p, n);
    return crc_dma_finish() == ~gw_crc(0xffffffffu, p, n);
}

// Two lengths, and neither a multiple of four: the payloads this will run on are
// whatever gb_geom() produces, so a mode that only agrees on word boundaries
// must not pass.
static void crc_probe(void)
{
    uint8_t v[259];
    uint32_t s = 0x12345678u;
    for (size_t i = 0; i < sizeof v; i++) {
        s = s * 1664525u + 1013904223u;
        v[i] = (uint8_t)(s >> 24);
    }

    static const uint32_t modes[2] = { DMA_SNIFF_CTRL_CALC_VALUE_CRC32,
                                       DMA_SNIFF_CTRL_CALC_VALUE_CRC32R };
    for (int m = 0; m < 2; m++)
        for (int rev = 0; rev < 2; rev++)
            for (int inv = 0; inv < 2; inv++) {
                if (!crc_probe_one(modes[m], rev, inv, v, sizeof v)) continue;
                if (!crc_probe_one(modes[m], rev, inv, v + 3, 61))   continue;
                sniff_ok   = true;
                sniff_mode = modes[m];
                sniff_rev  = rev;
                sniff_inv  = inv;
                dma_sniffer_enable((uint)dma_crc, sniff_mode, true);
                dma_sniffer_set_output_reverse_enabled(sniff_rev);
                dma_sniffer_set_output_invert_enabled(sniff_inv);
                return;
            }

    sniff_ok = false;
    dma_sniffer_disable();
}

bool gh_crc_sniffer(uint32_t *mode, bool *rev, bool *inv)
{
    if (mode) *mode = sniff_mode;
    if (rev)  *rev  = sniff_rev;
    if (inv)  *inv  = sniff_inv;
    return sniff_ok;
}

// ---------------------------------------------------------------------------
// The wire.
// ---------------------------------------------------------------------------
// Loads the PIO program for the current width and points the state machine at
// the pins that width uses. The two pin sets are disjoint in role, not in
// hardware: in configuration C the line that was the return (GPIO1 -> G3)
// becomes a *forward* data lane, the clock moves off GPIO2 onto the jumpered
// GPIO22, and the return comes back on GPIO6 (A4, the FPGA's NSTATUS ball).
// That is link_test.c's config-C wiring, which M2 measured at 26.81 MB/s clean.
static void link_setup(void)
{
    pio_clear_instruction_memory(LINK_PIO);

    // clkdiv 1 with the x2 program: two sys clocks per link clock, so
    // link_clk = sys_clk / 2 and the caller changes the rate with
    // set_sys_clock_khz(). A fractional clkdiv would be the obvious
    // alternative and is wrong here - PIO implements it by stretching some
    // state-machine cycles and not others, which leaves the *shortest* link
    // period at the undivided rate. The tile's Fmax is a constraint on the
    // shortest period, so a fractional divider would buy nothing.
    if (link_w == 1u) {
        pio_off = pio_add_program(LINK_PIO, &link_narrow_x2_program);
        link_pio_init(LINK_PIO, LINK_SM, pio_off, &link_narrow_x2_program,
                      PIN_FPGA_CLK, PIN_FPGA_MOSI, 1, PIN_FPGA_CS, 1.0f);
    } else {
        pio_off = pio_add_program(LINK_PIO, &link_wide_x2_program);
        link_pio_init(LINK_PIO, LINK_SM, pio_off, &link_wide_x2_program,
                      PIN_HDR_PIN2, PIN_FPGA_CS, 3, PIN_FPGA_NSTATUS, 1.0f);
    }
}

void gemm_host_init(void)
{
    if (dma_tx < 0) {
        dma_tx  = dma_claim_unused_channel(true);
        dma_tx2 = dma_claim_unused_channel(true);
        dma_rx  = dma_claim_unused_channel(true);
        dma_crc = dma_claim_unused_channel(true);
    }
    // Built once, into SRAM, so it is never fetched over flash XIP from inside
    // the decode loop. It is also what the sniffer probe below is checked
    // against, so it has to exist first.
    gw_crc_init();
    crc_probe();
    gh_rate_changed();
    link_setup();
}

unsigned gh_width(void) { return link_w; }

bool gh_set_width(unsigned w)
{
    if (w != 1u && w != 3u) return false;
    if (w == link_w) return true;

    // The hints are learned in bit-times of the *capture*, and changing the
    // width changes how many capture bits a given command occupies. Same
    // argument as gh_rate_changed(), and it also drains the pipeline, which
    // has to happen before the pins move under a response still in flight.
    link_w = w;
    gh_rate_changed();
    link_setup();

    // The idle tail of a command is defined to be zeros, and the dirty mark
    // says how much of txb has to be cleared to keep it that way. Repacking
    // moves every byte, so the old mark describes the old layout: clear the
    // whole buffer once and start the bookkeeping again.
    memset(txb, 0, sizeof txb);
    txb_dirty = 0;
    return true;
}

// Clocks `n` bytes (a multiple of 4) from txb while capturing n into `rx`.
//
// This is the only part of a transaction that is the wire's fault. Everything
// it costs is `n * 8` link clocks plus a few dozen register writes to set the
// DMA up, and M7a exists because in M6 it was 10% of the elapsed time.
//
// Split in two for M7d. Between arming and waiting the CPU is doing nothing at
// all - the DMA, not the CPU, is what clocks the tile - so that gap is where the
// previous response gets decoded and where the caller builds its next payload.
// M7c measured 1,246 ms of CPU against 918 ms of wire with the two serialized;
// this is the only structural change that can make the frame `max` rather than
// `sum`.
static uint64_t xfer_t0;

// M7f split the one word count in two. At width 1 they are equal and this is
// the code M7d shipped; at width 3 they differ 4:1, because the forward stream
// is three bytes per word and the return is still one bit per clock - eight
// clocks per wire byte, thirty-two per captured word. Passing `nbuf / 4` to
// both channels would have left the RX channel armed for four times the words
// the PIO will ever put in its FIFO, and `dma_channel_wait_for_finish_blocking`
// would then hang forever on a link that was working perfectly.
// What the last arm was handed, kept for gh_xfer_wait()'s report. Four stores
// per transaction on a path that does six thousand a frame, which is under a
// microsecond in total - and the alternative is diagnosing a hang by reflashing.
static gh_stall_t armed, stall;

static void GH_HOT(gh_xfer_arm)(uint8_t cmd, size_t len,
                                size_t nbuf, size_t ncap, uint8_t *rx)
{
    xfer_t0 = time_us_64();
    const size_t tx_words = nbuf / 4;
    const size_t rx_words = ncap / 4;
    // Everything past txb is the idle tail, which is zeros by definition. The
    // split is a buffer-size decision and nothing else: the wire sees one
    // uninterrupted stream either way.
    const size_t head_words = tx_words < GH_TXB_MAX / 4u ? tx_words
                                                         : GH_TXB_MAX / 4u;
    const size_t tail_words = tx_words - head_words;
    byte_count += ncap * link_w;    // wire bytes, so the two configs compare
    prof.xfers++;

    armed = (gh_stall_t){
        .cmd = cmd, .width = (uint8_t)link_w, .len = (uint32_t)len,
        .nbuf = (uint32_t)nbuf, .ncap = (uint32_t)ncap,
        .head_words = (uint32_t)head_words, .tail_words = (uint32_t)tail_words,
        .rx_words = (uint32_t)rx_words,
    };

    pio_sm_set_enabled(LINK_PIO, LINK_SM, false);
    pio_sm_clear_fifos(LINK_PIO, LINK_SM);
    pio_sm_restart(LINK_PIO, LINK_SM);

    // Armed first and triggered by the chain, so it has to be configured
    // before the channel that will chain into it.
    dma_channel_config c2 = dma_channel_get_default_config(dma_tx2);
    channel_config_set_transfer_data_size(&c2, DMA_SIZE_32);
    channel_config_set_read_increment(&c2, false);
    channel_config_set_write_increment(&c2, false);
    channel_config_set_dreq(&c2, pio_get_dreq(LINK_PIO, LINK_SM, true));
    dma_channel_configure(dma_tx2, &c2, &LINK_PIO->txf[LINK_SM], &tx_zero,
                          tail_words, false);

    dma_channel_config ct = dma_channel_get_default_config(dma_tx);
    channel_config_set_transfer_data_size(&ct, DMA_SIZE_32);
    channel_config_set_read_increment(&ct, true);
    channel_config_set_write_increment(&ct, false);
    channel_config_set_dreq(&ct, pio_get_dreq(LINK_PIO, LINK_SM, true));
    // Chaining to self is how the SDK spells "do not chain". A short
    // transaction has no tail and must not trigger a zero-length transfer.
    channel_config_set_chain_to(&ct, tail_words ? (uint)dma_tx2 : (uint)dma_tx);
    dma_channel_configure(dma_tx, &ct, &LINK_PIO->txf[LINK_SM], txb, head_words,
                          false);

    dma_channel_config cr = dma_channel_get_default_config(dma_rx);
    channel_config_set_transfer_data_size(&cr, DMA_SIZE_32);
    channel_config_set_read_increment(&cr, false);
    channel_config_set_write_increment(&cr, true);
    channel_config_set_dreq(&cr, pio_get_dreq(LINK_PIO, LINK_SM, false));
    dma_channel_configure(dma_rx, &cr, rx, &LINK_PIO->rxf[LINK_SM], rx_words,
                          false);

    dma_start_channel_mask((1u << dma_tx) | (1u << dma_rx));
    pio_sm_set_enabled(LINK_PIO, LINK_SM, true);
}

// The longest legal transaction is a full DRAIN: 8,244 captured bytes, 65,952
// link clocks, 0.88 ms at 75 MHz and 1.5 ms at the slowest rate m7.c will try.
// 50 ms is fifty times that and still short enough that a stalled frame reports
// rather than hangs.
#define GH_XFER_TIMEOUT_US 50000u

static bool GH_HOT(wait_ch)(int ch, uint64_t deadline)
{
    while (dma_channel_is_busy((uint)ch))
        if (time_us_64() > deadline) return false;
    __compiler_memory_barrier();
    return true;
}

static bool GH_HOT(gh_xfer_wait)(void)
{
    // We drive the clock, so no channel can be starved by the FPGA and this
    // cannot deadlock however dead the link is - a link that returns nothing
    // but ones still gets its bits captured. That argument is unchanged, and it
    // is not what the deadline below is for.
    //
    // What it is for: the driver arming a count the PIO will never satisfy.
    // M7f gave the two directions different word counts for the first time, and
    // the first configuration-C run on hardware stopped dead inside layer 1 -
    // no output, nothing to go on, and the whole diagnosis deferred to the next
    // reflash. A deadline turns that into one line of console naming the
    // command, the three counts and which channel was still moving.
    //
    // RX first, and that ordering is load-bearing now that TX is two chained
    // channels. Both sides finish on the same link clock by construction - the
    // TX words carry exactly as many clocks as the RX words capture bits - so
    // an RX that has finished proves the chain has fired and run to the end.
    // Checking dma_tx2 first could catch it in the gap between dma_tx
    // completing and the chain triggering, see it idle, and call it done.
    const uint64_t deadline = time_us_64() + GH_XFER_TIMEOUT_US;
    unsigned busy = 0;
    if (!wait_ch(dma_rx,  deadline)) busy |= 1u;
    if (!wait_ch(dma_tx,  deadline)) busy |= 2u;
    if (!wait_ch(dma_tx2, deadline)) busy |= 4u;
    pio_sm_set_enabled(LINK_PIO, LINK_SM, false);

    if (busy) {
        // Read the remaining counts before aborting - dma_channel_abort()
        // clears them, and the counts are most of the evidence.
        stall          = armed;
        stall.busy     = (uint8_t)busy;
        stall.rx_left  = dma_channel_hw_addr(dma_rx)->transfer_count;
        stall.tx_left  = dma_channel_hw_addr(dma_tx)->transfer_count;
        stall.tx2_left = dma_channel_hw_addr(dma_tx2)->transfer_count;
        // Left running they would fire into the next transaction's FIFO.
        dma_channel_abort(dma_rx);
        dma_channel_abort(dma_tx);
        dma_channel_abort(dma_tx2);
        prof.us_wire += (uint32_t)(time_us_64() - xfer_t0);
        return false;
    }

    // Elapsed, not wire-only: whatever ran in the window above is inside this.
    // prof.us_overlap is what says how much, and a window that overruns the DMA
    // shows up here as wire time that the link did not actually need.
    const uint32_t el = (uint32_t)(time_us_64() - xfer_t0);
    prof.us_wire += el;
    // Capture bytes are one bit per link clock, so ncap*8 is what the wire was
    // actually asked to do - the one quantity that is comparable across widths.
    const unsigned ci = armed.cmd & 7u;
    prof.us_cmd[ci]  += el;
    prof.clk_cmd[ci] += armed.ncap * 8u;
    prof.n_cmd[ci]++;
    return true;
}

const gh_stall_t *gh_last_stall(void) { return &stall; }

// The caller's one-shot job, run once and cleared. It must not touch the link
// and must not touch the buffer the driver is currently clocking - which is
// why m7.c double-buffers its strip and weight staging.
static void GH_HOT(run_overlap)(void)
{
    if (!overlap_fn) return;
    void (*fn)(void *) = overlap_fn;
    void *arg = overlap_arg;
    overlap_fn = NULL;

    const uint64_t t = time_us_64();
    fn(arg);
    prof.us_overlap += (uint32_t)(time_us_64() - t);
}

// ---------------------------------------------------------------------------
// Retiring a deferred response.
// ---------------------------------------------------------------------------
static gh_err_t GH_HOT(decode_now)(uint8_t cmd, uint8_t *rx, size_t n,
                                   size_t ref, uint32_t want_rxcrc,
                                   void *out, size_t nbytes,
                                   uint8_t *status_out)
{
    uint64_t t = time_us_64();
    const size_t bit = fast_decode
        ? gw_locate(rx, n, ref, &hint[hint_of(cmd)])
        : gw_scan(rx, n);
    prof.us_locate += (uint32_t)(time_us_64() - t);
    if (bit == GW_NOPOS) return GH_ERR_NO_PREAMBLE;

    t = time_us_64();
    const gh_err_t e = fast_decode
        ? gw_decode_n(rx, n, bit, cmd, want_rxcrc, out, nbytes, status_out)
        : gw_decode_slow_n(rx, n, bit, cmd, want_rxcrc, out, nbytes,
                           status_out);
    prof.us_decode += (uint32_t)(time_us_64() - t);
    prof.rx_body   += (uint32_t)GW_BODY_B(nbytes);
    return e;
}

static void GH_HOT(gh_retire)(void)
{
    if (!pend.armed) return;
    pend.armed = false;
    const gh_err_t e = decode_now(pend.cmd, rxb[pend.buf], pend.n, pend.ref,
                                  pend.want_rxcrc, NULL, 0, NULL);
    if (e && !pend_err) pend_err = e;
}

// A deferred failure outranks the current call's success: it happened first.
static gh_err_t GH_HOT(take_pend)(gh_err_t e)
{
    if (pend_err) { e = pend_err; pend_err = GH_OK; }
    return e;
}

gh_err_t gh_sync(void)
{
    gh_retire();
    return take_pend(GH_OK);
}

void gh_set_pipelined(bool on)
{
    gh_sync();
    pipelined = on;
}

// ---------------------------------------------------------------------------
// One transaction.
//
// Builds SYNC + cmd + len + payload, appends `idle` bytes of zeros, clocks the
// lot, then locates and decodes the response. `ref_extra` is added to the
// caller's reference position for the hint; it is the sweep budget for RUN and
// zero for everything else. See gw_locate() in gemm_wire.h for why the two
// classes need different references.
//
// M7f added `d`. With it, the response is captured into the caller's buffer and
// the decode is described rather than run - so the transaction does not consume
// an rxb[] slot and nothing here reads the response at all.
// ---------------------------------------------------------------------------
static gh_err_t GH_HOT(gh_frame)(uint8_t cmd, const void *pay, size_t len,
                                 size_t idle, size_t ref_extra, void *out,
                                 size_t nbytes, uint8_t *status_out,
                                 gh_defer_t *d)
{
    // Three counts, equal at width 1 and all different at width 3.
    //
    //   n     wire bytes - what gemm_link.v sees, and what `len` is in
    //   ncap  capture bytes - one bit per link clock, so n/width
    //   nbuf  buffer bytes - what the TX DMA reads, three wire bytes per word
    //
    // `idle` stays in capture bytes for every caller, which is what makes them
    // width-blind: they are asking for room for a *response*, and the response
    // is the same size either way. Supplying eight clocks takes one wire byte
    // per lane, hence the multiply.
    const unsigned w = link_w;
    size_t n = GW_WIRE_HDR(w) + len + idle * w;
    // A whole number of TX words *and* a whole number of captured words: 4 at
    // width 1, 12 at width 3.
    const size_t gran = 4u * w;
    n = (n + gran - 1u) & ~(gran - 1u);
    const size_t ncap = n / w;
    const size_t nbuf = GW_BUFB(w, n);
    // txb only has to hold the part that is not the idle tail; gh_xfer_arm()
    // chains the rest out of one zero word. So `nbuf` is checked against the
    // capture, which is what actually bounds a transaction, and the buffer is
    // checked against the header and payload alone.
    if (ncap > GH_CAP_MAX) return GH_ERR_TOOBIG;
    if (GW_BUFB(w, GW_WIRE_HDR(w)) + GW_BUFB(w, len) > GH_TXB_MAX)
        return GH_ERR_TOOBIG;

    // Only a command with nothing to say back can be left in the pipeline.
    // Anything the caller reads - a drained accumulator block, a status byte -
    // empties it first, so those calls are exactly as synchronous as they were.
    const bool defer = pipelined && !out && !nbytes && !status_out;
    if (!defer) gh_retire();

    uint64_t t = time_us_64();
    txb_dirty = gw_stage(txb, cmd, pay, len, nbuf, txb_dirty, w);
    prof.us_stage += (uint32_t)(time_us_64() - t);

    // M7f moved the CRC source from txb to `pay`. It has to: at width 3 the
    // payload in txb is interleaved with the slot the PIO discards, and both
    // the sniffer and the table want a contiguous run of the bytes gemm_link.v
    // will hash. It is also a simplification at width 1, where the two are the
    // same bytes and one of them needed no pointer arithmetic to name.
    t = time_us_64();
    uint32_t want_rxcrc = 0;
    if (!len)          want_rxcrc = ~gw_crc(0xffffffffu, NULL, 0);
    else if (sniff_ok) crc_dma_start(pay, len);
    else               want_rxcrc = ~gw_crc(0xffffffffu, pay, len);
    prof.us_crc    += (uint32_t)(time_us_64() - t);
    prof.tx_hashed += (uint32_t)len;

    // At most one response is ever outstanding, and the buffer alternates every
    // transaction, so the capture being filled is never the one being decoded.
    // A deferred-decode transaction takes neither: it brought its own buffer,
    // whose lifetime is longer than this driver can reason about.
    const int buf = rx_cur;
    uint8_t *cap = rxb[buf];
    if (d) cap = d->cap;
    else   rx_cur ^= 1;
    gh_xfer_arm(cmd, len, nbuf, ncap, cap);

    if (pipelined) {
        gh_retire();     // the previous response, while this one is in flight
        run_overlap();
    }
    const bool moved = gh_xfer_wait();
    if (!pipelined) run_overlap();   // same work, same order, just not hidden

    // The overlap ran either way: it is the caller's own work, it does not touch
    // the link, and skipping it on a stall would leave the caller's buffers in
    // a state it has no way to reason about. The CRC channel does have to be
    // drained, though - it is armed and the next transaction re-arms it.
    if (!moved) {
        if (len && sniff_ok) crc_dma_finish();
        return GH_ERR_STALL;
    }

    if (len && sniff_ok) {
        t = time_us_64();
        want_rxcrc = crc_dma_finish();
        prof.us_crc += (uint32_t)(time_us_64() - t);
    }

    // In capture bits, which are link clocks - so the forward byte count has to
    // be divided by the number of lanes carrying it.
    //
    // M7g: **rounded up, and the rounding is the whole point.** The last
    // payload bit is wire bit 8*(hdr+len)-1, carried by clock (8*(hdr+len)-1)/w,
    // so gemm_link enters R_EXEC at clock floor((8*(hdr+len)-1)/w) + 1, which is
    // ceil(8*(hdr+len)/w). At width 1 that is the same number and this line used
    // to truncate, which was invisible for exactly that reason.
    //
    // At width 3 truncating loses 2*len mod 3 bits, so the reference moves with
    // `len`'s residue class while the hint's delta cannot - the delta is pad
    // flight and the synchroniser, which are constant. Every ACT and WGT whose
    // length changed residue therefore missed and rescanned. M7f measured it:
    // the miss rate went from 5.7% to 25% and `locate` from 27 ms a frame to
    // 390, which was most of what configuration C's 286 ms of saved wire bought.
    const size_t ref = (8u * (GW_WIRE_HDR(w) + len) + (w - 1u)) / w + ref_extra;

    if (d) {
        d->cmd        = cmd;
        d->n          = ncap;
        d->ref        = ref;
        d->want_rxcrc = want_rxcrc;
        d->out        = out;
        d->nbytes     = nbytes;
        d->armed      = true;
        return take_pend(GH_OK);
    }

    if (defer) {
        pend.armed      = true;
        pend.cmd        = cmd;
        pend.n          = ncap;
        pend.ref        = ref;
        pend.want_rxcrc = want_rxcrc;
        pend.buf        = buf;
        return take_pend(GH_OK);
    }

    return take_pend(decode_now(cmd, rxb[buf], ncap, ref,
                                want_rxcrc, out, nbytes, status_out));
}

// A command with no return payload.
static gh_err_t GH_HOT(gh_simple)(uint8_t cmd, const void *pay, size_t len,
                          size_t extra_idle, size_t ref_extra,
                          uint8_t *status_out)
{
    return gh_frame(cmd, pay, len, GH_RESP_FIX + GH_SLACK + extra_idle,
                    ref_extra, NULL, 0, status_out, NULL);
}

// ---------------------------------------------------------------------------
// Commands.
// ---------------------------------------------------------------------------
gh_err_t GH_HOT(gh_cfg)(const gh_cfg_t *g)
{
    uint8_t p[GW_CFG_BYTES];
    gw_cfg_pack(p, g);
    return gh_simple(GW_CMD_CFG, p, GW_CFG_BYTES, 0, 0, NULL);
}

gh_err_t GH_HOT(gh_act)(const uint8_t *p, size_t n)
{
    return gh_simple(GW_CMD_ACT, p, n, 0, 0, NULL);
}

gh_err_t GH_HOT(gh_wgt)(const int8_t *p, size_t n)
{
    return gh_simple(GW_CMD_WGT, p, n, 0, 0, NULL);
}

gh_err_t GH_HOT(gh_rqp)(const uint8_t *p, size_t n)
{
    // Both checks are the link's too - CMD_RQP is length-policed at
    // GW_RQP_BYTES * GW_RQP_MAXQ - but a table with a partial entry on the end
    // is not a length error there, it is a channel whose bias is three bytes of
    // the previous one's shift. That is a wrong tensor with a good CRC, so it is
    // refused here where the entry structure is still visible.
    if (n % GW_RQP_BYTES)                      return GH_ERR_TOOBIG;
    if (n > GW_RQP_BYTES * GW_RQP_MAXQ)        return GH_ERR_TOOBIG;
    return gh_simple(GW_CMD_RQP, p, n, 0, 0, NULL);
}

gh_err_t GH_HOT(gh_run)(bool first_pass, uint32_t sweep_clocks)
{
    const uint8_t p = first_pass ? 1u : 0u;
    // The sweep runs on the clocks this appends, so the budget is idle bytes
    // and not a delay. One byte is eight link clocks.
    //
    // sweep_clocks is also the hint's reference, and it is a *budget*: the tile
    // finishes early and gemm_link starts the preamble the moment `busy` falls,
    // so the learned delta comes out negative by however much slack the caller
    // left. That is fine and is why gw_hint_t's delta is signed. What matters is
    // that the reference moves with the geometry, so a sequencer that changes P
    // or K does not have to invalidate anything.
    return gh_simple(GW_CMD_RUN, &p, 1, (sweep_clocks + 7u) / 8u,
                     sweep_clocks, NULL);
}

gh_err_t GH_HOT(gh_nop)(uint8_t *status_out)
{
    return gh_simple(GW_CMD_NOP, NULL, 0, 0, 0, status_out);
}

gh_err_t GH_HOT(gh_led)(uint8_t r, uint8_t g)
{
    const uint8_t p[2] = { r, g };
    return gh_simple(GW_CMD_LED, p, 2, 0, 0, NULL);
}

gh_err_t gh_led_badlen(void)
{
    // Three bytes into a length-exact 2-byte opcode. The fabric drops the frame
    // and raises bad_frame, which is the only way to make D1's fault display
    // happen on demand - and that display is worth being able to provoke,
    // because it is the one state where a wrong LED actively lies: a fault
    // rendered as solid red reads as a confident detection.
    //
    // Deliberately not built out of gh_led(), which cannot express a wrong
    // length, and deliberately named for what it is rather than hidden behind a
    // debug flag - a test hook that only exists in some builds is a test hook
    // that is not there when the board misbehaves.
    const uint8_t p[3] = { 0, 0, 0 };
    return gh_simple(GW_CMD_LED, p, 3, 0, 0, NULL);
}

gh_err_t GH_HOT(gh_drain_b)(void *out, size_t nbytes, uint8_t *status_out)
{
    if (nbytes > 4u * GH_MAXWORDS) return GH_ERR_TOOBIG;

    // The status byte here predates the readout it heads, so it cannot report
    // an underrun: that flag is raised *during* T_DATA. It is sticky for
    // exactly that reason, and the caller reads it back with a NOP.
    //
    // M15: the idle tail is the response's own length, so it shrinks with the
    // payload and the byte drain clocks a quarter of the wire the word one
    // does. That is where the milestone's saving actually appears - nothing
    // else on this path knows the difference.
    return gh_frame(GW_CMD_DRAIN, NULL, 0,
                    GH_RESP_FIX + nbytes + GH_SLACK, 0,
                    out, nbytes, status_out, NULL);
}

gh_err_t GH_HOT(gh_drain)(int32_t *out, size_t nwords, uint8_t *status_out)
{
    if (nwords > GH_MAXWORDS) return GH_ERR_TOOBIG;
    return gh_drain_b(out, 4u * nwords, status_out);
}

// ---------------------------------------------------------------------------
// M7f. The same DRAIN, decoded elsewhere. See gemm_host.h for the rules.
// ---------------------------------------------------------------------------
gh_err_t GH_HOT(gh_drain_defer_b)(void *out, size_t nbytes, uint8_t *cap,
                          size_t capbytes, gh_defer_t *d)
{
    if (nbytes > 4u * GH_MAXWORDS)          return GH_ERR_TOOBIG;
    if (capbytes < GH_DRAIN_CAP_B(nbytes))  return GH_ERR_TOOBIG;

    // Rule 3, and the one failure here that would be silent: arming over a
    // descriptor whose decode never ran loses a whole block of accumulators and
    // the frame still finishes. Nothing downstream would notice until the layer
    // CRC, which localises to a layer and not to this line.
    if (d->armed) return GH_ERR_STATUS;

    d->cap = cap;
    return gh_frame(GW_CMD_DRAIN, NULL, 0,
                    GH_RESP_FIX + nbytes + GH_SLACK, 0,
                    out, nbytes, NULL, d);
}

gh_err_t GH_HOT(gh_drain_defer)(int32_t *out, size_t nwords, uint8_t *cap,
                        size_t capbytes, gh_defer_t *d)
{
    if (nwords > GH_MAXWORDS) return GH_ERR_TOOBIG;
    return gh_drain_defer_b(out, 4u * nwords, cap, capbytes, d);
}

// Deliberately not decode_now(): that one writes `prof` and picks its hint from
// the shared table, and both are core 0's. This writes only dprof and the DRAIN
// hint, which the header makes the decoding core's exclusive property.
//
// In SRAM, and for the reason worker.c's loop is: the point of this function is
// to run on the other core *while core 0 is using the link*, and both cores
// fetch through one XIP cache and one QSPI. A wrapper left in flash would spend
// its first instructions competing with the core it is trying to unload. The
// callees it spends its time in - gw_locate(), gw_decode(), gw_crc() - have been
// GW_HOT since M7a for the single-core version of the same argument.
gh_err_t __not_in_flash_func(gh_decode_defer)(gh_defer_t *d)
{
    if (!d->armed) return GH_OK;
    d->armed = false;

    uint64_t t = time_us_64();
    const size_t bit = fast_decode
        ? gw_locate(d->cap, d->n, d->ref, &hint[HINT_DRAIN])
        : gw_scan(d->cap, d->n);
    dprof.us_locate += (uint32_t)(time_us_64() - t);
    dprof.calls++;
    if (bit == GW_NOPOS) return GH_ERR_NO_PREAMBLE;

    t = time_us_64();
    const gh_err_t e = fast_decode
        ? gw_decode_n(d->cap, d->n, bit, d->cmd, d->want_rxcrc,
                      d->out, d->nbytes, NULL)
        : gw_decode_slow_n(d->cap, d->n, bit, d->cmd, d->want_rxcrc,
                           d->out, d->nbytes, NULL);
    dprof.us_decode += (uint32_t)(time_us_64() - t);
    dprof.rx_body   += (uint32_t)GW_BODY_B(d->nbytes);
    return e;
}

const char *gh_strerror(gh_err_t e)
{
    switch (e) {
    case GH_OK:              return "ok";
    case GH_ERR_NO_PREAMBLE: return "no response preamble - link dead, or the sweep budget was too short";
    case GH_ERR_TXCRC:       return "response CRC mismatch - the return path corrupted it";
    case GH_ERR_RXCRC:       return "command CRC mismatch - the FPGA received something else";
    case GH_ERR_STATUS:      return "status byte does not echo the command - byte boundary is wrong";
    case GH_ERR_TOOBIG:      return "transaction exceeds the link buffers";
    case GH_ERR_STALL:       return "a DMA channel did not finish - the driver's word counts disagree with the PIO";
    default:                 return "unknown";
    }
}
