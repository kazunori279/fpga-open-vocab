// M6: the int8 GEMM tile.
//
// One block of the convolution, computed on the T8: P output positions x Q
// output channels, accumulating over the K = Cb*9 im2col taps handed to it.
// Terminates at the int32 accumulator - bias, requantization and the clamp stay
// on the MCU, because that is exactly where fgx_conv_acc() in firmware/encoder.c
// stops, and that function is the contract this has to match bit for bit.
//
// **The link is the bottleneck, not this array**, and every sizing decision here
// follows from that. With P positions and Q channels resident and K swept in
// full, forward traffic per frame is
//
//     traffic = input_bytes*(COUT/Q) + weight_bytes*(N/P)
//
// - weights reused across positions, activations across channels - so the
// accumulator count P*Q is what buys traffic, and BRAM is what caps it. At
// P*Q = 2048 the whole model costs 3.57 MB/frame, ~399 ms at the 8.94 MB/s M2
// measured. Every layer's optimum lands on a power of two with P*Q = 2048
// exactly, which is why P and Q are runtime configuration and not parameters:
// one tile, eight blockings, no special cases.
//
// **Why NMAC = 8 and not 4.** Take the conv2 blocking (P=128, Q=16, Cb=8): the
// block costs 2,944 forward bytes = 23,552 link clocks against 147,456 MACs. At
// 8 MAC/cycle that is 18,432 cycles plus ~720 of sweep overhead - compute hides
// behind the link *fully serialized*, so nothing here needs double-buffering. At
// 4 MAC/cycle it is 36,864 and the fabric becomes the bottleneck. 8 is the
// smallest array that keeps the link binding, and it is exactly what the T8 has.
//
// This also settles a claim the early README carried: "packed 2x for int8 -> 16
// multipliers" is wrong. An 18x18 multiplier cannot hold two int8xint8 products
// sharing an operand - the products are 16 bits wide and 18 bits cannot give 16
// bits of field separation. The honest figure is 8, and it happens to suffice.
// (At *int4* the products are 12 bits and they do separate - see the APACK
// parameter, which is M14's gate on whether the rest of the die can afford it.)
//
// One clock domain, `clk` = `link_clk`. The MCU is the only clock source and the
// tile only moves when data moves, so there is no CDC anywhere in M6.

`timescale 1ns / 1ps
`default_nettype none

module gemm_tile #(
    // Lanes, and therefore hard multipliers. Q must be a multiple of this;
    // every layer's Q is 16, 32 or 128, so that holds.
    parameter integer NMAC   = 8,
    // Accumulator entries per lane. S = NMAC*ADEPTH is the number of (p,q)
    // accumulators, and the single most important number in the design: halving
    // it costs a factor sqrt(2) in link traffic (3.57 -> 5.0 MB/frame, 399 ->
    // 565 ms). If M6b reports the BRAM does not fit, this is the knob to turn.
    parameter integer ADEPTH = 256,
    // Weight words, NMAC bytes each. K*QG must fit.
    parameter integer WDEPTH = 256,
    // Activation strip bytes.
    parameter integer STRIPD = 2048,
    parameter integer AW     = 11,   // clog2(STRIPD)
    parameter integer CW     = 10,   // spatial coordinate width, signed
    parameter integer PW     = 8,    // P  <= 128
    parameter integer GW     = 5,    // QG <= 16
    parameter integer KW     = 10,   // K  <= 512
    // Pipeline the drain walk. **Default 0, and it has to stay 0** for
    // gemm_top/gemm_top_wide. At 1 the walk takes one extra cycle per word and
    // gives back the two paths that M10 Stage 0 measured as gemm_tile's
    // critical path; see the "DPIPE" block below the drain-walk declarations
    // for what those are.
    //
    // At 0 every construct this parameter guards folds to the expression it
    // replaced and S_DMUX does what `default` used to, so the intent was a
    // bit-identical gemm_top. **Measured, that is not quite true**, and the
    // honest version is worth recording rather than the intended one. Built
    // twice at seed 2 against the pre-DPIPE source (the flow is deterministic -
    // verified by building the original twice and diffing):
    //
    //            FFs         LUTs      RAMs   MULTs     link_clk Fmax
    //   before   1743        930       21     8         62.449 MHz
    //   after    1744        931       21     8         64.737 MHz
    //
    // One flop and one LUT, both attributed to u_link, which was not touched -
    // the res.csv header warns that resources migrate across the hierarchy
    // under LUT mapping, so this is the FSM re-decomposing by one level and not
    // a new register. u_tile is unchanged in every column that matters (1074
    // FFs, 346 ADDs, 21 RAMs, 8 MULTs). The bitstreams differ; the Fmax delta is
    // inside the +/-2.4 MHz seed spread build.sh records, and it moved the
    // right way. tb_gemm / tb_gemm_link / tb_gemm_link_wide all still pass
    // bit-exact on 10,560 accumulators, which is the contract that actually
    // holds.
    parameter integer DPIPE  = 0,

    // ---- M14: int4 weights -------------------------------------------------
    // Both default to today's behaviour, so gemm_top and gemm_top_wide are
    // untouched until something passes them a different value.
    //
    // WNIB = 1 stores weights as nibbles: `wbuf` narrows from 8*NMAC bits to
    // 4*NMAC and the nibble is sign-extended to 8 bits before the multiply, so
    // the DSP sees the same 10x8 shape and the product is bit-identical to
    // WNIB = 0 fed the same values. The only question it asks is what the
    // narrower array costs in blocks and what the extra select costs in Fmax -
    // and Fmax is the one that matters, because M10 measured the critical path
    // as `wbuf|RDATA -> net -> mult|B` at **Logic Level 0**, which is precisely
    // where a nibble select would land.
    //
    // APACK = 2 is the packed-MAC experiment, and it is a *gate*, not a
    // feature. The header above says an 18x18 cannot hold two int8xint8
    // products sharing an operand, and that is right. int4 changes the
    // arithmetic: |a*w| <= 255*8 = 2040 fits in 12 signed bits, so
    //
    //     B = w_lo + (w_hi << 12)  ->  a*B = a*w_lo + 4096*(a*w_hi)
    //
    // separates, with `$signed(p[11:0])` recovering the low product exactly and
    // `p[11]` carrying the borrow into the high one. Two output channels per
    // multiplier, 16 MAC/cycle, RUN's 313 ms halved.
    //
    // What decides it is not the multiplier but `accram`. 16 accumulators a
    // cycle means a 512-bit write port at half the depth. Whether 128 x 512
    // costs the same 13 blocks as today's 256 x 256 - the total bit count is
    // identical - or twice that depends on whether the Trion block's bounded
    // data width scales with the aspect ratio, which is an Efinity question and
    // not a Verilog one. **Build tile_probe with APACK = 2 and read
    // tile_probe.res.csv.** That is the whole purpose of this parameter; the
    // number it produces is recorded in the M14 section of docs/milestones.md.
    //
    // Note APACK = 2 requires WNIB = 1 (there is no packed int8 form) and that
    // it changes the meaning of cfg_QG at the host: Q per accumulator word
    // becomes NMAC*APACK, so QG = Q/16 rather than Q/8.
    parameter integer APACK  = 1,
    parameter integer WNIB   = 0,

    // ---- M15: the requantize epilogue --------------------------------------
    // 0 is today's tile and every construct this guards folds away, so
    // gemm_top and gemm_top_wide are bit-identical until something passes 1.
    //
    // At 1 the tile can finish the job instead of handing back an int32
    // accumulator: bias, the fixed-point multiply, the rounding shift and the
    // clamp to [0,255], so DRAIN returns **one byte per accumulator instead of
    // four**. That is the whole of M15, because DRAIN is the largest line in
    // the frame - 161 ms of config C's 617 - and the return path is one lane
    // that cannot be widened. GPIO6 has no contiguous neighbour, so the only
    // lever left is sending fewer bytes.
    //
    // The contract is M15 Stage 0's, measured before a line of this was
    // written (docs/milestones.md):
    //
    //     M = round(mult * 2^s) in [2^17, 2^18)
    //     code = clamp(((acc + bias) * M + 2^(s-1)) >> s, 0, 255)
    //
    // 49 codes of 1,409,024 differ from the float epilogue, every one by +-1,
    // and retention over 5,000 images and 67 queries does not move: 94% either
    // way. firmware/encoder.h's fgx_code_fixed() is the same arithmetic and is
    // what this has to match bit for bit.
    //
    // relu is deliberately absent. fgx_sat8() maps every negative to 0, so a
    // relu in front of an unsigned saturate to [0,255] changes nothing and the
    // fabric needs none of it.
    parameter integer RQ     = 0,

    // ---- M16: two K steps per lane -------------------------------------------
    // 0 is today's tile, bit-identical, and every construct this guards folds
    // away - including the strip, which stays one flat array rather than two
    // banks, so the KPACK = 0 netlist has no bank select anywhere.
    //
    // At 1 each lane does **two taps per cycle**: the hard multiplier takes
    // (a_kx, w_kx) as it always did, an LE-built multiplier takes
    // (a_kx+1, w_kx+1), and the two products are summed before the accumulator.
    // Integer addition is associative, so this is bit-identical to accumulating
    // them on consecutive sweeps - every existing golden applies unchanged.
    //
    // Why this is the milestone: after M15, `test_plan` prices config C at 499
    // ms of which **RUN is 325** - the host idling while the tile computes. It
    // is the same 325 ms on one forward lane and on three, so it is tile cycles
    // and not bytes, and no link change can reach it. Every conv here is 3x3, so
    // pairing on kx turns three sweeps into two per (ky, ic): **1.5x**.
    //
    // Why kx and not p or g. Both steps must land in ONE accumulator word, or
    // `accram` needs a second write port and it is already 13 of the 21 blocks -
    // that rules out p. g is the axis the eight lanes already use. kx is left,
    // and it is free on both operand fetches: im2col_feed's `addr = rowb +
    // ix_pad` means taps kx and kx+1 are **one byte apart at any stride** (the
    // stride applies to positions, not taps), and the (k outer, g inner) walk
    // puts their weight words exactly cfg_QG apart in `wbuf`.
    //
    // Why the second multiplier is logic. APACK = 2's trick packs two weights
    // against a SHARED activation; here both operands differ, and packing both
    // gives four terms whose cross products land on top of the two wanted fields
    // at every legal shift. It is written out as a shift-add below rather than
    // as `*`, because all eight DSPs are committed and inference would either
    // steal one or pick which lane loses at random.
    parameter integer KPACK  = 0
) (
    input  wire                 clk,

    // ---- geometry, held for a whole block ----------------------------------
    input  wire signed [CW-1:0] cfg_H,        // full input height
    input  wire signed [CW-1:0] cfg_W,        // full input width
    input  wire signed [CW-1:0] cfg_OW,       // output row length
    input  wire                 cfg_stride2,  // 1 = stride 2
    input  wire        [AW-1:0] cfg_strip_rw, // strip bytes per row     = W
    input  wire        [AW-1:0] cfg_strip_ch, // strip bytes per channel = rows*W
    input  wire        [CW-1:0] cfg_oy0,      // output row of position p=0
    input  wire        [CW-1:0] cfg_ox0,      // output column of position p=0
    input  wire                 cfg_unsigned_in,

    // M14. The WGT stream for this block is nibbles: one incoming byte carries
    // two output channels, low nibble first, so a weight word completes in
    // WBYTES/2 bytes instead of WBYTES and each byte is expanded to two on the
    // way into the shift register.
    //
    // A *runtime* bit and not the WNIB parameter Stage 0 measured, and the
    // reason is conv0. `ends8` pins conv0's weights to 8 bits for accuracy, and
    // frame.c runs all eight convolutions on this tile - so both widths have to
    // be live in one bitstream and a 4*NMAC-bit `wbuf` physically cannot hold
    // conv0's row. See the M14 section of docs/milestones.md; the wire format
    // is identical either way, so nothing on the host side notices.
    input  wire                 cfg_w4,

    // M15. Runtime, not the RQ parameter: conv7 emits float and has no byte for
    // the tile to send, so its two blocks keep int32 DRAIN in the same
    // bitstream as the seven that do not. It is also what keeps the int32 path
    // on the shelf - ft_set_sweep()'s 174-of-174 accumulator sweep is the
    // standing guarantee that the MAC array was not disturbed, and it only
    // stays a guarantee if it can still be run.
    input  wire                 cfg_rq,

    // ---- blocking ----------------------------------------------------------
    input  wire        [PW-1:0] cfg_P,        // positions in this block
    input  wire        [GW-1:0] cfg_QG,       // channel groups, = Q/NMAC
    input  wire        [KW-1:0] cfg_K,        // taps in this pass, = Cb*9

    // ---- buffer loads ------------------------------------------------------
    // Activations are byte-addressed because the link delivers a flat strip.
    input  wire                 act_we,
    input  wire        [AW-1:0] act_addr,
    input  wire        [7:0]    act_data,
    // Weights arrive as a byte stream and are lane-interleaved *by
    // construction*: stream position n goes to lane n mod NMAC of word
    // n / NMAC. The host emits them k-major, g-minor, lane-innermost, so the
    // word address k*QG + g is just a counter and needs no multiplier here.
    // firmware/gen_gemm_vec.c is the only writer of that order.
    input  wire                 wgt_we,
    input  wire        [7:0]    wgt_data,
    input  wire                 wgt_rst,      // rewind the stream pointer

    // ---- run ---------------------------------------------------------------
    input  wire                 run,          // one-cycle pulse
    input  wire                 run_init,     // this pass initialises the accs
    output wire                 busy,

    // ---- drain -------------------------------------------------------------
    // P*Q int32 accumulators, channel-major (q outer, p inner) so the MCU can
    // store them straight into a CHW tensor.
    input  wire                 drain,        // one-cycle pulse
    output reg         [31:0]   dout,
    output reg                  dout_valid,
    input  wire                 dout_ready
);

    function integer clog2;
        input integer v;
        integer i;
        begin
            clog2 = 0;
            for (i = v - 1; i > 0; i = i >> 1)
                clog2 = clog2 + 1;
        end
    endfunction

    localparam integer AA   = clog2(ADEPTH);   // accumulator address bits
    localparam integer WA   = clog2(WDEPTH);   // weight word address bits

    // Output channels resident per accumulator word. This is NMAC at APACK = 1
    // - one channel per multiplier - and 2*NMAC when each multiplier carries a
    // packed pair. Everything downstream that used to say NMAC and meant "a
    // channel" says NQW; the places that still say NMAC mean "a multiplier".
    localparam integer NQW  = NMAC * APACK;
    localparam integer JW   = clog2(NQW);      // channel index bits
    localparam integer ACCT = 32 * NQW;        // accumulator word width

    // Weight storage width. A nibble per channel at WNIB = 1, a byte at 0; the
    // packed pair costs the same 64 bits as one byte per lane, which is why
    // APACK = 2 does not touch wbuf's block count at all.
    localparam integer WBITS = WNIB ? 4 : 8;
    localparam integer WGTT  = WBITS * NQW;    // weight word width

    // Bytes per weight word, which is no longer NMAC once WBITS is 4. The
    // stream is still a byte stream and still lane-innermost; only how many of
    // them make a word changed.
    localparam integer WBYTES = WGTT / 8;
    localparam integer BW     = clog2(WBYTES);
    localparam [BW-1:0] BLAST = WBYTES - 1;

    localparam [JW-1:0] JLAST = NQW - 1;

    // Cycles of quiet after a p sweep. The accumulator RMW loop is 5 cycles
    // deep (address registered -> read data -> operand register -> product ->
    // sum -> write commits), so an address must not be re-issued within 5
    // cycles of itself.
    //
    // **That is not what this is for**, and it is worth being exact, because
    // the obvious reason is wrong. Inside a sweep, addresses step by QG and so
    // repeat only after P cycles. Across a sweep boundary they repeat after
    // P + FLUSH + 1. P is 16 at its smallest, so both are safe at FLUSH = 0 -
    // and the smoke test confirms it, passing every case at FLUSH = 1 including
    // the QG = 1 blocking.
    //
    // What FLUSH actually buys is the *run* boundary. The last four issues of
    // a pass are still in flight when the FSM reaches S_IDLE, so without it a
    // back-to-back `run` (which is exactly how split-K sub-blocking drives this
    // tile) or an immediate `drain` would race the tail of the previous pass.
    // The host cannot issue commands that fast over a byte-serial link, but the
    // tile should not be correct only because the host is slow. This must equal
    // the pipeline depth exactly - then the final write commits on the last
    // S_FLUSH cycle. It was 3, then 4 when the operand register went in, then 5
    // when the writeback register did; anything that deepens the datapath has
    // to be paid for here. Cost is 5 + 1 cycles per sweep - 4.7% on the conv2
    // block, against a compute budget with ~28% of slack.
    //
    // M16 makes it 6 at KPACK = 1 - stage 2c, which the LE multiplier needs and
    // which the gate build proved is not optional. That is one more cycle per
    // sweep on top of the same 4.7%, and it is bought with 1.5x fewer sweeps.
    localparam integer FLUSH = (KPACK == 0) ? 5 : 6;

    localparam [2:0] S_IDLE  = 3'd0,
                     S_LOAD  = 3'd1,
                     S_SWEEP = 3'd2,
                     S_FLUSH = 3'd3,
                     S_DISS  = 3'd4,   // drain: issue accumulator read
                     S_DWAIT = 3'd5,   // drain: read data in flight
                     S_DHOLD = 3'd6,   // drain: word presented, await ready
                     S_DMUX  = 3'd7;   // drain: DPIPE only, lane select


    reg [2:0] state = S_IDLE;

    // ------------------------------------------------------------------------
    // Storage.
    //
    // Three arrays, each as WIDE as it can be rather than split into banks,
    // because Trion memory blocks are 5 Kbit with a bounded data width and the
    // cost is block count, not bit count. All NMAC lanes share one accumulator
    // address - the lane index *is* the low bits of q - so a single 256-bit-wide
    // array works and costs ~13 blocks, where eight separate 32-bit arrays would
    // each round up to 2 and cost 16. The weight buffer is the same trick: one
    // NMAC-byte word read once per sweep, not NMAC byte-wide memories read in
    // parallel.
    //
    // Whether this fits is an Efinity question, not a Verilog one, and M6b
    // answers it from place.rpt. The estimate is ~21 of the T8F49's 24 blocks.
    //
    // M14 asks the same question of a different shape. At APACK = 2 this array
    // becomes half as deep and twice as wide for the same 2048 accumulators, so
    // the bit count does not move and the block count might not either - if the
    // block's bounded data width scales with the aspect ratio. Arguing about it
    // is what tile_probe.res.csv is for.
    // ------------------------------------------------------------------------
    reg [ACCT-1:0] accram [0:ADEPTH-1];
    reg [WGTT-1:0] wbuf   [0:WDEPTH-1];

    // `strip` is declared down with its own always block rather than here,
    // because at KPACK = 1 it is two arrays and not one. See "the strip, banked
    // even and odd" below - the bit count does not move and the block count is
    // not supposed to either.

    // ------------------------------------------------------------------------
    // Weight stream assembly. Bytes arrive lane 0 first, so shifting the word
    // right by 8 and dropping each new byte in at the top leaves byte n in lane
    // n once NMAC of them have landed - no per-byte address arithmetic, and the
    // committed word is the same expression the shift register is about to take.
    // ------------------------------------------------------------------------
    reg [WGTT-1:0] wgt_sr;
    reg [BW-1:0]   wgt_j = {BW{1'b0}};
    reg [WA-1:0]   wgt_a = {WA{1'b0}};

    // M14's P3: expand at the write port, keep the storage. `wbuf` stays
    // WGTT bits wide and holds sign-extended bytes whatever the wire carried,
    // so the read side - `wreg`, the `w_j` fan-out, the multiplier operand, the
    // Logic-Level-0 path M10 measured - is bit-for-bit the design that has been
    // shipping since M6b. Nothing here is on that path: this is the byte-a-cycle
    // load side, which runs at one eighth of the sweep's rate and has never been
    // near critical.
    //
    // Both nibbles are sign-extended to 8 bits, which is what makes the read
    // side unchanged: an int4 weight fed to the same 10x8 multiplier produces
    // the identical product, so tb_gemm's golden values need no separate arm.
    wire [7:0] w4_lo = {{4{wgt_data[3]}}, wgt_data[3:0]};
    wire [7:0] w4_hi = {{4{wgt_data[7]}}, wgt_data[7:4]};

    // At WNIB = 1 the storage is already nibbles and the stream is already
    // halved, so there is nothing to expand; the parameter and the CFG bit are
    // two answers to the same question and only one of them can be asked at a
    // time. WNIB = 1 exists to keep the APACK = 2 gate buildable, and this
    // constant folds it out of every shipping configuration.
    wire w4_on = (WNIB == 0) && cfg_w4;

    localparam [BW-1:0] BLAST4 = (WBYTES / 2) - 1;
    wire [BW-1:0] wgt_last = w4_on ? BLAST4 : BLAST;

    // Two bytes in per byte on the wire, so the word fills in half the trips.
    // Byte b lands in lanes 2b (low nibble) and 2b+1 (high) - which is exactly
    // gb_weights()'s packing, and the reason it packs that way and not the
    // other.
    wire [WGTT-1:0] wgt_next   = w4_on ? {w4_hi, w4_lo, wgt_sr[WGTT-1:16]}
                                       : {wgt_data, wgt_sr[WGTT-1:8]};
    wire            wgt_commit = wgt_we && !wgt_rst && (wgt_j == wgt_last);

    always @(posedge clk) begin
        if (wgt_rst) begin
            wgt_j <= {BW{1'b0}};
            wgt_a <= {WA{1'b0}};
        end else if (wgt_we) begin
            wgt_sr <= wgt_next;
            if (wgt_j == wgt_last) begin
                wgt_j <= {BW{1'b0}};
                wgt_a <= wgt_a + 1'b1;
            end else begin
                wgt_j <= wgt_j + 1'b1;
            end
        end
    end

    // ------------------------------------------------------------------------
    // Tap and block walk. Loop order is (k, g, p): for each im2col tap, for each
    // channel group, sweep every position.
    //
    // (k,g,p) and not (k,p,g) because the innermost loop must cost ONE
    // activation read per cycle. With g innermost, each cycle would need NMAC
    // weight bytes *and* would re-read the same activation NMAC times; with p
    // innermost the NMAC weights are fetched once per sweep into registers and
    // held, and the only per-cycle memory traffic is a single strip byte plus
    // the accumulator RMW. That is what makes an 8-lane array feasible on 24
    // memory blocks.
    // ------------------------------------------------------------------------
    reg [KW-1:0] t_k;
    reg [1:0]    t_kx, t_ky;

    // M16: the odd step. The kernel is 3 wide and always has been - t_kx is a
    // two-bit counter that wraps at 2 - so pairing leaves exactly one unpaired
    // tap per (ky, ic), and it is the one at kx = 2.
    wire kp_tail = (t_kx == 2'd2);
    reg [GW-1:0] t_g;
    reg [PW-1:0] t_p;
    reg [WA-1:0] wcnt;
    reg [2:0]    flush_c;
    reg          init_pass;

    // Strip base of the current tap, accumulated rather than multiplied - see
    // the header of im2col_feed.v, which has no multiplier for the same reason
    // this does not: all eight belong to the MAC array. base_ic is
    // ic_local*strip_ch and base_ky is ky*strip_rw, each moving by one add when
    // its counter ticks. There is no separate ic counter because base_ic *is*
    // the only thing the datapath wants from it.
    reg [AW-1:0] base_ic, base_ky;
    wire [AW-1:0] ld_base = base_ic + base_ky;

    // Loop limits, decremented once instead of on every comparison. cfg_* are
    // written by a CFG frame and are stable for thousands of cycles before RUN,
    // so a free-running register is safe and costs one adder each rather than
    // one adder per compare site.
    reg [KW-1:0] k_m1;
    reg [GW-1:0] qg_m1;
    reg [GW:0]   qg2;                   // RQ: 2*cfg_QG, the pair stride
    reg [PW-1:0] p_m1, p_m2;
    reg          p_one;

    // The three loop-boundary flags are REGISTERED, not wires. As wires they
    // were `t_k == cfg_K - 1'b1` and friends: a subtract plus a KW-bit compare
    // sitting between cfg_K's flops and the clock enables of t_g / t_p / state,
    // five levels of logic and - once gemm_link's R_PAY path was fixed - the
    // critical path of the whole design at 18.9 ns. Registering them splits that
    // into two short hops. See the assignments at the top of the control block.
    reg last_tap, last_grp, last_pos;

    // Which of the four things S_FLUSH can do, decided before it has to be
    // acted on. Spelled out at the point of use, base_ic's clock enable was
    // `state == S_FLUSH && flush_c == 0 && last_grp && !last_tap && t_kx == 2
    // && t_ky == 2`: four LUT levels, five nets, 15.4 ns of pure routing from
    // state[1] to a 12-sink enable at the far corner of the die. Everything
    // after `flush_c == 0` in that expression is stable for the whole of
    // S_FLUSH - its inputs only move on the advance cycle itself, and the next
    // advance is FLUSH+1 cycles plus a whole p sweep away - so a free-running
    // register carrying a one-cycle-old value is exact here, and it collapses
    // the enable to two terms.
    //
    // fl_now is the single S_FLUSH cycle on which the loop advances, taken from
    // flush_c == 1 the cycle before. Every branch below leaves S_FLUSH on that
    // cycle, so it is high exactly once per sweep.
    reg fl_now = 1'b0;
    reg fl_g, fl_k, fl_ky, fl_ic;

    // ---- im2col address generator ------------------------------------------
    wire          feed_load = (state == S_LOAD);
    wire          feed_step = (state == S_SWEEP);
    wire [AW-1:0] feed_addr;
    wire          feed_zero;

    im2col_feed #(.AW(AW), .CW(CW)) u_feed (
        .clk          (clk),
        .cfg_H        (cfg_H),
        .cfg_W        (cfg_W),
        .cfg_OW       (cfg_OW),
        .cfg_stride2  (cfg_stride2),
        .cfg_strip_rw (cfg_strip_rw),
        .load         (feed_load),
        .ld_oy        (cfg_oy0),
        .ld_ox        (cfg_ox0),
        .ld_ky        (t_ky),
        .ld_kx        (t_kx),
        .ld_base      (ld_base),
        .step         (feed_step),
        .addr         (feed_addr),
        .zero         (feed_zero)
    );

    // ---- M16: the second tap's address ---------------------------------------
    // A whole second feed rather than `feed_addr + 1`, for two reasons and the
    // first is the one that matters. **An incrementer in front of the strip's
    // read address is exactly the edit gemm_tile has been avoiding** - see the
    // note above `strip_raddr`, where a 2:1 mux was placed on the address rather
    // than on the data on purpose. u_feed1's output is already registered inside
    // the feed, so the bank select downstream is a mux and never an adder.
    //
    // The second is that padding is per-tap: kx and kx+1 have independent `zero`
    // flags at the row edges, and the feed is the thing that knows. 77 FFs and
    // 133 LUTs (res.csv) is a cheap way to not get that wrong.
    //
    // ld_kx wraps 2 -> 3 on the odd tail, which is out of the kernel. The
    // address it produces is unused: kp_tail forces the second lane's zero.
    wire [AW-1:0] feed_addr1;
    wire          feed_zero1;

    generate
    if (KPACK != 0) begin : kp_feed
        im2col_feed #(.AW(AW), .CW(CW)) u_feed1 (
            .clk          (clk),
            .cfg_H        (cfg_H),
            .cfg_W        (cfg_W),
            .cfg_OW       (cfg_OW),
            .cfg_stride2  (cfg_stride2),
            .cfg_strip_rw (cfg_strip_rw),
            .load         (feed_load),
            .ld_oy        (cfg_oy0),
            .ld_ox        (cfg_ox0),
            .ld_ky        (t_ky),
            .ld_kx        (t_kx + 2'd1),
            .ld_base      (ld_base),
            .step         (feed_step),
            .addr         (feed_addr1),
            .zero         (feed_zero1)
        );
    end else begin : no_kp_feed
        assign feed_addr1 = {AW{1'b0}};
        assign feed_zero1 = 1'b1;
    end
    endgenerate

    // ------------------------------------------------------------------------
    // Datapath pipeline.
    //
    //   cycle t   : feed presents addr for position p; registered into s1
    //   cycle t+1 : strip and accumulator reads issued from s1
    //   cycle t+2 : strip_q / acc_q valid; the multiplier operand is formed
    //   cycle t+3 : products registered into s3
    //   cycle t+4 : accumulate adder runs; sum registered into s4
    //   cycle t+5 : accumulator write commits
    //
    // Stage 2b - the operand register - is there for placement, not for logic.
    // The strip BRAM sits at the top of the die and the eight multipliers sit at
    // the bottom, and `a_ext` fans out to all eight A ports: 81 sinks, 93 rows
    // of Manhattan distance, 6.141 ns on one net. Feeding the multipliers
    // straight from the memory made that hop share a clock period with the
    // 5.264 ns BRAM read and the sign-extension LUTs, 17.994 ns in total against
    // a 13.333 ns budget. Registering the operand lets the placer put the flop
    // between the two and gives each half its own period. The DSP's own A_REG
    // could not do this - it is inside the multiplier, at the far end of exactly
    // the net that is slow.
    // ------------------------------------------------------------------------
    reg [AA-1:0]   acc_addr;   // accumulator address of the current position

    reg            s1_val = 1'b0, s1_zero, s1_init;
    reg [AW-1:0]   s1_saddr;
    reg [AA-1:0]   s1_aaddr;

    reg            s2_val = 1'b0, s2_zero, s2_init;
    reg [AA-1:0]   s2_aaddr;
    // The strip's output register. At KPACK = 0 sq_a *is* what used to be called
    // strip_q and sq_b does not exist; at 1 they are the even and odd banks and
    // s2_par says which of them holds the first tap. See `strip_q` below.
    reg [7:0]      sq_a, sq_b;
    reg [ACCT-1:0] acc_q;
    reg [WGTT-1:0] wreg;

    reg               s2b_val = 1'b0, s2b_init;
    reg [AA-1:0]      s2b_aaddr;
    reg [ACCT-1:0]    s2b_accq;
    reg signed [9:0]  a_q;

    // ---- M16: the second tap, riding the same stages -------------------------
    // s1_saddr1 is u_feed1's address, s2_zero1 its padding flag ORed with the
    // odd tail. s2_par is the low bit of the *first* address, registered
    // alongside s2_zero, and it is what un-swaps the two banks.
    //
    // a_q1 is the second tap's operand register, the exact counterpart of a_q
    // and clocked on the same edge. An earlier revision did without it, folding
    // the first half of the LE multiplier into stage 2 so that what crossed the
    // s2 -> s2b boundary was a pair of partial sums; that saved a stage and cost
    // the milestone, because it left the RAM's clock-to-out, the parity swap,
    // the sign extend, the tap select and two 18-bit adds in one period. See
    // le_mul_lo / le_mul_hi for the numbers. The multiplier now lives entirely
    // in stage 2b and stage 2c is what pays for it.
    //
    // All are declared unconditionally and are dead at KPACK = 0: the banks
    // below never read s2_par, nothing drives wreg1, and synthesis removes them
    // along with u_feed1.
    reg [AW-1:0]      s1_saddr1;
    reg               s1_zero1;
    reg               s2_zero1;
    reg               s2_par;
    reg [WGTT-1:0]    wreg1;
    reg [WGTT-1:0]    wreg1_r;   // the LE multiplier's copy - see wbuf_two
    reg signed [9:0]  a_q1;

    // ---- M16: stage 2c -------------------------------------------------------
    // The extra stage, and it carries only these three bits plus the lanes' own
    // registers. There is deliberately no s2c_accq: the 256-bit accumulator word
    // is realigned by reading `accram` one cycle later instead - see acc_raddr -
    // which costs an address mux input rather than 256 flops. s2b_accq therefore
    // holds the issue's value during 2c at KPACK = 1 and during 2b at 0, and the
    // one assignment `s3_accq <= ... s2b_accq` is right in both.
    reg               s2c_val = 1'b0, s2c_init;
    reg [AA-1:0]      s2c_aaddr;

    // The un-swap, declared here rather than beside the banks that drive it
    // because a_ext reads strip_q several hundred lines above them and Verilog
    // wants the declaration first. At KPACK = 0 strip_q is a rename of sq_a -
    // the ternary is constant-folded - and strip_q1 has no readers.
    wire [7:0] strip_q  = (KPACK == 0) ? sq_a : (s2_par ? sq_b : sq_a);
    wire [7:0] strip_q1 = s2_par ? sq_a : sq_b;

    // No s3_init: the k = 0 case is folded into s3_accq below, so stage 3 has
    // nothing left to decide.
    reg            s3_val = 1'b0;
    reg [AA-1:0]   s3_aaddr;
    reg [ACCT-1:0] s3_accq;
    reg [ACCT-1:0] s3_prod;

    // Stage 4 - the writeback register, and like stage 2b it exists for the
    // wire, not for the work. Three of four placement seeds put the same family
    // of paths on top: DSP output, 4.720 ns of net to the accumulate adder,
    // 3.9 ns of 32-bit carry chain, then 3.554 + 3.397 ns of net to the memory's
    // write port - 16.7 ns for one add. Splitting after the adder gives the
    // arithmetic its own period and leaves the memory a register beside it.
    //
    // The cost is one more cycle of read-modify-write depth, which FLUSH pays
    // for. It is safe for the same reason the shallower pipeline was: within a
    // sweep every cycle addresses a different accumulator, and the next visit
    // to any one of them is a whole LOAD + P-cycle SWEEP + FLUSH away.
    reg            s4_val = 1'b0;
    reg [AA-1:0]   s4_aaddr;
    reg [ACCT-1:0] s4_wr;

    // ---- drain walk ---------------------------------------------------------
    reg [GW-1:0] d_g;
    reg [JW-1:0] d_j;
    reg [PW-1:0] d_p;
    reg [AA-1:0] d_addr;

    // RQ only. The engine takes two positions at a time - see "Two codes per
    // schedule" below - so the second one needs an address of its own. It is a
    // register and not `d_addr + cfg_QG` formed in the read path: acc_raddr
    // already carries a 2:1 in front of a memory whose clock-to-out M10
    // measured at 5.264 ns, and an adder there would be most of a cycle.
    reg [AA-1:0] d_addr2;

    // RQ only, and it exists because RQ moves the walk's advance off the accept
    // cycle - see the prefetch note above S_DWAIT. Once the counters run ahead
    // of the word on the wire, "was that the last one" is no longer a question
    // the accept cycle can ask, so it is answered when the word is latched and
    // carried here. At RQ = 0 nothing reads it and it has no driver.
    reg d_end = 1'b0;

    wire last_pos_d = (d_p == p_m1);

    // RQ only: (d_p, d_p+1) is the channel's last pair. gb_geom() refuses rq
    // for an odd P, so d_p is even, p_m1 is odd, and this is exact rather than
    // a bound. p_m2 already exists - S_FLUSH's last_pos uses it.
    wire last_pair_d = (d_p == p_m2);

    wire drain_mode = (state == S_DISS) || (state == S_DWAIT) ||
                      (state == S_DHOLD) ||
                      ((DPIPE != 0) && (state == S_DMUX));

    // Assigned in the M15 block below. Declared here because acc_raddr is the
    // one existing path this milestone edits, and the edit belongs beside the
    // walk it reads. At RQ = 0 it is a constant zero and the mux folds away.
    wire rq_second;

    // The run-mode source is one stage later at KPACK = 1. Stage 2c pushed the
    // product a cycle further out, and the accumulator operand has to arrive
    // with it; issuing the read from s2_aaddr rather than s1_aaddr does that
    // without a fourth 256-bit register on the read side. Both are registers, so
    // this is a select and not an adder - the thing d_addr2 exists to avoid.
    wire [AA-1:0] acc_raddr = drain_mode ? (rq_second ? d_addr2 : d_addr)
                            : (KPACK == 0) ? s1_aaddr : s2_aaddr;

    // ---- DPIPE: the two paths M10 Stage 0 measured -------------------------
    //
    // Four place-and-route seeds put gemm_tile's standalone Fmax at 66 +/- 3
    // MHz, and every one of the four named a path in this walk - never the MAC
    // array. Two distinct paths, and both have to go or the other becomes the
    // limiter and the re-measurement says nothing:
    //
    //   1. `accram` read data -> the lane mux -> `dout`. 14.900 ns, of which
    //      5.264 is the RAM's clock-to-out and **5.579 ns is one net**: accram
    //      places at (57,42) and its consumer at (43,98), 70 units of Manhattan
    //      distance. `acc_q2` below splits that net with a flop. Registering
    //      *after* the mux would not have worked - the mux is the two LUT
    //      levels the report counts, so the long net would still be in front of
    //      it.
    //
    //   2. `d_g`/`d_p`/`d_j`'s clock enable. Seeds 7 and 13 reported this at
    //      ~14.3 ns. S_DHOLD's nested ifs build each enable from a five-term
    //      AND - `state`, `dout_ready`, the p compare, the j compare, the g
    //      compare - whose terms are scattered across the die, and `dout_ready`
    //      arrives from outside the module. The three compares are loop-carried
    //      state that cannot change between S_DISS and S_DHOLD, so they are
    //      resolved a cycle early into `r_last_*` and the enable collapses to
    //      state AND dout_ready.
    //
    // The cost is one cycle per drained word. It is invisible: the return path
    // is one bit wide, so the link spends ~32 clocks shifting each word out and
    // the walk is idle in S_DHOLD for all but a handful of them. DRAIN's 153 ms
    // in the M7h census is bounded by the serializer, not by this FSM.
    //
    // **Result: it worked, and it did not matter.** Three seeds went 67.0 /
    // 71.4 / 71.2 MHz against 66.5 / 69.8 / 63.9 / 64.0 before - a 4 MHz mean
    // gain, at the noise floor build.sh documents. Two of the three then named
    //
    //     wbuf|RDATA  --5.264ns-->  net  --6.802ns-->  mult_18x18|B
    //
    // at **Logic Level 0**: a memory block's clock-to-out, one wire, and a hard
    // multiplier's input setup. There is no logic left in it to pipeline, and
    // 5.264 + 2.716 = 7.98 ns of fixed macro delay caps this fabric at 125 MHz
    // before a single millimetre of routing - on a die where u_tile holds 21 of
    // the 24 memory blocks, so the placer cannot shorten that 52-unit hop to
    // the multiplier either. That measurement is what closed M10; see the M10
    // section of docs/milestones.md.
    reg [ACCT-1:0] acc_q2   = {ACCT{1'b0}};
    reg            r_last_p = 1'b0;
    reg            r_last_j = 1'b0;
    reg            r_last_g = 1'b0;
    reg            r_last_pp = 1'b0;    // RQ: last *pair*, see last_pair_d

    // At DPIPE = 0 each of these is literally the expression it replaced, so
    // the synthesized netlist is unchanged.
    wire [ACCT-1:0] d_src    = (DPIPE != 0) ? acc_q2   : acc_q;
    wire            d_lastp  = (DPIPE != 0) ? r_last_p : last_pos_d;
    wire            d_lastj  = (DPIPE != 0) ? r_last_j : (d_j == JLAST);
    wire            d_lastg  = (DPIPE != 0) ? r_last_g : (d_g == qg_m1);

    // ---- M15: the requantize epilogue ---------------------------------------
    //
    // Everything below is dead at RQ = 0 - `rq_on` is a constant zero, so the
    // two muxes that reach the existing datapath fold to the expression they
    // replaced and nothing else has a load.
    //
    // ## Where the parameters live, and why they cost nothing
    //
    // Requantize needs {bias, M, s} per output channel, and test_plan gives
    // Q <= 32 for every code-emitting layer (conv7's Q = 128 is the float one).
    // 32 entries x 48 bits is 1,536 bits, and the three obvious homes for it
    // are all expensive:
    //
    //   - **registers**: ~1,536 FFs plus a 32:1 mux 48 bits wide, so roughly
    //     2,200 LEs of the 4,771 gemm_top leaves free. M16 wants those LEs for
    //     MACs, which is the entire reason M16 is ordered behind this.
    //   - **its own memory block**: 21 -> 22 of 24, and M16 also needs `wbuf`
    //     4 -> 7, which is 24/24 on its own. Spending a block here makes M16's
    //     problem strictly harder.
    //   - **packed into `wbuf`'s spare bits**: free if Efinity maps 256x80 onto
    //     the same four blocks as 256x64, and 5/24 if it does not. A gamble.
    //
    // So they live in **`strip`**, which is already there and already idle.
    // The array is 2,048 bytes; the largest block the plan builds is conv2's
    // Cb 8 x 6 rows x 32 = 1,536, so the top 256 bytes are unused in every
    // blocking - and even if they were not, RUN has finished consuming the
    // strip by the time DRAIN starts. They arrive through the strip's existing
    // write port, so this costs no memory block and no second port; the only
    // thing it costs is a command code, because ACT always addresses from zero
    // (gemm_link.v's R_PAY writes `act_addr <= pcnt`) and cannot reach 1792.
    //
    // Six bytes per channel, LSB first, so the assembly register is the same
    // shift-right trick `wgt_sr` uses:
    //
    //     byte 0..2   bias[23:0]   (|bias| <= 277,460 over the exported model)
    //     byte 3..4   M[15:0]
    //     byte 5      {s[5:0], M[17:16]}
    //
    // ## The multiplier is serial, and it is serial because it can be
    //
    // 28x18 combinationally is ~1,000 LEs and one of the eight hard
    // multipliers is not available - all eight are committed to the MAC array,
    // and M10 measured the survivor path as `wbuf|RDATA -> net -> mult|B` at
    // Logic Level 0, so muxing a multiplier input is the one edit in this file
    // with no slack. A serial radix-8 walk is ~300 LEs, touches neither, and
    // there is room for it: at one byte per accumulator the link spends 8
    // clocks per code where it used to spend 32.
    //
    // Six steps of three bits each, MSB first. The odd multiples 3a/5a/7a are
    // precomputed once per word in one adder level, so selecting a partial
    // product is an 8:1 mux and nothing more.
    //
    // ## Two structural choices, both bought with measurements
    //
    // The first cut of this engine did the obvious thing - mux the partial
    // product and accumulate it in the same cycle - and the tile_probe build
    // priced it at **50.4 MHz against the control's 67.0**, on a path reported
    // as `rq_a3[7] -> 3 LUT levels -> add_643 -> rq_p[48]`, 19.7 ns. The two
    // halves were 10.4 ns of routing through the mux and 6.1 ns of carry chain
    // across 42 bits. Efinity's model puts essentially all of the delay in
    // nets - every LUT above reports 0.000 - so what a cycle can afford is
    // roughly **four hops**, and that one had seven plus a carry chain.
    //
    // So the loop is split into two paths that each end at a flop:
    //
    //   - **the mux runs a cycle ahead.** `rq_pp` holds the partial product for
    //     digit i while the accumulator absorbs digit i-1. Throughput is still
    //     one digit per cycle; it costs one extra step, not one per digit.
    //   - **the accumulator is carry-save.** `rq_ps + rq_pc` is the running
    //     product, and a step is a 3:2 compressor: one LUT for the sum bit, one
    //     for the majority, no carry chain at all. The single carry-propagate
    //     add is hoisted out of the loop into RQ_RES, where it is alone in its
    //     cycle. Redundant form survives the `<< 3` because dropping the top
    //     three bits of each word is arithmetic mod 2^50, and the true partial
    //     sums are bounded by 2^44.
    //
    // The clamp is the other thing that does not fit. `rq_p >>> s` as written
    // is a 6-level barrel shifter 50 bits wide, and the saturation test on top
    // of it is another OR tree. Both are avoided rather than pipelined: the
    // overflow test becomes **one comparison against `256 << s`**, which is a
    // 1-level decoder feeding a carry chain and runs *beside* the shifter, and
    // the shifter itself only has to produce **15 bits** - coarse by 8·s[5:3],
    // then fine by s[2:0] - because everything above bit 7 of the result has
    // already been answered by that comparison.
    //
    // ## Two codes per schedule, because the wire cannot wait
    //
    // The first Stage 2 cut ran one code per schedule and did not work, for a
    // reason worth writing down: **gemm_link has no back-pressure on the return
    // path.** Its T_DATA arm reaches for a word every eight link clocks and the
    // `!have` branch is not a stall, it sets `underrun` and abandons the frame
    // (gemm_link.v, T_RXC -> T_DATA). So the engine's period, not the wire's,
    // has to be the shorter of the two - and a single engine's is sixteen
    // clocks against the wire's eight. The probe said so before the arithmetic
    // did: 16 clocks between dout_valid rises, underrun on the third code.
    //
    // A single engine cannot be trimmed to eight. Fixed overhead is BIAS, PRE,
    // MUX0 in front of the six radix-8 compressions and RND, RES, SH1, SH2
    // behind them - eight cycles outside the multiply, each of them a cycle
    // because Stage 1 measured what happens when they are not.
    //
    // So the datapath is built twice and the schedule is not. The drain walk is
    // position-innermost, so codes 2k and 2k+1 are two *positions of the same
    // output channel* - identical bias, M and s. That is the whole reason this
    // is cheap: one parameter store, one strip fetch, one step counter, one
    // decode of `1 << (s-1)` and `256 << s`, and two copies of the adder, the
    // partial-product mux, the carry-save accumulator and the shifters.
    // gemm_rq_lane at the bottom of this file is that copy, instantiated twice.
    //
    // Lane 1 runs exactly one step behind lane 0 - `k = rq_c - e` - which costs
    // nothing and buys two things: the two accumulator reads are on consecutive
    // cycles of a single port, and lane 1's code lands the cycle after lane 0's,
    // which is 15 clocks before the wire asks for it.
    //
    // Fifteen clocks a pair against the wire's sixteen. The margin is one clock
    // a pair, so the codes go into a **two-slot output buffer** and the engine
    // stalls at RQ_DONE rather than racing: four bytes of slack absorb the
    // start-up transient, and after that the engine is never the thing being
    // waited for. 161 x 8/32 is **about 42 ms**, the wire's own floor, which is
    // the number the plan projected.
    //
    // The one thing that would break it is a channel change costing six extra
    // cycles for a fresh parameter fetch, so it does not: rq_par is dead after
    // RQ_PRE and the strip port is idle all through a readout, so the next
    // channel's six bytes are fetched *during* the current pair, at RQ_PF0.
    // Only the first pair of a readout pays, and there the link's preamble and
    // status give the tile a forty-clock head start.
    //
    // The pairing needs an even P. gb_geom() refuses rq otherwise, which is the
    // same place it already refuses Q > 32 and a strip that reaches RQBASE.
    // RQBASE is `STRIPD - 256` and not `STRIPD - RQB*RQMAX`, which would be
    // 1856. The table is still 192 bytes; the extra 64 buy the *link* an
    // address. 1792 is 11'b111_0000_0000, so gemm_link's CMD_RQP write address
    // is the concatenation {RQBASE[AW-1:8], pcnt[7:0]} - no adder, no carry
    // chain, nothing on a path that today is a plain register copy. At 1856
    // (0x740) the base collides with a 0..191 offset at bits 6 and 7 and the
    // link would need a real 8-bit add in R_PAY.
    //
    // The dead-space argument survives the move: the largest block gemm_plan
    // builds is conv2's 1,536 bytes, and gb_rqp() rejects anything above
    // GB_RQBASE when rq is on, so the two never overlap.
    localparam integer RQMAX   = 32;                    // channels per block
    localparam integer RQB     = 6;                     // bytes per entry
    localparam integer RQBASE  = STRIPD - 256;          // 1792 at STRIPD = 2048

    // Step numbers, spelled out because the schedule is the whole engine. The
    // numbers below are lane 0's; lane 1 sees `rq_c - 1` and does the same
    // things one cycle later.
    //
    // 0..7 is the prologue and runs once per readout, not once per channel: it
    // is the only place a parameter fetch is not already done, and the only
    // place the first pair's accumulators have not already been read.
    localparam [4:0] RQ_FRESH = 5'd0,   // 0..5   six strip reads, this channel
                     RQ_PACC  = 5'd3,   // 3      prologue: address position p
                     RQ_PSEL  = 5'd4,   // 4      address p+1; lane 0 latches
                     RQ_PADV  = 5'd6,   // 6      step the walk to the next pair
                                        // 7      slack: rq_par lands at 6
                     RQ_BIAS  = 5'd8,   // 8      a <- acc + bias
                     RQ_PRE   = 5'd9,   // 9      3a, 5a, 7a; the two decodes
                     RQ_MUX0  = 5'd10,  // 10     the first partial product
                     RQ_MUL0  = 5'd11,  // 11..16 six radix-8 compress steps
                     RQ_PF0   = 5'd16,  // 16..21 next channel's six strip reads
                     RQ_RND   = 5'd17,  // 17     compress in 2^(s-1)
                     RQ_RES   = 5'd18,  // 18     ps + pc, the one carry chain
                     RQ_ACC   = 5'd19,  // 19     address the next pair's p
                     RQ_SH1   = 5'd19,  // 19     coarse >> 8*s[5:3]; sign, sat
                     RQ_SEL   = 5'd20,  // 20     address p+1; lane 0 latches
                     RQ_SH2   = 5'd20,  // 20     fine >> s[2:0]
                     RQ_LAST  = 5'd21,  // 21     lane 0's code is valid
                     RQ_DONE  = 5'd22;  // 22     lane 1's too; park, hand over

    wire rq_on = (RQ != 0) && cfg_rq;

    reg  [4:0]  rq_c    = RQ_DONE;      // engine step; parked when idle
    reg  [47:0] rq_par  = 48'd0;        // {s, M, bias} for the current channel
    reg  [5:0]  rq_s    = 6'd0;
    reg  [49:0] rq_rcq  = 50'd0;        // 1 << (s-1), decoded at RQ_PRE
    reg  [50:0] rq_limq = 51'd0;        // 256 << s, likewise
    reg  [AW-1:0] rq_qb = RQBASE[AW-1:0];   // strip base of the current channel
    reg  rq_newch = 1'b0;               // the next pair starts a new channel

    // The two-slot output buffer. A slot is one pair - `{code(p+1), code(p)}` -
    // and the readout's last code is flagged on the slot rather than counted at
    // the far end, because by the time a byte is accepted the engine is three
    // pairs past it.
    reg  [15:0] rq_slot [0:1];
    reg  [1:0]  rq_sf = 2'd0;           // slot holds a pair
    reg  [1:0]  rq_se = 2'd0;           // ... and it is the last one
    reg  rq_wsel = 1'b0, rq_rsel = 1'b0;
    reg  rq_half = 1'b0;                // 0 = next byte out is code(p)
    reg  rq_end_c = 1'b0;               // last-pair flag, fetch stage -> compute

    // S_DHOLD is in here, and that is the whole overlap: the engine keeps
    // stepping while the previous byte is on the wire. It parks at RQ_DONE on
    // its own, so nothing needs a done flag.
    wire rq_busy = (state == S_DISS) || (state == S_DWAIT) ||
                   (state == S_DHOLD);
    wire rq_run  = rq_on && rq_busy;

    // The prologue's fetch, and the prefetch that replaces the per-channel one.
    // RQ_PF0 is 16 so that `rq_c[2:0]` counts 0..5 over 16..21 exactly as it
    // does over the prologue's 0..5 - the address is still one add of a
    // three-bit slice and not a counter with a clock enable, which is what the
    // third gate build reported as its worst path.
    wire rq_pf   = rq_newch && (rq_c >= RQ_PF0) && (rq_c < RQ_DONE);
    wire rq_ld   = rq_run && ((rq_c < 5'd6) || rq_pf);
    // strip_q lags its address by a cycle, so the six captures are the six
    // issues delayed by one. Deriving them from rq_ld rather than from a second
    // compare on rq_c is not tidiness: it is what keeps the two fetch windows
    // from needing two range tests.
    reg  rq_ldq = 1'b0;

    wire [AW-1:0] rq_pa = rq_qb + {{(AW-3){1'b0}}, rq_c[2:0]};

    // The second accumulator of the pair is addressed on the cycle lane 0
    // latches the first. Both windows, prologue and steady state, are one
    // cycle wide and the default is d_addr.
    //
    // It is a **register**, decoded one step early, and the first gate build of
    // the two-lane engine is why. Written as a comparison on rq_c it measured
    // `state[1] -> 3 LUT levels -> rq_second -> 2 more -> accram|RADDR`, five
    // levels and 18.5 ns, the worst path in the design - a decode sitting in
    // front of a memory whose address has to be stable at the clock edge. rq_c
    // is a counter, so what it will be next cycle is known now: RQ_PSEL - 1 and
    // RQ_SEL - 1 are both inside the schedule's free-running stretch, where the
    // next step is always this one plus one.
    reg rq_sec_q = 1'b0;
    assign rq_second = rq_sec_q;

    // The only edit to an existing path: one 2:1 on the strip read address.
    // Not on `strip_q`, which feeds the sign-extend into the multiplier operand
    // and is the path stage 2b exists to protect - an address mux is in front
    // of the memory, where a LUT costs a period the RAM's 5.264 ns clock-to-out
    // is not already spending.
    wire [AW-1:0] strip_raddr = rq_ld ? rq_pa : s1_saddr;

    // Bias is 24-bit signed and the accumulator 32, so `a` could in principle
    // wrap 28 bits. It does not: |acc| <= 1728*255*127 = 55,961,280 and
    // |bias| <= 277,460, so |a| <= 56,238,740 < 2^26 - 27 signed bits, carried
    // in 28. That bound is what lets the partial products be 31 bits and the
    // product 2^26 * 2^18 = 2^44, which is why the carry-save words can be
    // shifted left three and truncated without losing anything.
    wire signed [27:0] rq_bias = {{4{rq_par[23]}}, rq_par[23:0]};

    // The lane-muxed accumulator word, shared by both lanes: they read it on
    // consecutive cycles, from consecutive addresses, and d_j is the same for
    // both because a pair never crosses a channel.
    wire signed [31:0] rq_accw = $signed(acc_q[32*d_j +: 32]);

    // ## The step is decoded once, into flops, and the lanes are told
    //
    // The first two-lane gate build gave 55 +/- 2 MHz against the single lane's
    // 61 +/- 2, and the three seeds disagreed about which path was worst while
    // agreeing about where it started: **every one of them began at `rq_c`**
    // and ended at some flop's D or CE - `u_rq0/pc[42]|D`, `rq_slot[1][4]|CE`,
    // `u_rq0/ps[33]|CE`. That is one cause and not three. A five-bit compare is
    // two LUT levels on a LUT4 device, the schedule has nine of them, and with
    // two lanes each one is built twice and reached over a longer net.
    //
    // So `rq_c` is decoded here, once, into nine registered strobes, and the
    // lanes take those instead of a step number. Each is a flop driving a clock
    // enable directly: zero levels where there were two, and `rq_c`'s fanout
    // drops from every comparator in both lanes to this one decode.
    //
    // The strobes are decoded a step early - `rq_c == C - 1` for a thing that
    // happens at C - which is free because rq_c is a counter. The one step that
    // is not a counter step is the jump out of RQ_DONE, and RQE_BIAS carries it
    // as an explicit `|| rq_take` term.
    //
    // **Lane 1 is lane 0's strobes through one flop.** That is the whole
    // interleave now: it used to be `k = rq_c - 1` and a subtractor, and it is
    // now nine registers, which is both cheaper and exactly the same thing.
    localparam integer RQE_ACC  = 0,    // latch acc_q into the lane
                       RQE_BIAS = 1,
                       RQE_PRE  = 2,
                       RQE_MUX  = 3,    // partial-product mux, one step ahead
                       RQE_MUL  = 4,    // 3:2 compress
                       RQE_RND  = 5,
                       RQE_RES  = 6,
                       RQE_SH1  = 7,
                       RQE_SH2  = 8;

    reg [8:0] rq_e0 = 9'd0;             // lane 0's strobes
    reg [8:0] rq_e1 = 9'd0;             // lane 1's, one step behind

    wire [7:0] rq_code0, rq_code1;

    gemm_rq_lane u_rq0 (
        .clk(clk), .e(rq_e0),
        .acc_w(rq_accw), .bias(rq_bias), .mm(rq_par[41:24]), .ss(rq_s),
        .rcq(rq_rcq), .limq(rq_limq), .code(rq_code0));

    gemm_rq_lane u_rq1 (
        .clk(clk), .e(rq_e1),
        .acc_w(rq_accw), .bias(rq_bias), .mm(rq_par[41:24]), .ss(rq_s),
        .rcq(rq_rcq), .limq(rq_limq), .code(rq_code1));

    // ---- multiply ------------------------------------------------------------
    // The activation is signed or unsigned depending on the layer, and taking
    // that from configuration rather than from the layer index matters: reading
    // the wrong one is a factor-of-two error that still produces a
    // plausible-looking tensor. Zero-injection for out-of-range taps lands here,
    // so a padded tap costs a multiply by zero and no branch - which is what
    // keeps this a fixed-latency pipeline with no stalls.
    wire signed [9:0] a_ext =
        s2_zero         ? 10'sd0 :
        cfg_unsigned_in ? $signed({2'b00, strip_q})
                        : $signed({{2{strip_q[7]}}, strip_q});

    // The second tap's operand, same three-way select over the other bank. Both
    // of these now carry the s2_par swap inside `strip_q`/`strip_q1`, which is
    // the point: the extra mux is in this cone, between the memory's output
    // register and the a_q flop, and NOT between a_q and the multiplier - the
    // hop that stage 2b exists to keep clear.
    wire signed [9:0] a_ext1 =
        s2_zero1        ? 10'sd0 :
        cfg_unsigned_in ? $signed({2'b00, strip_q1})
                        : $signed({{2{strip_q1[7]}}, strip_q1});

    // ---- M16: the LE multiplier ----------------------------------------------
    // Written as a shift-add and not as `a * w`, because all eight DSPs are
    // committed and inference with sixteen multiplies would either overflow the
    // device or decide on its own which eight lanes lose their hard multiplier.
    //
    // Eight partial products of the sign-extended activation, the top one
    // subtracted because w[7] is w's sign bit: a*w = sum(w[i]*a*2^i) for i<7
    // minus w[7]*a*2^7. |a| <= 511 and |w| <= 128 so |p| < 2^17 and 18 signed
    // bits hold it, the same width the hard multiplier produces - the two
    // products are interchangeable by construction.
    //
    // The tree is written balanced rather than as a chain so the carry depth is
    // three adds and not seven, and it is **split in two halves with a register
    // between them**. Where that register goes took three gate builds to settle,
    // and the two wrong answers are worth recording because both looked right:
    //
    //   k1  whole tree in one cycle, at stage 2b.   39.834 MHz. Worst path was
    //       wbuf RAM out -> nibble select -> three 18-bit adds -> the sum with
    //       the DSP product -> s3_prod: 17 levels, 24.96 ns of data path.
    //   k2  split at the existing s2 -> s2b boundary, no new stage: lo/hi at
    //       stage 2 off a_ext1, the final add at 2b. 40.713 / 43.844 / 40.903 /
    //       42.251 MHz over four seeds. The tree stopped being the limiter and
    //       stage 2 became one: strip RAM clock-to-out 5.264 ns, then 4.648 ns
    //       of net to the parity swap, 3.679 to the sign extend, 2.516 to the
    //       tap select, then the adds - 18.774 ns, of which ~10.8 is pure wire
    //       at 76% device occupancy. Splitting a path that is mostly routing
    //       between two points that are far apart does not shorten it.
    //   k3  what is written below: a_q1 registers the operand exactly where a_q
    //       does, the whole multiplier moves into stage 2b, and **stage 2c** is
    //       added to finish it. The RAM-to-operand hop and the arithmetic each
    //       get a period of their own.
    //
    // M15's band on this probe is 55.3 / 60.1 / 58.3 / 58.5, and the analyser
    // runs ~1.3x pessimistic here (docs/milestones.md:4336 - a netlist reporting
    // 56.5 MHz ran bit-exact on the board at 74.0). So 40 MHz is about 52 real
    // against the 75 MHz the board clocks the tile at, and 1.444 x 52/75 is 1.00:
    // k1 and k2 would both have been worth exactly nothing.
    //
    // Stage 2c costs one cycle per sweep through FLUSH, no more: it carries three
    // control bits and no accumulator copy.
    function signed [17:0] le_mul_lo;   // taps 0..3
        input signed [9:0] a;
        input signed [7:0] w;
        reg signed [17:0] t0, t1, t2, t3;
        begin
            t0 = w[0] ? {{8{a[9]}}, a}             : 18'sd0;
            t1 = w[1] ? {{7{a[9]}}, a, 1'b0}       : 18'sd0;
            t2 = w[2] ? {{6{a[9]}}, a, 2'b0}       : 18'sd0;
            t3 = w[3] ? {{5{a[9]}}, a, 3'b0}       : 18'sd0;
            le_mul_lo = (t0 + t1) + (t2 + t3);
        end
    endfunction

    // The top tap is subtracted because w[7] is w's sign bit.
    function signed [17:0] le_mul_hi;   // taps 4..7
        input signed [9:0] a;
        input signed [7:0] w;
        reg signed [17:0] t4, t5, t6, t7;
        begin
            t4 = w[4] ? {{4{a[9]}}, a, 4'b0}       : 18'sd0;
            t5 = w[5] ? {{3{a[9]}}, a, 5'b0}       : 18'sd0;
            t6 = w[6] ? {{2{a[9]}}, a, 6'b0}       : 18'sd0;
            t7 = w[7] ? -{{1{a[9]}}, a, 7'b0}      : 18'sd0;
            le_mul_hi = (t4 + t5) + (t6 + t7);
        end
    endfunction

    genvar j;
    generate
        for (j = 0; j < NMAC; j = j + 1) begin : lane
            if (APACK == 1) begin : plain
                // A generate-if and not a ternary on WNIB: both arms of a
                // ternary elaborate, and at WNIB = 1 `wreg` is 32 bits, so the
                // dead byte select runs off the end of the vector. iverilog
                // warns, Efinity would quietly pad with x.
                //
                // The nibble is widened here rather than stored wide, so the
                // multiplier keeps its 10x8 shape and the product is identical
                // to the byte-fed one. WNIB is a storage change and nothing
                // else; anything it costs shows up in Fmax, not in the answer.
                wire signed [7:0] w_j;
                if (WNIB)
                    assign w_j = $signed({{4{wreg[WBITS*j+3]}},
                                          wreg[WBITS*j +: 4]});
                else
                    assign w_j = $signed(wreg[8*j +: 8]);

                // 10x8 signed. |a*w| <= 255*128 needs 16 bits; 18 maps onto the
                // T8's hard multiplier directly, with no packing tricks.
                wire signed [17:0] p_j = a_q * w_j;

                if (KPACK == 0) begin : k1
                    always @(posedge clk)
                        if (s2b_val)
                            s3_prod[32*j +: 32] <= {{14{p_j[17]}}, p_j};
                end else begin : k2
                    // The partner tap's weight comes from wreg1, same lane, same
                    // WNIB shape. Summing the two products before the
                    // accumulator is bit-identical to accumulating them on
                    // consecutive sweeps, because integer addition is
                    // associative - which is why this milestone regenerates no
                    // goldens and changes no wire format.
                    wire signed [7:0] w1_j;
                    if (WNIB)
                        assign w1_j = $signed({{4{wreg1_r[WBITS*j+3]}},
                                               wreg1_r[WBITS*j +: 4]});
                    else
                        assign w1_j = $signed(wreg1_r[8*j +: 8]);

                    // Stage 2b: the two halves, off the registered operand -
                    // the same a_q the hard multiplier reads, one tap over.
                    // Unconditional, like a_q: the pipeline's valid bits decide
                    // what is used, never what is clocked, and a clock enable
                    // here would put an extra input on 288 flops for nothing.
                    reg signed [17:0] lo1_j, hi1_j;
                    always @(posedge clk) begin
                        lo1_j <= le_mul_lo(a_q1, w1_j);
                        hi1_j <= le_mul_hi(a_q1, w1_j);
                    end

                    // The hard product is delayed to match. This is a plain
                    // register on a multiplier output, which is what the DSP's
                    // own O_REG is for - unlike A_REG (see the stage 2b note
                    // above) it is on the near side of the slow net, so it
                    // should cost no logic.
                    reg signed [17:0] p_j_r;
                    always @(posedge clk)
                        p_j_r <= p_j;

                    // Stage 2c: one add to finish the LE product and one to
                    // join it to the hard multiplier's, which is the same two
                    // levels the DSP path already had at 2b.
                    wire signed [17:0] p1_j = lo1_j + hi1_j;
                    wire signed [18:0] psum =
                        {p_j_r[17], p_j_r} + {p1_j[17], p1_j};

                    always @(posedge clk)
                        if (s2c_val)
                            s3_prod[32*j +: 32] <= {{13{psum[18]}}, psum};
                end
            end else begin : packed
                // Two channels on one multiplier. Lane j owns channels 2j and
                // 2j+1, which is one byte of the weight stream - so the packed
                // form needs no reordering at the host, only nibbles where
                // bytes used to be.
                wire signed [3:0] w_lo = $signed(wreg[WBITS*(2*j)   +: 4]);
                wire signed [3:0] w_hi = $signed(wreg[WBITS*(2*j+1) +: 4]);

                // B = w_lo + (w_hi << 12). |a*w| <= 255*8 = 2040 fits in 12
                // signed bits, so the two products do not collide - which is
                // exactly the thing the module header says is impossible at
                // int8, and is true there: 255*128 needs 16.
                wire signed [17:0] b_j = $signed({w_hi, 12'd0})
                                       + $signed({{14{w_lo[3]}}, w_lo});
                wire signed [26:0] p_j = a_q * b_j;

                // The low product comes out exact in the bottom 12 bits. The
                // high one is the rest, plus the borrow the low field took when
                // it was negative - p = lo + 4096*hi, so an arithmetic shift
                // lands one short whenever lo < 0.
                wire signed [11:0] lo_j = p_j[11:0];
                wire signed [14:0] hi_j = $signed(p_j[26:12]) + p_j[11];

                always @(posedge clk)
                    if (s2b_val) begin
                        s3_prod[32*(2*j)   +: 32] <= {{20{lo_j[11]}}, lo_j};
                        s3_prod[32*(2*j+1) +: 32] <= {{17{hi_j[14]}}, hi_j};
                    end
            end
        end
    endgenerate

    // Writeback. Kept out of the generate block so `accram` has one driver.
    //
    // The k = 0 pass has no previous value - which saves a 256-cycle walk to
    // zero the array, and is exact because every (p,q) is touched exactly once
    // per tap. It used to be expressed here, as `init ? prod : accq + prod`,
    // and that put a 2:1 mux between the adder output and the memory's write
    // port: two more net hops on the one path that already carries a 32-bit
    // ripple carry, 3.554 ns from the adder to the mux LUT and 3.397 ns from
    // there to WDATA. The same result comes from zeroing the *other* operand
    // one stage earlier - see `s3_accq` in the pipeline block - where the mux
    // is fed by a register and drives a register and its placement costs
    // nothing. Adding zero and substituting are the same thing; the difference
    // is entirely which side of the adder the choice is made on.
    reg [ACCT-1:0] acc_wr;
    integer jj;
    always @* begin
        acc_wr = {ACCT{1'b0}};
        for (jj = 0; jj < NQW; jj = jj + 1)
            acc_wr[32*jj +: 32] = s3_accq[32*jj +: 32] + s3_prod[32*jj +: 32];
    end

    // ---- memories ------------------------------------------------------------
    // Simple dual-port throughout: one write address, one read address, never
    // the same one in the same cycle. For `accram` that is guaranteed by the
    // FLUSH gap above; for the other two, loading and running never overlap.
    // ---- the strip, banked even and odd at KPACK = 1 -------------------------
    // One flat 2048x8 array places as **four blocks in the x2 configuration**
    // (gemm_top.place.rpt) - 8 bits wide, 2048 deep, which is width-bound: a
    // 16-bit-wide array of the same depth would want eight. Two banks of 1024x8
    // want two blocks each in the x5 configuration, so the pair is four again
    // and the total stays at 21 of 24. That is an assertion about Efinity's
    // inference and not about arithmetic, which is why tile_probe's place.rpt is
    // the gate for this milestone and not a calculation.
    //
    // Addressing, with a0 the first tap's address and a1 = a0 + 1 the second's:
    //
    //   a0 even -> (even[a0>>1], odd[a0>>1])
    //   a0 odd  -> (odd [a0>>1], even[a1>>1])
    //
    // so the **odd bank's address is a0[AW-1:1] in both cases and needs no mux
    // at all**, and only the even bank gets a 2:1 - which merges into the
    // rq_ld select that is already there. The swap on the way out is s2_par,
    // and it lands in `a_ext`, in front of the a_q flop, rather than between
    // the memory and the multiplier.
    //
    // The write and the rq parameter fetch are single bytes and just pick a
    // bank by the low address bit.
    //
    // strip_q / strip_q1 - the un-swap these two banks feed - are declared up
    // with the stage-2 registers, because a_ext reads them before this point.
    wire [AW-2:0] sra_o = strip_raddr[AW-1:1];
    wire [AW-2:0] sra_e = strip_raddr[0] ? s1_saddr1[AW-1:1] : strip_raddr[AW-1:1];

    generate
    if (KPACK == 0) begin : strip_flat
        reg [7:0] strip [0:STRIPD-1];
        always @(posedge clk) begin
            if (act_we)
                strip[act_addr] <= act_data;
            sq_a <= strip[strip_raddr];
        end
    end else begin : strip_bank
        // One always block per bank, each an exact copy of the flat template
        // above. The first version put both banks in a single block; that build
        // failed place-and-route with RAM_5K at 17 instead of 21 and 19,154
        // flip-flops instead of 2,513, and splitting it changed nothing - the
        // map log named `wbuf`, not the strip, and the fix was there (see the
        // wbuf_two arm). So the shared block was not the fault. It stays split
        // anyway: it is the template Efinity documents, it costs nothing, and
        // the banks did infer correctly here - place.rpt shows two blocks each,
        // SDP, READ_WIDTH 4, four for the pair exactly as predicted.
        reg [7:0] strip_e [0:STRIPD/2-1];
        reg [7:0] strip_o [0:STRIPD/2-1];

        wire we_e = act_we && !act_addr[0];
        wire we_o = act_we &&  act_addr[0];

        always @(posedge clk) begin
            if (we_e)
                strip_e[act_addr[AW-1:1]] <= act_data;
            sq_a <= strip_e[sra_e];
        end

        always @(posedge clk) begin
            if (we_o)
                strip_o[act_addr[AW-1:1]] <= act_data;
            sq_b <= strip_o[sra_o];
        end
    end
    endgenerate


    always @(posedge clk) begin
        acc_q <= accram[acc_raddr];
        if (s4_val)
            accram[s4_aaddr] <= s4_wr;
    end

    // ---- M16: the pair partner's weight word ---------------------------------
    // This is where task #73's recorded premise was wrong. It said `wbuf` must
    // supply two weights per lane per cycle and so widen 64 -> 128 bits, 4 -> 7
    // blocks, 24/24 on its own. It does not: **`wbuf` is read once per sweep**,
    // in the single S_LOAD cycle in front of S_SWEEP's P cycles.
    //
    // One read port is therefore enough for both words. The partner is fetched
    // on the FIRST S_SWEEP cycle, which the S_LOAD read has already vacated.
    // **The sweep does not get longer, so the speedup is the full 1.5x** rather
    // than the 1.48x budgeted for a two-cycle S_LOAD.
    //
    // The partner is cfg_QG words on because the walk is (k outer, g inner):
    // tap k, group g lives at k*QG + g. wpair is latched in S_LOAD from the
    // pre-increment wcnt.
    //
    // **One destination register, not two.** The first version wrote the two
    // reads to two registers -
    //
    //     if (state == S_LOAD) wreg  <= wbuf[wbuf_ra];
    //     else if (wpair_ph)   wreg1 <= wbuf[wbuf_ra];
    //
    // - and Efinity refused to infer the memory at all: "Mapping into logic
    // memory block 'u_tile/wbuf' (16384 bits) because read port is not
    // synchronous", which cost 16,384 flip-flops and its four RAM blocks and
    // failed pnr on carry chains. A block RAM has one output register, so the
    // read has to land in one place and be *copied* from there.
    //
    // So wreg1 IS the memory's output register, and wreg is a plain register
    // copy of it taken one cycle later:
    //
    //   L    S_LOAD, reads wcnt          L+1  wreg1 = tap 0, wcap high
    //   L+1  S_SWEEP 0, reads wpair      L+2  wreg1 = tap 1, wreg = tap 0
    //
    // wreg1 then holds until the next S_LOAD, because wrd_en is low for the
    // rest of the sweep. Both are valid at L+2 and the earliest reader is
    // position 0's multiply at L+3, so the one-cycle copy is free.
    //
    // wreg1_r is a *second* copy, and it is a timing fix rather than a schedule
    // one. With stage 2c in place the worst path in the whole tile became
    // wbuf's RDATA -> 3.527 ns of net -> the LE multiplier's tap select -> its
    // adders -> lo1_j: 19.583 ns, 16 levels, 50.7 MHz. The hard multiplier never
    // saw that because `wreg` is already a register copy sitting near its lanes;
    // the LE one was reading the RAM's own output pin from twenty tiles away.
    // wreg1_r gives it the same treatment for 64 flops. It costs no cycle: it is
    // valid at L+3 and the LE multiply is at stage 2b, which is L+4 now that
    // the operand register a_q1 sits in front of it.
    reg  [WA-1:0] wpair;
    reg           wpair_r = 1'b0;
    reg           wcap    = 1'b0;
    wire          wpair_ph = (KPACK != 0) && wpair_r;
    wire [WA-1:0] wbuf_ra  = wpair_ph ? wpair : wcnt;
    wire          wrd_en   = (state == S_LOAD) || wpair_ph;

    generate
    if (KPACK == 0) begin : wbuf_one
        always @(posedge clk) begin
            if (wgt_commit)
                wbuf[wgt_a] <= wgt_next;
            if (state == S_LOAD)
                wreg <= wbuf[wcnt];
        end
    end else begin : wbuf_two
        always @(posedge clk) begin
            if (wgt_commit)
                wbuf[wgt_a] <= wgt_next;
            if (wrd_en)
                wreg1 <= wbuf[wbuf_ra];
        end
        always @(posedge clk) begin
            wcap    <= (state == S_LOAD);
            wreg1_r <= wreg1;
            if (wcap)
                wreg <= wreg1;
        end
    end
    endgenerate

    // ---- M15: the requantize engine -----------------------------------------
    // One counter walking one schedule for both lanes. It starts at RQ_FRESH
    // when a readout begins, runs 8..RQ_DONE once per pair, and parks at
    // RQ_DONE until the output buffer has room - which is the only stall in
    // the whole path and is on the side that is allowed to stall.
    //
    // The kicks are decoded here rather than written from the control block
    // below, because rq_c, rq_qb and the slots may have exactly one driver
    // each. The conditions are the control block's own, restated: `!run &&
    // drain` in S_IDLE starts a readout, and `dout_ready` in S_DHOLD is the
    // cycle a byte is accepted.
    //
    // The whole block is inside `if (RQ != 0)`, a constant, so at RQ = 0 there
    // is no logic and every register above has no driver and no load.

    // A byte leaves the buffer. rq_half says which half of the slot it was.
    wire rq_pop = rq_on && (state == S_DHOLD) && dout_ready;

    // "The engine is parked at RQ_DONE with a pair to hand over", held rather
    // than compared. Same reason as rq_sec_q: written as `rq_c == RQ_DONE` this
    // put a five-bit compare in front of rq_slot's clock enable, and seed 7
    // reported exactly that path - `rq_c[0] -> 3 levels -> rq_slot[1][4]|CE`.
    // The engine can sit here for several cycles when the buffer is full, so it
    // is a set/clear bit and not a one-step strobe.
    reg rq_done_q = 1'b0;

    // A pair enters the buffer. The `||` arm is the case where the buffer is
    // full and the last byte of the slot the engine wants is being accepted
    // this cycle: the pop below runs first, so the push simply overwrites its
    // clear.
    wire rq_take = rq_done_q &&
                   (!rq_sf[rq_wsel] ||
                    (rq_pop && rq_half && (rq_wsel == rq_rsel)));

    // The walk steps once per pair, on the cycle after the pair's two
    // accumulator addresses have been presented - RQ_PADV in the prologue,
    // RQ_DONE from then on. So the walk always names the pair being *fetched*,
    // which is one schedule ahead of the pair being computed.
    wire rq_adv = rq_run && ((rq_c == RQ_PADV) || rq_take);

    always @(posedge clk) begin
        if (RQ != 0) begin
            rq_ldq <= rq_ld;

            if (rq_ldq)
                rq_par <= {strip_q, rq_par[47:8]};

            // The step decode. Everything here is `what will rq_c be next
            // cycle`, which for a counter is `what is it now, minus one` - so
            // these are comparisons against constants exactly like the ones they
            // replace, except that there are nine of them instead of nine per
            // lane, and their outputs are flops rather than nets into a CE.
            //
            // The single non-counter step is the jump out of RQ_DONE, carried by
            // the `|| rq_take` on RQE_BIAS. Parked cycles decode to all-zero,
            // which is what parking means.
            rq_e0 <= 9'd0;
            if (rq_run) begin
                rq_e0[RQE_ACC]  <= (rq_c == RQ_PSEL - 5'd1) ||
                                   (rq_c == RQ_SEL  - 5'd1);
                rq_e0[RQE_BIAS] <= (rq_c == RQ_BIAS - 5'd1) || rq_take;
                rq_e0[RQE_PRE]  <= (rq_c == RQ_PRE  - 5'd1);
                rq_e0[RQE_MUX]  <= (rq_c >= RQ_MUX0 - 5'd1) &&
                                   (rq_c <  RQ_RND  - 5'd1);
                rq_e0[RQE_MUL]  <= (rq_c >= RQ_MUL0 - 5'd1) &&
                                   (rq_c <  RQ_RND  - 5'd1);
                rq_e0[RQE_RND]  <= (rq_c == RQ_RND - 5'd1);
                rq_e0[RQE_RES]  <= (rq_c == RQ_RES - 5'd1);
                rq_e0[RQE_SH1]  <= (rq_c == RQ_SH1 - 5'd1);
                rq_e0[RQE_SH2]  <= (rq_c == RQ_SH2 - 5'd1);
            end
            rq_e1    <= rq_e0;
            rq_sec_q <= rq_run && ((rq_c == RQ_PSEL - 5'd1) ||
                                   (rq_c == RQ_SEL  - 5'd1));

            if (rq_run) begin
                if (rq_c != RQ_DONE)
                    rq_c <= rq_c + 1'b1;

                // Set as the engine steps into RQ_DONE and held until the pair
                // is handed over, which may be several cycles later.
                if (rq_c == RQ_LAST)
                    rq_done_q <= 1'b1;

                // Shared with both lanes and loaded a cycle before lane 0 needs
                // it, which is a cycle and a half before lane 1 does. rq_par is
                // read for the last time at lane 1's RQ_PRE, seven cycles
                // before the prefetch below starts overwriting it.
                if (rq_c == RQ_BIAS)
                    rq_s <= rq_par[47:42];
                else if (rq_c == RQ_PRE) begin
                    // s is at least 1 - fgx_rq_pick() returns 1 even for the
                    // degenerate mult <= 0 - so this never shifts by -1.
                    rq_rcq  <= 50'd1   << (rq_s - 6'd1);
                    rq_limq <= 51'd256 << rq_s;
                end

                // Pop before push. Both can name the same slot in the same
                // cycle, and when they do the push is what has to win.
                if (rq_pop) begin
                    if (rq_half) begin
                        rq_half        <= 1'b0;
                        rq_sf[rq_rsel] <= 1'b0;
                        rq_rsel        <= ~rq_rsel;
                    end else
                        rq_half <= 1'b1;
                end

                if (rq_take) begin
                    rq_slot[rq_wsel] <= {rq_code1, rq_code0};
                    rq_sf[rq_wsel]   <= 1'b1;
                    rq_se[rq_wsel]   <= rq_end_c;
                    rq_wsel          <= ~rq_wsel;
                    rq_c             <= RQ_BIAS;
                    rq_done_q        <= 1'b0;
                end
            end

            // The drain walk is position-innermost, so the output channel
            // changes exactly when d_p wraps - once per channel and not once
            // per code, which is why the table costs 31 fetches a frame and not
            // 356,352. rq_qb steps to the next six-byte entry there, and
            // rq_newch opens the prefetch window in the schedule that follows:
            // by the time those params are read at RQ_BIAS the fetch is six
            // cycles finished, so a channel change costs nothing at all.
            //
            // rq_end_c rides the same edge. The pair the walk names here is the
            // one the *next* schedule computes, so its "is this the last pair
            // of the readout" answer has to be carried across one schedule -
            // and it is read at RQ_DONE in the same cycle it is rewritten,
            // where non-blocking assignment gives the older, correct one.
            // r_last_* and not d_last*: see the walk below for why the RQ path
            // never reads the DPIPE-selected wires.
            if (rq_adv) begin
                rq_end_c <= r_last_pp && r_last_j && r_last_g;
                if (r_last_pp) begin
                    rq_qb    <= rq_qb + RQB[AW-1:0];
                    rq_newch <= 1'b1;
                end
            end
            if (rq_run && (rq_c == RQ_LAST))
                rq_newch <= 1'b0;

            // Last, so a readout always starts from the first entry even if the
            // previous one ended mid-table, and with an empty buffer even if
            // the previous one was abandoned mid-frame.
            if ((state == S_IDLE) && !run && drain) begin
                rq_c     <= RQ_FRESH;
                rq_qb    <= RQBASE[AW-1:0];
                rq_newch <= 1'b0;
                rq_wsel  <= 1'b0;
                rq_rsel  <= 1'b0;
                rq_half  <= 1'b0;
                rq_sf    <= 2'd0;
                rq_se    <= 2'd0;
                // Both are held rather than decoded, so both have to be told
                // that the previous readout is over - a park flag left set
                // would hand over a pair the prologue has not computed yet.
                rq_done_q <= 1'b0;
                rq_e0     <= 9'd0;
            end
        end
    end

    // What the control block hands to the link. rq_lastb is the readout's final
    // byte: the second half of a slot that was flagged when it was filled.
    wire       rq_avail = rq_sf[rq_rsel];
    wire [7:0] rq_byte  = rq_half ? rq_slot[rq_rsel][15:8]
                                  : rq_slot[rq_rsel][7:0];
    wire       rq_lastb = rq_se[rq_rsel] && rq_half;

    // ------------------------------------------------------------------------
    // Control.
    // ------------------------------------------------------------------------
    assign busy = (state != S_IDLE);

    always @(posedge clk) begin
        k_m1  <= cfg_K  - 1'b1;
        qg_m1 <= cfg_QG - 1'b1;
        p_m1  <= cfg_P  - 1'b1;
        p_m2  <= cfg_P  - 2'd2;
        p_one <= (cfg_P == {{(PW-1){1'b0}}, 1'b1});
        // RQ only: the drain walk steps two positions at a time, so it steps
        // the accumulator address by two channel groups. Registered here beside
        // the other decodes of CFG so the walk's adder stays one level.
        qg2   <= {cfg_QG, 1'b0};

        // t_k and t_g only move in S_FLUSH, and the next read of these flags is
        // a whole LOAD + P-cycle SWEEP + FLUSH away, so a one-cycle-stale value
        // is never observed.
        last_tap <= (t_k == k_m1);
        last_grp <= (t_g == qg_m1);

        // t_p moves every cycle, so last_pos cannot lag: compare against P-2 and
        // let the register itself supply the +1. At cycle c this holds
        // (t_p[c-1] == P-2), and inside S_SWEEP t_p[c] = t_p[c-1] + 1, so it is
        // exactly (t_p[c] == P-1). P == 1 has no such predecessor cycle and is
        // handled by the OR - every S_SWEEP cycle is the last one.
        last_pos <= (t_p == p_m2) || p_one;

        // Two cycles behind t_g / t_k / t_kx / t_ky, since last_grp and
        // last_tap are themselves registered. The gap between two advance
        // cycles is FLUSH + 1 + P + 1 clocks and P is never below 16, so the
        // values are long settled by the time fl_now reads them.
        fl_now <= (state == S_FLUSH) && (flush_c == 3'd1);
        fl_g   <= !last_grp;
        fl_k   <=  last_grp && !last_tap;
        fl_ky  <=  last_grp && !last_tap && (t_kx == 2'd2) && (t_ky != 2'd2);
        fl_ic  <=  last_grp && !last_tap && (t_kx == 2'd2) && (t_ky == 2'd2);

        // Pipeline advances every cycle; S_SWEEP overrides s1 below. Everything
        // downstream of s1 is unconditional, which is what makes FLUSH a matter
        // of simply not issuing rather than of gating four stages.
        s1_val   <= 1'b0;
        s2_val   <= s1_val;
        s2_zero  <= s1_zero;
        s2_init  <= s1_init;
        s2_aaddr <= s1_aaddr;
        // M16's three riders, in the same stages. s2_par is the low bit of the
        // address the memory is being read at THIS cycle, so it arrives with
        // sq_a/sq_b and says which bank the first tap landed in.
        s2_zero1 <= s1_zero1;
        s2_par   <= strip_raddr[0];
        s2b_val   <= s2_val;
        s2b_init  <= s2_init;
        s2b_aaddr <= s2_aaddr;
        s2b_accq  <= acc_q;
        a_q       <= a_ext;
        a_q1      <= a_ext1;
        // Stage 2c, dead at KPACK = 0 - the three selects below are the only
        // readers and they constant-fold.
        s2c_val   <= s2b_val;
        s2c_init  <= s2b_init;
        s2c_aaddr <= s2b_aaddr;
        s3_val   <= (KPACK == 0) ? s2b_val   : s2c_val;
        s3_aaddr <= (KPACK == 0) ? s2b_aaddr : s2c_aaddr;
        // The k = 0 pass, forced to add zero instead of adding whatever stale
        // value the array happens to hold. Register to register through one
        // AND gate; the accumulate adder downstream sees a plain operand.
        //
        // s2b_accq and not an s2c copy: acc_raddr already lags by a stage at
        // KPACK = 1, so this register holds the right issue's word on the cycle
        // this assignment fires either way. Only the init flag has to move.
        s3_accq  <= ((KPACK == 0) ? s2b_init : s2c_init) ? {ACCT{1'b0}}
                                                         : s2b_accq;
        s4_val   <= s3_val;
        s4_aaddr <= s3_aaddr;
        s4_wr    <= acc_wr;

        // The two widest S_FLUSH side effects, lifted out of the case so their
        // clock enables are two registered bits and not the whole priority cone
        // plus a state compare. fl_ky and fl_ic are mutually exclusive, and
        // each already implies the `fl_k` arm below - fl_now only rises inside
        // S_FLUSH, and both flags carry `last_grp && !last_tap` - so this fires
        // on exactly the cycles the nested form did. The S_IDLE reset still
        // wins, because the case comes after.
        if (fl_now && fl_ky) begin
            t_ky    <= t_ky + 2'd1;
            base_ky <= base_ky + cfg_strip_rw;
        end
        if (fl_now && fl_ic) begin
            t_ky    <= 2'd0;
            base_ky <= {AW{1'b0}};
            base_ic <= base_ic + cfg_strip_ch;
        end

        // r_last_* are resolved in S_DISS, and under RQ the walk passes through
        // S_DISS exactly once per readout instead of once per word - so they
        // would go stale after the first byte. Refreshing them every drain cycle
        // costs nothing and cannot race: the counters they watch move once per
        // pair, and a pair is never shorter than the engine's fifteen steps.
        // The S_DISS arm below still writes the same values on its own cycle,
        // so DPIPE at rq off is untouched.
        //
        // The walk itself moves here too, and not in S_DWAIT as it does at
        // int32. Under RQ it is driven by the engine's step and not by the
        // link's accept - the two are three pairs apart by then - so it belongs
        // with the flags rather than in a state arm. The rq arms of S_DWAIT and
        // S_DHOLD write none of these, so there is no second driver and no
        // ordering to reason about against the case below.
        //
        // ## The walk reads r_last_*, never d_last*, and that is the whole gap
        //    between the two gate builds
        //
        // d_lastj and d_lastg are `(DPIPE != 0) ? r_last_j : (d_j == JLAST)`.
        // tile_probe instantiates the tile at DPIPE = 1 and gemm_top at 0, so
        // written with d_last* this block is a *different circuit* in the two
        // netlists - and that is exactly what the gate builds measured. The
        // registered-strobe rewrite moved tile_probe 55 -> 58 MHz and left
        // gemm_top at 54.7, because gemm_top's worst path was never in the
        // engine: it was `d_g[3] -> 5 LUT levels -> d_g[4]`, 18.1 ns - the two
        // combinational compares chained in front of the counter's own enable
        // (!r_last_pp -> !(d_j == JLAST) -> !(d_g == qg_m1) -> d_g + 1).
        //
        // Under RQ the registered copies are refreshed unconditionally every
        // drain cycle, four lines up, so they are available whatever DPIPE says
        // and there is no reason to build the compares twice. They are one
        // cycle stale by construction, which cannot matter here: rq_adv fires
        // at RQ_PADV once and then only at RQ_DONE, and the schedule between
        // two RQ_DONEs is fifteen steps long.
        //
        // DPIPE = 0 keeps meaning what it meant for the int32 walk below, which
        // still reads d_last* and is untouched.
        if ((RQ != 0) && rq_on && drain_mode) begin
            r_last_p  <= last_pos_d;
            r_last_pp <= last_pair_d;
            r_last_j  <= (d_j == JLAST);
            r_last_g  <= (d_g == qg_m1);

            if (rq_adv) begin
                if (!r_last_pp) begin
                    d_p     <= d_p + 2'd2;
                    d_addr  <= d_addr  + {{(AA-GW-1){1'b0}}, qg2};
                    d_addr2 <= d_addr2 + {{(AA-GW-1){1'b0}}, qg2};
                end else begin
                    d_p <= {PW{1'b0}};
                    if (!r_last_j) begin
                        d_j     <= d_j + 1'b1;
                        d_addr  <= {{(AA-GW){1'b0}}, d_g};
                        d_addr2 <= {{(AA-GW){1'b0}}, d_g}
                                 + {{(AA-GW){1'b0}}, cfg_QG};
                    end else begin
                        d_j <= {JW{1'b0}};
                        if (!r_last_g) begin
                            d_g     <= d_g + 1'b1;
                            d_addr  <= {{(AA-GW){1'b0}}, d_g} + 1'b1;
                            d_addr2 <= {{(AA-GW){1'b0}}, d_g} + 1'b1
                                     + {{(AA-GW){1'b0}}, cfg_QG};
                        end
                    end
                end
            end
        end

        case (state)
        // --------------------------------------------------------------------
        S_IDLE: begin
            dout_valid <= 1'b0;
            if (run) begin
                t_k <= {KW{1'b0}};
                t_kx <= 2'd0;
                t_ky <= 2'd0;
                t_g <= {GW{1'b0}};
                t_p <= {PW{1'b0}};
                wcnt <= {WA{1'b0}};
                base_ic <= {AW{1'b0}};
                base_ky <= {AW{1'b0}};
                init_pass <= run_init;
                state <= S_LOAD;
            end else if (drain) begin
                d_g <= {GW{1'b0}};
                d_j <= {JW{1'b0}};
                d_p <= {PW{1'b0}};
                d_addr <= {AA{1'b0}};
                // RQ only, and the walk's own initial value for position 1.
                // At RQ = 0 nothing reads it; the register is optimized away
                // because RQ is a parameter.
                d_addr2 <= {{(AA-GW){1'b0}}, cfg_QG};
                d_end <= 1'b0;
                state <= S_DISS;
            end
        end

        // --------------------------------------------------------------------
        // One cycle to latch the tap into the feed and fetch this group's NMAC
        // weights. The feed's output is only valid from the next cycle, so no
        // position is issued here.
        S_LOAD: begin
            acc_addr <= {{(AA-GW){1'b0}}, t_g};
            t_p      <= {PW{1'b0}};
            // Seed last_pos for t_p = 0 rather than let the free-running
            // compare above do it. Coming from S_FLUSH the predecessor value of
            // t_p is P-1 and the compare answers correctly by luck; coming from
            // S_IDLE it is already 0, which would fire a cycle early at P = 2.
            last_pos <= p_one;
            wcnt     <= wcnt + 1'b1;
            // M16: the partner's word address, from the pre-increment wcnt, and
            // the one-cycle phase that fetches it on the first S_SWEEP cycle.
            wpair    <= wcnt + {{(WA-GW){1'b0}}, cfg_QG};
            wpair_r  <= 1'b1;
            state    <= S_SWEEP;
        end

        // --------------------------------------------------------------------
        // P cycles, one output position each, NMAC MACs per cycle.
        S_SWEEP: begin
            s1_val   <= 1'b1;
            s1_saddr <= feed_addr;
            s1_zero  <= feed_zero;
            s1_aaddr <= acc_addr;
            s1_init  <= init_pass && (t_k == {KW{1'b0}});

            // M16's second tap. kp_tail is the odd step - kx = 2 with no
            // partner, once per (ky, ic) because the kernel is 3 wide - and it
            // is retired through the padding path that is already here: s2_zero1
            // gates a_ext1 to zero, so the LE multiplier contributes nothing and
            // no separate tail schedule is needed.
            s1_saddr1 <= feed_addr1;
            s1_zero1  <= feed_zero1 || kp_tail;

            wpair_r  <= 1'b0;

            acc_addr <= acc_addr + {{(AA-GW){1'b0}}, cfg_QG};
            t_p      <= t_p + 1'b1;

            if (last_pos) begin
                flush_c <= FLUSH[2:0] - 3'd1;
                state   <= S_FLUSH;
            end
        end

        // --------------------------------------------------------------------
        // Retire the pipeline, then advance (g, k). The tap decomposition is
        // three ripple counters rather than a divide by 9, and the two strip
        // bases ride along on adders.
        S_FLUSH: begin
            // Decrementing past zero would wrap, and does not: every arm of the
            // fl_now branch leaves S_FLUSH on the cycle it fires.
            if (!fl_now) begin
                flush_c <= flush_c - 3'd1;
            end else if (fl_g) begin
                t_g   <= t_g + 1'b1;
                state <= S_LOAD;
            end else if (fl_k) begin
                t_g  <= {GW{1'b0}};
                // M16 walks kx in {0, 2} instead of {0, 1, 2}: the pair (0,1)
                // then the tail (2). t_k stays the LOW tap of the step, so it
                // advances by two out of the pair and by one out of the tail -
                // 0, 2 | 3, 5 | 6, 8 for K = 9, six steps where there were nine.
                //
                // fl_ky and fl_ic key off `t_kx == 2'd2` and are untouched by
                // this: the tail is still where kx runs out.
                t_k  <= t_k + ((KPACK == 0) ? 1'b1 : (kp_tail ? 1'b1 : 2'd2));
                t_kx <= (KPACK == 0) ? ((t_kx != 2'd2) ? t_kx + 2'd1 : 2'd0)
                                     : (kp_tail ? 2'd0 : 2'd2);
                // wcnt has walked one word per group and now sits at
                // (t_k + 1)*QG. Out of a pair the next tap is t_k + 2, so skip
                // the partner's QG words - they were read as wpair.
                if ((KPACK != 0) && !kp_tail)
                    wcnt <= wcnt + {{(WA-GW){1'b0}}, cfg_QG};
                // t_ky, base_ky and base_ic are NOT updated here - see the two
                // hoisted blocks above the case.
                state <= S_LOAD;
            end else begin
                state <= S_IDLE;
            end
        end

        // --------------------------------------------------------------------
        // Readout, channel-major. Slow by construction - the return path is one
        // bit wide, so 32 link clocks per word - which is why this is a plain
        // three-state walk and not a pipeline, and why DPIPE can afford to make
        // it a four-state one.
        //
        // The loop counters only ever move in S_DHOLD, so d_p / d_j / d_g are
        // stable from the moment S_DISS is entered until the word is accepted.
        // That is what makes resolving the three end-of-loop compares here,
        // one cycle before the enable needs them, an identity rather than an
        // approximation.
        S_DISS: begin
            r_last_p  <= last_pos_d;
            r_last_pp <= last_pair_d;
            r_last_j  <= (d_j == JLAST);
            r_last_g  <= (d_g == qg_m1);
            state     <= S_DWAIT;
        end

        // ## Where the walk went, and what S_DWAIT is now
        //
        // At int32 the walk advances on the accept cycle, in S_DHOLD, and the
        // three states cost 1 + 1 + 32 clocks against a serializer that needs
        // 32 - so the FSM is free and its shape does not matter. At one byte
        // the serializer needs 8, a pair of codes needs 15, and there is no
        // room to do them in sequence.
        //
        // So under rq_on the walk is not here at all: it is stepped by the
        // engine's own counter, in the free-running block above, one schedule
        // ahead of the codes it produces. What is left in these two states is
        // a byte at a time out of the engine's two-slot buffer, and the only
        // thing they have to get right is that **dout_valid falls between
        // words**, because gemm_link latches on `dv_rise` and not on a level.
        // It does: S_DHOLD clears it on the accept cycle and the earliest this
        // arm can raise it again is the cycle after next, so there is always a
        // gap of one.
        //
        // "Was that the last byte" moves with the data rather than being asked
        // of the counters, which by then are three pairs past the answer - see
        // rq_se, filled when a slot is filled.
        S_DWAIT: begin
            if (rq_on) begin
                // A level test on the buffer, not on the engine's step. The
                // engine runs 15 clocks a pair against the wire's 16, so in
                // steady state the byte is already there and S_DWAIT is one
                // cycle long; at the very start of a readout it is where the
                // prologue's 23 clocks are spent, against the ~40 the link's
                // preamble and status give away before it asks.
                //
                // RQ takes over the whole readout rather than composing with
                // DPIPE. S_DMUX exists to put a flop between accram and the
                // lane mux; the lanes' own asel register is that flop, so at
                // rq_on the pipelined arm would only add a state. The two
                // settings stay orthogonal because the lanes read acc_q
                // directly and never d_src.
                if (rq_avail) begin
                    dout       <= {24'd0, rq_byte};
                    dout_valid <= 1'b1;
                    d_end      <= rq_lastb;
                    state      <= S_DHOLD;
                end
            end else if (DPIPE != 0) begin
                // acc_q is valid this cycle; hold it in fabric so the long
                // route out of the memory block ends at a flop instead of at
                // the lane mux.
                acc_q2 <= acc_q;
                state  <= S_DMUX;
            end else begin
                dout       <= acc_q[32*d_j +: 32];
                dout_valid <= 1'b1;
                state      <= S_DHOLD;
            end
        end

        // DPIPE only. At DPIPE = 0 this arm is written to do exactly what the
        // `default` arm below used to do for 3'd7, so bit-identity does not
        // depend on the synthesizer proving the state unreachable.
        S_DMUX: begin
            if (DPIPE != 0) begin
                dout       <= d_src[32*d_j +: 32];
                dout_valid <= 1'b1;
                state      <= S_DHOLD;
            end else begin
                state <= S_IDLE;
            end
        end

        S_DHOLD: begin
            if (dout_ready) begin
                dout_valid <= 1'b0;
                if (rq_on) begin
                    // Nothing to advance - S_DWAIT did it when the byte was
                    // latched - so this is only "is there another one".
                    state <= d_end ? S_IDLE : S_DWAIT;
                end else if (!d_lastp) begin
                    d_p    <= d_p + 1'b1;
                    d_addr <= d_addr + {{(AA-GW){1'b0}}, cfg_QG};
                    state  <= S_DISS;
                end else begin
                    d_p <= {PW{1'b0}};
                    if (!d_lastj) begin
                        d_j    <= d_j + 1'b1;
                        d_addr <= {{(AA-GW){1'b0}}, d_g};
                        state  <= S_DISS;
                    end else begin
                        d_j <= {JW{1'b0}};
                        if (!d_lastg) begin
                            d_g    <= d_g + 1'b1;
                            d_addr <= {{(AA-GW){1'b0}}, d_g} + 1'b1;
                            state  <= S_DISS;
                        end else begin
                            state <= S_IDLE;
                        end
                    end
                end
            end
        end

        default: state <= S_IDLE;
        endcase
    end

endmodule

`default_nettype wire

// ---------------------------------------------------------------------------
// M15: one requantize lane.
//
// `code = clamp(((acc + bias) * M + 2^(s-1)) >> s, 0, 255)`, serial, radix 8,
// built from LEs. This is Stage 1's engine unchanged - every path named in the
// gate builds is in here and none of them crossed a lane boundary, which is
// what made two of them affordable.
//
// The lane owns only what differs between the two positions of a pair: the
// bias adder, the odd multiples, the partial-product mux, the carry-save
// accumulator and the two shift stages. bias, M, s and the two decodes of s
// come in from gemm_tile, because a pair is two positions of the *same* output
// channel and shares all four.
//
// `k` is the lane's step. gemm_tile drives lane 0 with rq_c and lane 1 with
// rq_c - 1, so this module is written once and the interleave is a subtraction.
// The two lanes' accumulator reads land on consecutive cycles of one port for
// the same reason.
//
// ## Two structural choices, both bought with measurements
//
// The first cut of this engine did the obvious thing - mux the partial product
// and accumulate it in the same cycle - and the tile_probe build priced it at
// **50.4 MHz against the control's 67.0**, on a path reported as
// `rq_a3[7] -> 3 LUT levels -> add_643 -> rq_p[48]`, 19.7 ns. The two halves
// were 10.4 ns of routing through the mux and 6.1 ns of carry chain across 42
// bits. Efinity's model puts essentially all of the delay in nets - every LUT
// above reports 0.000 - so what a cycle can afford is roughly **four hops**,
// and that one had seven plus a carry chain.
//
// So the loop is split into two paths that each end at a flop:
//
//   - **the mux runs a cycle ahead.** `pp` holds the partial product for digit
//     i while the accumulator absorbs digit i-1. Throughput is still one digit
//     per cycle; it costs one extra step, not one per digit.
//   - **the accumulator is carry-save.** `ps + pc` is the running product, and
//     a step is a 3:2 compressor: one LUT for the sum bit, one for the
//     majority, no carry chain at all. The single carry-propagate add is
//     hoisted out of the loop into K_RES, where it is alone in its cycle.
//     Redundant form survives the `<< 3` because dropping the top three bits of
//     each word is arithmetic mod 2^50, and the true partial sums are bounded
//     by 2^44.
//
// The clamp is the other thing that does not fit. `p >>> s` as written is a
// 6-level barrel shifter 50 bits wide, and the saturation test on top of it is
// another OR tree. Both are avoided rather than pipelined: the overflow test
// becomes **one comparison against `256 << s`**, which is a 1-level decoder
// feeding a carry chain and runs *beside* the shifter, and the shifter itself
// only has to produce **15 bits** - coarse by 8*s[5:3], then fine by s[2:0] -
// because everything above bit 7 of the result has already been answered by
// that comparison.
// ---------------------------------------------------------------------------
`default_nettype none
module gemm_rq_lane (
    input  wire               clk,
    // One strobe per thing this lane does, decoded from the step counter in
    // gemm_tile and registered there. A lane holds no comparator and no step
    // number of its own: the first two-lane gate build had one five-bit compare
    // per event per lane and every reported worst path started at that counter.
    input  wire        [8:0]  e,      // see RQE_* in gemm_tile
    input  wire signed [31:0] acc_w,  // the lane-muxed accumulator
    input  wire signed [27:0] bias,
    input  wire        [17:0] mm,     // M, whole; consumed three bits a cycle
    input  wire        [5:0]  ss,
    input  wire        [49:0] rcq,    // 1 << (s-1)
    input  wire        [50:0] limq,   // 256 << s
    output wire        [7:0]  code
);
    // The strobe order, which is gemm_tile's RQE_* and has to stay in step with
    // it. E_ACC and E_SH2 arrive together on purpose: the accumulator for the
    // *next* pair is latched while this one is being shifted, which is what
    // removes the read from the critical loop.
    localparam integer E_ACC  = 0,
                       E_BIAS = 1,
                       E_PRE  = 2,
                       E_MUX  = 3,
                       E_MUL  = 4,
                       E_RND  = 5,
                       E_RES  = 6,
                       E_SH1  = 7,
                       E_SH2  = 8;

    // The register between accram and the bias adder. It is the same path DPIPE
    // was added for - accram's clock-to-out plus a 5.579 ns net plus two LUT
    // levels of select - and putting the adder on the far end of it would
    // rebuild exactly the 14.9 ns path M10 measured.
    reg signed [31:0] asel = 32'sd0;

    // Bias is 24-bit signed and the accumulator 32, so `a` could in principle
    // wrap 28 bits. It does not: |acc| <= 1728*255*127 = 55,961,280 and
    // |bias| <= 277,460, so |a| <= 56,238,740 < 2^26 - 27 signed bits, carried
    // in 28. That bound is what lets the partial products be 31 bits and the
    // product 2^26 * 2^18 = 2^44, which is why the carry-save words can be
    // shifted left three and truncated without losing anything.
    reg signed [27:0] a  = 28'sd0;
    reg signed [30:0] a3 = 31'sd0, a5 = 31'sd0, a7 = 31'sd0;
    reg signed [30:0] pp = 31'sd0;
    reg        [17:0] m  = 18'd0;
    reg        [49:0] ps = 50'd0, pc = 50'd0;
    reg        [49:0] p  = 50'd0;
    reg        [14:0] c15 = 15'd0;
    reg        [7:0]  f8  = 8'd0;
    reg               neg = 1'b0, sat = 1'b0;

    // The radix-8 digit and its multiple of `a`. This is the mux that runs a
    // cycle ahead of the accumulator; its result lands in the pp flop.
    wire [2:0] dg = m[17:15];
    reg signed [30:0] ppn;
    always @* begin
        case (dg)
        3'd0: ppn = 31'sd0;
        3'd1: ppn = {{3{a[27]}}, a};
        3'd2: ppn = {{2{a[27]}}, a, 1'b0};
        3'd3: ppn = a3;
        3'd4: ppn = {a[27], a, 2'b0};
        3'd5: ppn = a5;
        3'd6: ppn = {a3[29:0], 1'b0};
        default: ppn = a7;
        endcase
    end

    // The 3:2 compressor. The third input is whatever is being folded in - a
    // shifted partial product during the multiply, the rounding constant at
    // K_RND - and `ps + pc` is the running value in both cases. One LUT per
    // output bit, one level, and the carry that a real adder would ripple is
    // instead written down as a word and dealt with once, at K_RES.
    wire [49:0] ps3 = {ps[46:0], 3'b0};
    wire [49:0] pc3 = {pc[46:0], 3'b0};
    wire [49:0] ppx = {{19{pp[30]}}, pp};

    wire [49:0] mul_s = ps3 ^ pc3 ^ ppx;
    wire [49:0] mul_m = (ps3 & pc3) | (ps3 & ppx) | (pc3 & ppx);
    wire [49:0] mul_c = {mul_m[48:0], 1'b0};

    wire [49:0] rnd_s = ps ^ pc ^ rcq;
    wire [49:0] rnd_m = (ps & pc) | (ps & rcq) | (pc & rcq);
    wire [49:0] rnd_c = {rnd_m[48:0], 1'b0};

    // Saturation, decided by a comparison and not by looking at the shifted
    // result: `p >= 256 << s` is one carry chain, and it runs in the same cycle
    // as the coarse shift rather than after it. The sign is taken separately so
    // this stays an unsigned compare.
    //
    // 51 bits carries the limit for s <= 42, and s = 18 - e from fgx_rq_pick(),
    // so s > 42 would mean a channel whose mult is below 6e-8 against a
    // measured minimum of 1.8e-4 - a layer that emits nothing but zeros.
    wire over = !p[49] && ({1'b0, p} >= limq);

    // The coarse stage keeps 15 bits because the fine stage shifts by at most 7
    // and the clamp only ever reads 8 - everything above that is `over`'s
    // business.
    wire signed [49:0] psgn = $signed(p);
    wire        [14:0] coarse = psgn >>> {ss[5:3], 3'b0};

    assign code = neg ? 8'd0 : sat ? 8'd255 : f8;

    always @(posedge clk) begin
        begin
            if (e[E_ACC])
                asel <= acc_w;

            if (e[E_BIAS]) begin
                a  <= $signed(asel[27:0]) + bias;
                m  <= mm;
                ps <= 50'd0;
                pc <= 50'd0;
            end else if (e[E_PRE]) begin
                // One adder level, not three chained: 6a is 3a shifted and 2a
                // and 4a are wires, so selecting a partial product above is an
                // 8:1 mux and nothing more.
                a3 <= {{3{a[27]}}, a} + {{2{a[27]}}, a, 1'b0};
                a5 <= {{3{a[27]}}, a} + {a[27], a, 2'b0};
                a7 <= {a, 3'b0}       - {{3{a[27]}}, a};
            end

            // The mux is a cycle ahead of the compressor, so the two of them
            // are separate flop-to-flop paths and neither carries the other's
            // routing. E_MUX leads E_MUL by one step to fill the pipe; the last
            // mux output is computed and never used, which is a register and no
            // cycles.
            if (e[E_MUX]) begin
                pp <= ppn;
                m  <= {m[14:0], 3'b0};
            end

            if (e[E_MUL]) begin
                ps <= mul_s;
                pc <= mul_c;
            end else if (e[E_RND]) begin
                // A constant shifted by a variable is a 6-to-50 decoder, not a
                // barrel shifter, so folding it in is the same one LUT level as
                // a multiply step.
                ps <= rnd_s;
                pc <= rnd_c;
            end else if (e[E_RES]) begin
                // The only carry-propagate add in the lane, and it has its
                // cycle to itself.
                p <= ps + pc;
            end

            if (e[E_SH1]) begin
                c15 <= coarse;
                neg <= p[49];
                sat <= over;
            end
            if (e[E_SH2]) begin
                f8 <= c15[14:0] >> ss[2:0];
            end
        end
    end
endmodule
`default_nettype wire
