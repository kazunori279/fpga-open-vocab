// See gemm_plan.h.

#include "gemm_plan.h"
#include "gemm_wire.h"

// ---------------------------------------------------------------------------
// The framing, restated.
//
// These four numbers are gemm_host.c's GH_RESP_FIX, GH_SLACK, the 5-byte header
// and the DMA's word rounding. They are private to that file, and it needs Pico
// headers, so a cost model that lives on the host cannot include it.
//
// **The duplication is checked rather than trusted.** test_gemm_plan.c asserts
// that this arithmetic reproduces M6c's four measured transaction lengths -
// 1588 / 1204 / 2528 / 8244 - and the 50,980-byte block they add up to. If
// anyone changes the slack in gemm_host.c, that assertion is what fails.
// ---------------------------------------------------------------------------
#define GP_RESP_FIX 13L
#define GP_SLACK    32L

// gh_frame(), in wire bytes: header, payload, and `idle` bytes *per lane* -
// idle is a demand for response room, which is measured in link clocks, and it
// takes one byte on each lane to supply one clock. Rounded up to 4*w so the
// transaction is a whole number of TX words and of captured words at once.
//
// At w = 1 this is `(5 + len + idle + 3) & ~3`, which is what M7c shipped and
// what test_gemm_plan.c checks against M6c's four measured lengths.
static long gp_xfer_w(long len, long idle, int w)
{
    const long gran = 4L * w;
    const long n = (long)GW_WIRE_HDR((unsigned)w) + len + idle * (long)w;
    return (n + gran - 1L) & ~(gran - 1L);
}

// gh_simple(): every command whose response carries no payload.
static long gp_simple_w(long len, long extra_idle, int w)
{
    return gp_xfer_w(len, GP_RESP_FIX + GP_SLACK + extra_idle, w);
}

// Each transaction is charged whole - its 5-byte header and its response idle
// along with its payload - to the component it exists for. The only bytes left
// over are the NOP/CFG brackets, which is what `framing` counts.
void gp_block_parts_kw(const gb_geom_t *g, int w, int kpack, gp_parts_t *p)
{
    const long npass = g->npass;
    const long nacc  = g->nacc;
    const long sweep = gp_sweep_cycles_k(g, kpack);

    // M15. The DRAIN payload, in bytes. One byte per accumulator at rq and four
    // otherwise, and this one expression is the entire milestone as far as the
    // cost model is concerned - every other line below is unchanged.
    const long dbody = g->rq ? nacc : 4L * nacc;

    // run_block() brackets the block with a NOP each side: one to clear the
    // sticky faults, one to read them back after the drain.
    p->framing = 2L * gp_simple_w(0, 0, w) + gp_simple_w(GW_CFG_BYTES, 0, w);
    p->act     = npass * gp_simple_w(g->a_len, 0, w);
    p->wgt     = npass * gp_simple_w(g->w_len, 0, w);
    // Almost entirely idle bytes, and they are not data: they are the clocks the
    // tile computes on. So widening the link makes this component exactly `w`
    // times bigger in bytes and not one microsecond faster - which is why the
    // time below is bytes/w rather than bytes, and why config C is worth so much
    // less than the byte totals suggest. See "The road to 280 ms" in
    // docs/milestones.md.
    p->run     = npass * gp_simple_w(1, (sweep + 7L) / 8L, w);
    p->drain   = gp_xfer_w(0, GP_RESP_FIX + dbody + GP_SLACK, w);

    // Charged whole to the one block per channel group that sends it, so a sum
    // over a layer's blocks is the layer's real RQP traffic. Zero everywhere
    // else, and zero entirely at rq off.
    p->rqp     = g->rqp_send
                 ? gp_simple_w((long)g->Q * (long)GW_RQP_BYTES, 0, w) : 0L;

    // gb_strip() writes one byte per output byte. gb_weights() is charged per
    // *weight*, which is w_len bytes at 8 bits and twice that at 4 - the nibble
    // arm halves the strided stores but does two extracts per store, so it is
    // slightly cheaper than the byte arm **per weight**, not per byte. Charging
    // w_len here would tell gp_choose that a 4-bit layer's stream costs half the
    // CPU to build, and gp_choose ranks blockings by exactly this sum.
    p->us_act_build = npass * g->a_len * GP_NS_BUILD / 1000L;
    p->us_wgt_build = npass * (g->w4 ? 2L * g->w_len : g->w_len)
                      * GP_NS_BUILD / 1000L;

    // gh_frame() hashes the payload only, so NOP and DRAIN hash nothing and RUN
    // hashes its single first-pass flag.
    p->us_crc = (GW_CFG_BYTES + npass * (g->a_len + g->w_len + 1L)
                 + (g->rqp_send ? (long)g->Q * (long)GW_RQP_BYTES : 0L))
                * GP_NS_CRC / 1000L;

    // GW_BODY_B(0) for every command but DRAIN - nine bytes of status and two
    // CRCs, walked whether or not anything follows them. RQP adds one more of
    // those on the blocks that send it.
    p->us_decode = (9L * (3L + 3L * npass + (g->rqp_send ? 1L : 0L))
                    + (long)GW_BODY_B(dbody))
                   * GP_NS_DECODE / 1000L;
}

void gp_block_parts_w(const gb_geom_t *g, int w, gp_parts_t *p)
{
    gp_block_parts_kw(g, w, GP_KPACK, p);
}

void gp_block_parts(const gb_geom_t *g, gp_parts_t *p)
{
    gp_block_parts_w(g, 1, p);
}

void gp_block_cost_w(const gb_geom_t *g, int w, gp_cost_t *c)
{
    gp_parts_t p;
    gp_block_parts_w(g, w, &p);

    c->bytes   = p.act + p.wgt + p.run + p.drain + p.rqp + p.framing;
    // GP_NS_WIRE is per byte at one lane, which is per *link clock*. `bytes` is
    // wire bytes across all of them, so the clock count is bytes/w.
    c->us_wire = c->bytes * GP_NS_WIRE / (1000L * (long)w);
    c->us_cpu  = p.us_act_build + p.us_wgt_build + p.us_crc + p.us_decode;
}

void gp_block_cost(const gb_geom_t *g, gp_cost_t *c)
{
    gp_block_cost_w(g, 1, c);
}

// ---------------------------------------------------------------------------
// Choosing.
// ---------------------------------------------------------------------------

// P is restricted so that every block in the layer has the *same* geometry.
//
// gb_geom() computes rowspan as (ox0 + P - 1) / OW, so a P that neither divides
// a row nor covers whole rows gives blocks with different strip heights - legal,
// but it would mean a per-block a_len, a per-block CFG, and a cost model that
// cannot be summarised by one triple. Either of the two clean cases avoids that
// and between them they cover every P worth having.
static bool gp_p_ok(int P, int OW, int N)
{
    if (P < 1 || P > 255) return false;      // CFG carries P as one byte
    if (N % P) return false;
    return (OW % P == 0) || (P % OW == 0);
}

const char *gp_choose(const fgx_desc_t *d, int layer, int rq, gp_layer_t *pl)
{
    const int OW = d->ow, OH = d->oh, N = OH * OW;
    const int CIN = d->cin, COUT = d->cout;

    if (d->ksize != 3) return "only 3x3 layers can go to the tile";
    if (N < 1 || CIN < 1 || COUT < 1) return "degenerate layer";

    long best = 0;
    bool have = false;

    for (int P = 1; P <= 255; P++) {
        if (!gp_p_ok(P, OW, N)) continue;

        for (int QG = 1; QG * GB_NMAC <= COUT; QG++) {
            if (COUT % (QG * GB_NMAC)) continue;
            if (P * QG > GB_ADEPTH) break;      // monotone in QG

            for (int Cb = 1; Cb <= CIN; Cb++) {
                if (CIN % Cb) continue;

                // rqp_send is deliberately 0 here: the representative block is
                // priced without the table, and the table is added once per
                // channel group below. gp_blocks() puts the flag on the blocks
                // that really send it, so a sum over the layer's blocks lands on
                // the same number - test_gemm_plan.c checks exactly that.
                const gb_spec_t s = {
                    .layer = layer, .P = P, .QG = QG, .Cb = Cb,
                    .oy0 = 0, .ox0 = 0, .q0 = 0,
                    .rq = rq, .rqp_send = 0,
                };
                gb_geom_t g;
                if (gb_geom(d, &s, &g)) continue;

                // Ranked at width 1, deliberately, and it is not an oversight
                // that configuration C does not get its own optimum.
                //
                // The two would not always agree - ACT and WGT get three times
                // cheaper while RUN and DRAIN do not move, so C would push P and
                // Q around to buy fewer clocks rather than fewer bytes. But the
                // whole point of running both in one boot is that everything
                // except the pins is held fixed, and a plan that changed with
                // the configuration would make the ratio a comparison of two
                // different frames. One plan, two wires. c3 below is the same
                // blocking priced at three lanes, for the projection only.
                gp_cost_t c, c3;
                gp_block_cost(&g, &c);
                gp_block_cost_w(&g, 3, &c3);

                const int nposblk = N / P;
                const int nchblk  = COUT / (QG * GB_NMAC);
                const int nblocks = nposblk * nchblk;

                // M15's table, once per channel group. Priced as the *difference*
                // between a block that sends it and one that does not, rather
                // than as its own arithmetic, so it inherits every rounding the
                // block cost already makes and the layer total is exactly what a
                // sum over gp_blocks()'s specs gives. Zero at rq off.
                gb_geom_t gr = g;
                gr.rqp_send = g.rq;
                gp_cost_t cr, cr3;
                gp_block_cost(&gr, &cr);
                gp_block_cost_w(&gr, 3, &cr3);

                const long score  = (c.us_wire + c.us_cpu) * nblocks
                    + ((cr.us_wire - c.us_wire) + (cr.us_cpu - c.us_cpu))
                      * nchblk;

                // Ties go to the larger Cb, which is the same blocking with
                // fewer passes and therefore fewer transactions - the cost
                // model rounds to the microsecond and cannot always see that.
                if (have && score >= best) continue;

                have = true;
                best = score;
                pl->base    = s;
                // The plan's own answer, not the request: gb_geom() may have
                // refused rq for this geometry, and everything downstream -
                // frame.c's drain, gp_blocks()'s rqp_send - has to agree with
                // the cost that was just used to rank it.
                pl->base.rq = g.rq;
                pl->Q       = QG * GB_NMAC;
                pl->npass   = g.npass;
                pl->nposblk = nposblk;
                pl->nchblk  = nchblk;
                pl->nblocks = nblocks;
                pl->cost.bytes    = c.bytes    * nblocks
                                  + (cr.bytes   - c.bytes)   * nchblk;
                pl->cost.us_wire  = c.us_wire  * nblocks
                                  + (cr.us_wire - c.us_wire) * nchblk;
                pl->cost.us_cpu   = c.us_cpu   * nblocks
                                  + (cr.us_cpu  - c.us_cpu)  * nchblk;
                pl->cost3.bytes   = c3.bytes   * nblocks
                                  + (cr3.bytes   - c3.bytes)   * nchblk;
                pl->cost3.us_wire = c3.us_wire * nblocks
                                  + (cr3.us_wire - c3.us_wire) * nchblk;
                pl->cost3.us_cpu  = c3.us_cpu  * nblocks
                                  + (cr3.us_cpu  - c3.us_cpu)  * nchblk;
            }
        }
    }

    if (!have) return "no blocking of this layer fits the tile";
    return 0;
}

int gp_blocks(const fgx_desc_t *d, const gp_layer_t *pl, gb_spec_t *out, int max)
{
    if (max < pl->nblocks) return -1;

    const int OW = d->ow, P = pl->base.P;
    int n = 0;

    for (int cb = 0; cb < pl->nchblk; cb++)
        for (int pb = 0; pb < pl->nposblk; pb++) {
            const int pos0 = pb * P;
            out[n]     = pl->base;
            out[n].q0  = cb * pl->Q;
            out[n].oy0 = pos0 / OW;
            out[n].ox0 = pos0 % OW;
            // M15. The table is a function of (layer, q0) and q0 changes only
            // when cb does, so the first block of each channel group carries it.
            // This is the same condition frame.c's run_block() evaluates as a
            // cache miss - stated twice, deliberately, because the cost model
            // has to be able to answer it without running the sequencer.
            out[n].rqp_send = (pb == 0);
            n++;
        }

    return n;
}
