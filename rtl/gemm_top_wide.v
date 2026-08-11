// M7f top level - configuration C, which needs the PIN2 <-> PIN17 jumper.
//
// Same tile, same framing layer, three forward data lines instead of one:
//
//     RP GPIO22 -> B3 (CLK2)     link_clk    the tile's only clock
//     RP GPIO1  -> G3 (SS_N)     link_d0     commands, lane 0 (oldest)
//     RP GPIO2  -> F3 (CCK)      link_d1
//     RP GPIO3  -> F2 (CDI0)     link_d2
//     RP GPIO6  <- A4 (NSTATUS)  link_ret    responses
//
// Two differences from gemm_top.v, and both follow from the pin budget rather
// than from any design preference:
//
//   - **link_clk is on B3, a global-clock ball.** That is the whole reason for
//     the jumper. Configuration A puts link_clk on F3, which is general fabric,
//     and gemm_top.timing.rpt reports the clock-skew line as the number to read
//     before the Fmax line because of it. B3 is the only global-clock ball the
//     RP2354A can reach on this board, and only through PIN2.
//
//   - **There is no heartbeat output.** A4 is carrying return data, so nothing
//     is left to drive it with. Proof-of-life falls back to the blinking green
//     LED, exactly as link_wide.v documents for the same reason. That loses the
//     one signal that distinguishes "unconfigured" from "misclocked" without
//     the MCU's help, which is a real cost and the reason configuration A is
//     still the one gemm_top.v builds.
//
// The return path is one bit wide in both configurations, so DRAIN and the RUN
// idle clocks are unchanged by this file. Only ACT and WGT get faster.

`timescale 1ns / 1ps
`default_nettype none

module gemm_top_wide #(
    parameter integer NMAC   = 8,
    parameter integer ADEPTH = 256,
    parameter integer WDEPTH = 256,
    parameter integer STRIPD = 2048,
    parameter integer AW     = 11,
    parameter integer CW     = 10,
    parameter integer PW     = 8,
    parameter integer GW     = 5,
    parameter integer KW     = 10,
    // M15, and see gemm_top.v for what it is and why it is a build flag:
    //     TOP_PARAMS="RQ=1" ./build.sh gemm_top_wide
    parameter integer RQ     = 0,
    // M16, likewise:
    //     TOP_PARAMS="RQ=1,KPACK=1" ./build.sh gemm_top_wide
    parameter integer KPACK  = 0,
    // green LED blink = f(clk_32m) / 2**(LED_DIV+1) = 3.81 Hz @ 32 MHz
    parameter integer LED_DIV = 22
) (
    input  wire clk_32m,    // B4
    input  wire link_clk,   // B3  via the PIN2 <-> PIN17 jumper
    input  wire link_d0,    // G3
    input  wire link_d1,    // F3
    input  wire link_d2,    // F2
    output wire link_ret,   // A4
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

    // Three scalar ports rather than a vector, so the .isf only needs
    // create_input_gpio / assign_pkg_pin on plain names - the same choice
    // link_wide.v made, and for the same unverified-API reason.
    //
    // Lane order is time order: d0 is the oldest of the three bits that arrive
    // together, which is what the PIO's `out pins, 3` puts on out_base.
    gemm_link #(
        .WIDTH(3), .AW(AW), .CW(CW), .PW(PW), .GW(GW), .KW(KW),
        .ACTMAX(STRIPD), .WGTMAX(WDEPTH * NMAC)
    ) u_link (
        .clk            (link_clk),
        .rx             ({link_d2, link_d1, link_d0}),
        .tx             (link_ret),
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
        // M15, and see gemm_top.v: harmless at RQ = 0, which is what keeps the
        // parameter a real fallback rather than a fork of this file.
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

    // The diagnostic crossings carry a signal that changes at most once per
    // configuration, so two flops are the whole synchroniser.
    reg [1:0] seen_s = 2'b00;
    reg [1:0] err_s  = 2'b00;

    always @(posedge clk_32m) begin
        seen_s <= {seen_s[0], dbg_seen};
        err_s  <= {err_s[0],  dbg_err};
    end

    // ---- D1 as a score meter (M11) ------------------------------------------
    // gemm_top.v's block, verbatim, and the reasoning is all there: why the
    // multi-bit crossing is safe to tear, why the slew limiter exists, why hb
    // is reused for both the PWM phase and the slew tick, and why `led_own`
    // leaves every bring-up meaning intact until the host asks for D1.
    reg [7:0] r_s0 = 8'h00, r_s1 = 8'h00;
    reg [7:0] g_s0 = 8'h00, g_s1 = 8'h00;
    reg [1:0] own_s = 2'b00;
    reg [7:0] r_cur = 8'h00, g_cur = 8'h00;

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

    wire r_on = (hb[7:0] < r_cur);
    wire g_on = (hb[7:0] < g_cur);

    assign led_r_n = own_s[1] ? (err_s[1] ? hb[LED_DIV] : ~r_on)
                              : ~err_s[1];   // lit: a frame or drain fault
    assign led_g_n = own_s[1] ? (err_s[1] ? 1'b1        : ~g_on)
                              : hb[LED_DIV]; // blinking: fabric alive
    assign led_b_n = own_s[1] ? 1'b1
                              : ~seen_s[1];  // lit: link_clk has ticked

endmodule

`default_nettype wire
