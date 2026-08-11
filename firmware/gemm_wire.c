// See gemm_wire.h. Framing, CRC, and response decode - no PIO, no DMA.

#include <string.h>

#include "gemm_wire.h"

// Defined by firmware/CMakeLists.txt for the on-device build only, so the same
// source compiles with a bare `cc` for firmware/test_gemm_wire.c. The hot path
// runs 28 times a block and is small; M5b measured 21% from nothing but moving
// code off flash XIP, and that was a loop that ran far less often per byte.
#ifdef GW_PICO
#include "pico.h"
#define GW_HOT(f) __not_in_flash_func(f)
#else
#define GW_HOT(f) f
#endif

// ---------------------------------------------------------------------------
// CRC-32, reflected.
//
// M6 hashed one bit at a time and the comment in gemm_host.c argued a table was
// not worth it "beside the 16 K link clocks the same payload spends on the
// wire". **That was wrong.** It is true per byte on the wire and false in
// elapsed time, because the CPU and the wire never overlap: we are the FPGA's
// only clock, so the tile is frozen for the entire duration of the decode. One
// conv2 block hashes ~22 KB outbound and ~30 KB inbound, and at eight
// iterations a byte that was ~27% of the block.
// ---------------------------------------------------------------------------
static uint32_t crc_tab[256];
static bool     crc_ready;

static inline uint32_t crc1(uint32_t c, uint32_t b)
{
    return (c >> 1) ^ (GW_CRCPOLY & (0u - ((c ^ b) & 1u)));
}

void gw_crc_init(void)
{
    if (crc_ready) return;
    for (unsigned i = 0; i < 256u; i++) {
        uint32_t c = i;
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (GW_CRCPOLY & (0u - (c & 1u)));
        crc_tab[i] = c;
    }
    crc_ready = true;
}

uint32_t GW_HOT(gw_crc)(uint32_t c, const uint8_t *p, size_t n)
{
    // One predictable branch per call, not per byte. Cheaper than the class of
    // bug where a new caller forgets gw_crc_init() and gets a table of zeros -
    // which would hash to a constant and pass nothing, but only on hardware.
    if (!crc_ready) gw_crc_init();
    for (size_t i = 0; i < n; i++)
        c = (c >> 8) ^ crc_tab[(c ^ p[i]) & 0xffu];
    return c;
}

uint32_t gw_crc_slow(uint32_t c, const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++)
        for (int b = 0; b < 8; b++)
            c = crc1(c, (uint32_t)(p[i] >> b));
    return c;
}

// ---------------------------------------------------------------------------
// Building a command.
// ---------------------------------------------------------------------------
void gw_hdr(uint8_t *tx, uint8_t cmd, size_t len)
{
    tx[0] = 0xa5;
    tx[1] = 0x5a;
    tx[2] = cmd;
    tx[3] = (uint8_t)(len & 0xffu);
    tx[4] = (uint8_t)(len >> 8);
}

// M7f. Twelve wire bytes to four words at a time.
//
// The byte-at-a-time version is `dst[(i/3)*4 + (i%3)] = src[i]`, one line and
// about four cycles a byte. That is not affordable: this runs over every byte
// gw_stage() ever sends, ~4 MB a frame, so at four cycles a byte it would cost
// ~110 ms - most of what the third data line is being fitted to buy. Three
// loads, four shifts-and-ors and four stores per twelve bytes is ~3 cycles per
// *word*, which puts it in the same class as the memcpy it replaces.
//
// The four constants are just the byte lanes written out. s0 supplies bytes
// 0,1,2 whole and then its top byte opens d1; s1 finishes d1 and opens d2; s2
// finishes d2 and fills d3. Every dest word's top byte falls out as zero, which
// is what the PIO discards.
void GW_HOT(gw_pack3)(uint8_t *dst, const uint8_t *src, size_t len)
{
    size_t i = 0, o = 0;

    // M7h: four scalars and four stores, not an array and a memcpy.
    //
    // The array version read exactly the same, and GCC 14 compiled it into four
    // stores to the stack, an `ldmia sp!, {r0,r1,r2,r3}` to read them straight
    // back, and four more stores to `dst` - a 16-byte round trip through memory
    // per twelve bytes, on top of the three loads and four stores the packing
    // actually needs. `memcpy(dst + o, d, 16)` is what asked for it: `d` has to
    // be addressable to be a memcpy source, and nothing told GCC that the copy
    // was the only reader.
    //
    // Naming the four words separately costs nothing in clarity - the lane
    // arithmetic below is unchanged - and leaves seven memory operations per
    // twelve bytes, which is the floor for this permutation.
    for (; i + 12u <= len; i += 12u, o += 16u) {
        uint32_t s0, s1, s2;
        memcpy(&s0, src + i,      4);
        memcpy(&s1, src + i + 4u, 4);
        memcpy(&s2, src + i + 8u, 4);
        const uint32_t d0 =  s0        & 0x00ffffffu;
        const uint32_t d1 = (s0 >> 24) | ((s1 & 0x0000ffffu) <<  8);
        const uint32_t d2 = (s1 >> 16) | ((s2 & 0x000000ffu) << 16);
        const uint32_t d3 =  s2 >>  8;
        memcpy(dst + o,       &d0, 4);
        memcpy(dst + o +  4u, &d1, 4);
        memcpy(dst + o +  8u, &d2, 4);
        memcpy(dst + o + 12u, &d3, 4);
    }
    // Tail: at most eleven bytes, and written a whole word at a time rather
    // than byte by byte so that **every** byte below GW_BUFB(3, len) ends up
    // defined - the unused slots of a final partial group zeroed, and slot 3
    // zeroed to match what the fast loop's `d[3] = s2 >> 8` already does.
    //
    // Not a tidiness point. gw_stage() starts clearing the idle tail at exactly
    // GW_BUFB(3, len), so a byte left undefined below it is a stale wire byte
    // from a longer previous transaction sitting immediately behind the
    // payload. test_stage_w(3) caught it at once and put the first wrong byte
    // at hdr + len every time; the packing arithmetic itself was never wrong.
    while (i < len) {
        uint8_t g[4] = { 0, 0, 0, 0 };
        for (unsigned k = 0; k < 3u && i < len; k++, i++) g[k] = src[i];
        memcpy(dst + o, g, 4);
        o += 4u;
    }
}

// M7d. See gw_stage() in the header for why the tail is not simply memset.
//
// GW_HOT since M7g. It runs once per transaction, 6,264 times a frame, and it
// was the only member of this file's per-transaction path still being fetched
// over XIP - an oversight rather than a decision, since M7a marked its
// neighbours. It measured 50 ms, the largest thing left on core 0 that is not
// a stall.
size_t GW_HOT(gw_stage)(uint8_t *tx, uint8_t cmd, const void *pay, size_t len,
                        size_t nbuf, size_t dirty, unsigned width)
{
    size_t body;

    if (width == 1u) {
        gw_hdr(tx, cmd, len);
        if (len) memcpy(tx + GW_HDR_BYTES, pay, len);
        body = GW_HDR_BYTES + len;
    } else {
        // Six wire bytes is two whole dest words, so the payload starts at
        // tx[8] with no phase and packs on its own.
        uint8_t h[GW_WIRE_HDR(3u)];
        h[0] = 0x00;
        gw_hdr(h + 1, cmd, len);
        gw_pack3(tx, h, sizeof h);
        if (len) gw_pack3(tx + GW_BUFB(3u, GW_WIRE_HDR(3u)), pay, len);
        // Rounded up, so this can name up to two slots past the last wire byte
        // - and `body` is where the memset *starts*, so naming a byte here is
        // a promise that gw_pack3() wrote it. That is why gw_pack3()'s tail
        // loop writes whole words: the first version left those slots alone,
        // and every transaction shorter than its predecessor then carried a
        // stale wire byte immediately behind its payload. test_stage_w(3)
        // found it on iteration 12.
        body = GW_BUFB(3u, GW_WIRE_HDR(3u)) + GW_BUFB(3u, len);
    }

    if (dirty > body) {
        const size_t upto = dirty < nbuf ? dirty : nbuf;
        if (upto > body) memset(tx + body, 0, upto - body);
    }
    // Anything past nbuf was not cleared and is still dirty, so the mark can
    // only drop when the whole dirty region fell inside this transaction.
    return dirty <= nbuf ? body : dirty;
}

void gw_cfg_pack(uint8_t p[GW_CFG_BYTES], const gh_cfg_t *g)
{
    p[0]  = (uint8_t)g->H;         p[1]  = (uint8_t)(g->H >> 8);
    p[2]  = (uint8_t)g->W;         p[3]  = (uint8_t)(g->W >> 8);
    p[4]  = (uint8_t)g->OW;        p[5]  = (uint8_t)(g->OW >> 8);
    p[6]  = (uint8_t)g->strip_rw;  p[7]  = (uint8_t)(g->strip_rw >> 8);
    p[8]  = (uint8_t)g->strip_ch;  p[9]  = (uint8_t)(g->strip_ch >> 8);
    p[10] = (uint8_t)g->oy0;       p[11] = (uint8_t)(g->oy0 >> 8);
    p[12] = (uint8_t)g->ox0;       p[13] = (uint8_t)(g->ox0 >> 8);
    p[14] = (uint8_t)g->K;         p[15] = (uint8_t)(g->K >> 8);
    p[16] = g->P;
    p[17] = g->QG;
    // Bits 2 and 3 were spare, so M14's width flag and M15's requantize flag
    // both ride along and GW_CFG_BYTES stays 20 - which matters more than it
    // looks: the CFG length is baked into the RTL's shift-register width and
    // into every recorded framing measurement since M6c, and widening it would
    // invalidate test_gemm_plan.c's anchors. p[19] is still reserved, so the
    // next four flags are free too.
    p[18] = (uint8_t)((g->stride2 ? 1u : 0u) | (g->unsigned_in ? 2u : 0u)
                    | (g->w4 ? 4u : 0u) | (g->rq ? 8u : 0u));
    p[19] = 0;                     // reserved
}

bool gw_rqp_pack(uint8_t p[GW_RQP_BYTES], int32_t bias, int32_t m, int s)
{
    // Three range checks and not an assert, because the caller is the only one
    // who can say what to do about a channel that does not fit - gb_rqp()
    // refuses the block, and the layer falls back to the int32 readout rather
    // than returning a wrong tensor that passes CRC.
    //
    // bias is 24 signed on the wire. The exported model's widest is 277,460,
    // which is 20 bits, so the margin is real rather than a coincidence to be
    // preserved. m is 18 unsigned and s is 6, both exactly as fgx_rq_pick()
    // produces them: M lands in [2^17, 2^18) by construction and s = 18 - e.
    if (bias < -8388608 || bias > 8388607) return false;
    if (m < 0 || m > 0x3ffff)              return false;
    if (s < 0 || s > 63)                   return false;

    const uint32_t b = (uint32_t)bias & 0xffffffu;
    p[0] = (uint8_t)b;
    p[1] = (uint8_t)(b >> 8);
    p[2] = (uint8_t)(b >> 16);
    p[3] = (uint8_t)m;
    p[4] = (uint8_t)((uint32_t)m >> 8);
    // The tile shifts these in from the top of a 48-bit register, so byte 5
    // ends up as bits 47:40 - s in 47:42 and M's top two bits in 41:40.
    p[5] = (uint8_t)((((uint32_t)m >> 16) & 3u) | ((uint32_t)s << 2));
    return true;
}

// ---------------------------------------------------------------------------
// Finding the response.
// ---------------------------------------------------------------------------
static inline uint32_t gwbit(const uint8_t *rx, size_t ncap, size_t i)
{
    return (i < ncap * 8u) ? (uint32_t)((rx[i >> 3] >> (i & 7u)) & 1u) : 0u;
}

size_t gw_scan_slow(const uint8_t *rx, size_t ncap)
{
    const size_t nbits = ncap * 8u;
    uint32_t h = 0;
    for (size_t i = 0; i < nbits; i++) {
        h = (h >> 1) | ((uint32_t)((rx[i >> 3] >> (i & 7u)) & 1u) << 31);
        if (h == GW_PREAMBLE) return i + 1u;
    }
    return GW_NOPOS;
}

// The 32 bits starting at bit `b`, oldest bit in the LSB - exactly what
// gw_scan_slow()'s accumulator holds once it has consumed bit b+31. Bounds
// checked, so it is also the definition of what "past the capture" means: zero.
static inline uint32_t gw_win_safe(const uint8_t *rx, size_t ncap, size_t b)
{
    uint32_t h = 0;
    for (int k = 0; k < 32; k++)
        h |= gwbit(rx, ncap, b + (size_t)k) << k;
    return h;
}

// M7g. Both of these were strictly one bit at a time, and M7f's configuration C
// made that expensive enough to see: `locate` went from 27 ms a frame to 390.
// Most of that is the reference bug gemm_host.c fixes, but not all - a *hit* was
// already 32 bounds-checked bit extracts, on a path that runs 6,090 times a
// frame, and a miss was a bit-at-a-time walk of the whole capture.
//
// A little-endian 64-bit load holds all eight bit alignments of a byte at once,
// so one load serves eight candidate positions and the compare is the only work
// left. Same little-endian assumption gw_align() already makes two functions
// down, and the T8 link is not involved: this is purely how the MCU reads its
// own capture buffer.
size_t GW_HOT(gw_scan)(const uint8_t *rx, size_t ncap)
{
    size_t o = 0;
    for (; o + 8u <= ncap; o++) {
        uint64_t w;
        memcpy(&w, rx + o, 8);
        for (unsigned s = 0; s < 8u; s++)
            if ((uint32_t)(w >> s) == GW_PREAMBLE)
                return o * 8u + s + 32u;
    }
    // The last seven bytes, where a 64-bit load would read past the buffer.
    // gw_scan_slow() never matches before bit 31 - it would need the preamble's
    // LSB to be zero and it is one - so starting the window at o*8 loses
    // nothing, and ncap < 4 simply never enters the loop.
    for (size_t b = o * 8u; b + 32u <= ncap * 8u; b++)
        if (gw_win_safe(rx, ncap, b) == GW_PREAMBLE)
            return b + 32u;
    return GW_NOPOS;
}

// Is the 32-bit window ending just before bit `p` the preamble? Same bit order
// gw_scan() accumulates: the oldest bit is the LSB.
static bool GW_HOT(gw_check)(const uint8_t *rx, size_t ncap, size_t p)
{
    const size_t b = p - 32u;
    if ((b >> 3) + 8u <= ncap) {
        uint64_t w;
        memcpy(&w, rx + (b >> 3), 8);
        return (uint32_t)(w >> (b & 7u)) == GW_PREAMBLE;
    }
    // Within eight bytes of the end. A real response never sits here - its body
    // follows it - but a stale hint can point here, and it has to say no rather
    // than read off the end.
    return gw_win_safe(rx, ncap, b) == GW_PREAMBLE;
}

size_t GW_HOT(gw_locate)(const uint8_t *rx, size_t ncap, size_t ref, gw_hint_t *h)
{
    if (h->valid) {
        const ptrdiff_t p = (ptrdiff_t)ref + h->delta;
        if (p >= 32 && (size_t)p <= ncap * 8u && gw_check(rx, ncap, (size_t)p)) {
            h->hits++;
            return (size_t)p;
        }
        h->misses++;
    }

    const size_t p = gw_scan(rx, ncap);
    if (p == GW_NOPOS) return GW_NOPOS;

    // Latch even on a suspicious value. A false lock inside the payload gives a
    // delta that fails gw_check() next time and costs one more rescan, which is
    // strictly better than refusing to learn and rescanning forever.
    h->delta = (ptrdiff_t)p - (ptrdiff_t)ref;
    h->valid = true;
    return p;
}

// ---------------------------------------------------------------------------
// Decoding.
// ---------------------------------------------------------------------------
void GW_HOT(gw_align)(uint8_t *rx, size_t ncap, size_t bit, size_t nbytes)
{
    const size_t   o = bit >> 3;
    const unsigned s = (unsigned)(bit & 7u);

    // o >= 4 always, because `bit` sits just past a 32-bit preamble. That is
    // what makes this safe in place: every write lands at least four bytes
    // behind the read it came from.
    if (s == 0) {
        const size_t have = (o < ncap) ? ncap - o : 0u;
        const size_t n    = (nbytes < have) ? nbytes : have;
        memmove(rx, rx + o, n);
        if (n < nbytes) memset(rx + n, 0, nbytes - n);
        return;
    }

    size_t i = 0;
    while (i + 4u <= nbytes && o + i + 8u <= ncap) {
        uint32_t w0, w1;
        memcpy(&w0, rx + o + i,      4);   // unaligned; GCC emits a plain LDR
        memcpy(&w1, rx + o + i + 4u, 4);   // on M33, and a byte loop elsewhere
        const uint32_t v = (w0 >> s) | (w1 << (32u - s));
        memcpy(rx + i, &v, 4);
        i += 4u;
    }
    for (; i < nbytes; i++) {
        const uint32_t lo = (o + i      < ncap) ? rx[o + i]      : 0u;
        const uint32_t hi = (o + i + 1u < ncap) ? rx[o + i + 1u] : 0u;
        rx[i] = (uint8_t)((lo >> s) | (hi << (8u - s)));
    }
}

// The check order is load-bearing and matches gw_decode_slow() exactly, because
// the two are A/B'd against each other on the board.
//
// A wrong command echo means the byte boundary is wrong, which would make every
// later check meaningless - so it is tested first. And the payload is written
// out *before* txcrc is checked, because that is what the M6 cursor did: it
// read the words and only then read the trailing CRC. Reordering would be an
// improvement nobody asked for and would make the two paths disagree on what
// `out` holds after a failure.
// M15 made the payload's unit a byte, so this counts bytes and gw_decode() is
// the word-shaped wrapper. Nothing else moved - in particular the memcpy is
// still one call whatever the length, so the int8 path costs a quarter of the
// copy rather than four times the calls.
gh_err_t GW_HOT(gw_decode_n)(uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                             uint32_t want_rxcrc, void *out, size_t nbytes,
                             uint8_t *status_out)
{
    const size_t body = GW_BODY_B(nbytes);
    gw_align(rx, ncap, bit, body);

    const uint8_t status = rx[0];
    uint32_t rxcrc;
    memcpy(&rxcrc, rx + 1, 4);

    if (status_out) *status_out = status;

    if (!(status & GH_ST_MARK) || ((status >> 4) != (cmd & 0x0fu)))
        return GH_ERR_STATUS;
    if (rxcrc != want_rxcrc)
        return GH_ERR_RXCRC;

    if (nbytes) memcpy(out, rx + 5, nbytes);

    // txcrc covers status..payload and stops there, which is why the length
    // hashed is body minus the four bytes of the CRC itself.
    uint32_t txcrc;
    memcpy(&txcrc, rx + body - 4u, 4);
    if (txcrc != ~gw_crc(0xffffffffu, rx, body - 4u))
        return GH_ERR_TXCRC;

    return GH_OK;
}

gh_err_t GW_HOT(gw_decode)(uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                           uint32_t want_rxcrc, int32_t *out, size_t nwords,
                           uint8_t *status_out)
{
    return gw_decode_n(rx, ncap, bit, cmd, want_rxcrc, out, 4u * nwords,
                       status_out);
}

// --- the M6 path, kept as the reference -----------------------------------
typedef struct {
    const uint8_t *rx;
    size_t         ncap;
    size_t         pos;
    uint32_t       crc;
    bool           crc_on;
} cur_t;

static uint8_t cur_byte(cur_t *c)
{
    uint8_t v = 0;
    for (int i = 0; i < 8; i++) {
        // Reading past the capture yields zeros rather than faulting. It can
        // only happen when the response was truncated, and the txcrc check is
        // what reports that - a bounds check here would report it as a
        // different, less informative failure.
        const uint32_t b = gwbit(c->rx, c->ncap, c->pos);
        c->pos++;
        v |= (uint8_t)(b << i);
        if (c->crc_on) c->crc = crc1(c->crc, b);
    }
    return v;
}

static uint32_t cur_word(cur_t *c)
{
    uint32_t v = cur_byte(c);
    v |= (uint32_t)cur_byte(c) << 8;
    v |= (uint32_t)cur_byte(c) << 16;
    v |= (uint32_t)cur_byte(c) << 24;
    return v;
}

// Byte-counted, for the same reason gw_decode_n() is - and the loop below is
// cur_byte() rather than cur_word() precisely so that the two agree at lengths
// that are not multiples of four. The check order still matches gw_decode_n()
// statement for statement; that is what makes the board A/B meaningful.
gh_err_t gw_decode_slow_n(const uint8_t *rx, size_t ncap, size_t bit,
                          uint8_t cmd, uint32_t want_rxcrc, void *out,
                          size_t nbytes, uint8_t *status_out)
{
    cur_t c = { .rx = rx, .ncap = ncap, .pos = bit,
                .crc = 0xffffffffu, .crc_on = true };

    const uint8_t  status = cur_byte(&c);
    const uint32_t rxcrc  = cur_word(&c);

    if (status_out) *status_out = status;

    if (!(status & GH_ST_MARK) || ((status >> 4) != (cmd & 0x0fu)))
        return GH_ERR_STATUS;
    if (rxcrc != want_rxcrc)
        return GH_ERR_RXCRC;

    uint8_t *o = (uint8_t *)out;
    for (size_t i = 0; i < nbytes; i++)
        o[i] = cur_byte(&c);

    const uint32_t calc = ~c.crc;
    c.crc_on = false;
    return (cur_word(&c) == calc) ? GH_OK : GH_ERR_TXCRC;
}

gh_err_t gw_decode_slow(const uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                        uint32_t want_rxcrc, int32_t *out, size_t nwords,
                        uint8_t *status_out)
{
    return gw_decode_slow_n(rx, ncap, bit, cmd, want_rxcrc, out, 4u * nwords,
                            status_out);
}
