// Probe C - probe_b plus the two configuration *inputs*: F3 (CCK) and F2
// (CDI0).
//
// This is the interesting rung. During configuration the MCU is driving CCK as
// the SPI clock and CDI0 as the SPI data, and here the fabric also takes CCK as
// a clock domain - exactly what link_narrow does. If C fails where B passed,
// the link cannot use the configuration clock pin as its clock and the M2
// pin plan needs rethinking, not just this bitstream.

`timescale 1ns / 1ps
`default_nettype none

module probe_c (
    input  wire clk_32m,    // B4
    input  wire link_clk,   // F3  CCK
    input  wire link_mosi,  // F2  CDI0
    output wire link_flag,  // A4  NSTATUS
    output wire led_r_n,    // E1
    output wire led_g_n,    // F1
    output wire led_b_n     // G1
);

    reg [23:0] cnt;
    always @(posedge clk_32m)
        cnt <= cnt + 1'b1;

    // Clocked by link_clk so the CCK ball is a real clock input rather than
    // something synthesis can fold away.
    reg sampled;
    always @(posedge link_clk)
        sampled <= link_mosi;

    assign link_flag = cnt[10];
    assign led_r_n = ~cnt[23];
    assign led_g_n = ~sampled;
    assign led_b_n = 1'b1;

endmodule

`default_nettype wire
