// The half of the gemm_top link that is pure buffer arithmetic: framing, CRC,
// finding the response in the capture, and decoding it. No PIO, no DMA, no Pico
// headers - gemm_host.c keeps all of those.
//
// Split out for the same reason gemm_block.c was: **a strap is expensive.**
// Reflashing this board needs a physical PRG-GND jumper, so any bug that can be
// caught on the laptop should be. Everything here is a function of a byte
// buffer and nothing else, so firmware/test_gemm_wire.c can fabricate captures
// and check the fast path against the slow one at every bit offset, before the
// board is touched at all.
//
// Bit order, restated because this file is where it is finally cashed in. PIO
// shifts right, so byte i of the capture is the i'th byte on the wire and bit j
// of that byte is the j'th bit, LSB first. A reflected CRC-32 consumes bits in
// exactly that order, which is why the bit-at-a-time reference and the
// byte-at-a-time table agree, and why realigning the buffer turns the whole
// decode into memcpy and plain loads.

#ifndef GEMM_WIRE_H
#define GEMM_WIRE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// For gh_err_t, gh_cfg_t and the GH_ST_* status bits. That header is already
// free of Pico headers, so including it costs this file nothing.
#include "gemm_host.h"

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "gemm_wire assumes a little-endian host: the wire is LSB-first and the \
decode reads multi-byte fields with memcpy."
#endif

#define GW_PREAMBLE   0x3c5ac3a5u
#define GW_CRCPOLY    0xedb88320u

#define GW_HDR_BYTES  5u    // 0xa5 0x5a cmd len_lo len_hi
#define GW_CFG_BYTES  20u

// ---------------------------------------------------------------------------
// M7f. Forward width: 1 (configuration A) or 3 (configuration C, the jumper).
// The return line is one bit in both, so nothing below the "Finding the
// response" heading is a function of it - captures are one bit per link clock
// either way. Only the transmit buffer changes shape.
//
// link.pio's autopull threshold is 24 at width 3, because 3 does not divide 32
// and a mid-word refill would slide the bit alignment every OSR reload. So each
// 32-bit TX word carries three wire bytes and its top byte is never sent.
//
// That makes the header length matter. It is five, which is not a multiple of
// three, so a payload copied in behind it would start two slots into a word -
// and a phase-shifted byte-at-a-time scatter is the one thing this path cannot
// afford: gw_stage() moves about 4 MB a frame, and at four cycles a byte that
// would put back half of what the jumper buys.
//
// One 0x00 lead byte fixes it. Six is a multiple of three, so the payload
// starts on a word boundary and packs three-source-words-to-four with no phase
// at all. On the wire it costs one byte per transaction and means nothing: the
// receiver sits in R_HUNT until it matches SYNC, so a zero before 0xa5 0x5a is
// indistinguishable from the idle zeros already there. No RTL change.
#define GW_LEAD(w)      ((w) == 1u ? 0u : 1u)
#define GW_WIRE_HDR(w)  (GW_LEAD(w) + GW_HDR_BYTES)

// Buffer bytes needed to carry `n` wire bytes at width `w`.
#define GW_BUFB(w, n)   ((w) == 1u ? (size_t)(n) : (((size_t)(n) + 2u) / 3u) * 4u)

// Response bytes, counted from the first bit after the preamble: 1 status +
// 4 rxcrc + payload + 4 txcrc.
//
// M15 made the payload's unit a byte rather than a word. gemm_link.v's T_DATA
// sends one byte per accumulator when cfg_rq is set and four when it is not, so
// the byte form is the general one and GW_BODY is the special case. Both are
// kept because every recorded framing measurement since M6c is in words, and
// test_gemm_plan.c's anchors are those measurements.
#define GW_BODY_B(nbytes) (9u + (size_t)(nbytes))
#define GW_BODY(nwords)   GW_BODY_B(4u * (size_t)(nwords))

// Commands, as gemm_link.v decodes them.
#define GW_CMD_CFG    0x01u
#define GW_CMD_ACT    0x02u
#define GW_CMD_WGT    0x03u
#define GW_CMD_RUN    0x04u
#define GW_CMD_DRAIN  0x05u
#define GW_CMD_NOP    0x06u
#define GW_CMD_LED    0x07u   // M11: two duty bytes for D1, red then green
#define GW_CMD_RQP    0x08u   // M15: requantize params, 6 B per output channel

// M15. One entry per output channel of the block, in DRAIN walk order - the
// order gb_golden() emits, gg outer and j inner - so the tile can index the
// table by the channel it is draining and never needs to know q0.
//
// The layout is the tile's, not the host's: gemm_tile.v assembles the 48 bits
// by shifting bytes in from the top, so this is LSB first.
//
//     byte 0..2   bias[23:0]
//     byte 3..4   M[15:0]
//     byte 5      {s[5:0], M[17:16]}
//
// 32 is the cap because that is the widest code-emitting block gemm_plan builds
// (conv7's Q = 128 is the float layer and keeps the int32 readout), and because
// the tile parks the table in the strip's top 256 bytes.
#define GW_RQP_BYTES  6u
#define GW_RQP_MAXQ   32u

// "No preamble anywhere in the capture."
#define GW_NOPOS      ((size_t)-1)

// ---------------------------------------------------------------------------
// CRC-32, reflected, poly 0xedb88320, seed ~0, final XOR ~0 - the same one
// gemm_link.v's rxcrc/txcrc implement.
// ---------------------------------------------------------------------------

// Builds the 256-entry table. Idempotent. gw_crc() calls it if you forget, so
// the only reason to call it explicitly is to keep the cost out of a timed
// region.
void gw_crc_init(void);

// Table-driven. The table is a mutable static, so on the Pico it lives in SRAM
// and the hot loop never fetches it over flash XIP.
uint32_t gw_crc(uint32_t c, const uint8_t *p, size_t n);

// Bit-at-a-time reference. Kept because it is what M6 shipped and passed with,
// so it is the thing the table has to be proved equal to.
uint32_t gw_crc_slow(uint32_t c, const uint8_t *p, size_t n);

// ---------------------------------------------------------------------------
// Building a command.
// ---------------------------------------------------------------------------

// Writes GW_HDR_BYTES of header. Always the five logical bytes, never the lead
// byte and never packed - this is the header as gemm_link.v sees it, and
// gw_stage() is what puts it on the wire.
void gw_hdr(uint8_t *tx, uint8_t cmd, size_t len);

// Packs `len` wire bytes from `src` into `dst`, three per 32-bit word, low slot
// first. `dst` must be 4-aligned; `src` need not be aligned at all.
//
// Writes exactly GW_BUFB(3, len) bytes and leaves none of them undefined: the
// wire bytes in slots 0-2, zero in every slot past the last one, and zero in
// slot 3 of each word (the byte the PIO discards). gw_stage()'s dirty mark
// starts clearing the idle tail at that same offset, so "written" has to mean
// all of it. Exposed for test_gemm_wire.c to check against a byte-at-a-time
// model at every length and every source alignment.
void gw_pack3(uint8_t *dst, const uint8_t *src, size_t len);

// Stages a whole transaction into `tx`: header, payload, and an idle tail of
// zeros. Returns the new dirty mark, which the caller keeps and passes back next
// time.
//
// `nbuf` and the dirty mark are in **buffer** bytes - what DMA will read - not
// wire bytes. At width 1 the two are the same. At width 3 the caller gets from
// one to the other with GW_BUFB(); doing it here would mean this function
// deciding the transaction length, which is gh_frame()'s job.
//
// **The tail is not memset.** M7c measured the naive version at 4.35 MB of
// zero-writing a frame, 2.778 MB of it RUN's sweep budget - bytes that are
// already zero, stay zero, and reach the tile as clock rather than as data. So
// `dirty` tracks how far past the header `tx` has ever been written, and only
// that much is cleared. `tx` must start out all zeros.
//
// It matters that this is right rather than nearly right: a stray non-zero byte
// in an idle tail is not noise, it is two bytes away from being a frame marker.
// Which is why it lives here, where test_gemm_wire.c can check it against a
// model that does memset everything, instead of in gemm_host.c where only a
// strap could.
size_t gw_stage(uint8_t *tx, uint8_t cmd, const void *pay, size_t len,
                size_t nbuf, size_t dirty, unsigned width);

// The CFG payload, in gemm_link.v's byte order rather than the struct's. cfg_sr
// shifts right by 8 per payload byte, so byte i ends up at bit 8*i, which is
// what the field offsets in R_EXEC index. Getting this wrong scrambles the
// geometry into something that still runs and returns a plausible wrong tensor.
void gw_cfg_pack(uint8_t p[GW_CFG_BYTES], const gh_cfg_t *g);

// M15. One RQP entry, at `p`. `m` is the 18-bit multiplier and `s` the shift,
// as fgx_rq_pick() returns them; `bias` is the layer's bias for this channel.
//
// The narrowing is checked rather than assumed, because all three fields are
// wider in C than on the wire and a silent truncation here is a wrong tensor
// that still passes CRC. Returns false and writes nothing if any field is out
// of range; the caller treats that as a model that cannot be run in rq mode.
bool gw_rqp_pack(uint8_t p[GW_RQP_BYTES], int32_t bias, int32_t m, int s);

// ---------------------------------------------------------------------------
// Finding the response.
//
// The old driver hunted the preamble bit by bit from the start of every
// capture, which on one conv2 block is ~418,000 iterations and most of the
// elapsed time. It never needed to: **we drive the clock**, so nothing about
// the return path is asynchronous and the response lands at a position that is
// a fixed offset from something we already know.
//
// What that "something" is differs by command, which is why the offset is
// learned rather than derived. For CFG/ACT/WGT/DRAIN/NOP gemm_link starts the
// preamble in R_EXEC, a fixed number of clocks after the last payload bit, so
// 8*(GW_HDR_BYTES + len) is the right reference. RUN is not: it branches to
// R_WAIT and holds the preamble until `busy` has risen and fallen again
// (rtl/gemm_link.v:487-521), so its position carries the sweep as well, and the
// reference has to include the sweep budget. RUN is 8 of the 28 transactions in
// a block and 39% of the bits, so it could not simply be left on the slow path.
//
// gw_locate() takes whatever reference the caller considers predictable and
// learns the signed remainder once. Everything not in the reference - pad
// flight, the PIO input synchroniser's two flops, R_CHK, and for RUN the gap
// between the budget and the tile's actual sweep - ends up in the delta. That
// keeps this file ignorant of both the RTL's pipeline depth and the caller's
// sweep formula.
// ---------------------------------------------------------------------------

typedef struct {
    bool      valid;
    ptrdiff_t delta;    // signed: RUN's response arrives *before* its idle ends
    uint32_t  hits;
    uint32_t  misses;
} gw_hint_t;

// Full scan, the M6 behaviour. Returns the bit index just past the preamble.
size_t gw_scan(const uint8_t *rx, size_t ncap);

// The bit-at-a-time scan gw_scan() replaced in M7g. Kept compiled in as the
// oracle the word loop is checked against on the host - same arrangement as
// gw_decode_slow(). The two must agree on every capture, including on *which*
// false lock they find in noise, because gw_locate()'s contract is to return
// whatever the scan returns rather than to be cleverer than it.
size_t gw_scan_slow(const uint8_t *rx, size_t ncap);

// Predict, verify, and fall back. Returns the same value gw_scan() would, and
// updates *h. A wrong delta - from a false lock, a rate change, or a caller
// that changed its reference - costs one failed 32-bit compare and a rescan,
// and re-latches. So this can be slow but it cannot be wrong, which is the
// property worth having: a bad bit boundary returns a plausible wrong tensor
// rather than an error.
size_t gw_locate(const uint8_t *rx, size_t ncap, size_t ref, gw_hint_t *h);

// Invalidates the learned offset. The delta is in bit-times but pad flight is
// in nanoseconds, so it moves when the link rate does.
static inline void gw_hint_reset(gw_hint_t *h) { h->valid = false; }

// ---------------------------------------------------------------------------
// Decoding.
// ---------------------------------------------------------------------------

// Shifts `nbytes` bytes starting at bit `bit` down to rx[0..nbytes), in place.
// Safe in place because `bit` is at least 32 - the preamble precedes it - so
// the write index always trails the read index. Bits past the capture read as
// zero, which is what makes a truncated response present as a CRC failure
// rather than a fault.
void gw_align(uint8_t *rx, size_t ncap, size_t bit, size_t nbytes);

// Realign, then read the response with plain loads. `bit` is gw_locate()'s
// return. `out` may be NULL when nwords is 0. Clobbers the head of rx.
gh_err_t gw_decode(uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                   uint32_t want_rxcrc, int32_t *out, size_t nwords,
                   uint8_t *status_out);

// The same thing counted in payload bytes, which is what M15's DRAIN needs:
// at cfg_rq the tile returns one byte per accumulator, so the body is no
// longer a whole number of words. gw_decode() is a wrapper on this.
gh_err_t gw_decode_n(uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                     uint32_t want_rxcrc, void *out, size_t nbytes,
                     uint8_t *status_out);

// The M6 decode: a bit cursor, one bit at a time, no realignment. This is the
// reference gw_decode() is checked against on the host and A/B'd against on the
// board, so it stays compiled in rather than being deleted.
gh_err_t gw_decode_slow(const uint8_t *rx, size_t ncap, size_t bit, uint8_t cmd,
                        uint32_t want_rxcrc, int32_t *out, size_t nwords,
                        uint8_t *status_out);

gh_err_t gw_decode_slow_n(const uint8_t *rx, size_t ncap, size_t bit,
                          uint8_t cmd, uint32_t want_rxcrc, void *out,
                          size_t nbytes, uint8_t *status_out);

#endif
