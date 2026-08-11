// M6: im2col address generation, in the fabric.
//
// This module is the reason M6 is worth building. A 3x3 convolution makes every
// input byte appear in up to 9 im2col columns; expanding on the MCU and shipping
// the columns spends that 9x on the link, which is the scarcest resource in the
// system (8.94 MB/s measured, M2). Expanding here spends it on BRAM reads, which
// are free. It is the difference between ~1.06 s and ~400 ms per frame.
//
// What it does: given a position index p within the current output block and a
// tap index k = (ic_local, ky, kx), produce the address of that tap inside the
// activation strip - or assert `zero` if the tap falls outside the input tensor.
//
// **Zero, not skip.** fgx_conv_ref() in firmware/encoder.c *skips* out-of-range
// taps (`if (iy < 0 || iy >= H) continue;`). Adding zero and skipping are the
// same thing, and this holds for the unsigned_in layers too: the reference
// contributes nothing at all for those taps, rather than contributing whatever
// code 0 dequantizes to. So injecting a literal zero byte here is bit-exact, and
// it is what lets the datapath stay a fixed-latency pipeline with no stalls.
//
// **No multipliers, and that is a hard requirement rather than an
// optimization.** The T8F49 has eight, all eight are committed to the MAC array
// in gemm_tile.v, and there is no ninth. So the two products this needs -
// channel base `ic*strip_ch` and row base `ky*strip_rw` - are not computed here
// at all: gemm_tile walks k as three nested counters and accumulates both bases
// with adders as those counters tick, handing the sum in as `ld_base`. What is
// left inside this module is an add per cycle and a stride that is 1 or 2, so
// `*stride` is a shift-mux on `cfg_stride2`.

`timescale 1ns / 1ps
`default_nettype none

module im2col_feed #(
    // Strip address width. 2 KB of activation strip is the budget; 11 bits
    // covers it and the synthesis report says whether it fit.
    parameter integer AW = 11,
    // Widest spatial coordinate the geometry registers must hold. Signed, so
    // pad-induced -1 is representable without a special case.
    parameter integer CW = 10
) (
    input  wire                 clk,

    // ---- geometry, latched by the caller at CFG time -----------------------
    input  wire signed [CW-1:0] cfg_H,        // full input height, for bounds
    input  wire signed [CW-1:0] cfg_W,        // full input width, for bounds
    input  wire signed [CW-1:0] cfg_OW,       // output row length
    input  wire                 cfg_stride2,  // 1 = stride 2, 0 = stride 1
    input  wire        [AW-1:0] cfg_strip_rw, // bytes per strip row  = W

    // ---- per-sweep setup ---------------------------------------------------
    // Asserted for one cycle before a p sweep begins. Latches the tap and the
    // sweep's starting output position; the sweep then needs no further input.
    input  wire                 load,
    input  wire        [CW-1:0] ld_oy,        // output row of position p=0
    input  wire        [CW-1:0] ld_ox,        // output column of position p=0
    input  wire        [1:0]    ld_ky,
    input  wire        [1:0]    ld_kx,
    // Strip offset of (this tap's input channel, this tap's ky) - i.e.
    // ic_local*strip_ch + ky*strip_rw, accumulated by gemm_tile so that no
    // multiplier is needed. See the header.
    input  wire        [AW-1:0] ld_base,

    // ---- sweep -------------------------------------------------------------
    // `step` advances to the next output position. `addr`/`zero` are valid
    // combinationally for the current position, so the caller registers them
    // into the strip BRAM's address port on the same edge it asserts step.
    input  wire                 step,
    output wire        [AW-1:0] addr,
    output wire                 zero
);

    // Running state. Held signed so the pad row/column at -1 compares correctly
    // against 0 without a separate "is padding" flag - the bounds test below is
    // then literally the reference's two `continue` conditions.
    reg signed [CW-1:0] oy_r, ox_r;   // output coordinate
    reg signed [CW-1:0] iy_r, ix_r;   // input coordinate of this tap
    reg signed [CW-1:0] ix_row0;      // ix_r at the start of an output row
    reg        [AW-1:0] rowb;         // strip offset of iy_r

    // How far the strip pointer moves when the output column wraps to the next
    // output row. Registered at load so it is a mux, not a multiply.
    reg        [AW-1:0] row_advance;

    // cfg_OW is written by a CFG frame and does not move for the rest of the
    // run, so the subtract belongs on the config side of a register rather than
    // inside the sweep. ow_m1 seeds `wrap` at load; ow_m2 lets it predict.
    reg signed [CW-1:0] ow_m1, ow_m2;
    reg                 ow_one;

    // `ox_r == cfg_OW - 1`, held one cycle ahead of when it is read. Spelled
    // out at the point of use it was a CW-bit subtract and a CW-bit compare
    // sitting between cfg_OW's flops and the clock enables of rowb, ix_r and
    // iy_r: five LUT levels and 18.354 ns end to end, of which every single
    // nanosecond was routing - 5.316 ns just to leave cfg_OW, and 4.600 ns on
    // the 22-sink enable at the end.
    //
    // Unlike gemm_tile's last_pos this cannot free-run. ox_r only moves when
    // `step` is asserted, so a compare against ox_r evaluated every cycle would
    // not track it; the update is gated exactly the way ox_r's own is.
    reg wrap;

    always @(posedge clk) begin
        ow_m1  <= cfg_OW - 1;
        ow_m2  <= cfg_OW - 2;
        ow_one <= (cfg_OW == 1);
    end

    wire signed [CW-1:0] ky_s = {{(CW-2){1'b0}}, ld_ky};
    wire signed [CW-1:0] kx_s = {{(CW-2){1'b0}}, ld_kx};

    // oy*stride and ox*stride, where stride is 1 or 2.
    wire signed [CW-1:0] oy_scaled = cfg_stride2 ? {ld_oy[CW-2:0], 1'b0} : ld_oy;
    wire signed [CW-1:0] ox_scaled = cfg_stride2 ? {ld_ox[CW-2:0], 1'b0} : ld_ox;
    wire signed [CW-1:0] step_inc  = cfg_stride2 ? 2 : 1;

    always @(posedge clk) begin
        if (load) begin
            oy_r        <= ld_oy;
            ox_r        <= ld_ox;
            // pad is 1, matching fgx_conv_ref()'s `const int pad = 1`.
            iy_r        <= oy_scaled - 1 + ky_s;
            ix_r        <= ox_scaled - 1 + kx_s;
            ix_row0     <= -1 + kx_s;
            rowb        <= ld_base;
            row_advance <= cfg_stride2 ? {cfg_strip_rw[AW-2:0], 1'b0}
                                       : cfg_strip_rw;
            wrap        <= (ld_ox == ow_m1);
        end else if (step) begin
            // ox_r goes to 0 on a wrap, so the next position is the row end only
            // if the row is one column long; otherwise it is one short of it.
            wrap <= wrap ? ow_one : (ox_r == ow_m2);
            if (wrap) begin
                // Output row wrap. This is where the halo lives: the next row's
                // taps reach back into strip rows the previous row already used,
                // which is correct only because the strip is addressed in its
                // own coordinates and the strip origin does not move mid-block.
                ox_r <= 0;
                oy_r <= oy_r + 1;
                ix_r <= ix_row0;
                iy_r <= iy_r + step_inc;
                rowb <= rowb + row_advance;
            end else begin
                ox_r <= ox_r + 1;
                ix_r <= ix_r + step_inc;
            end
        end
    end

    // The two bounds tests, in the same order and with the same semantics as
    // encoder.c:113 and encoder.c:117. Note these test against the FULL tensor
    // (cfg_H, cfg_W), not against the strip - padding is a property of the
    // image, and a tap that lands on a real row the strip happens not to hold
    // would be a blocking bug, not a pad. The testbench checks for exactly that.
    wire iy_ok = (iy_r >= 0) && (iy_r < cfg_H);
    wire ix_ok = (ix_r >= 0) && (ix_r < cfg_W);

    assign zero = ~(iy_ok & ix_ok);

    // Zero-extended rather than sign-extended, and deliberately so: when ix_r is
    // the pad column at -1 this wraps to a large offset, but `zero` is asserted
    // in exactly that case and gemm_tile substitutes a literal zero for whatever
    // byte comes back. The address is a don't-care there, so the cheap extension
    // is the correct one. Requires AW >= CW.
    wire [AW-1:0] ix_pad = {{(AW-CW){1'b0}}, ix_r};
    assign addr = rowb + ix_pad;

endmodule

`default_nettype wire
