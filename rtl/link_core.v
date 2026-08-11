// M2 link test core, shared by both pin configurations.
//
// The MCU drives link_clk and WIDTH data lines. The core XOR-reduces the data
// lines to a single bit, pushes it through a LATENCY-deep shift register, and
// returns it inverted on link_ret. The MCU knows what it sent, so it can
// predict the returned stream exactly and count bit errors.
//
// Why XOR-reduce rather than echo every line: the return path is one wire and
// it cannot be widened (see docs/pinmap.md), so a wide forward bus can never be
// echoed in real time. Reducing sidesteps that. The cost is that two lines
// failing in the same clock can alias to no error. That is the right trade
// here, because the failure mode a fast source-synchronous bus actually has is
// one line missing its setup window while the others still make it, and every
// single-line error is caught. Whole-bus failure shows up on ~half of clocks.
//
// The inversion is deliberate: a solder bridge from a data line to the return
// line would loop back with no delay and no inversion, and would otherwise look
// like a perfect link.

`timescale 1ns / 1ps
`default_nettype none

module link_core #(
    parameter integer WIDTH    = 1,
    // link_clk cycles from data in to data out. 8 keeps host-side alignment a
    // whole byte, which makes a hexdump of sent vs received directly readable.
    parameter integer LATENCY  = 8,
    // flag square wave = f(clk_32m) / 2**(FLAG_DIV+1) = 488.28 Hz @ 32 MHz
    parameter integer FLAG_DIV = 15,
    // green LED blink = f(clk_32m) / 2**(LED_DIV+1) = 3.81 Hz @ 32 MHz
    parameter integer LED_DIV  = 22
) (
    input  wire             clk_32m,
    input  wire             link_clk,
    input  wire [WIDTH-1:0] link_data,
    output wire             link_ret,
    output wire             flag,
    output wire             led_r_n,
    output wire             led_g_n,
    output wire             led_b_n
);

    // ---------------------------------------------------------- link domain

    reg [LATENCY-1:0] sr = {LATENCY{1'b0}};

    always @(posedge link_clk) begin
        sr <= (sr << 1) | (^link_data);
    end

    assign link_ret = ~sr[LATENCY-1];

    // Sticky diagnostics. These answer the first two questions you ask when the
    // link returns garbage: did the clock arrive at all, and did every data
    // line move? A line stuck at 0 or 1 - an open jumper, a pin left in SPI
    // mode - is invisible in the XOR-reduced stream until it happens to matter.
    // Both hold until the next reconfiguration.
    reg             link_seen = 1'b0;
    reg [WIDTH-1:0] saw_zero  = {WIDTH{1'b0}};
    reg [WIDTH-1:0] saw_one   = {WIDTH{1'b0}};

    always @(posedge link_clk) begin
        link_seen <= 1'b1;
        saw_one   <= saw_one  |  link_data;
        saw_zero  <= saw_zero | ~link_data;
    end

    // ------------------------------------------------- free-running domain

    // Independent of link_clk on purpose: it separates "the FPGA is not
    // configured" from "the link timing is broken", which look identical from
    // the MCU if the only evidence is a dead return line.
    reg [LED_DIV:0] hb = {(LED_DIV+1){1'b0}};

    always @(posedge clk_32m) begin
        hb <= hb + 1'b1;
    end

    assign flag = hb[FLAG_DIV];

    // All three lit = healthy. Red and blue are status, not error.
    assign led_g_n = hb[LED_DIV];                       // blinking: fabric alive
    assign led_b_n = ~link_seen;                        // lit: link_clk ticked
    assign led_r_n = ~(&saw_zero & &saw_one);           // lit: every line toggled

endmodule

`default_nettype wire
