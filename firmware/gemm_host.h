// MCU side of the gemm_top link. See rtl/gemm_link.v for the wire format.
//
// Split out of m6.c because M7's per-layer sequencer needs exactly this and
// nothing else from the M6 harness: the tile does not know what a layer is, so
// the only thing M7 adds above these six calls is a loop that picks P, Q and Cb.
//
// Configuration A only, matching the RTL. link_clk = sys_clk / 2, set by the
// x2 PIO program at clkdiv 1 - so the way to change the link rate is to change
// sys_clk, and the driver needs no notification when you do.

#ifndef GEMM_HOST_H
#define GEMM_HOST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Status byte, as gemm_link.v packs it: {cmd[3:0], busy, bad_frame, underrun, 1}.
#define GH_ST_MARK      0x01u   // always set; a zero here means we misread it
#define GH_ST_UNDERRUN  0x02u   // sticky: the tile ran dry during a readout
#define GH_ST_BADFRAME  0x04u   // sticky: a frame was dropped before the tile
#define GH_ST_BUSY      0x08u

// Accumulators the tile can hold, and so the longest DRAIN. Sizes the buffers.
#define GH_MAXWORDS     2048u

typedef enum {
    GH_OK = 0,
    GH_ERR_NO_PREAMBLE,   // no response in the clocks we gave it
    GH_ERR_TXCRC,         // the response was corrupted on the way back
    GH_ERR_RXCRC,         // the command was corrupted on the way out
    GH_ERR_STATUS,        // status byte does not echo the command we sent
    GH_ERR_TOOBIG,        // the transaction would not fit the buffers
    // A DMA channel did not finish. Cannot happen on a link that is merely
    // wrong - we drive the clock, so a dead FPGA still gets clocked and the
    // channels still run to their counts - so it means the *driver* armed a
    // count the PIO will never satisfy. See gh_xfer_wait().
    GH_ERR_STALL,
} gh_err_t;

// Filled in when a transaction returns GH_ERR_STALL, and only then. Every field
// is what gh_xfer_arm() was handed or derived, plus which of the three channels
// was still busy when the deadline passed - between them they say whether the
// driver's three counts disagree, and which direction.
typedef struct {
    uint8_t  cmd;
    uint8_t  width;
    uint8_t  busy;        // bit 0 rx, bit 1 tx, bit 2 tx2
    uint32_t len;         // payload wire bytes
    uint32_t nbuf, ncap;
    uint32_t head_words, tail_words, rx_words;
    uint32_t rx_left, tx_left, tx2_left;   // transfers still to go
} gh_stall_t;

// The last stall, or .cmd == 0 if there has not been one.
const gh_stall_t *gh_last_stall(void);

// One position/channel block. Mirrors the CFG payload field for field; see
// gh_cfg() for the byte order, which is the RTL's and not this struct's.
typedef struct {
    uint16_t H, W, OW;          // full input tensor, for the padding bounds
    uint16_t strip_rw;          // bytes per strip row  = W
    uint16_t strip_ch;          // bytes per strip channel = SROWS * W
    uint16_t oy0, ox0;          // output position of p = 0
    uint16_t K;                 // taps per pass = Cb * 9
    uint8_t  P, QG;
    bool     stride2, unsigned_in;

    // M14. The WGT payload for this block is nibbles, two lanes per byte. The
    // tile needs telling because it decides when a weight word is complete -
    // four incoming bytes instead of eight - and because conv0 still runs at 8
    // bits on the same bitstream, so it cannot be a build parameter.
    bool     w4;

    // M15. The tile applies the requantize epilogue and DRAIN returns one byte
    // per accumulator. Per block for two reasons: conv7 emits floats and has no
    // (M, s) to send, and the accumulator sweep that guards the MAC array has
    // to keep running in int32 on the same bitstream. Setting this without
    // having sent a matching GW_CMD_RQP first drains against whatever table the
    // strip happens to hold - see gh_rqp().
    bool     rq;
} gh_cfg_t;

// Claims a PIO state machine and two DMA channels. Call after
// fpga_release_link_pins(), once. Starts in configuration A, width 1.
void gemm_host_init(void);

// ---------------------------------------------------------------------------
// M7f. Forward width: 1 for configuration A, 3 for configuration C.
//
// Configuration C needs the PIN2 <-> PIN17 jumper fitted *and* a bitstream
// built from gemm_top_wide.v. Neither is checkable from here: with the jumper
// missing the FPGA simply never sees a clock, and with the wrong bitstream it
// sees a scrambled one. Both present as a CRC failure or a lost preamble on the
// first transaction, which is the right failure - loud, immediate, and before
// any accumulator has been believed.
//
// Runtime rather than a build switch so that one boot can measure both. Returns
// false only for a width that is not 1 or 3. Drains the pipeline, reloads the
// PIO program, moves the state machine onto the other pin set, and invalidates
// the response-position hints, which are learned in capture bits and so depend
// on the width as well as the rate.
bool     gh_set_width(unsigned w);
unsigned gh_width(void);

const char *gh_strerror(gh_err_t e);

gh_err_t gh_cfg(const gh_cfg_t *c);
gh_err_t gh_act(const uint8_t *p, size_t n);
gh_err_t gh_wgt(const int8_t *p, size_t n);

// M15. The requantize table: `n / GW_RQP_BYTES` entries, one per output channel
// of the block, in DRAIN walk order. See gw_rqp_pack() for the byte layout.
//
// It goes to the top of the tile's strip array, which is why it is a command
// and not an offset on ACT - see gemm_link.v's CMD_RQP. The table survives ACT
// and WGT and RUN, so a sequencer that walks blocks q0-major sends it once per
// (layer, q0) and not once per block, which is the whole reason this is cheap:
// 31 sends a frame against 174 blocks.
//
// `n` must be a multiple of GW_RQP_BYTES and at most
// GW_RQP_BYTES * GW_RQP_MAXQ. A short table is legal and means the block has
// fewer channels; the tile only reads the entries it drains.
gh_err_t gh_rqp(const uint8_t *p, size_t n);

// `first_pass` drives run_init, which tells the tile to overwrite its
// accumulators rather than add to them. `sweep_clocks` is how long to keep the
// clock running while it computes - link_clk is the tile's only clock, so this
// is not a timeout, it is the compute budget. Short-changing it returns
// GH_ERR_NO_PREAMBLE with the tile stranded mid-sweep.
gh_err_t gh_run(bool first_pass, uint32_t sweep_clocks);

// Ping. Reports the sticky faults and then clears them, so a caller that wants
// to know must read `status_out` from this call and not the next.
gh_err_t gh_nop(uint8_t *status_out);

// M11. Two PWM duties for D1, red then green, 0 = off and 255 = full. The
// fabric holds them until the next call and slews toward them over ~260 ms, so
// this is a colour to move to and not a colour to display: calling it once per
// frame produces a glide, and never calling it leaves D1 in its bring-up
// meanings (green heartbeat, blue link-seen, red sticky fault) for good. What a
// duty *means* is decided by the caller - see led_map() in m9.c.
gh_err_t gh_led(uint8_t r, uint8_t g);

// Send a deliberately over-long LED frame, so the fabric raises bad_frame and
// D1 shows its fault display. The only on-demand route to that display; clear it
// with gh_nop().
//
// EXPECT GH_OK FROM THIS CALL. Like gh_led() it has no return payload, so its
// response is deferred (see `defer` in gemm_host.c) and GH_OK means "queued",
// not "acknowledged". The fabric drops the frame and answers nothing, and that
// silence is reported by the *next* call - so the evidence the drop happened is
// gh_nop() returning GH_ERR_NOPREAMBLE with GH_ST_BADFRAME set, not this.
gh_err_t gh_led_badlen(void);

gh_err_t gh_drain(int32_t *out, size_t nwords, uint8_t *status_out);

// M15. The same DRAIN, counted in payload bytes rather than accumulators, and
// writing them to `out` unchanged. With cfg_rq set the tile returns one code
// byte per accumulator instead of a 32-bit word, so `nbytes` is the accumulator
// count and `out` is a uint8_t buffer; with it clear the two forms are the same
// transaction and gh_drain() is 4*nwords of this one.
//
// It is a separate entry point and not a mode flag because the *caller* has to
// know which it asked for anyway - the buffer type differs - and because m6.c's
// accumulator sweep must keep reaching the int32 form on a bitstream that can do
// both. Nothing here checks that cfg_rq agrees with the call: a mismatch is a
// length mismatch and comes back as GH_ERR_NO_PREAMBLE or GH_ERR_TXCRC.
gh_err_t gh_drain_b(void *out, size_t nbytes, uint8_t *status_out);

// ---------------------------------------------------------------------------
// M7f. Decoding a DRAIN somewhere else.
//
// M7e put the build and the requantize scatter on core 1 and left 369 ms on
// core 0, of which **197 ms is one DRAIN's decode plus its locate**, per frame.
// It is exposed for a reason gh_frame() states in one line: a response can be
// left in the pipeline only when nobody reads it, and DRAIN is the sole command
// whose caller reads a payload. Every other decode in a frame is already free.
//
// So DRAIN cannot be *deferred* into the next transaction's window - but it can
// be handed to another core, because a decode is a pure function of the capture
// buffer. gh_drain_defer() clocks the transaction and returns without looking at
// the response; gh_decode_defer() is the looking, and touches no driver state
// beyond the DRAIN hint and gh_dprof_t.
//
// **The capture buffer is the caller's, and that is the whole design.** rxb[] is
// two deep and alternates every transaction, so a decode still running two
// transactions later would be reading a buffer the DMA had started to overwrite.
// Rather than grow rxb[] to some depth that happens to be enough, a deferred
// DRAIN captures somewhere the driver does not own and the caller can keep alive
// for exactly as long as it needs to - which for m7.c is the same double-buffer
// discipline, and the same wait, that already protects got[].
//
// Three rules, all the caller's to keep:
//
// 1. `cap` must stay untouched until gh_decode_defer() returns. The decode
//    realigns in place.
// 2. Exactly one core calls gh_decode_defer(), and for the whole frame - it owns
//    the DRAIN hint slot. Mixing it with plain gh_drain() in one frame races that
//    slot for a few hundred bit-times of scan, not for correctness of the data,
//    but it is still a race and there is no reason to have one.
// 3. Call it once per armed descriptor, before reusing `cap`.
// ---------------------------------------------------------------------------

// Capture bytes a DRAIN of `nwords` accumulators needs: 5 command header,
// 13 fixed response bytes (4 preamble, 1 status, 4 rxcrc, 4 txcrc), the payload,
// and 32 bytes of slack for the return path's flight time - rounded up to the
// DMA's word, which is why a legal transaction can never overrun it.
//
// M15 made the payload byte count the parameter, since a drained accumulator is
// four bytes or one depending on cfg_rq. The word form is kept because every
// caller that predates rq counts accumulators, and because a buffer sized for
// the int32 readout is trivially large enough for the byte one - which is what
// lets frame.c keep a single capture buffer for both modes.
#define GH_DRAIN_CAP_B(nbytes) \
    ((5u + 13u + (size_t)(nbytes) + 32u + 3u) & ~(size_t)3u)
#define GH_DRAIN_CAP(nwords)  GH_DRAIN_CAP_B(4u * (size_t)(nwords))

// Everything gh_decode_defer() needs, and nothing that is also driver state.
// Transparent because the caller allocates it, usually one per capture buffer.
typedef struct {
    uint8_t *cap;          // set by the caller; the rest is the driver's
    // void *, and byte-counted, since M15: at cfg_rq the payload is codes and
    // the caller's buffer is uint8_t.
    void    *out;
    size_t   n, ref, nbytes;
    uint32_t want_rxcrc;
    uint8_t  cmd;
    bool     armed;
} gh_defer_t;

// Clocks a DRAIN into `d->cap`, which must be GH_DRAIN_CAP(nwords) bytes, and
// arms `d`. Does not decode, does not write `out`, and reports only failures
// that happen before the response is looked at.
//
// There is no status_out: the status byte at the head of a DRAIN response
// predates the readout it heads and so cannot carry the underrun flag anyway -
// the flag is sticky and the caller reads it back with the NOP it already sends.
// gh_decode_defer() still *checks* the byte, and a wrong one is GH_ERR_STATUS.
gh_err_t gh_drain_defer(int32_t *out, size_t nwords, uint8_t *cap,
                        size_t capbytes, gh_defer_t *d);

// M15's byte form, the same relationship gh_drain_b() has to gh_drain(). `cap`
// must be GH_DRAIN_CAP_B(nbytes) bytes.
gh_err_t gh_drain_defer_b(void *out, size_t nbytes, uint8_t *cap,
                          size_t capbytes, gh_defer_t *d);

// Locate, realign, check both CRCs, copy out. Safe on a core that is not driving
// the link: it reads `d->cap`, writes `d->out`, and touches no PIO, DMA or stdio.
// Disarms `d`. Returns GH_OK on an unarmed descriptor, so a retry is harmless.
gh_err_t gh_decode_defer(gh_defer_t *d);

// What the deferred decodes cost, accumulated by whichever core ran them. Kept
// out of gh_prof_t because two cores incrementing one counter lose increments,
// and because "what did core 0 pay" is the question the ladder is asking.
typedef struct {
    uint32_t us_locate, us_decode, rx_body, calls, hint_hit, hint_miss;
} gh_dprof_t;

void gh_dprof(gh_dprof_t *out);
void gh_dprof_reset(void);

// Bytes clocked since the last reset. One counter, not two: the link is full
// duplex and every transaction clocks the same number of bytes each way, so the
// forward count *is* the transaction size. Divide by the elapsed time to get
// the byte rate the silicon actually sustained, which is the number M6c is for.
void     gh_bytes_reset(void);
uint32_t gh_bytes(void);

// ---------------------------------------------------------------------------
// M7a. The decode path exists twice.
//
// M6 measured 0.92 MB/s against a link that M2 measured at 8.94 MB/s: the wire
// is idle 90% of the time and the MCU is the bottleneck. Fixing that means
// replacing the decode, and the honest way to report a replacement on this
// board is not to quote a ratio across two builds - M5b's bring-up entry is
// explicit that ratios quoted across builds are not measurements. So both
// paths ship in one binary and the caller picks at runtime, exactly as the
// SMLAD kernel is still a runtime bool rather than a second build.
// ---------------------------------------------------------------------------

// false = the M6 decode, bit at a time from the start of the capture.
// true  = predicted response offset, word-at-a-time realign, table CRC.
// Defaults to true.
void gh_set_fast(bool on);

// ---------------------------------------------------------------------------
// M7d. The CPU and the wire, at the same time.
//
// M7c measured 1,246 ms of CPU against 918 ms of wire and found them fully
// serialized: gh_xfer() armed both DMA channels and then span on a flag for the
// whole transfer, while the DMA - not the CPU - clocked the tile. So the wire is
// a window the CPU can work in, and M7d's whole thesis is putting things in it.
//
// Two things go there. The driver's own deferred decode, which is automatic; and
// whatever the caller registers with gh_overlap(), which for m7.c is building
// the *next* pass's strip and weight stream.
//
// Same A/B discipline as gh_set_fast(): both paths ship in one binary, because
// this board's ratios are only honest inside a single boot. In serial mode the
// overlap callback still runs, and in the same order - just after the wait
// instead of during it - so the two modes compute identical results.
// ---------------------------------------------------------------------------

// Defaults to true.
void gh_set_pipelined(bool on);

// Register a one-shot job to run inside the next transaction's DMA window. It
// is cleared as it fires. It must not call into this driver, and it must not
// write the payload buffer whose transaction is currently in flight.
void gh_overlap(void (*fn)(void *), void *arg);

// Retire any deferred response. gh_drain() and gh_nop() do this for you, since
// their results would otherwise be read before they landed - so the only caller
// that needs it is one shutting down or about to change the clock.
//
// Returns the first error deferred since the last call. A deferred failure is
// reported at most one transaction late; it is never lost.
gh_err_t gh_sync(void);

// Which DMA sniffer configuration reproduced gw_crc(), or false if none did and
// the outbound CRC is still being computed in software. Reported rather than
// asserted: the software path is correct, only slower.
bool gh_crc_sniffer(uint32_t *mode, bool *rev, bool *inv);

// Per-phase costs, in microseconds, accumulated since gh_prof_reset(). The
// timer's resolution is 1 us and some phases are shorter than that per
// transaction, so a phase reported as 0 means "under ~30 us across the whole
// block", not "free".
typedef struct {
    uint32_t us_wire;      // arm to done, so anything overlapped is inside it
    uint32_t us_stage;     // header, payload memcpy, and clearing the dirty tail
    uint32_t us_overlap;   // the caller's work, run inside the DMA window
    uint32_t us_locate;    // finding the response in the capture
    uint32_t us_crc;       // the outbound CRC only; the inbound one is in us_decode
    uint32_t us_decode;    // realign, inbound CRC, payload copy
    uint32_t tx_hashed;    // bytes fed to the outbound CRC
    uint32_t rx_body;      // response bytes realigned and hashed
    uint32_t xfers;
    uint32_t hint_hit;     // fast path only
    uint32_t hint_miss;    // a rescan; expect one per command class per rate
    // M7g. The same two, per command, indexed by GW_CMD_*. A miss is a full
    // rescan of the capture, so this is where locate's milliseconds come from -
    // and M7f left 390 ms of them unexplained by the reference bug alone. A
    // total cannot say which command is thrashing; these can, without a run.
    uint32_t hint_hit_cmd[8];
    uint32_t hint_miss_cmd[8];
    // M7f. us_wire and the link clocks it bought, split by command, indexed by
    // GW_CMD_*. The whole configuration-C question turns on this: the first
    // hardware run moved exactly the bytes the model projected and still spent
    // 869 ms of the 918 it was meant to save 300 of, which is either a slower
    // link clock or a fixed per-transaction cost. RUN and DRAIN send the same
    // number of clocks at either width, so comparing *their* ns/clock across
    // the two configurations answers it with no arithmetic in between.
    uint32_t us_cmd[8];
    uint32_t clk_cmd[8];   // capture bytes * 8
    uint32_t n_cmd[8];
} gh_prof_t;

void gh_prof_reset(void);
void gh_prof(gh_prof_t *out);

// Forget the learned response offsets. They are in bit-times, but the pad
// flight and synchroniser latency that make them up are in nanoseconds, so they
// move when the link rate does. **Call this after every set_sys_clock_khz().**
// Skipping it is not a correctness bug - a stale offset fails its preamble
// check and falls back to a full scan - but it silently gives back the speed.
void gh_rate_changed(void);

#endif
