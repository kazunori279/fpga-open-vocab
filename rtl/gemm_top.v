// M6 top level - configuration A, no board modification.
//
// Same four wires M2 measured at 8.94 MB/s, same pin assignment, same
// link_narrow port list. What changed is what sits behind them: instead of a
// shift register that echoes the stream back inverted, there is a framing layer
// and an 8-lane int8 MAC array.
//
//     RP GPIO2 -> F3 (CCK)      link_clk    the tile's only clock
//     RP GPIO3 -> F2 (CDI0)     link_mosi   commands
//     RP GPIO1 <- G3 (SS_N)     link_miso   responses
//     RP GPIO6 <- A4 (NSTATUS)  link_flag   free-running heartbeat
//
// Two clock domains and one bit crossing between them. Everything that computes
// runs on link_clk; clk_32m carries only the heartbeat and the LEDs, exactly as
// in link_core.v, and for the same reason: a board that is unconfigured and a
// board that is misclocked look identical from the MCU if the only evidence is
// a dead return line.

`timescale 1ns / 1ps
`default_nettype none

module gemm_top #(
    parameter integer NMAC   = 8,
    parameter integer ADEPTH = 256,
    parameter integer WDEPTH = 256,
    parameter integer STRIPD = 2048,
    parameter integer AW     = 11,
    parameter integer CW     = 10,
    parameter integer PW     = 8,
    parameter integer GW     = 5,
    parameter integer KW     = 10,
    // M14 had a WNIB parameter here for one afternoon, to build the narrow-wbuf
    // form beside the wide one and compare in a congested top. It is gone
    // because the choice is not open: conv0's weights are 8-bit and conv0 runs
    // on this tile, so a 4*NMAC-bit weight buffer cannot serve the layer set
    // this bitstream has to serve, whatever it measures. The width is a CFG bit
    // now - see gemm_tile.v's cfg_w4 - and there is nothing to parameterize.
    // tile_probe.v keeps WNIB, because APACK = 2's gate needs it to elaborate.

    // M15. Unlike WNIB this one *is* open, because there is a working
    // bitstream on either side of it: at RQ = 0 the requantize engine is not
    // built and DRAIN is the int32 readout M14 shipped, and at RQ = 1 both
    // paths exist and cfg_rq picks between them per block. It is a parameter
    // and not simply always-on because Stage 1 measured the engine at 13-15%
    // of tile_probe's Fmax, and until a board has run the RQ = 1 bitstream the
    // fallback has to be one build flag away:
    //
    //     ./build.sh gemm_top                         the M14 netlist
    //     TOP_PARAMS="RQ=1" ./build.sh gemm_top       M15
    parameter integer RQ = 0,

    // M16, and open for the same reason RQ is: two working bitstreams, one build
    // flag apart. At KPACK = 1 each lane does two kernel taps per sweep - the
    // second on a logic-element multiplier, since all eight DSPs are spoken for -
    // and the tile needs 1.408x fewer cycles for the identical answer. It is not
    // free: LE goes 4,650 -> ~6,230 of 7,384 on this device, so if a later
    // milestone needs the fabric back, this is the switch that returns it.
    //
    //     TOP_PARAMS="RQ=1" ./build.sh gemm_top            M15
    //     TOP_PARAMS="RQ=1,KPACK=1" ./build.sh gemm_top    M16
    parameter integer KPACK = 0,

    // green LED blink = f(clk_32m) / 2**(LED_DIV+1) = 3.81 Hz @ 32 MHz
    parameter integer LED_DIV  = 22,
    // flag square wave = f(clk_32m) / 2**(FLAG_DIV+1) = 488.28 Hz @ 32 MHz
    parameter integer FLAG_DIV = 15
) (
    input  wire clk_32m,    // B4
    input  wire link_clk,   // F3
    input  wire link_mosi,  // F2
    output wire link_miso,  // G3
    output wire link_flag,  // A4
    output wire led_r_n,    // G1 - see the isf: the schematic and the vendor
    output wire led_g_n,    // F1   example disagree on red vs blue
    output wire led_b_n     // E1
);

    // ---- link domain --------------------------------------------------------
    wire signed [CW-1:0] cfg_H, cfg_W, cfg_OW, cfg_oy0_s, cfg_ox0_s;
    wire        [AW-1:0] cfg_strip_rw, cfg_strip_ch;
    wire        [PW-1:0] cfg_P;
    wire        [GW-1:0] cfg_QG;
    wire        [KW-1:0] cfg_K;
    wire                 cfg_stride2, cfg_unsigned_in, cfg_w4, cfg_rq;

    wire            act_we, wgt_we, wgt_rst, run, run_init, drain;
    wire [AW-1:0]   act_addr;
    wire [7:0]      act_data, wgt_data;
    wire            busy, dout_valid, dout_ready;
    wire [31:0]     dout;
    wire            dbg_seen, dbg_err;
    wire [7:0]      led_r_duty, led_g_duty;
    wire            led_own;

    gemm_link #(
        .WIDTH(1), .AW(AW), .CW(CW), .PW(PW), .GW(GW), .KW(KW),
        .ACTMAX(STRIPD), .WGTMAX(WDEPTH * NMAC)
    ) u_link (
        .clk            (link_clk),
        .rx             (link_mosi),
        .tx             (link_miso),
        .cfg_H          (cfg_H),
        .cfg_W          (cfg_W),
        .cfg_OW         (cfg_OW),
        .cfg_stride2    (cfg_stride2),
        .cfg_strip_rw   (cfg_strip_rw),
        .cfg_strip_ch   (cfg_strip_ch),
        .cfg_oy0        (cfg_oy0_s),
        .cfg_ox0        (cfg_ox0_s),
        .cfg_unsigned_in(cfg_unsigned_in),
        .cfg_w4         (cfg_w4),
        .cfg_rq         (cfg_rq),
        .cfg_P          (cfg_P),
        .cfg_QG         (cfg_QG),
        .cfg_K          (cfg_K),
        .act_we         (act_we),
        .act_addr       (act_addr),
        .act_data       (act_data),
        .wgt_we         (wgt_we),
        .wgt_data       (wgt_data),
        .wgt_rst        (wgt_rst),
        .run            (run),
        .run_init       (run_init),
        .busy           (busy),
        .drain          (drain),
        .dout           (dout),
        .dout_valid     (dout_valid),
        .dout_ready     (dout_ready),
        .dbg_seen       (dbg_seen),
        .dbg_err        (dbg_err),
        .led_r_duty     (led_r_duty),
        .led_g_duty     (led_g_duty),
        .led_own        (led_own)
    );

    gemm_tile #(
        .NMAC(NMAC), .ADEPTH(ADEPTH), .WDEPTH(WDEPTH), .STRIPD(STRIPD),
        .AW(AW), .CW(CW), .PW(PW), .GW(GW), .KW(KW), .RQ(RQ),
        .KPACK(KPACK)
    ) u_tile (
        .clk            (link_clk),
        .cfg_H          (cfg_H),
        .cfg_W          (cfg_W),
        .cfg_OW         (cfg_OW),
        .cfg_stride2    (cfg_stride2),
        .cfg_strip_rw   (cfg_strip_rw),
        .cfg_strip_ch   (cfg_strip_ch),
        .cfg_oy0        (cfg_oy0_s),
        .cfg_ox0        (cfg_ox0_s),
        .cfg_unsigned_in(cfg_unsigned_in),
        .cfg_w4         (cfg_w4),
        // M15. At RQ = 0 the tile folds this away whatever the host sends, and
        // the netlist is the one M14 shipped - so a bitstream built without the
        // parameter simply ignores the cfg bit rather than misbehaving on it.
        .cfg_rq         (cfg_rq),
        .cfg_P          (cfg_P),
        .cfg_QG         (cfg_QG),
        .cfg_K          (cfg_K),
        .act_we         (act_we),
        .act_addr       (act_addr),
        .act_data       (act_data),
        .wgt_we         (wgt_we),
        .wgt_data       (wgt_data),
        .wgt_rst        (wgt_rst),
        .run            (run),
        .run_init       (run_init),
        .busy           (busy),
        .drain          (drain),
        .dout           (dout),
        .dout_valid     (dout_valid),
        .dout_ready     (dout_ready)
    );

    // ---- free-running domain ------------------------------------------------
    reg [LED_DIV:0] hb = {(LED_DIV+1){1'b0}};

    always @(posedge clk_32m) begin
        hb <= hb + 1'b1;
    end

    assign link_flag = hb[FLAG_DIV];

    // The diagnostic crossings carry a signal that changes at most once per
    // configuration, so two flops are the whole synchroniser.
    reg [1:0] seen_s = 2'b00;
    reg [1:0] err_s  = 2'b00;

    always @(posedge clk_32m) begin
        seen_s <= {seen_s[0], dbg_seen};
        err_s  <= {err_s[0],  dbg_err};
    end

    // ---- D1 as a score meter (M11) ------------------------------------------
    // The MCU sends two duties, one per frame; this turns them into light. The
    // colour was decided in firmware/m9.c and nothing here knows what a score
    // is - the split is deliberate and m9.c's led_map() comment argues it.
    //
    // THE DUTIES ARE A MULTI-BIT CROSSING, which the paragraph above used to be
    // able to say did not exist here. It can tear, and the tear is harmless
    // twice over. link_clk is stopped between frames - the MCU is its only
    // source - so each byte is static except during the ~2 us of an LED frame;
    // and the slew limiter below moves one LSB per millisecond, so a target
    // that is wrong for one 31 ns cycle cannot be reached before it is right
    // again. Nothing downstream integrates it.
    reg [7:0] r_s0 = 8'h00, r_s1 = 8'h00;
    reg [7:0] g_s0 = 8'h00, g_s1 = 8'h00;
    reg [1:0] own_s = 2'b00;

    // Current duty, walked toward the target rather than assigned to it. At
    // ~1 frame per second an assignment would step; a step of one LSB every
    // 32768 clocks is 1.024 ms, so a full 0 -> 255 sweep glides over 261 ms and
    // is finished well before the next frame arrives.
    reg [7:0] r_cur = 8'h00, g_cur = 8'h00;

    // hb is the counter, reused rather than duplicated: hb[7:0] is the PWM
    // phase at 32 MHz / 256 = 125 kHz, and the slew tick is the one clock in
    // 32768 where the low 15 bits are zero. Reusing it costs no registers and
    // buys one real property - the tick lands exactly when hb[7:0] wraps, so a
    // duty never changes in the middle of a pulse. Needs LED_DIV >= 15, which
    // is 22.
    wire slew_tick = ~|hb[14:0];

    always @(posedge clk_32m) begin
        r_s0 <= led_r_duty;  r_s1 <= r_s0;
        g_s0 <= led_g_duty;  g_s1 <= g_s0;
        own_s <= {own_s[0], led_own};

        if (slew_tick) begin
            if (r_cur != r_s1) r_cur <= r_cur + (r_cur < r_s1 ? 8'd1 : 8'hff);
            if (g_cur != g_s1) g_cur <= g_cur + (g_cur < g_s1 ? 8'd1 : 8'hff);
        end
    end

    // Common anode, so the cathode is driven low to light the channel.
    wire r_on = (hb[7:0] < r_cur);
    wire g_on = (hb[7:0] < g_cur);

    // Two modes, and the legacy one is bit-identical to what shipped through
    // M10 - that is the point of `led_own` rather than a build flag. m7 and m8
    // never send an LED command, so every meaning in docs/pinmap.md and the
    // bring-up log still holds for the whole of bring-up, which is exactly when
    // they are the only diagnostic there is.
    //
    // A fault still wins, and it blinks. Solid red is now a hot score, so the
    // two had to stop looking alike.
    assign led_r_n = own_s[1] ? (err_s[1] ? hb[LED_DIV] : ~r_on)
                              : ~err_s[1];   // lit: a frame or drain fault
    assign led_g_n = own_s[1] ? (err_s[1] ? 1'b1        : ~g_on)
                              : hb[LED_DIV]; // blinking: fabric alive
    assign led_b_n = own_s[1] ? 1'b1
                              : ~seen_s[1];  // lit: link_clk has ticked

endmodule

`default_nettype wire
