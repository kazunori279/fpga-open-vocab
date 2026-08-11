// M10 Stage 0: what does gemm_tile close at when it is not sharing a clock
// domain with gemm_link?
//
// This module computes nothing and is never loaded onto the board. It exists to
// put one number in a timing report, and that number decides whether M10 is
// worth building at all.
//
// ---------------------------------------------------------------------------
// Why the question is the whole milestone
// ---------------------------------------------------------------------------
//
// M10's idea is to give u_tile its own clock so the MCU stops clocking it. The
// tempting arithmetic is that RUN's 314 ms of idle bytes leave the wire and the
// frame gets 314 ms shorter. **That arithmetic is wrong**, and it is worth
// stating here because the README carried it for a day.
//
// RUN's idle bytes are not overhead. firmware/m7.c:490 sizes them as
// `sweep = K*QG*(P+6) + 512` - the tile's cycle count, plus slack - and M7h
// measured RUN at 23.24 Mclk over 314 ms, which is 74.0 MHz, which is link_clk.
// Take away the 1,856 x 512 cycles of per-pass slack and the tile really does
// spend ~22.3 M cycles computing. Give it an independent clock at the same
// 75 MHz and the MCU idle-*waits* instead of idle-*clocking*: the bytes go, the
// time stays, the frame does not move.
//
// So the entire prize is
//
//     22.3e6 x (1/75MHz - 1/f_tile)
//
// which is 0 ms at 75 MHz, 137 ms at 125 MHz and 166 ms at 150 MHz. Nobody has
// ever measured f_tile, because the tile has never been synthesized on its own.
// gemm_top closes at 62.449 MHz and gemm_top_wide at 58.630 MHz, but both
// reports name a path inside gemm_link's framing logic, never the MAC array
// (see the notes in gemm_top_wide.sdc), so neither number is the tile's.
//
// ---------------------------------------------------------------------------
// Why a wrapper, and what the wrapper must not do
// ---------------------------------------------------------------------------
//
// gemm_tile has 131 input bits and 34 output bits. The T8F49 is a 49-ball part.
// The tile cannot be a top module as it stands, so something has to drive it -
// and that something can wreck the measurement in two opposite ways.
//
//   - **Too little stimulus** and synthesis folds the tile away. A constant on
//     cfg_K propagates into the FSM, and what gets timed is a shell. The
//     defence is not a code review, it is `tile_probe.res.csv`: it must still
//     report 8 multipliers and 21 memory blocks, exactly as gemm_top.res.csv
//     does. Fewer means the probe was optimised out and the Fmax is fiction.
//
//   - **Too much wrapper** and the wrapper becomes the critical path. A 34-bit
//     XOR of the outputs is ~9 LUT4 levels, deeper than anything inside the
//     tile, and the report would then be measuring the reducer. So the output
//     reduction is pipelined into two shallow stages below, and every tile
//     input is driven directly by a flop.
//
// An LFSR is the stimulus because it satisfies both: 168 flops, one per tile
// input plus spares, no constants, and its own feedback path is a single 4-input
// XOR. The seed is XORed with an input pin so no amount of constant propagation
// can prove the register bank is zero.
//
// ---------------------------------------------------------------------------
// What the number means, and what it does not
// ---------------------------------------------------------------------------
//
// This is an **upper bound** on what Stage 1 can close at, not a prediction of
// it. The probe is smaller than gemm_top_tclk will be - no gemm_link, no second
// domain, 3 pins instead of 8 - so the placer has room this design will not
// have. That is the right shape for a gate: if the upper bound is 75 MHz, M10
// is worth nothing and stops here; if it is 150 MHz, Stage 1 is worth building
// and will land somewhere below.
//
// Clock ball is B3, which is a global-clock ball (CLK2 [GCLK]). Under M10 the
// tile clock comes off the PLL and therefore rides the global network; feeding
// the probe from a general-fabric ball would understate it for a reason that
// will not exist in Stage 1. gemm_top's link_clk is on F3, general fabric, and
// gemm_top_io.isf already flags that as the thing to read first.
//
//     ./build.sh tile_probe
//     PNR_OPTS="seed=7" ./build.sh tile_probe      # and one more; <3 MHz is noise
//
// See build.sh: four seeds on one netlist spread 60.7 to 65.4 MHz, so a single
// build's delta is not evidence.

`default_nettype none

module tile_probe #(
    // Identical to gemm_top's, and they have to be: the point is to time the
    // tile the rest of the project ships, not a differently-shaped one. If
    // these ever diverge, res.csv stops matching gemm_top.res.csv and the
    // sanity check above catches it.
    parameter integer NMAC   = 8,
    parameter integer ADEPTH = 256,
    parameter integer WDEPTH = 256,
    parameter integer STRIPD = 2048,
    parameter integer AW     = 11,
    parameter integer CW     = 10,
    parameter integer PW     = 8,
    parameter integer GW     = 5,
    parameter integer KW     = 10,
    // 131 tile input bits; the rest is headroom so the tap positions can stay
    // at a documented maximal-length polynomial rather than being re-derived.
    parameter integer LFSRW  = 168,

    // ---- M14, driven from build.sh's MAP_OPTS ------------------------------
    // Exposed rather than hard-coded so the M14 variants are one command line
    // apart on an otherwise byte-identical source tree. A seed is not portable
    // across netlists, so every M14 number has to come with a control built
    // from *this* file at the same seeds - see docs/milestones.md.
    //
    //   control  --top-params "DPIPE=1"                        today's tile
    //   P1       --top-params "DPIPE=1,APACK=2,WNIB=1,ADEPTH=128"
    //   P2       --top-params "DPIPE=1,WNIB=1"                 nibbles in wbuf
    //
    // P1 halves ADEPTH because APACK = 2 doubles the channels per accumulator
    // word: 128 x 512 is the same 2048 accumulators as 256 x 256, and whether
    // it is also the same number of memory blocks is the entire question.
    //
    // **Neither P1 nor P2 is what shipped, and P2's numbers are not why.** P2
    // measured well and is still unusable: conv0's weights are 8-bit and conv0
    // runs on the same tile as the nibble layers, so a 4*NMAC-bit wbuf cannot
    // hold a row it has to hold. What shipped is P3 - full-width storage,
    // expand at wgt_we under cfg_w4 - and WNIB survives here only because
    // APACK = 2 needs it to elaborate. Both rows above are gate evidence, kept
    // reproducible; see the M14 section of docs/milestones.md.
    parameter integer DPIPE  = 1,
    parameter integer APACK  = 1,
    parameter integer WNIB   = 0,

    // ---- M15, same discipline ----------------------------------------------
    //   control  --top-params "DPIPE=1"          today's tile, RQ = 0
    //   S1       --top-params "DPIPE=1,RQ=1"     the requantize epilogue
    //
    // Read for two things and only two: memory blocks must stay at 21 - the
    // params live in the strip's dead top 192 bytes, so unlike the plan's
    // estimate this design asks for no new block - and multipliers must stay at
    // 8, i.e. the serial radix-8 walk did not quietly become a hard multiply.
    parameter integer RQ     = 0,

    // ---- M16, same discipline ------------------------------------------------
    //   control  --top-params "DPIPE=1,RQ=1"           what M15 shipped
    //   S1       --top-params "DPIPE=1,RQ=1,KPACK=1"   two taps per lane
    //
    // Read for three things, and the binding one is NOT what task #73 recorded.
    //
    //   Logic Elements  the real gate. M15's gemm_top is 4650 of 7384, so the
    //                   budget is 2734 and the estimate is 1600-2100: eight
    //                   LE-built 10x8 multipliers, eight 18-bit sums, a second
    //                   im2col_feed, and the bank select.
    //   Memory Blocks   must stay at 21. `wbuf` does not widen - it is read once
    //                   per sweep, not once per cycle - and the strip's two
    //                   1024x8 banks should take two blocks each at x5 where the
    //                   flat 2048x8 takes four at x2. If they come back as eight,
    //                   Efinity did not pick x5 and the milestone is a NO-GO.
    //   Multipliers     still 8. le_mul() is written as a shift-add precisely so
    //                   inference cannot decide otherwise.
    parameter integer KPACK  = 0
) (
    input  wire tile_clk,
    input  wire seed_in,
    output wire probe_out
);

    // ------------------------------------------------------------------------
    // Stimulus.
    //
    // Taps for x^168 + x^166 + x^153 + x^151 + 1, maximal length (XAPP052). The
    // period does not actually matter - nothing simulates this - but a
    // degenerate polynomial that walks into an absorbing state is one more thing
    // a synthesizer could reason about, and there is no reason to leave it open.
    //
    // `seed_in` is in the feedback rather than in a reset, so the register bank
    // has no provable value at any time. A reset-seeded LFSR is provably
    // periodic from a known state, and while no tool is likely to unroll 2^168
    // cycles, the cheap version of that argument is to not make it.
    // ------------------------------------------------------------------------
    reg [LFSRW-1:0] lfsr = {{(LFSRW-1){1'b0}}, 1'b1};

    wire fb = lfsr[167] ^ lfsr[165] ^ lfsr[152] ^ lfsr[150] ^ seed_in;

    always @(posedge tile_clk)
        lfsr <= {lfsr[LFSRW-2:0], fb};

    // ------------------------------------------------------------------------
    // Fan-out to the tile's 132 input bits. Every one comes straight off a flop,
    // so each timed path starts at a register and ends inside the tile - which
    // is what makes the report the tile's report.
    //
    // The values are nonsense: `run` pulses at random, cfg_P is whatever bit 74
    // happens to be, act_we writes constantly. None of that matters. Synthesis
    // and place-and-route see a netlist, not a trace, and the analyser walks
    // every path whether the design would ever exercise it or not. What would
    // matter is a *constant*, and there are none here.
    // ------------------------------------------------------------------------
    wire [LFSRW-1:0] r = lfsr;

    wire        busy;
    wire [31:0] dout;
    wire        dout_valid;

    // DPIPE(1) is the one deliberate difference from gemm_top's instantiation,
    // and it is what this second build is for. The first four seeds all named a
    // path in gemm_tile's drain walk, never the MAC array, so the 66 +/- 3 MHz
    // they reported is the walk's number and not the compute loop's. DPIPE
    // pipelines the walk out of the way; if Fmax moves, the first measurement
    // was measuring the wrong thing.
    //
    // gemm_top and gemm_top_wide leave it at 0 and stay bit-identical.
    gemm_tile #(
        .NMAC(NMAC), .ADEPTH(ADEPTH), .WDEPTH(WDEPTH), .STRIPD(STRIPD),
        .AW(AW), .CW(CW), .PW(PW), .GW(GW), .KW(KW), .DPIPE(DPIPE),
        .APACK(APACK), .WNIB(WNIB), .RQ(RQ), .KPACK(KPACK)
    ) u_tile (
        .clk            (tile_clk),
        .cfg_H          (r[  9:  0]),
        .cfg_W          (r[ 19: 10]),
        .cfg_OW         (r[ 29: 20]),
        .cfg_stride2    (r[ 30]),
        .cfg_strip_rw   (r[ 41: 31]),
        .cfg_strip_ch   (r[ 52: 42]),
        .cfg_oy0        (r[ 62: 53]),
        .cfg_ox0        (r[ 72: 63]),
        .cfg_unsigned_in(r[ 73]),
        .cfg_P          (r[ 81: 74]),
        .cfg_QG         (r[ 86: 82]),
        .cfg_K          (r[ 96: 87]),
        .act_we         (r[ 97]),
        .act_addr       (r[108: 98]),
        .act_data       (r[116:109]),
        .wgt_we         (r[117]),
        .wgt_data       (r[125:118]),
        .wgt_rst        (r[126]),
        .run            (r[127]),
        .run_init       (r[128]),
        .busy           (busy),
        .drain          (r[129]),
        .dout           (dout),
        .dout_valid     (dout_valid),
        .dout_ready     (r[130]),
        // Appended, so every bit assignment above keeps the index it had when
        // the M14 control was built. A seed is not portable across netlists and
        // renumbering these would quietly make it a different netlist.
        .cfg_w4         (r[131]),
        .cfg_rq         (r[132])
    );

    // ------------------------------------------------------------------------
    // Sink, pipelined so it cannot become the critical path.
    //
    // Stage 1 folds dout 4:1 - eight independent 4-input XORs, one LUT4 level
    // each, fed by the tile's own dout register. Stage 2 reduces the surviving
    // byte together with the two status bits: a 10-input XOR, ~2 levels, and
    // `busy` is combinational off `state` (gemm_tile.v:437) so that path is
    // state FF -> 1 -> 2 -> out_q. Three levels. Everything inside the tile is
    // deeper, which is the property that has to hold for the report to be about
    // the tile.
    //
    // If a future revision of gemm_tile gets *shallower* than this, the reducer
    // starts to matter and the report will name a path in tile_probe rather than
    // in u_tile. That is visible in the report, not silent - the path is printed
    // - so it does not need guarding against, only noticing.
    // ------------------------------------------------------------------------
    reg [7:0] red0   = 8'd0;
    reg [1:0] stat0  = 2'd0;
    reg       out_q  = 1'b0;

    always @(posedge tile_clk) begin
        red0  <= dout[7:0] ^ dout[15:8] ^ dout[23:16] ^ dout[31:24];
        stat0 <= {dout_valid, busy};
        out_q <= (^red0) ^ (^stat0);
    end

    assign probe_out = out_q;

endmodule

`default_nettype wire
