// M7c: how a layer is cut into gemm_top blocks, and what that costs.
//
// M6c ran one block whose blocking was written out by hand. A frame needs a few
// hundred, across eight layers whose shapes have nothing in common, and **the
// hand-written table is the wrong way to get them.** Drafting one produced two
// errors in eight rows - a Cb that did not divide CIN, which gb_geom() rejects
// outright, and a block count off by two - both from misremembering a channel
// count. Every input the table needs is already in the descriptor, so this file
// derives it instead.
//
// The optimum is not a matter of taste either. Summing the link traffic a whole
// layer generates (see gp_choose) gives
//
//     WGT   = (N/P) * COUT * CIN * 9                  -> wants P large
//     ACT   = (N/P) * (COUT/Q) * CIN * SROWS * W      -> wants P*Q large, Q more
//     DRAIN = N * COUT * 4                            -> constant, no knob
//
// under P*QG <= GB_ADEPTH, so P and Q are competing for one budget and the
// answer is a minimum rather than "as big as it fits". A brute-force sweep over
// the few dozen legal triples costs microseconds and cannot disagree with the
// cost model the way a remembered table can.
//
// Free of Pico headers, like gemm_block.c and gemm_wire.c and for the same
// reason: firmware/test_gemm_plan.c settles the whole table on the laptop, and
// reflashing this board costs a physical PRG-GND strap.

#ifndef GEMM_PLAN_H
#define GEMM_PLAN_H

#include "gemm_block.h"

// M7a's measured per-byte rates at 150 MHz sys / 75 MHz link, in ns/byte:
// 8.94 MB/s on the wire, 0.118 us/B to build a strip or a weight stream,
// 0.0673 us/B to hash one outbound, 0.1125 us/B to decode a response body.
//
// They are here to *rank* blockings and to print a projection, not to stand in
// for a measurement. Ranking is insensitive to a few percent - the differences
// between candidate triples are factors, not percentages - and m7.c reports
// what the board actually does beside what this predicted.
//
// **The 150 MHz basis is load-bearing now that the config C ladder can land at
// 220.** Every one of these four scales with sys_clk, so at 220 they are ~47%
// high: 118 ns/B of build is ~80. Two things save this from being a bug rather
// than a caveat. Ranking is scale-invariant - one uniform factor on all four
// cannot reorder anything, so gp_choose() picks the same triple at any rate.
// And gp_cost_t.bytes is rate-free, which is why m7.c's bytes-projected line
// still matches the board to -0.1% at 220 while its ms line does not.
//
// So this is not rescaled here, and deliberately: gemm_plan.c is free of Pico
// headers so that test_gemm_plan.c can settle the whole table on the laptop,
// and reading clock_get_hz() would end that. test_gemm_plan.c's M6c anchors are
// 150 MHz measurements and must keep passing against 150 MHz constants. What
// the rate change requires is that anything *printing* these ms says which rate
// they are on; m7.c's plan table does.
#define GP_NS_WIRE    112
#define GP_NS_BUILD   118
#define GP_NS_CRC      67
#define GP_NS_DECODE  113

typedef struct {
    long bytes;      // clocked over the link; the two directions share the clock
    long us_wire;
    long us_cpu;     // build + outbound CRC + response decode
} gp_cost_t;

// The same cost, split by what each byte and each microsecond is *for*. Not
// needed to choose a blocking - gp_choose() only compares totals - but every
// optimisation past M7c deletes one component and leaves the others alone, so
// the split is what says which lever is worth pulling and by how much.
//
// Two of these are counter-intuitive and are the reason this is computed rather
// than eyeballed. RUN is almost all *idle* bytes, which are the clocks the tile
// computes on rather than data being sent - widening the link makes RUN bigger
// in bytes and no faster in time, which is what the `w` parameter below exists
// to price. And us_wgt_build is pure lane interleaving into an order fixed at
// export time, so it is deletable rather than tunable.
typedef struct {
    long act, wgt, run, drain, rqp, framing;   // bytes, by transaction
    long us_act_build;                    // gb_strip()
    long us_wgt_build;                    // gb_weights()
    long us_crc;                          // hashing what goes out
    long us_decode;                       // walking what comes back
} gp_parts_t;

typedef struct {
    gb_spec_t base;      // layer, P, QG, Cb; oy0 = ox0 = q0 = 0
    int       Q;         // QG * GB_NMAC
    int       npass;     // CIN / Cb
    int       nposblk;   // OH*OW / P
    int       nchblk;    // COUT / Q
    int       nblocks;   // nposblk * nchblk
    gp_cost_t cost;      // the whole layer, all blocks, one forward data line
    // The same blocking over three. Not a second plan - gp_choose() ranks at
    // width 1 and both configurations run the blocking it picked, so that the
    // A/B compares two wires and not two frames. This is here so m7.c can print
    // the projection its config-C rows should be read against.
    gp_cost_t cost3;
} gp_layer_t;

// Picks the cheapest blocking gb_geom() will accept for this layer, or returns
// why no blocking exists. `layer` only fills base.layer; the geometry all comes
// from *d.
//
// M15's `rq` is the caller's answer to "may this layer be drained as codes",
// which is fgx_emits_float() and therefore a question about the model this
// function is not given. It is not merely recorded: DRAIN is the largest byte
// component of most blocks, so quartering it changes which blocking is
// cheapest, and a plan chosen at int32 and then run at int8 would be the wrong
// plan. Pass what the sequencer will actually do.
const char *gp_choose(const fgx_desc_t *d, int layer, int rq, gp_layer_t *pl);

// Writes pl->nblocks specs into `out`, returning how many. Channel block outer,
// position block inner - the order m7.c walks them, chosen so consecutive
// blocks share a weight range rather than an activation range, because the
// activations are already in SRAM and the weights come over flash XIP.
//
// Returns -1 if `max` is too small rather than truncating: a short block list
// produces a tensor with holes in it, which reads as an arithmetic bug.
int gp_blocks(const fgx_desc_t *d, const gp_layer_t *pl, gb_spec_t *out, int max);

// ---------------------------------------------------------------------------
// M16. How many clocks one RUN has to hold the tile for, which until now was
// one expression copied into three files - gemm_plan.c's cost model, frame.c's
// sequencer and m6.c's clock sweep. It is here instead because the copies are
// not equivalent in their failure: the model being wrong prints a wrong
// projection, and the sequencer being wrong **strands the tile mid-sweep**,
// because the idle bytes of the RUN transaction are the tile's only clock.
// Over-budgeting is free and merely slow. So there is one definition now.
//
// GP_KPACK must match the bitstream. At 1 the tile pairs kernel taps on kx, so
// a 3x3's nine taps become six sweeps per channel block - (0,1) and (2, -) at
// each of three ky - and each sweep is one clock longer because FLUSH goes from
// 5 to 6 for stage 2c. Net 1.408x, measured in tb_gemm's busy counter.
//
// The two directions are not symmetric. GP_KPACK = 0 against a packed
// bitstream over-budgets: the tile finishes early and idles, which loses the
// milestone and nothing else. **GP_KPACK = 1 against an unpacked one
// under-budgets by a third and strands the tile mid-sweep**, because RUN's
// idle bytes are the only clock it gets.
//
// For the embedded path that is hard to get wrong - build.sh writes the .hex
// and hex2c.py embeds it at cmake time, so a clean build of the pair matches
// by construction. **m7.py is the exception and it is the dangerous one**: it
// sends a bitstream over the serial link at run time, overriding the embedded
// blob, so the flag baked into the .uf2 and the .hex named on the command line
// are chosen hours apart by a human. Flashing the default build and then
// pointing m7.py at an M14- or M15-era .hex is one flag away and hangs the
// board. Build the matching firmware first:
//
//     cmake -DGP_KPACK=0 ...   <-> any .hex built without KPACK=1
//
//     TOP_PARAMS="RQ=1,KPACK=1" ./build.sh gemm_top     <-> GP_KPACK 1
#ifndef GP_KPACK
#define GP_KPACK 1
#endif

static inline long gp_sweep_cycles_k(const gb_geom_t *g, int kpack)
{
    // g->K is Cb * 9 - every conv in this model is 3x3, which is what makes the
    // pairing a constant 9 -> 6 rather than a per-layer question.
    const long nsweep = (kpack ? (long)g->Cb * 6 : (long)g->K) * (long)g->QG;
    // Per sweep: one S_LOAD, P issues, FLUSH. 512 is the transaction's own
    // turnaround, unchanged.
    return nsweep * ((long)g->P + 6 + (kpack ? 1 : 0)) + 512;
}

static inline long gp_sweep_cycles(const gb_geom_t *g)
{
    return gp_sweep_cycles_k(g, GP_KPACK);
}

// What one block costs, from its geometry alone. Mirrors gemm_host.c's framing
// exactly - see the note in gemm_plan.c on why that duplication is checked
// rather than trusted.
void gp_block_cost(const gb_geom_t *g, gp_cost_t *c);

// The same block, by component. The six byte fields sum to gp_block_cost()'s
// `bytes` and the four time fields to its `us_cpu`, both asserted in
// test_gemm_plan.c rather than left to inspection.
//
// `rqp` is nonzero on one block in nposblk - see gb_spec_t's rqp_send - so it is
// a component of the *layer* that happens to be billed to a particular block.
// That is why gp_choose() cannot get the layer total by multiplying one block by
// nblocks and adds this term at nchblk instead.
void gp_block_parts(const gb_geom_t *g, gp_parts_t *p);

// M7f. Both of the above at `w` forward data lines - 1 for configuration A,
// 3 for configuration C. `bytes` is wire bytes, so it grows with w wherever the
// bytes were idle; `us_wire` is bytes/w, because what the link actually spends
// is clocks. The four CPU times do not depend on w at all: build, CRC and
// decode are per *logical* payload byte, and the payload is the same either way.
//
// The unsuffixed forms above are these at w = 1, unchanged to the byte.
void gp_block_cost_w(const gb_geom_t *g, int w, gp_cost_t *c);
void gp_block_parts_w(const gb_geom_t *g, int w, gp_parts_t *p);

// M16. The same again with the tile packing spelled out instead of taken from
// GP_KPACK. Nothing in the firmware wants this - the bitstream is what it is -
// but test_gemm_plan.c has to price M6c's block on an unpacked tile to keep its
// anchor a check against a measurement, and on a packed one to say what the
// pairing costs. gp_block_parts_w() is this at kpack = GP_KPACK.
void gp_block_parts_kw(const gb_geom_t *g, int w, int kpack, gp_parts_t *p);

static inline void gp_parts_add(gp_parts_t *a, const gp_parts_t *b)
{
    a->act          += b->act;
    a->wgt          += b->wgt;
    a->run          += b->run;
    a->drain        += b->drain;
    a->rqp          += b->rqp;
    a->framing      += b->framing;
    a->us_act_build += b->us_act_build;
    a->us_wgt_build += b->us_wgt_build;
    a->us_crc       += b->us_crc;
    a->us_decode    += b->us_decode;
}

static inline void gp_cost_add(gp_cost_t *a, const gp_cost_t *b)
{
    a->bytes   += b->bytes;
    a->us_wire += b->us_wire;
    a->us_cpu  += b->us_cpu;
}

#endif
