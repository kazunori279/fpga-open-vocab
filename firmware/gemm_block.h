// M6/M7: what one gemm_top block looks like, in host memory.
//
// The tile does not know what a convolution is. It is handed a strip of
// activations, a stream of weights and eleven numbers, and everything that
// turns a layer into those three things lives here - which is exactly the part
// M7's per-layer sequencer needs and the part gemm_host.c deliberately does
// not do.
//
// **This file is what the simulation was checked against.** The layout used to
// live inside gen_gemm_vec.c, which writes the vectors rtl/im2col_feed.v is fed
// in tb_gemm and tb_gemm_link; both PASS bit-exact. Transcribing it a second
// time into m6.c would have put an unverified copy on the hardware path, and a
// strip bug there presents as "0 of 2048 accumulators match" - which localises
// nothing. So gen_gemm_vec.c now links this file instead of carrying its own
// copy, the regenerated vectors were byte-identical, and both testbenches still
// PASS. That makes their PASS evidence about the code the MCU runs.
//
// Free of Pico headers for the same reason encoder.c is: it has to compile and
// run on the host.

#ifndef GEMM_BLOCK_H
#define GEMM_BLOCK_H

#include <stddef.h>
#include <stdint.h>

#include "encoder.h"

// Lanes. Must match gemm_tile's NMAC parameter and gen_gemm_vec's.
#define GB_NMAC 8

// What goes in strip rows that fall outside the image. A correct tile never
// reads them - im2col_feed asserts `zero` and gemm_tile substitutes a literal
// zero - so the value is a don't-care, and that is precisely why it must not be
// zero: filling with zero makes a stray read of a pad row return the right
// answer and hides every padding bug. See gen_gemm_vec.c, which measured that.
#define GB_STRIP_POISON 0xa5

// Tile capacities, from gemm_tile.v's parameters and gemm_link.v's length
// policing. Checked by gb_geom() rather than assumed, because every one of them
// fails as a silently wrong tensor rather than as an error.
#define GB_ADEPTH 256    // P*QG accumulator entries
#define GB_WDEPTH 256    // K*QG weight words
#define GB_STRIPD 2048   // strip bytes
#define GB_ACTMAX 2048   // ACT payload ceiling
#define GB_WGTMAX 2048   // WGT payload ceiling

// M15. Where gemm_tile.v parks the requantize table inside the strip array, and
// therefore the strip ceiling for a block that runs in rq mode. It is 256 below
// GB_STRIPD and not 192 because the tile picked a power-of-two boundary to save
// the link an adder - see the RQBASE note in gemm_tile.v.
//
// The largest strip gemm_plan builds is conv2's 1,536 bytes, so nothing real
// comes near it; the check exists because the failure is an activation written
// over a channel's bias, which produces a wrong tensor with a good CRC. Checked
// in gb_rqp() rather than gb_geom(), since gb_geom() does not know whether the
// caller intends to run this block in rq mode.
#define GB_RQBASE 1792

// One block: P output positions starting at (oy0, ox0), Q = QG*NMAC output
// channels starting at q0, swept in npass passes of Cb input channels.
typedef struct {
    int layer;
    int P, QG, Cb;
    int oy0, ox0;
    int q0;

    // M15. What the caller *wants*: drain this block as codes rather than as
    // int32 accumulators. A request and not a fact, because the geometry can
    // refuse it - see gb_geom_t's rq, which is the one to read afterwards.
    //
    // Whether a layer may be asked at all is the model's business and not this
    // struct's: conv7 emits floats and has no code to compute. The caller checks
    // fgx_emits_float() before setting this, and gb_rqp() checks it again.
    int rq;

    // M15. This block also carries the requantize table. gp_blocks() sets it on
    // the first block of each channel group, because the table is a function of
    // (layer, q0) and the enumeration is q0-major - so 31 sends a frame instead
    // of 174. It is in the spec rather than inferred so that the cost model and
    // the sequencer cannot disagree about which blocks pay for it.
    int rqp_send;
} gb_spec_t;

typedef struct {
    // M15 carried this over from gb_spec_t. gb_rqp() has to ask
    // fgx_emits_float(), which is a question about the layer's *position* in the
    // model and not about anything in the descriptor.
    int layer;
    int H, W, OW, OH, CIN, COUT, st;   // from the layer
    int P, Q, QG, Cb, K, npass;        // blocking
    int q0, oy0, ox0;
    int rowspan;                       // extra output rows this block spans
    int SROWS;                         // strip rows per input channel
    int strip_rw, strip_ch;            // strip byte pitches
    int iy_base;                       // input row of strip row 0; may be -1
    int a_len, w_len;                  // payload bytes PER PASS
    int nacc;                          // accumulators = P*Q
    int unsigned_in;

    // M14. The layer's weights are nibbles, so the WGT payload is half as long
    // and gb_weights() packs two output channels into each byte. It is a
    // per-layer property and not a build flag: conv0 is pinned to 8 bits by the
    // accuracy work and still runs on the tile, so both widths have to be live
    // in the same bitstream. gemm_wire carries it as one CFG bit.
    int w4;

    // M15, and this is the authority - s->rq is only the request. The two
    // geometry conditions the tile imposes are resolved here rather than raised
    // as errors, because unlike every other check in gb_geom() this one has a
    // working fallback: a block that cannot be drained as codes is drained as
    // int32, which is what M14 shipped.
    //
    //   Q <= GW_RQP_MAXQ    the table lives in 32 entries of strip
    //   a_len <= GB_RQBASE  and the strip has to stay clear of it
    //
    // Everything downstream reads this and not s->rq: frame.c decides which
    // drain to issue by it, gemm_plan prices the readout by it, and gb_rqp()
    // refuses to build a table without it.
    int rq;
    int rqp_send;   // g->rq && s->rqp_send
} gb_geom_t;

// Fills *g, or returns a reason the block will not run. Never partially
// succeeds in a way the caller can use.
//
// Takes the descriptor rather than looking it up through s->layer, so a caller
// can hand it a doctored one - which is how gen_gemm_vec.c's saturation case
// substitutes synthetic weights without a second code path.
const char *gb_geom(const fgx_desc_t *d, const gb_spec_t *s, gb_geom_t *g);

// `in` is the layer's full input tensor, CHW. `out` needs g->a_len bytes.
void gb_strip(const gb_geom_t *g, const uint8_t *in, int pass, uint8_t *out);

// `wb` is the layer's weight base, m->weights + d->w_off. `out` needs
// g->w_len bytes, laid out k-major, g-minor, lane-innermost - so the tile's
// word address is k*QG + g and it needs no multiplier.
//
// At g->w4 the same sequence is emitted as nibbles, two lanes per byte, the
// even lane in the low half. That is not a choice: gemm_tile assembles a weight
// word from WBYTES bytes low-byte-first and reads lane j at `wreg[4*j +: 4]`,
// so lane 0 has to be the low nibble of the first byte for the lanes to line up
// with the host's channels without a shuffle anywhere.
void gb_weights(const gb_geom_t *g, const int8_t *wb, int pass, int8_t *out);

// The loop order gb_weights() had before M7h, kept compiled in as the oracle it
// is checked against - same arrangement as gw_scan_slow() and gw_decode_slow().
// This one is the more direct transcription of "word address is k*QG + g", so
// it stays the definition and the fast one stays the thing that has to match.
void gb_weights_slow(const gb_geom_t *g, const int8_t *wb, int pass,
                     int8_t *out);

// The int32 accumulators the tile must return, in drain order: channel group
// outer, lane next, position inner. `out` needs g->nacc words.
//
// Every word comes from fgx_conv_acc(), never from a loop written here. A
// transcription would make this a second implementation, and agreement between
// two of my own loops proves nothing about the kernel the MCU actually runs.
void gb_golden(const fgx_model_t *m, const fgx_desc_t *d, const gb_geom_t *g,
               const void *in, int32_t *out);

// M15. The requantize table for this block, in the same order gb_golden() emits
// accumulators - channel group outer, lane next - so the tile indexes it by the
// channel it is draining and never has to know q0. `out` needs
// g->Q * GW_RQP_BYTES bytes; returns the length written, or 0 if the block
// cannot run in rq mode.
//
// It refuses rather than truncating, and there are three ways to be refused:
// the layer emits floats and has no code to compute, Q exceeds the tile's table,
// or a channel's (bias, M, s) does not fit the wire fields. All three are
// "run this block in int32 instead", which is a decision for the caller and not
// a fallback to make silently.
//
// Positions do not appear. mult and bias are per output channel, so the P
// positions of a channel share one entry - which is why the table is 192 bytes
// and not 12 KB, and why it is sent once per (layer, q0) rather than per block.
size_t gb_rqp(const fgx_model_t *m, const fgx_desc_t *d, const gb_geom_t *g,
              uint8_t *out);

// The int8 codes the tile must return at cfg_rq, in the same order. Built from
// gb_golden() and fgx_code_fixed(), for the reason gb_golden() is built from
// fgx_conv_acc(): the golden has to be the kernel the MCU runs and not a
// transcription of it. `acc` is gb_golden()'s output, `out` needs g->nacc bytes.
void gb_golden_code(const fgx_model_t *m, const fgx_desc_t *d,
                    const gb_geom_t *g, const int32_t *acc, uint8_t *out);

#endif
