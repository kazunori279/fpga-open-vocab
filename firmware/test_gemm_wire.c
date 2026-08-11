// Host test for gemm_wire.c. No Pico, no board, no strap.
//
// The point is narrow and worth stating: **the fast decode has to be provably
// identical to the one M6 shipped**, not merely plausible. A realignment that
// is one bit out, or a CRC table that disagrees with the bit loop on a single
// input, presents on hardware as "0 of 2048 accumulators match" - which
// localises nothing, and costs a PRG-GND strap to iterate on. So every claim
// gw_decode() makes is checked here against gw_decode_slow(), at every bit
// offset, including on which gh_err_t comes back.
//
//   cc -O2 -Wall -Wextra -o /tmp/test_gemm_wire \
//      firmware/test_gemm_wire.c firmware/gemm_wire.c
//   /tmp/test_gemm_wire

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "gemm_wire.h"

// ---------------------------------------------------------------------------
// A capture that ends at a guard page.
//
// M7g made gw_scan() and gw_check() read eight bytes to serve eight bit
// alignments at once, and a wrong bound there reads past the capture *and
// still returns the right answer* - the slack in an oversized buffer is zeros,
// which is exactly what a short capture is defined to look like. So the bug is
// invisible to any test that compares return values, which is every test in
// this file.
//
// -fsanitize=address would be the usual answer and it deadlocks in its own
// initialiser on this machine's macOS. A guard page is smaller anyway: put the
// last byte of the capture flush against a PROT_NONE page and an over-read is
// a SIGSEGV rather than an opinion.
static void *cap_map;
static size_t cap_pg;

static uint8_t *cap_alloc(size_t n)
{
    if (!cap_pg) cap_pg = (size_t)sysconf(_SC_PAGESIZE);
    const size_t body = ((n + cap_pg - 1u) / cap_pg + 1u) * cap_pg;
    cap_map = mmap(NULL, body + cap_pg, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (cap_map == MAP_FAILED) { perror("mmap"); exit(2); }
    mprotect((char *)cap_map + body, cap_pg, PROT_NONE);
    return (uint8_t *)cap_map + body - n;   // last byte abuts the guard
}

static void cap_free(size_t n)
{
    const size_t body = ((n + cap_pg - 1u) / cap_pg + 1u) * cap_pg;
    munmap(cap_map, body + cap_pg);
}

static int fails;

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        if (!(cond)) {                                                        \
            printf("  FAIL %s:%d  ", __func__, __LINE__);                     \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
            if (++fails > 20) { printf("  (too many)\n"); return; }           \
        }                                                                     \
    } while (0)

// Reproducible, so a failure is a failure again next run. xorshift32.
static uint32_t rng_s = 0x1234567u;
static uint32_t rnd(void)
{
    rng_s ^= rng_s << 13;
    rng_s ^= rng_s >> 17;
    rng_s ^= rng_s << 5;
    return rng_s;
}

// ---------------------------------------------------------------------------
// Building a capture the way gemm_link.v drives one.
// ---------------------------------------------------------------------------
#define CAP 4096u

static void put_bits(uint8_t *buf, size_t bit, uint32_t v, int n)
{
    for (int k = 0; k < n; k++, bit++) {
        const uint8_t m = (uint8_t)(1u << (bit & 7u));
        if ((v >> k) & 1u) buf[bit >> 3] |= m;
        else               buf[bit >> 3] &= (uint8_t)~m;
    }
}

// [status][rxcrc:4][payload:4n][txcrc:4]. Returns the byte length.
static size_t make_body(uint8_t *body, uint8_t status, uint32_t rxcrc,
                        const int32_t *pay, size_t nwords)
{
    body[0] = status;
    memcpy(body + 1, &rxcrc, 4);
    if (nwords) memcpy(body + 5, pay, 4u * nwords);
    const uint32_t tx = ~gw_crc(0xffffffffu, body, 5u + 4u * nwords);
    memcpy(body + 5u + 4u * nwords, &tx, 4);
    return 9u + 4u * nwords;
}

// Lays the preamble and the body into `cap` starting at bit `start`. Everything
// outside is left as the caller set it - zeros model an idle link_miso.
static void lay(uint8_t *cap, size_t start, const uint8_t *body, size_t nbody)
{
    put_bits(cap, start, GW_PREAMBLE, 32);
    for (size_t i = 0; i < nbody; i++)
        put_bits(cap, start + 32u + 8u * i, body[i], 8);
}

// ---------------------------------------------------------------------------
static void test_crc(void)
{
    uint8_t buf[600];
    for (size_t n = 0; n <= 600u; n += (n < 40u ? 1u : 37u)) {
        for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)rnd();
        const uint32_t a = gw_crc(0xffffffffu, buf, n);
        const uint32_t b = gw_crc_slow(0xffffffffu, buf, n);
        CHECK(a == b, "n=%zu table=%08x bitwise=%08x", n, a, b);
    }

    // A fixed vector, so a future edit to the table generator cannot quietly
    // agree with a matching edit to the bit loop.
    const uint8_t nine[9] = "123456789";
    const uint32_t v = ~gw_crc(0xffffffffu, nine, 9);
    CHECK(v == 0xcbf43926u, "CRC-32/ISO-HDLC of \"123456789\" = %08x", v);
}

// ---------------------------------------------------------------------------
static void test_align(void)
{
    static uint8_t src[CAP], got[CAP];

    for (size_t i = 0; i < CAP; i++) src[i] = (uint8_t)rnd();

    // Every sub-byte phase, and a few whole-byte offsets, against a
    // bit-at-a-time extraction of the same window.
    for (size_t bit = 32u; bit < 32u + 64u; bit++) {
        for (size_t nb = 1u; nb <= 64u; nb += 7u) {
            memcpy(got, src, CAP);
            gw_align(got, CAP, bit, nb);
            for (size_t j = 0; j < nb; j++) {
                uint8_t want = 0;
                for (int k = 0; k < 8; k++) {
                    const size_t p = bit + 8u * j + (size_t)k;
                    want |= (uint8_t)(((src[p >> 3] >> (p & 7u)) & 1u) << k);
                }
                CHECK(got[j] == want, "bit=%zu nb=%zu j=%zu got=%02x want=%02x",
                      bit, nb, j, got[j], want);
            }
        }
    }

    // Long enough to exercise the word loop, and a truncated capture, where
    // everything past ncap must read as zero.
    for (unsigned s = 0; s < 8u; s++) {
        const size_t bit = 40u + s, ncap = 300u, nb = 512u;
        memcpy(got, src, CAP);
        gw_align(got, ncap, bit, nb);
        for (size_t j = 0; j < nb; j++) {
            uint8_t want = 0;
            for (int k = 0; k < 8; k++) {
                const size_t p = bit + 8u * j + (size_t)k;
                if (p < ncap * 8u)
                    want |= (uint8_t)(((src[p >> 3] >> (p & 7u)) & 1u) << k);
            }
            CHECK(got[j] == want, "trunc s=%u j=%zu got=%02x want=%02x",
                  s, j, got[j], want);
        }
    }
}

// ---------------------------------------------------------------------------
// M7g replaced gw_scan()'s bit accumulator with a word loop. Same argument as
// gw_decode() vs gw_decode_slow(): the two must agree on *every* capture, and
// agreeing on the clean ones is the easy half. What matters is noise, where the
// scan locks onto whatever 32 bits happen to look like the preamble first - a
// word loop that visits candidate positions in a different order would find a
// different false lock, be just as "valid", and quietly move the byte boundary.
static void test_scan(void)
{
    static uint8_t cap[CAP];

    // Guard-paged, at every length across the word loop's boundary, including
    // the ones too short for a 64-bit load and too short for a preamble at all.
    for (size_t ncap = 0; ncap <= 40u; ncap++) {
        uint8_t *b8 = cap_alloc(ncap);
        for (int it = 0; it < 40; it++) {
            for (size_t i = 0; i < ncap; i++) b8[i] = (uint8_t)rnd();
            CHECK(gw_scan(b8, ncap) == gw_scan_slow(b8, ncap),
                  "noise ncap=%zu it=%d fast=%zu slow=%zu", ncap, it,
                  gw_scan(b8, ncap), gw_scan_slow(b8, ncap));
        }
        cap_free(ncap);
    }

    // A preamble at *every* legal window position of a short capture. This is
    // the handover: the word loop covers windows that start in the first
    // ncap-7 bytes, the bounds-checked loop covers the rest, and an off-by-one
    // between them is a position that only one of the two can find. It also
    // exercises gw_check()'s own fallback, since a hint latched here points
    // into the last eight bytes.
    for (size_t ncap = 4u; ncap <= 24u; ncap++) {
        uint8_t *c = cap_alloc(ncap);
        for (size_t b = 0; b + 32u <= ncap * 8u; b++) {
            memset(c, 0, ncap);
            put_bits(c, b, GW_PREAMBLE, 32);
            const size_t slow = gw_scan_slow(c, ncap);
            CHECK(gw_scan(c, ncap) == slow, "edge ncap=%zu b=%zu fast=%zu "
                  "slow=%zu", ncap, b, gw_scan(c, ncap), slow);

            gw_hint_t h = { 0 };
            gw_locate(c, ncap, slow, &h);             // scans, latches delta 0
            CHECK(gw_locate(c, ncap, slow, &h) == slow && h.hits == 1u,
                  "edge hit ncap=%zu b=%zu hits=%u", ncap, b, h.hits);
        }
        cap_free(ncap);
    }

    // Noise *and* a response, which is the case that actually happens: RUN's
    // capture holds the tile's chatter before the preamble.
    uint8_t body[64];
    const size_t nbody = make_body(body, 0x51, 0xa5a5a5a5u, NULL, 0);
    for (int it = 0; it < 400; it++) {
        for (size_t i = 0; i < CAP; i++) cap[i] = (uint8_t)rnd();
        lay(cap, 1500u + (size_t)(rnd() % 97u), body, nbody);
        CHECK(gw_scan(cap, CAP) == gw_scan_slow(cap, CAP),
              "noisy it=%d fast=%zu slow=%zu", it,
              gw_scan(cap, CAP), gw_scan_slow(cap, CAP));
    }
}

// ---------------------------------------------------------------------------
static void test_locate(void)
{
    static uint8_t cap[CAP];
    uint8_t body[64];
    gw_hint_t h = { 0 };

    const int32_t pay[2] = { 0x11223344, (int32_t)0xdeadbeef };
    const size_t  nbody  = make_body(body, 0x51, 0xa5a5a5a5u, pay, 2);

    // Same reference every time and a fixed offset: the first call scans and
    // latches, every one after it must hit.
    for (int it = 0; it < 20; it++) {
        memset(cap, 0, CAP);
        lay(cap, 700u, body, nbody);
        const size_t want = gw_scan(cap, CAP);
        const size_t got  = gw_locate(cap, CAP, 640u, &h);
        CHECK(got == want, "it=%d got=%zu want=%zu", it, got, want);
    }
    CHECK(h.hits == 19u && h.misses == 0u, "hits=%u misses=%u", h.hits, h.misses);

    // Move the response without moving the reference: one miss, then it
    // re-latches and hits again.
    const uint32_t miss0 = h.misses;
    for (int it = 0; it < 5; it++) {
        memset(cap, 0, CAP);
        lay(cap, 900u, body, nbody);
        const size_t want = gw_scan(cap, CAP);
        const size_t got  = gw_locate(cap, CAP, 640u, &h);
        CHECK(got == want, "moved it=%d got=%zu want=%zu", it, got, want);
    }
    CHECK(h.misses == miss0 + 1u, "one relatch expected, misses %u -> %u",
          miss0, h.misses);

    // A negative delta - the response arriving *before* the reference - is
    // exactly the RUN case, where the idle is the sweep budget and the tile
    // finishes early. It has to latch like any other.
    gw_hint_reset(&h);
    for (int it = 0; it < 5; it++) {
        memset(cap, 0, CAP);
        lay(cap, 500u, body, nbody);
        const size_t got = gw_locate(cap, CAP, 2000u, &h);
        CHECK(got == gw_scan(cap, CAP), "negative delta it=%d got=%zu", it, got);
    }
    CHECK(h.delta < 0, "delta should be negative, is %td", h.delta);

    // With noise ahead of the response, gw_scan may lock early. locate's
    // contract is to return whatever scan returns, not to be cleverer than it.
    gw_hint_reset(&h);
    for (int it = 0; it < 200; it++) {
        for (size_t i = 0; i < CAP; i++) cap[i] = (uint8_t)rnd();
        lay(cap, 1500u, body, nbody);
        const size_t want = gw_scan(cap, CAP);
        const size_t got  = gw_locate(cap, CAP, 1440u, &h);
        CHECK(got == want, "noisy it=%d got=%zu want=%zu", it, got, want);
    }

    // Nothing there at all.
    memset(cap, 0, CAP);
    gw_hint_reset(&h);
    CHECK(gw_scan(cap, CAP) == GW_NOPOS, "empty capture must not lock");
    CHECK(gw_locate(cap, CAP, 100u, &h) == GW_NOPOS, "locate on empty capture");
    CHECK(!h.valid, "a failed scan must not latch a delta");
}

// ---------------------------------------------------------------------------
// The one that matters: fast against slow, byte for byte and error for error.
// ---------------------------------------------------------------------------
static void one_decode(size_t start, uint8_t cmd, size_t nwords, size_t ncap,
                       uint8_t status, uint32_t rxcrc, uint32_t want_rxcrc,
                       int corrupt_tx, const char *what)
{
    static uint8_t capf[CAP], caps[CAP], body[CAP];
    static int32_t pay[256], out_f[256], out_s[256];

    for (size_t i = 0; i < nwords; i++) pay[i] = (int32_t)rnd();
    const size_t nbody = make_body(body, status, rxcrc, pay, nwords);
    if (corrupt_tx) body[nbody - 1u] ^= 0x40u;

    memset(caps, 0, CAP);
    lay(caps, start, body, nbody);
    memcpy(capf, caps, CAP);

    memset(out_f, 0x5a, sizeof out_f);
    memset(out_s, 0x5a, sizeof out_s);

    uint8_t st_f = 0xff, st_s = 0xff;
    const size_t bit = start + 32u;

    const gh_err_t es = gw_decode_slow(caps, ncap, bit, cmd, want_rxcrc,
                                       out_s, nwords, &st_s);
    const gh_err_t ef = gw_decode(capf, ncap, bit, cmd, want_rxcrc,
                                  out_f, nwords, &st_f);

    CHECK(ef == es, "%s start=%zu nw=%zu: fast=%d slow=%d", what, start, nwords,
          (int)ef, (int)es);
    CHECK(st_f == st_s, "%s start=%zu: status fast=%02x slow=%02x",
          what, start, st_f, st_s);
    CHECK(memcmp(out_f, out_s, sizeof out_f) == 0,
          "%s start=%zu nw=%zu: payload differs", what, start, nwords);
}

static void test_decode(void)
{
    // The good case, at every bit phase, across the payload sizes a real block
    // uses: NOP/CFG/ACT/WGT/RUN return nothing, DRAIN returns up to 2048.
    static const size_t NW[] = { 0, 1, 2, 7, 64, 255 };
    for (size_t start = 32u; start < 32u + 64u; start++)
        for (size_t k = 0; k < sizeof NW / sizeof NW[0]; k++)
            one_decode(start, GW_CMD_DRAIN, NW[k], CAP,
                       0x51, 0xa5a5a5a5u, 0xa5a5a5a5u, 0, "ok");

    // Each failure mode, and both paths must name it the same way.
    for (size_t start = 32u; start < 32u + 16u; start++) {
        // Command echo wrong -> the byte boundary is wrong.
        one_decode(start, GW_CMD_DRAIN, 8, CAP,
                   0x21, 0xa5a5a5a5u, 0xa5a5a5a5u, 0, "bad echo");
        // Mark bit clear -> we misread the status byte entirely.
        one_decode(start, GW_CMD_DRAIN, 8, CAP,
                   0x50, 0xa5a5a5a5u, 0xa5a5a5a5u, 0, "no mark");
        // The FPGA received a different command than we sent.
        one_decode(start, GW_CMD_DRAIN, 8, CAP,
                   0x51, 0x00000001u, 0xa5a5a5a5u, 0, "rxcrc");
        // The return path corrupted the response.
        one_decode(start, GW_CMD_DRAIN, 8, CAP,
                   0x51, 0xa5a5a5a5u, 0xa5a5a5a5u, 1, "txcrc");
        // Truncated: the sweep budget was too short and the tail never
        // arrived. Must be GH_ERR_TXCRC from both, not a fault from either.
        one_decode(start, GW_CMD_DRAIN, 64, 60u,
                   0x51, 0xa5a5a5a5u, 0xa5a5a5a5u, 0, "truncated");
    }

    // Every command code, so the (status >> 4) comparison is exercised for all
    // of them rather than just DRAIN's.
    static const uint8_t CMDS[] = { GW_CMD_CFG, GW_CMD_ACT, GW_CMD_WGT,
                                    GW_CMD_RUN, GW_CMD_DRAIN, GW_CMD_NOP };
    for (size_t i = 0; i < sizeof CMDS / sizeof CMDS[0]; i++)
        for (size_t start = 32u; start < 32u + 8u; start++)
            one_decode(start, CMDS[i], 0, CAP,
                       (uint8_t)((CMDS[i] << 4) | 1u), 0x12345678u,
                       0x12345678u, 0, "cmd echo");
}

// ---------------------------------------------------------------------------
// The framing bytes moved from gemm_host.c to gemm_wire.c. Checked against
// literals, because "I transcribed twenty assignments correctly" is not
// something to take on trust when getting it wrong returns a plausible wrong
// tensor instead of an error.
// ---------------------------------------------------------------------------
static void test_pack(void)
{
    uint8_t h[GW_HDR_BYTES];
    gw_hdr(h, GW_CMD_ACT, 0x0634u);
    CHECK(h[0] == 0xa5 && h[1] == 0x5a && h[2] == GW_CMD_ACT &&
          h[3] == 0x34 && h[4] == 0x06,
          "hdr = %02x %02x %02x %02x %02x", h[0], h[1], h[2], h[3], h[4]);

    gh_cfg_t g = {
        .H = 32, .W = 32, .OW = 32, .strip_rw = 32, .strip_ch = 0x0420,
        .oy0 = 0, .ox0 = 0, .K = 72, .P = 128, .QG = 2,
        .stride2 = false, .unsigned_in = true, .w4 = false,
    };
    static const uint8_t want[GW_CFG_BYTES] = {
        0x20, 0x00,  0x20, 0x00,  0x20, 0x00,  0x20, 0x00,
        0x20, 0x04,  0x00, 0x00,  0x00, 0x00,  0x48, 0x00,
        0x80,  0x02,  0x02,  0x00,
    };
    uint8_t p[GW_CFG_BYTES];
    gw_cfg_pack(p, &g);
    for (size_t i = 0; i < GW_CFG_BYTES; i++)
        CHECK(p[i] == want[i], "cfg[%zu] = %02x, want %02x", i, p[i], want[i]);

    // M14 rides in bit 2 of the same byte, so the packed length is unchanged.
    // Checked here and not only in the testbench because this is the one place
    // that pins the *bit position*: gemm_link.v reads cfg_sr[146], and 145 or
    // 147 would still produce a well-formed 20-byte CFG that the tile misreads
    // as a stride or a sign.
    g.w4 = true;
    gw_cfg_pack(p, &g);
    CHECK(p[18] == 0x06, "cfg[18] = %02x at w4, want 06", p[18]);
    for (size_t i = 0; i < GW_CFG_BYTES; i++)
        if (i != 18)
            CHECK(p[i] == want[i], "w4 moved cfg[%zu] to %02x", i, p[i]);
}

// ---------------------------------------------------------------------------
// M7f. gw_pack3() against the one-line version it exists to replace.
//
// The fast path handles twelve bytes at a time with three loads and four
// stores; the slow one is `dst[(i/3)*4 + (i%3)] = src[i]` and is obviously
// right. Every length from 0 to 200 crosses the boundary between them in every
// possible phase, and the source is walked through all four alignments because
// the fast path loads it as words and the payloads it will see - strip and
// weight buffers offset by a block index - are not all 4-aligned.
//
// The zero-fill half of the contract is not checkable against a zeroed
// destination, so the buffer starts as 0xa5 and so does the model. Everything
// below GW_BUFB(3, len) then has to become an explicit write - the wire bytes,
// the unused slots of the last group, and slot 3 - while everything above it
// has to still read 0xa5. Both directions matter: an unwritten byte below the
// mark is a stale wire byte behind the payload, and a written byte above it is
// the dirty mark lying to gw_stage().
// ---------------------------------------------------------------------------
static void test_pack3(void)
{
    static uint8_t src[280], got[400], want[400];

    for (size_t i = 0; i < sizeof src; i++) src[i] = (uint8_t)(rnd() | 1u);

    for (unsigned off = 0; off < 4u; off++)
        for (size_t len = 0; len <= 200u; len++) {
            memset(got, 0xa5, sizeof got);
            memset(want, 0xa5, sizeof want);
            memset(want, 0, GW_BUFB(3u, len));
            for (size_t i = 0; i < len; i++)
                want[(i / 3u) * 4u + (i % 3u)] = src[off + i];

            gw_pack3(got, src + off, len);

            if (memcmp(got, want, sizeof got) != 0) {
                size_t i = 0;
                while (got[i] == want[i]) i++;
                CHECK(0, "off=%u len=%zu: buf[%zu] = %02x, want %02x",
                      off, len, i, got[i], want[i]);
            }
        }
}

// ---------------------------------------------------------------------------
// gw_stage() against a model that memsets the whole tail.
//
// The two buffers must agree over [0, n) after *every* call, not just at the
// end, because every call is a transaction that goes out on the wire. A byte
// the dirty mark wrongly believes is still zero is a byte the tile sees, and in
// an idle tail it is two bytes away from being a frame marker - so this is the
// one property worth checking exhaustively before a strap is spent.
//
// M7f compares **wire** bytes rather than buffer bytes, so the model does not
// have to know how the buffer is packed and the same twenty lines cover both
// configurations. What gw_stage() owes the caller is a stream of wire bytes;
// which buffer byte each one came out of is gw_pack3()'s business and is
// checked above.
// ---------------------------------------------------------------------------
static void unwire(uint8_t *wire, const uint8_t *buf, size_t n, unsigned width)
{
    if (width == 1u) { memcpy(wire, buf, n); return; }
    for (size_t i = 0; i < n; i++) wire[i] = buf[(i / 3u) * 4u + (i % 3u)];
}

static void test_stage_w(unsigned width)
{
    static uint8_t got[CAP], gotw[CAP], want[CAP], pay[CAP];
    const size_t hdr  = GW_WIRE_HDR(width);
    const size_t gran = 4u * width;      // n must divide into whole TX words
    // Wire bytes the buffer can hold. At width 3 four buffer bytes carry three.
    const size_t nmax = ((width == 1u ? CAP : CAP / 4u * 3u) / gran) * gran;

    memset(got, 0, sizeof got);       // gw_stage()'s precondition: tx starts zero
    size_t dirty = 0;

    for (int it = 0; it < 4000; it++) {
        // Deliberately unordered lengths: the whole point of the dirty mark is
        // what a short transaction does after a long one.
        const size_t len = rnd() % (nmax - hdr - 80u);
        size_t n = hdr + len + (rnd() % 64u);
        n = (n + gran - 1u) & ~(gran - 1u);
        if (n > nmax) n = nmax;
        if (n < hdr + len) continue;
        const size_t nbuf = GW_BUFB(width, n);

        const uint8_t cmd = (uint8_t)(1u + rnd() % 6u);
        for (size_t i = 0; i < len; i++) pay[i] = (uint8_t)(rnd() | 1u);

        // The model: the lead byte if this width has one, the header, the
        // payload, and every remaining byte of the transaction explicitly
        // zeroed. This is what M7c shipped, plus GW_LEAD.
        memset(want, 0, n);
        gw_hdr(want + GW_LEAD(width), cmd, len);
        if (len) memcpy(want + hdr, pay, len);

        dirty = gw_stage(got, cmd, pay, len, nbuf, dirty, width);
        unwire(gotw, got, n, width);

        if (memcmp(gotw, want, n) != 0) {
            size_t i = 0;
            while (i < n && gotw[i] == want[i]) i++;
            CHECK(0, "w=%u it=%d len=%zu n=%zu: wire byte %zu = %02x, want %02x",
                  width, it, len, n, i, gotw[i], want[i]);
        }

        // The mark is only useful if it is tight enough to be cheap: it must
        // never sit below what was actually written, or the next short
        // transaction leaves this payload behind.
        const size_t body = GW_BUFB(width, hdr + len);
        CHECK(dirty >= body,
              "w=%u it=%d dirty=%zu below body %zu", width, it, dirty, body);
    }
}

static void test_stage(void) { test_stage_w(1u); test_stage_w(3u); }

int main(void)
{
    gw_crc_init();

    printf("crc     ... "); fflush(stdout); test_crc();    printf("done\n");
    printf("align   ... "); fflush(stdout); test_align();  printf("done\n");
    printf("scan    ... "); fflush(stdout); test_scan();   printf("done\n");
    printf("locate  ... "); fflush(stdout); test_locate(); printf("done\n");
    printf("decode  ... "); fflush(stdout); test_decode(); printf("done\n");
    printf("pack    ... "); fflush(stdout); test_pack();   printf("done\n");
    printf("pack3   ... "); fflush(stdout); test_pack3();  printf("done\n");
    printf("stage   ... "); fflush(stdout); test_stage();  printf("done\n");

    printf("\n%s\n", fails ? "FAIL" : "PASS");
    return fails ? 1 : 0;
}
