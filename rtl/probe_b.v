// Probe B - probe_a plus one output on A4 (NSTATUS).
//
// A4 is the first of the four active x1-passive configuration pins that
// link_narrow reuses. It is also the least likely to matter: NSTATUS is only an
// error indication, not part of the data path. If B fails where A passed, the
// config engine objects to a user output on NSTATUS.

`timescale 1ns / 1ps
`default_nettype none

module probe_b (
    input  wire clk_32m,    // B4
    output wire link_flag,  // A4  NSTATUS
    output wire led_r_n,    // E1
    output wire led_g_n,    // F1
    output wire led_b_n     // G1
);

    reg [23:0] cnt;
    always @(posedge clk_32m)
        cnt <= cnt + 1'b1;

    assign link_flag = cnt[10];   // heartbeat, same role as in link_narrow
    assign led_r_n = ~cnt[23];
    assign led_g_n = 1'b1;
    assign led_b_n = 1'b1;

endmodule

`default_nettype wire
