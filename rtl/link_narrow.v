// M2 configuration A - baseline, no board modification.
//
// Uses only the four on-board wires that survive configuration. 1 bit each way,
// full duplex, with the clock on F3 (CCK). F3 is NOT a global-clock ball on the
// T8F49 - the GCLK balls are B3, C3, E4, E6 - so link_clk here routes on general
// fabric. Fanout is ~11 flops, so that is tolerable, but it is the reason this
// configuration is expected to top out lower than link_wide.
//
//     RP GPIO2 -> F3 (CCK)      link_clk
//     RP GPIO3 -> F2 (CDI0)     link_mosi
//     RP GPIO1 <- G3 (SS_N)     link_miso   direction reverses after DONE
//     RP GPIO6 <- A4 (NSTATUS)  link_flag   free-running heartbeat

`timescale 1ns / 1ps
`default_nettype none

module link_narrow (
    input  wire clk_32m,    // B4
    input  wire link_clk,   // F3
    input  wire link_mosi,  // F2
    output wire link_miso,  // G3
    output wire link_flag,  // A4
    output wire led_r_n,    // G1
    output wire led_g_n,    // F1
    output wire led_b_n     // E1
);

    link_core #(.WIDTH(1)) core (
        .clk_32m  (clk_32m),
        .link_clk (link_clk),
        .link_data(link_mosi),
        .link_ret (link_miso),
        .flag     (link_flag),
        .led_r_n  (led_r_n),
        .led_g_n  (led_g_n),
        .led_b_n  (led_b_n)
    );

endmodule

`default_nettype wire
