// Probe A - control. Blinks the LEDs off the on-board oscillator and touches
// no configuration pin that the x1 passive interface uses.
//
// This is deliberately the same shape as the vendor's plasm_led, which is known
// to configure. If probe_a configures and link_narrow does not, the toolchain
// is fine and the difference is what link_narrow does with F3/F2/G3/A4. If
// probe_a *also* fails, no pin assignment is to blame and the containerised
// Efinity 2026.1 is producing bitstreams this part will not accept.
//
// See docs/pinmap.md and the M2 bring-up log in docs/milestones.md.

`timescale 1ns / 1ps
`default_nettype none

module probe_a (
    input  wire clk_32m,    // B4
    output wire led_r_n,    // E1
    output wire led_g_n,    // F1
    output wire led_b_n     // G1
);

    // ~32 MHz / 2^23 is a visible blink. The LEDs are common-anode, so the
    // cathode is driven low to light.
    reg [23:0] cnt;
    always @(posedge clk_32m)
        cnt <= cnt + 1'b1;

    assign led_r_n = ~cnt[23];
    assign led_g_n = 1'b1;
    assign led_b_n = 1'b1;

endmodule

`default_nettype wire
