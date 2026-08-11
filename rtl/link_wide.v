// M2 configuration C - one jumper, 3 bits forward.
//
// Header pad PIN2 (RP GPIO22) wired to header pad PIN17 (FPGA B3). That single
// jumper buys two things at once:
//
//   1. B3 is GPIOL_16_CLK2, a global-clock ball. link_clk lands on the GCLK
//      network instead of general routing.
//   2. It frees GPIO1/2/3 - the only three contiguous RP GPIO on the board -
//      to be data rather than one-of-them-being-the-clock. PIO takes out_base
//      and sideset_base from independent registers, so a 3-bit `out` group on
//      GPIO1-3 with side-set on GPIO22 is one state machine.
//
// Forward path is 3x wider than link_narrow. The return path stays 1 bit and
// cannot be widened: GPIO5 is CDONE and GPIO7 has no pad, so GPIO6 has no
// contiguous neighbour. link_core XOR-reduces the forward data to fit.
//
//     RP GPIO22 -> PIN2 = jumper = PIN17 -> B3 (CLK2)   link_clk   [GCLK]
//     RP GPIO1  -> G3 (SS_N)                            link_data[0]
//     RP GPIO2  -> F3 (CCK)                             link_data[1]
//     RP GPIO3  -> F2 (CDI0)                            link_data[2]
//     RP GPIO6  <- A4 (NSTATUS)                         link_ret
//
// Bit order matches PIO `out pins, 3`: OSR bit 0 goes to out_base = GPIO1.

`timescale 1ns / 1ps
`default_nettype none

// The data lines are three scalar ports rather than a vector purely so the
// Efinity .isf only needs create_input_gpio/assign_pkg_pin on plain names -
// exactly the calls the vendor example demonstrates. The bus form of that API
// has not been verified against a real Efinity install yet.
module link_wide (
    input  wire clk_32m,   // B4
    input  wire link_clk,  // B3  via jumper
    input  wire link_d0,   // G3  <- RP GPIO1
    input  wire link_d1,   // F3  <- RP GPIO2
    input  wire link_d2,   // F2  <- RP GPIO3
    output wire link_ret,  // A4  -> RP GPIO6
    output wire led_r_n,   // G1
    output wire led_g_n,   // F1
    output wire led_b_n    // E1
);

    // The heartbeat has nowhere to go in this configuration - A4 is carrying
    // return data - so proof-of-life falls back to the blinking green LED.
    wire flag_unused;

    link_core #(.WIDTH(3)) core (
        .clk_32m  (clk_32m),
        .link_clk (link_clk),
        .link_data({link_d2, link_d1, link_d0}),
        .link_ret (link_ret),
        .flag     (flag_unused),
        .led_r_n  (led_r_n),
        .led_g_n  (led_g_n),
        .led_b_n  (led_b_n)
    );

endmodule

`default_nettype wire
